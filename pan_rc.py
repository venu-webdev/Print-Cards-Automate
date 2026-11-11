"""
PAN card (e-PAN / Instant e-PAN) and Vehicle RC exports.

PAN : the PDF is an A4 letter; at the bottom are the printable front & back cut-outs.
      We find the two cards from the page itself, make the signature / thumb-impression
      image transparent (no white box), render each card sharp from the PDF, fill the
      rounded corners with the card's own background, and size it like the DL exports.
RC  : each page has one card-sized artwork at the top-left; it is rendered as-is.
"""
import re

from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

RENDER_DPI = 600
OUT_W, OUT_H = 1016, 638
ASPECT = OUT_W / OUT_H
PAN_RE = re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b")

# signature / thumb impression -> transparent
INK_FLOOR = 0.12          # alpha below this is paper noise -> fully transparent
EDGE_CLEAR_PX = 3         # outermost pixels of the signature image are always cleared
CORNER_EXTRA_MM = 0.8     # fill a little beyond the measured corner arc (bevel highlight)



# ------------------------------------------------------------------ detection
def is_rc(doc):
    t = " ".join(p.get_text() for p in doc).lower()
    return "vehicle registration" in t or ("regn" in t and "chassis" in t)


def is_pan(doc):
    t = doc[0].get_text()
    tl = t.lower()
    return bool(PAN_RE.search(t)) and ("income tax" in tl or "permanent account" in tl
                                       or "digitally signed" in tl)


# ------------------------------------------------------------------ helpers
def _runs(mask, minlen):
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if j - i >= minlen:
                out.append((i, j))
            i = j
        else:
            i += 1
    return out


def _fit_aspect(r):
    """Trim (never stretch) a rect to the export aspect, centred."""
    if r.width / r.height > ASPECT:
        d = (r.width - r.height * ASPECT) / 2
        return pymupdf.Rect(r.x0 + d, r.y0, r.x1 - d, r.y1)
    d = (r.height - r.width / ASPECT) / 2
    return pymupdf.Rect(r.x0, r.y0 + d, r.x1, r.y1 - d)


def _render(page, rect, dpi=RENDER_DPI):
    pix = page.get_pixmap(dpi=dpi, clip=rect, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()


def _is_paper(rgb):
    a = rgb.astype(np.int16)
    return (a.mean(2) > 225) & ((a.max(2) - a.min(2)) < 30)


# ------------------------------------------------------------------ PAN
def find_pan_cards(page, dpi=150):
    """Front & back card rectangles (PDF coords) in the lower part of the page."""
    a = _render(page, page.rect, dpi)
    H, W = a.shape[:2]
    ppm = dpi / 25.4
    fg = ~_is_paper(a)
    top = int(0.5 * H)
    rows = _runs(fg[top:].mean(1) > 0.55, int(30 * ppm))
    if not rows:
        raise ValueError("PAN layout not recognised (no card cut-outs found)")
    y0, y1 = max(rows, key=lambda r: r[1] - r[0])
    y0, y1 = y0 + top, y1 + top
    # columns: a real gap between the cards is (almost) all paper over the card height
    cols = _runs(fg[y0:y1].mean(0) > 0.15, int(40 * ppm))
    cols = sorted(sorted(cols, key=lambda r: r[1] - r[0], reverse=True)[:2])
    if len(cols) < 2:
        raise ValueError("PAN layout not recognised (front / back not separated)")
    cards = []
    for x0, x1 in cols:
        rr = [r for r in _runs(fg[:, x0:x1].mean(1) > 0.3, int(30 * ppm)) if r[0] < y1 and r[1] > y0]
        cy0, cy1 = max(rr, key=lambda r: r[1] - r[0])
        s = 72 / dpi
        cards.append(pymupdf.Rect(x0 * s, cy0 * s, x1 * s, cy1 * s))
    return cards


def _edge_depth(profile, ppm, search_mm=4.5):
    """profile: grey values from outside (paper) inward -> index of the first card pixel.
    The card starts where non-paper continues for >= 1 mm. If the card is drawn with a
    bright rim (Instant e-PAN: shadow, dark outline, white rim) the edge is just after the
    last bright pixel that is directly followed by the card."""
    prof = np.asarray(profile, np.float32)
    L = max(3, int(1.0 * ppm))
    card = (prof <= 225).astype(np.float32)
    if len(card) < L + 2:
        return 0
    win = np.convolve(card, np.ones(L) / L, "valid")          # win[i] = card share of i..i+L
    lim = min(len(win) - 2, int(search_mm * ppm))
    rim = [k for k in range(lim) if prof[k] > 236 and win[k + 1] >= 0.75]
    if rim:
        return rim[-1] + 2
    hits = np.nonzero(win >= 0.85)[0]
    if not len(hits):
        return 0
    i = int(hits[0])
    while i < len(card) and not card[i]:
        i += 1
    return i + 1


def _side_depths(g, ppm):
    """Edge depth of each side, measured in 4 bands; the earliest card start wins
    (content near an edge, like a white QR box, can only make a band later)."""
    h, w = g.shape
    bands = [(.1, .3), (.3, .5), (.5, .7), (.7, .9)]
    def best(fn):
        return min(fn(lo, hi) for lo, hi in bands)
    l = best(lambda lo, hi: _edge_depth(np.median(g[int(h * lo):int(h * hi), :], 0), ppm))
    r = best(lambda lo, hi: _edge_depth(np.median(g[int(h * lo):int(h * hi), ::-1], 0), ppm))
    t = best(lambda lo, hi: _edge_depth(np.median(g[:, int(w * lo):int(w * hi)], 1), ppm))
    b = best(lambda lo, hi: _edge_depth(np.median(g[::-1, int(w * lo):int(w * hi)], 1), ppm))
    return l, r, t, b


def refine_card(page, rect, dpi=300):
    """Exact inner card edges (past any outline / white rim) -> (rect, corner radius mm)."""
    pad = 1.2 / 25.4 * 72              # less than half the gap between front and back
    big = pymupdf.Rect(rect.x0 - pad, rect.y0 - pad, rect.x1 + pad, rect.y1 + pad) & page.rect
    g = _render(page, big, dpi).mean(2)
    h, w = g.shape
    ppm = dpi / 25.4
    l, r, t, b = _side_depths(g, ppm)
    # corner radius from the diagonals (distance to the arc = R * (sqrt2 - 1))
    radii = []
    for (y0, x0, dy, dx) in [(t, l, 1, 1), (t, w - 1 - r, 1, -1), (h - 1 - b, l, -1, 1), (h - 1 - b, w - 1 - r, -1, -1)]:
        diag = []
        for k in range(int(8 * ppm)):
            yy, xx = y0 + dy * k, x0 + dx * k
            if not (0 <= yy < h and 0 <= xx < w):
                break
            diag.append(g[yy, xx])
        diag = np.array(diag)
        if len(diag):
            dd = _edge_depth(diag, ppm)
            radii.append(dd * np.sqrt(2) / (np.sqrt(2) - 1) / ppm)
    s = 72 / dpi
    inner = pymupdf.Rect(big.x0 + l * s, big.y0 + t * s, big.x1 - r * s, big.y1 - b * s)
    rad = float(np.median(radii)) if radii else 0.0
    return inner, float(np.clip(rad, 1.5, 4.5))


def _transparent_ink(rgb):
    """Colour-to-alpha against the (uneven) paper colour of a scanned signature."""
    a = rgb.astype(np.float32)
    k = max(15, (min(a.shape[:2]) // 3) | 1)
    paper = np.stack([cv2.dilate(a[..., c], np.ones((k, k), np.uint8)) for c in range(3)], -1)
    paper = np.maximum(cv2.GaussianBlur(paper, (0, 0), k / 3), a)
    alpha = np.max((paper - a) / np.maximum(paper, 1), axis=2)
    alpha = np.clip((alpha - INK_FLOOR) / (1 - INK_FLOOR), 0, 1)
    e = EDGE_CLEAR_PX
    alpha[:e], alpha[-e:], alpha[:, :e], alpha[:, -e:] = 0, 0, 0, 0
    al = alpha[..., None]
    col = np.where(al > 0.01, (a - (1 - al) * paper) / np.maximum(al, 1e-3), 0)
    return np.dstack([np.clip(col, 0, 255), alpha * 255]).round().astype(np.uint8)


# The shop's wish (marked on a card): the signature sits in a box right above the printed
# "हस्ताक्षर / Signature" label - the box is SIGN_BOX_W x the label's width, SIGN_BOX_H of the card height,
# its bottom SIGNATURE_GAP_PT above the label. The signature is trimmed to its ink, fitted into the box
# (shape kept, never enlarged), centred on the label, and its strokes made SIGNATURE_SOLID x stronger.
SIGN_BOX_W = 1.7
SIGN_BOX_H = 0.078
SIGNATURE_GAP_PT = 1.7         # 0.6 mm between the signature and the label
SIGNATURE_SOLID = 3.0          # faint e-PAN signatures (ink ~40 % opaque) -> solid
SIGNATURE_SCALE = 0.7          # only if the label is not found: 70 % size at the old spot


def _label_box(page, raw, card_rect):
    """The printed 'हस्ताक्षर / Signature' label under the signature spot -> Rect (pt) or None.
    It is part of the card artwork, so it is found by its dark lettering in a render (the old
    signature has been blanked by then, so only the label is dark there)."""
    w = card_rect.width
    clip = pymupdf.Rect(card_rect.x0 + 0.22 * w, raw.y0 + raw.height * 0.3,
                        card_rect.x0 + 0.72 * w, min(card_rect.y1, raw.y1 + raw.height * 1.6)) & page.rect
    pix = page.get_pixmap(dpi=300, clip=clip, alpha=False)
    dark = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).mean(axis=2) < 140
    per_row = dark.sum(axis=1)
    peak = int(per_row.argmax())
    if per_row[peak] < 20:                             # no line of lettering here
        return None
    # the label = the block of rows around the densest row; the card's watermark only gives a few
    # stray dark pixels per row (<= 7 measured) against 70-100 for the lettering
    keep = per_row >= max(8, 0.08 * per_row[peak])
    top = bottom = peak
    while top > 0 and keep[top - 1]:
        top -= 1
    while bottom < len(keep) - 1 and keep[bottom + 1]:
        bottom += 1
    per_col = dark[top:bottom + 1].sum(axis=0)
    cols = np.nonzero(per_col >= 2)[0]
    if len(cols) < 10:
        return None
    # columns in groups (word gaps up to 25 px = 2 mm allowed); the label is the group with most ink
    groups, start = [], cols[0]
    for a, b in zip(cols[:-1], cols[1:]):
        if b - a > 25:
            groups.append((start, a)); start = b
    groups.append((start, cols[-1]))
    c0, c1 = max(groups, key=lambda g: per_col[g[0]:g[1] + 1].sum())
    k = clip.width / pix.width
    return pymupdf.Rect(clip.x0 + c0 * k, clip.y0 + top * k, clip.x0 + (c1 + 1) * k, clip.y0 + (bottom + 1) * k)


def clear_signature_boxes(doc, page, card_rect):
    """Signature / thumb-impression images on the card: remove their white box and draw them at
    SIGNATURE_SCALE of their size, moved by SIGNATURE_SHIFT_X. The original image is blanked and a
    transparent copy is placed on the page at the new spot (vector page, so it stays sharp and
    nothing gets clipped)."""
    done = 0
    for img in page.get_images(full=True):
        xref = img[0]
        for raw in page.get_image_rects(xref):            # the same image can also be in the letter part
            r = raw * page.rotation_matrix
            if not card_rect.intersects(r) or r.get_area() > 0.25 * card_rect.get_area():
                continue
            if r.width / max(r.height, 1e-6) < 1.8:      # photos are portrait, QR square
                continue
            pix = pymupdf.Pixmap(doc, xref)
            if pix.n - pix.alpha != 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
            rgba = _transparent_ink(rgb)                            # raw = its spot ON THE CARD (unrotated)
            rgba[..., 3] = np.clip(rgba[..., 3].astype(np.float32) * SIGNATURE_SOLID, 0, 255).astype(np.uint8)
            blank = np.zeros((pix.height, pix.width, 4), np.uint8)  # the old spot becomes empty
            page.replace_image(xref, pixmap=pymupdf.Pixmap(pymupdf.csRGB, pix.width, pix.height, blank.tobytes(), True))
            ys, xs = np.nonzero(rgba[..., 3] > 25)                  # trim to the ink
            if not len(xs):
                break
            ink = np.ascontiguousarray(rgba[max(0, ys.min() - 1):ys.max() + 2, max(0, xs.min() - 1):xs.max() + 2])
            ih, iw = ink.shape[:2]
            # size of the ink as it appeared on the e-PAN (the PDF draws the image stretched: keep that look)
            dw, dh = iw * raw.width / pix.width, ih * raw.height / pix.height
            label = _label_box(page, raw, card_rect)
            if label is not None:
                bw, bh = label.width * SIGN_BOX_W, card_rect.height * SIGN_BOX_H
                s = min(1.0, bw / dw, bh / dh)                       # fit the box, shape kept, never enlarged
                w, h = dw * s, dh * s
                cx, y1 = (label.x0 + label.x1) / 2, label.y0 - SIGNATURE_GAP_PT
                rect = pymupdf.Rect(cx - w / 2, y1 - h, cx + w / 2, y1)
            else:                                                    # label not found: smaller, same spot
                w, h = dw * SIGNATURE_SCALE, dh * SIGNATURE_SCALE
                cx, cy = (raw.x0 + raw.x1) / 2, (raw.y0 + raw.y1) / 2
                rect = pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            page.insert_image(rect, keep_proportion=False,
                              pixmap=pymupdf.Pixmap(pymupdf.csRGB, iw, ih, ink.tobytes(), True))
            done += 1
            break
    return done


def _fill_corners(rgb, ppm, radius_mm):
    """Fill everything outside the rounded corners with the card's own background."""
    h, w = rgb.shape[:2]
    R = (radius_mm + CORNER_EXTRA_MM) * ppm + 2
    if R < 3:
        return rgb, 0.0
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    mask = np.zeros((h, w), np.uint8)
    for cy, cx in [(R, R), (R, w - 1 - R), (h - 1 - R, R), (h - 1 - R, w - 1 - R)]:
        zone = (np.abs(yy - cy) <= R) & (np.abs(xx - cx) <= R)
        corner = zone & (((yy < cy) == (cy < h / 2)) & ((xx < cx) == (cx < w / 2)))
        out = corner & (np.hypot(yy - cy, xx - cx) > R - 3)
        mask[out] = 1
    small = 4                                   # inpaint at 1/4 size (fast), then upsample
    sm = cv2.resize(rgb, (w // small, h // small), interpolation=cv2.INTER_AREA)
    mm_ = cv2.dilate(cv2.resize(mask, (w // small, h // small), interpolation=cv2.INTER_NEAREST),
                     np.ones((3, 3), np.uint8))
    fill = cv2.inpaint(sm, mm_, 8, cv2.INPAINT_TELEA)
    fill = cv2.GaussianBlur(fill, (0, 0), 1.5)
    fill = cv2.resize(fill, (w, h), interpolation=cv2.INTER_CUBIC)
    soft = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 2.0)[..., None]
    out = (rgb * (1 - soft) + fill * soft).round().astype(np.uint8)
    return out, mask.mean()


def pan_card_images(doc):
    page = doc[0]
    cards = find_pan_cards(page)
    sig = clear_signature_boxes(doc, page, cards[0])
    out = []
    for side, rect in zip(("Front", "Back"), cards):
        rect, radius = refine_card(page, rect)
        rect = _fit_aspect(rect)
        rgb = _render(page, rect)
        ppm = rgb.shape[1] / (rect.width / 72 * 25.4)
        rgb, filled = _fill_corners(rgb, ppm, radius)
        print(f"    PAN {side}: {rect.width / 72 * 25.4:.1f} x {rect.height / 72 * 25.4:.1f} mm, "
              f"corner radius {radius:.1f} mm" + (f", signature made transparent" if side == "Front" and sig else ""))
        img = Image.fromarray(rgb).resize((OUT_W, OUT_H), Image.LANCZOS)
        if side == "Back":
            img = apply_hologram(img)
        out.append((side, img))
    return out


HOLOGRAM_IMG = Path(__file__).resolve().parent / "templates" / "pan_hologram.png"   # cut from a scanned PAN card (600 dpi, corner r 2.0 mm)


def apply_hologram(img):
    """Put the hologram sticker on the grey 'HOLOGRAM' placeholder of the e-PAN back."""
    if not HOLOGRAM_IMG.exists():
        return img
    a = np.asarray(img.convert("RGB")).astype(np.int16)
    W, H = img.size
    sat, g = a.max(2) - a.min(2), a.mean(2)
    box = (sat < 25) & (g > 150) & (g < 235)
    box[:, : int(W * 0.55)] = False                     # placeholder is in the top-right part
    box[int(H * 0.6):, :] = False
    m = cv2.morphologyEx(box.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    found = None
    if n > 1:
        i = 1 + int(np.argmax(st[1:, 4]))
        x, y, w, h, area = st[i]
        if area > 0.6 * w * h and 0.8 < w / max(h, 1) < 1.25 and 0.12 * W < w < 0.3 * W:
            found = (x + w / 2, y + h / 2, max(w, h) + 3)              # cover the box completely
    if found is None:                    # layouts without the 'HOLOGRAM' box (older e-PAN) stay as they are
        return img
    cx, cy, size = found
    # 1) erase the whole grey 'HOLOGRAM' square (its corners would show outside the rounded sticker)
    x, y, w, h, _ = st[i]
    pad = 9                                           # the box has a ~6 px soft edge around it
    hole = np.zeros((H, W), np.uint8)
    hole[max(0, y - pad):y + h + pad, max(0, x - pad):x + w + pad] = 255
    src = np.ascontiguousarray(np.asarray(img.convert("RGB")))
    filled = cv2.inpaint(src, hole, 12, cv2.INPAINT_TELEA)
    feather = cv2.GaussianBlur(hole.astype(np.float32) / 255, (0, 0), 2.0)[..., None]   # no visible seam
    base = (src * (1 - feather) + filled * feather).round().astype(np.uint8)
    # 2) the sticker (cut from a real card with its own rounded corners), real size 17.0 mm
    stk = Image.open(HOLOGRAM_IMG).convert("RGBA")
    s = round(size)
    sticker = stk.resize((s, round(s * stk.height / stk.width)), Image.LANCZOS)
    out = Image.fromarray(base).convert("RGBA")
    out.alpha_composite(sticker, (round(cx - sticker.width / 2), round(cy - sticker.height / 2)))
    print(f"    PAN Back: hologram sticker {s}px at ({cx:.0f}, {cy:.0f})")
    return out.convert("RGB")


# ------------------------------------------------------------------ card pictures on an A4 sheet
# Newer RC prints (Parivahan, made with TCPDF - e.g. PDF_AP40EE6247.pdf) and similar PDFs: one A4 page
# with the front and back as two high-resolution card PICTURES side by side (86 x 54 mm each, ~600 dpi,
# rounded corners on white, a thin outline around each) and no text at all.
PAIR_W_MM = (70, 100)          # each picture is about a card wide


def card_pictures(doc):
    """[(xref, rect), (xref, rect)] = front, back (reading order) when the PDF is such a sheet, else None."""
    page = doc[0]
    if len(page.get_text("words")) > 30:          # real text -> a normal card PDF, not a picture sheet
        return None
    found = []
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            r = r * page.rotation_matrix
            w_mm, ar = r.width / 72 * 25.4, r.width / max(r.height, 1e-6)
            if PAIR_W_MM[0] <= w_mm <= PAIR_W_MM[1] and 1.4 < ar < 1.8:
                found.append((img[0], r))
    if len(found) < 2:
        return None
    found.sort(key=lambda t: (round(t[1].y0 / 36), t[1].x0))   # rows (half-inch bands), then left -> right
    a, b = found[:2]
    if abs(a[1].width - b[1].width) > 0.1 * a[1].width:          # front and back are the same size
        return None
    return [a, b]


WHITE = 250                   # paper white around the card in the picture (the card itself is <= ~245)


def _trim_white(rgb):
    """Cut off pure-white strips along the picture's edges (not part of the card)."""
    g = rgb.mean(2)
    white_row, white_col = (g >= WHITE).mean(1) > 0.97, (g >= WHITE).mean(0) > 0.97
    def run(flags):
        n = 0
        while n < len(flags) // 4 and flags[n]:
            n += 1
        return n
    t, b = run(white_row), run(white_row[::-1])
    l, r = run(white_col), run(white_col[::-1])
    if any((t, b, l, r)):                       # +1 px: the anti-aliased step from white to card
        t, b, l, r = [k + 1 if k else 0 for k in (t, b, l, r)]
    return rgb[t:rgb.shape[0] - b, l:rgb.shape[1] - r], (t, b, l, r)


def _edge_band(med, max_px):
    """med: row (or column) medians from the edge inward. Width of a thin uniform strip at the edge that
    ends in a sharp step (an outline, or a lighter strip outside the card), else 0. A smooth colour
    gradient has no step, so it is never cut."""
    for k in range(1, max_px):
        band = med[:k]
        if band.max() - band.min() <= 3 and abs(float(med[k]) - float(band.mean())) >= 6 \
                and abs(float(med[k + 3]) - float(med[k])) <= 3:       # the card continues evenly after the step
            return k
    return 0


def _trim_bands(rgb, max_px=40):
    g = rgb.mean(2)
    t = _edge_band(np.median(g, 1), max_px)
    b = _edge_band(np.median(g, 1)[::-1], max_px)
    l = _edge_band(np.median(g, 0), max_px)
    r = _edge_band(np.median(g, 0)[::-1], max_px)
    return rgb[t:rgb.shape[0] - b, l:rgb.shape[1] - r], (t, b, l, r)


def _to_card_shape(rgb):
    """Bring to the card's proportions WITHOUT cutting anything off: the short side is extended with
    the card's own edge colours (same idea as make_cards.pad_edges)."""
    rim = 3                                     # the outermost pixels are often a 1-px line / anti-alias step:
    h, w = rgb.shape[:2]                        # extend from just inside them, not from them
    if w / h > ASPECT:                          # too wide -> add rows (top and bottom)
        add = round(w / ASPECT) - h + 2 * rim
        core = rgb[rim:h - rim]
        return cv2.copyMakeBorder(core, add // 2, add - add // 2, 0, 0, cv2.BORDER_REPLICATE)
    add = round(h * ASPECT) - w                 # too tall -> add columns (left and right)
    if add <= 0:
        return rgb
    core = rgb[:, rim:w - rim]
    add += 2 * rim
    return cv2.copyMakeBorder(core, 0, 0, add // 2, add - add // 2, cv2.BORDER_REPLICATE)


def _white_corner_radius(rgb, ppm):
    """Rounded corners left white in the picture -> radius in mm (0 = square card)."""
    g = rgb.mean(2)
    h, w = g.shape
    ks = []
    for y, x, dy, dx in ((0, 0, 1, 1), (0, w - 1, 1, -1), (h - 1, 0, -1, 1), (h - 1, w - 1, -1, -1)):
        k = 0
        while k < min(h, w) // 4 and g[y + dy * k, x + dx * k] >= WHITE:
            k += 1
        ks.append(k)
    k = float(np.median(ks))
    return 0.0 if k <= 2 else k * np.sqrt(2) / (np.sqrt(2) - 1) / ppm


def card_picture_images(doc):
    """Front / back straight from the embedded pictures (their own ~600 dpi, no re-rendering): white strips
    trimmed, short side extended with the card's background (nothing cut off), white corners filled."""
    out = []
    for side, (xref, rect) in zip(("Front", "Back"), card_pictures(doc)):
        pix = pymupdf.Pixmap(doc, xref)
        if pix.n - pix.alpha != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
        rgb, cut = _trim_white(rgb)
        rgb, band = _trim_bands(rgb)                  # old outline / light strip outside the card
        cut = tuple(a + b for a, b in zip(cut, band))
        ppm = pix.width / (rect.width / 72 * 25.4)
        radius = _white_corner_radius(rgb, ppm)
        if radius:
            rgb, _ = _fill_corners(rgb, ppm, radius)
        rgb = _to_card_shape(np.ascontiguousarray(rgb))
        print(f"    {side}: card picture {pix.width}x{pix.height} px, white trimmed (t,b,l,r) {cut}"
              + (f", corners r {radius:.1f} mm filled" if radius else ""))
        out.append((side, Image.fromarray(rgb).resize((OUT_W, OUT_H), Image.LANCZOS)))
    return out


# ------------------------------------------------------------------ RC
def rc_card_images(doc):
    if card_pictures(doc):                          # new layout: both sides as pictures on one A4 page
        return card_picture_images(doc)
    out = []
    names = ["Front", "Back"]
    for i, page in enumerate(doc):
        if i >= 2:
            break
        best = None
        for img in page.get_images(full=True):
            for r in page.get_image_rects(img[0]):
                r = r * page.rotation_matrix
                ar = r.width / max(r.height, 1e-6)
                if 1.4 < ar < 1.8 and (best is None or r.get_area() > best.get_area()):
                    best = r
        if best is None:
            raise ValueError(f"RC page {i + 1}: card artwork not found")
        rect = _fit_aspect(best & page.rect)
        rgb = _render(page, rect)
        print(f"    RC {names[i]}: {rect.width / 72 * 25.4:.1f} x {rect.height / 72 * 25.4:.1f} mm")
        out.append((names[i], Image.fromarray(rgb).resize((OUT_W, OUT_H), Image.LANCZOS)))
    return out
