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


# ------------------------------------------------------------------ PAN front "template"
# The front is built from layers so the shop can fine-tune it per card later (exports list -> Front ->
# "PAN front" editor) without the PDF:
#   base      the card with the holder's name, father's name and signature taken off
#   name      / father   alpha masks of those two printed values (black Helvetica text)
#   sign      the signature ink (RGBA, white box removed), drawn into SIGN_BOX
# compose_front() puts them together with the settings below.
PAN_FRONT_DEFAULTS = {
    "name_gap": 6,          # px (1016 px card, 0.085 mm each) the name moves DOWN, away from "नाम / Name"
    "father_gap": 6,        # same for the father's name under "पिता का नाम / Father's Name"
    "sign_size": 100,       # % of "fits the box" (100 = as big as fits, shape kept)
    "sign_x": 0, "sign_y": 0,   # px, move the signature (right / down are +)
    "sign_strength": 300,   # % ink opacity (e-PAN signatures are faint, ~40 % -> x3 = solid)
    "sign_contrast": 0,     # -50..100: crisper strokes (soft edges -> solid or clear)
    "sign_dark": 0,         # 0..100: ink colour towards black
    "sign_color": "original",   # original | black | blue
}
PAN_FRONT_RANGE = {"name_gap": (-10, 40), "father_gap": (-10, 40), "sign_size": (30, 200), "sign_x": (-150, 150),
                   "sign_y": (-60, 60), "sign_strength": (50, 600), "sign_contrast": (-50, 100), "sign_dark": (0, 100)}
SIGN_COLORS = {"black": (20, 20, 24), "blue": (22, 38, 125)}
# The box the shop marked on a card (references/pan-changes-1.png): 350 x 36 px on the 1016 x 638 card,
# just above the printed "हस्ताक्षर / Signature" label. Long signatures fill its width, short ones its
# height; the signature is centred in it and never stretched.
SIGN_BOX = (284, 545, 634, 581)                 # where it is on the reference card (label at SIGN_LABEL_REF)
SIGN_LABEL_REF = (332, 590, 523, 613)          # the printed label found on that same card (px)


def front_params(p=None):
    """Clean settings: every key present, numbers clamped, colour known."""
    out = dict(PAN_FRONT_DEFAULTS)
    for k, v in (p or {}).items():
        if k in PAN_FRONT_RANGE:
            lo, hi = PAN_FRONT_RANGE[k]
            try:
                out[k] = int(round(max(lo, min(hi, float(v)))))
            except (TypeError, ValueError):
                pass
        elif k == "sign_color" and v in ("original", *SIGN_COLORS):
            out[k] = v
    return out


def _sign_box(label_px):
    """SIGN_BOX moved along with the printed label (other e-PAN layouts sit a little differently)."""
    if label_px is None or SIGN_LABEL_REF is None:
        return list(SIGN_BOX)
    dx = (label_px[0] + label_px[2]) / 2 - (SIGN_LABEL_REF[0] + SIGN_LABEL_REF[2]) / 2
    dy = label_px[1] - SIGN_LABEL_REF[1]
    return [round(SIGN_BOX[0] + dx), round(SIGN_BOX[1] + dy), round(SIGN_BOX[2] + dx), round(SIGN_BOX[3] + dy)]


def compose_front(base, layers, p=None):
    """base (RGB card) + layers {"name", "father": L masks, "sign": RGBA or None, "box", "aspect"} -> RGB card."""
    p = front_params(p)
    out = np.asarray(base.convert("RGB")).astype(np.float32)
    H, W = out.shape[:2]
    for key, gap in (("name", p["name_gap"]), ("father", p["father_gap"])):
        m = layers.get(key)
        if m is None:
            continue
        a = np.zeros((H, W), np.float32)
        src = np.asarray(m, np.float32) / 255
        y0, y1 = max(0, gap), min(H, H + gap)
        a[y0:y1] = src[y0 - gap:y1 - gap]
        out *= 1 - a[..., None]                                   # black text
    sign = layers.get("sign")
    if sign is not None:
        x0, y0, x1, y1 = layers["box"]
        bw, bh = x1 - x0, y1 - y0
        asp = layers.get("aspect") or sign.width / sign.height   # shape as the e-PAN showed it
        k = min(bw / asp, bh) * p["sign_size"] / 100              # height that fits the box, shape kept
        sh, sw = max(2, round(k)), max(2, round(k * asp))
        s = np.asarray(sign.convert("RGBA").resize((sw, sh), Image.LANCZOS)).astype(np.float32)
        rgb, al = s[..., :3], s[..., 3] / 255
        al = np.clip(al * p["sign_strength"] / 100, 0, 1)
        if p["sign_contrast"]:
            al = np.clip((al - 0.5) * (1 + p["sign_contrast"] / 50) + 0.5, 0, 1) * (al > 0.02)
        if p["sign_color"] in SIGN_COLORS:
            rgb = np.broadcast_to(np.array(SIGN_COLORS[p["sign_color"]], np.float32), rgb.shape)
        rgb = rgb * (1 - p["sign_dark"] / 100)
        cx, cy = (x0 + x1) / 2 + p["sign_x"], (y0 + y1) / 2 + p["sign_y"]
        px, py = round(cx - sw / 2), round(cy - sh / 2)
        # clip to the card
        ax0, ay0, ax1, ay1 = max(0, px), max(0, py), min(W, px + sw), min(H, py + sh)
        if ax1 > ax0 and ay1 > ay0:
            a = al[ay0 - py:ay1 - py, ax0 - px:ax1 - px, None]
            region = out[ay0:ay1, ax0:ax1]
            out[ay0:ay1, ax0:ax1] = region * (1 - a) + rgb[ay0 - py:ay1 - py, ax0 - px:ax1 - px] * a
    return Image.fromarray(np.clip(out, 0, 255).round().astype(np.uint8))


def take_signature(doc, page, card_rect):
    """Blank the signature / thumb-impression image on the card -> (ink RGBA Image trimmed to the ink,
    aspect as the e-PAN draws it, its rect on the page) or None."""
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
            rgba = _transparent_ink(rgb)
            blank = np.zeros((pix.height, pix.width, 4), np.uint8)
            page.replace_image(xref, pixmap=pymupdf.Pixmap(pymupdf.csRGB, pix.width, pix.height, blank.tobytes(), True))
            ys, xs = np.nonzero(rgba[..., 3] > 8)                   # trim to the ink
            if not len(xs):
                return None
            ink = np.ascontiguousarray(rgba[max(0, ys.min() - 1):ys.max() + 2, max(0, xs.min() - 1):xs.max() + 2])
            ih, iw = ink.shape[:2]
            dw, dh = iw * raw.width / pix.width, ih * raw.height / pix.height   # PDF draws it stretched: keep that
            return Image.fromarray(ink, "RGBA"), dw / dh, raw
    return None


def _value_spans(page, card_rect):
    """The printed name and father's name on the front (black text; PAN number and date left out),
    top to bottom -> [Rect, ...]."""
    spans = []
    for b in page.get_text("dict", clip=card_rect)["blocks"]:
        for l in b.get("lines", []):
            for s in l["spans"]:
                t = s["text"].strip()
                if not t or PAN_RE.fullmatch(t) or re.fullmatch(r"\d{2}[/-]\d{2}[/-]\d{4}", t):
                    continue
                spans.append(pymupdf.Rect(s["bbox"]))
    # one value per line (a name drawn in several spans = one line)
    lines = []
    for r in sorted(spans, key=lambda r: r.y0):
        if lines and abs(lines[-1].y0 - r.y0) < 2:
            lines[-1] |= r
        else:
            lines.append(pymupdf.Rect(r))
    return lines[:2]


def pan_front_layers(doc, page, card_rect, rect, radius):
    """-> (finished front at the default settings, layers for compose_front)."""
    sig = take_signature(doc, page, card_rect)
    label = _label_box(page, sig[2], card_rect) if sig else None      # signature already blanked
    k = OUT_W / rect.width
    to_px = lambda r: [(r.x0 - rect.x0) * k, (r.y0 - rect.y0) * k, (r.x1 - rect.x0) * k, (r.y1 - rect.y0) * k]

    def card():
        rgb = _render(page, rect)
        ppm = rgb.shape[1] / (rect.width / 72 * 25.4)
        rgb, _ = _fill_corners(rgb, ppm, radius)
        return np.asarray(Image.fromarray(rgb).resize((OUT_W, OUT_H), Image.LANCZOS)).astype(np.float32)

    layers = {}
    cur = card()
    for key, span in zip(("name", "father"), _value_spans(page, card_rect)):
        page.add_redact_annot(span, fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                              text=pymupdf.PDF_REDACT_TEXT_REMOVE)
        nxt = card()
        a = np.clip(1 - cur.mean(2) / np.maximum(nxt.mean(2), 1), 0, 1)      # black text: with = without x (1 - a)
        x0, y0, x1, y1 = to_px(span)
        keep = np.zeros_like(a)
        keep[max(0, int(y0) - 4):int(y1) + 5, max(0, int(x0) - 4):int(x1) + 5] = 1
        layers[key] = Image.fromarray((a * keep * 255).round().astype(np.uint8), "L")
        cur = nxt
    base = Image.fromarray(cur.round().astype(np.uint8))
    if sig:
        lab = [round(v) for v in to_px(label)] if label is not None else None
        layers.update(sign=sig[0], aspect=sig[1], box=_sign_box(lab), label=lab)
    return base, layers


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


def pan_card_images(doc, front=None):
    """front: PAN front settings (see PAN_FRONT_DEFAULTS); the front image carries
    info["cps_panfront"] = (base, layers) so the website can change them later."""
    page = doc[0]
    cards = find_pan_cards(page)
    out = []
    for side, card_rect in zip(("Front", "Back"), cards):
        rect, radius = refine_card(page, card_rect)
        rect = _fit_aspect(rect)
        print(f"    PAN {side}: {rect.width / 72 * 25.4:.1f} x {rect.height / 72 * 25.4:.1f} mm, "
              f"corner radius {radius:.1f} mm")
        if side == "Front":
            base, layers = pan_front_layers(doc, page, card_rect, rect, radius)
            img = compose_front(base, layers, front)
            img.info["cps_panfront"] = (base, layers)
            import make_cards                                # exact photo spot: the card rect -> whole card
            box = make_cards.card_photo_box(doc, page, rect, card_rect)
            if box:
                img.info["cps_photo_box"] = box
            print(f"    PAN Front: values {[k for k in ('name', 'father') if k in layers]}, "
                  f"signature box {layers.get('box')} (label {layers.get('label')})")
        else:
            rgb = _render(page, rect)
            ppm = rgb.shape[1] / (rect.width / 72 * 25.4)
            rgb, _ = _fill_corners(rgb, ppm, radius)
            img = apply_hologram(Image.fromarray(rgb).resize((OUT_W, OUT_H), Image.LANCZOS))
        out.append((side, img))
    return out


HOLOGRAM_IMG = Path(__file__).resolve().parent / "templates" / "pan_hologram.png"   # cut from a scanned PAN card (600 dpi, corner r 2.0 mm)
# Sticker brightness (Settings -> General, and per card in the exports list). Gamma on the scan:
# below 1 lifts the dark / mid tones and leaves white white, so the silver texture stays visible.
HOLOGRAM_LEVELS = {"A": 1.00, "B": 0.85, "C": 0.72, "D": 0.60, "E": 0.50, "F": 0.40}
HOLOGRAM_DEFAULT = "E"


def hologram_sticker(size, level=HOLOGRAM_DEFAULT):
    """The sticker picture (RGBA), `size` px wide, at brightness level A (scan as is) .. F (brightest)."""
    stk = Image.open(HOLOGRAM_IMG).convert("RGBA")
    stk = stk.resize((size, round(size * stk.height / stk.width)), Image.LANCZOS)
    g = HOLOGRAM_LEVELS.get(level, HOLOGRAM_LEVELS[HOLOGRAM_DEFAULT])
    if g != 1.0:
        a = np.asarray(stk).astype(np.float32)
        a[..., :3] = np.clip((a[..., :3] / 255) ** g * 255, 0, 255)
        stk = Image.fromarray(a.round().astype(np.uint8), "RGBA")
    return stk


def place_hologram(base, place, level=HOLOGRAM_DEFAULT):
    """Card back without sticker + where it goes {"x", "y", "s"} -> finished card back (RGB)."""
    sticker = hologram_sticker(place["s"], level)
    out = base.convert("RGBA")
    out.alpha_composite(sticker, (place["x"] - sticker.width // 2, place["y"] - sticker.height // 2))
    return out.convert("RGB")


def apply_hologram(img, level=HOLOGRAM_DEFAULT):
    """Put the hologram sticker on the grey 'HOLOGRAM' placeholder of the e-PAN back.
    The result carries info["cps_holo"] = {"base": card without sticker, "place": {...}} so the
    sticker brightness can be changed later without the PDF."""
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
    s = round(size)
    w0, h0 = Image.open(HOLOGRAM_IMG).size
    sh = round(s * h0 / w0)
    place = {"x": round(cx - s / 2) + s // 2, "y": round(cy - sh / 2) + sh // 2, "s": s}   # same spot as before
    base = Image.fromarray(base)
    out = place_hologram(base, place, level)
    out.info["cps_holo"] = {"base": base, "place": place}
    print(f"    PAN Back: hologram sticker {s}px at ({cx:.0f}, {cy:.0f}), brightness {level}")
    return out


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
