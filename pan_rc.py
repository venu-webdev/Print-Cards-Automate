"""
PAN card (e-PAN / Instant e-PAN) and Vehicle RC exports.

PAN : the PDF is an A4 letter; at the bottom are the printable front & back cut-outs.
      We find the two cards from the page itself, make the signature / thumb-impression
      image transparent (no white box), render each card sharp from the PDF, fill the
      rounded corners with the card's own background, and size it like the DL exports.
RC  : each page has one card-sized artwork at the top-left; it is rendered as-is.
"""
import re

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


def clear_signature_boxes(doc, page, card_rect):
    """Signature / thumb-impression images on the card: remove their white box."""
    done = 0
    for img in page.get_images(full=True):
        xref = img[0]
        for r in page.get_image_rects(xref):
            r = r * page.rotation_matrix
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
            page.replace_image(xref, pixmap=pymupdf.Pixmap(
                pymupdf.csRGB, pix.width, pix.height, rgba.tobytes(), True))
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
        out.append((side, Image.fromarray(rgb).resize((OUT_W, OUT_H), Image.LANCZOS)))
    return out


# ------------------------------------------------------------------ RC
def rc_card_images(doc):
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
