"""
Print-card automation (replaces the Photoshop action).

For every PDF in ./raw:
  page 1 -> <name>-Front.jpeg, page 2 -> <name>-Back.jpeg  (saved in ./export)

Per page:
  1. render the PDF page at high resolution
  2. find the white surround + the (double) rounded border lines
  3. remove them by extending the nearest card pixels outward (like Photoshop edge extend)
     - frame segments that are also a table's outer edge are kept
  4. resize to print size and save as JPEG @ 300 DPI

Usage:
  python make_cards.py                 # process all PDFs in raw/
  python make_cards.py a.pdf b.pdf     # process specific PDFs
"""
import sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parent
RAW_DIR = ROOT / "raw"
EXPORT_DIR = ROOT / "export"

# ---- output -------------------------------------------------------------------------
OUT_W, OUT_H = 1016, 638      # same pixel size as your Photoshop exports
OUT_DPI = 300
JPEG_QUALITY = 95
PAGE_NAMES = ["Front", "Back"]

# ---- detection tuning ---------------------------------------------------------------
RENDER_DPI = 600               # render big, then downscale -> sharp text
DARK = 110                     # gray value below which a pixel is "ink"
MAX_LAYERS = 2                 # number of frame lines to peel off (double frame = 2)
MAX_GAP_MM = 0.8               # white gap between the two frame lines is thinner than this
MAX_LINE_MM = 0.5              # a frame line is thinner than this
EDGE_BAND_MM = 4.0             # frame lives within this distance of the page edge
CORNER_MM = 5.0                # rounded-corner zone size
STRAIGHT_MM = 3.0              # min straight run for a pixel to count as frame line
HALO_MM = 0.15                 # extra margin removed past the frame (anti-alias halo)
TABLE_JOIN_MM = 7.0            # keep frame where table lines meet it closer than this
BG_EXTRA_MM = 0.3              # background mode: extra strip removed past the frame
BG_CROP_INSET_PT = 0.3         # trim this much off the artwork edge (anti-alias pixels)
INK_SOURCE = 150              # darker than this is never used as fill colour
ART_MARGIN_MM = 0.5            # ...and nothing within this distance of it
LINE_SAMPLE_MM = 4.0           # lines reaching the edge are copied from this far inside
LINE_WIDEN_MM = 0.12           # ...including this much above/below (anti-aliasing)
PLATE_INSET_MM = 0.35           # background colour is sampled this far inside the card
SAMPLE_INSET_MM = 1.0          # copy lines/background from this far inside the card
                               # (right next to the old frame they are thinner/lighter)
JUNCTION_MM = 1.0              # ink must continue this far inward to count as a table line


def mm(px_per_mm, v):
    return int(round(v * px_per_mm))


def render_page(page, dpi):
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()


def frame_candidates(dark, ppm):
    """Ink pixels that could belong to the frame: long straight runs parallel to the
    nearest edge, or anything inside the corner zones (the rounded corners)."""
    h, w = dark.shape
    L = mm(ppm, STRAIGHT_MM)
    horiz = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((1, L), np.uint8))
    vert = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((L, 1), np.uint8))
    b, c = mm(ppm, EDGE_BAND_MM), mm(ppm, CORNER_MM)
    cand = np.zeros_like(dark)
    cand[:b, :] |= horiz[:b, :]
    cand[h - b:, :] |= horiz[h - b:, :]
    cand[:, :b] |= vert[:, :b]
    cand[:, w - b:] |= vert[:, w - b:]
    for ys, xs in [(slice(0, c), slice(0, c)), (slice(0, c), slice(w - c, w)),
                   (slice(h - c, h), slice(0, c)), (slice(h - c, h), slice(w - c, w))]:
        cand[ys, xs] = dark[ys, xs]
    return cand


def border_mask(rgb, ppm):
    """Returns (mask, keep): mask = pixels to replace, keep = frame bits to restore."""
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    dark = (gray < DARK).astype(np.uint8)
    light = 1 - dark
    cand = frame_candidates(dark, ppm)

    _, light_lab = cv2.connectedComponents(light, connectivity=4)
    k3 = np.ones((3, 3), np.uint8)
    mask = np.zeros((h, w), np.uint8)
    frontier = np.zeros((h, w), np.uint8)
    frontier[0, :] = frontier[-1, :] = frontier[:, 0] = frontier[:, -1] = 1
    last_line = np.zeros((h, w), np.uint8)
    lines = 0

    for layer in range(MAX_LAYERS):
        # light region (white surround / gap between frame lines) touching the frontier
        added = False
        for lab in np.unique(light_lab[(frontier > 0) & (light > 0)]):
            if lab == 0:
                continue
            comp = light_lab == lab
            if layer > 0:
                d = cv2.distanceTransform(comp.astype(np.uint8), cv2.DIST_L2, 3)
                if d.max() > MAX_GAP_MM * ppm:      # that's the card itself -> stop
                    continue
            mask[comp] = 255
            added = True
        if layer > 0 and not added:
            break

        # frame line touching it: grow into frame-candidate ink, max one line thickness
        line = ((cv2.dilate(mask, k3) > 0) & (cand > 0) & (mask == 0)).astype(np.uint8)
        for _ in range(mm(ppm, MAX_LINE_MM)):
            new = (cv2.dilate(line, k3) > 0) & (cand > 0) & (line == 0) & (mask == 0)
            if not new.any():
                break
            line[new] = 1
        if not line.any():
            break
        lines += 1
        last_line = line
        mask[line > 0] = 255
        frontier = cv2.dilate(mask, k3) - mask

    # rounded corners: take all ink connected to the frame inside the corner zones
    c = mm(ppm, CORNER_MM)
    for ys, xs in [(slice(0, c), slice(0, c)), (slice(0, c), slice(w - c, w)),
                   (slice(h - c, h), slice(0, c)), (slice(h - c, h), slice(w - c, w))]:
        sub_dark, sub_mask = dark[ys, xs], mask[ys, xs]
        n, lab = cv2.connectedComponents(sub_dark, connectivity=8)
        touch = np.unique(lab[(cv2.dilate(sub_mask, k3) > 0) & (sub_dark > 0)])
        sub_mask[np.isin(lab, touch[touch > 0])] = 255

    keep = table_edges(last_line, dark, ppm)

    # halo: grow a little, but never into other ink (text touching the frame stays)
    k = mm(ppm, HALO_MM) * 2 + 1
    grown = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    mask = np.where((grown > 0) & ~((dark > 0) & (mask == 0)), 255, mask).astype(np.uint8)

    frac = mask.mean() / 255
    print(f"    frame lines removed: {lines}, area replaced: {frac:.1%}")
    if lines == 0 or frac > 0.25:
        print("    ! frame not detected cleanly -> using a plain 1.2 mm inset instead")
        inset = mm(ppm, 1.2)
        mask = np.full((h, w), 255, np.uint8)
        mask[inset:h - inset, inset:w - inset] = 0
        keep = np.zeros((h, w), np.uint8)
    return mask, keep


def table_edges(inner, dark, ppm):
    """Segments of the innermost frame line that are also a table's outer edge
    (several table lines join the frame close together) -> keep them."""
    h, w = inner.shape
    keep = np.zeros((h, w), np.uint8)
    if not inner.any():
        return keep
    b, J, join = mm(ppm, EDGE_BAND_MM), mm(ppm, JUNCTION_MM), TABLE_JOIN_MM * ppm
    c = mm(ppm, CORNER_MM)

    def scan(line_band, ink_band):
        """line_band/ink_band: rows = positions along the edge, cols = depth inward."""
        hits = []
        n = line_band.shape[0]
        for i in range(c, n - c):                       # skip the rounded corners
            xs = np.nonzero(line_band[i])[0]
            if len(xs) == 0:
                continue
            e = xs.max() + 1
            seg = ink_band[i, e:e + J + 4]               # small anti-alias gap allowed
            if len(seg) == J + 4 and seg.mean() >= 0.85:
                hits.append(i)
        # cluster consecutive rows into junction centres
        centres, run = [], []
        for i in hits:
            if run and i - run[-1] > 2:
                centres.append(int(np.mean(run)))
                run = []
            run.append(i)
        if run:
            centres.append(int(np.mean(run)))
        spans = [(a, b2) for a, b2 in zip(centres, centres[1:]) if b2 - a <= join]
        out = np.zeros_like(line_band)
        for a, b2 in spans:
            out[a:b2 + 1] = line_band[a:b2 + 1]
        return out

    deep = b + J
    keep[:, :b] |= scan(inner[:, :b], dark[:, :deep])                               # left
    keep[:, w - b:] |= scan(inner[:, w - b:][:, ::-1], dark[:, w - deep:][:, ::-1])[:, ::-1]  # right
    keep[:b, :] |= scan(inner[:b, :].T, dark[:deep, :].T).T                         # top
    keep[h - b:, :] |= scan(inner[h - b:, :][::-1].T, dark[h - deep:, :][::-1].T).T[::-1]   # bottom
    return keep


def edge_extend(rgb, mask, ppm):
    """Fill the masked border area:
      - background: nearest clean card-background pixel, smoothed (no seams, no rays)
      - real lines running into the frame (e.g. DL No separator) continue straight
        to the card edge."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    dark = gray < DARK
    # clean background sources: card pixels away from the frame and away from any ink
    ring = cv2.dilate(mask, np.ones((2 * mm(ppm, PLATE_INSET_MM) + 1,) * 2, np.uint8)) > 0
    # (only real ink counts - coloured areas like the blue header ARE background)
    # artwork (emblem, text, photos) incl. its light highlights is never used as background:
    # close the gaps inside it, then keep a small safety margin around it
    ink = (gray < INK_SOURCE).astype(np.uint8)
    ink = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((2 * mm(ppm, 0.4) + 1,) * 2, np.uint8))
    inky = cv2.dilate(ink, np.ones((2 * mm(ppm, ART_MARGIN_MM) + 1,) * 2, np.uint8)) > 0
    keep_src = (~ring & ~inky).astype(np.uint8)
    _, labels = cv2.distanceTransformWithLabels(1 - keep_src, cv2.DIST_L2, 5,
                                                labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(keep_src)
    lut_y = np.zeros(labels.max() + 1, np.int32)
    lut_x = np.zeros(labels.max() + 1, np.int32)
    lab = labels[ys, xs]
    lut_y[lab], lut_x[lab] = ys, xs
    plate = cv2.GaussianBlur(rgb[lut_y[labels], lut_x[labels]], (0, 0), mm(ppm, 0.4))
    out = rgb.copy()                                # card itself is never touched
    out[mask > 0] = plate[mask > 0]
    # soften the join between the filled strip and the card (no visible step in shade)
    fw = mm(ppm, 0.25)
    inner = cv2.dilate(mask, np.ones((2 * fw + 1,) * 2, np.uint8)) > 0
    band = inner & (mask == 0) & ~inky
    if band.any():
        dist = cv2.distanceTransform((mask == 0).astype(np.uint8), cv2.DIST_L2, 3)
        wgt = np.clip(1 - dist / (fw + 1), 0, 1)[..., None]
        mix = (plate * wgt + rgb * (1 - wgt)).astype(np.uint8)
        out[band] = mix[band]

    h, w = mask.shape
    unm = mask == 0
    yy, xx = np.mgrid[0:h, 0:w]
    xl = np.argmax(unm, axis=1)                     # first card pixel from the left, per row
    xr = w - 1 - np.argmax(unm[:, ::-1], axis=1)
    yt = np.argmax(unm, axis=0)                     # first card pixel from the top, per column
    yb = h - 1 - np.argmax(unm[::-1, :], axis=0)
    big = np.int32(1 << 30)
    cands = [  # (valid, distance-to-edge, source y, source x)
        (xx < xl[:, None], xx, yy, np.broadcast_to(xl[:, None], (h, w))),
        (xx > xr[:, None], w - 1 - xx, yy, np.broadcast_to(xr[:, None], (h, w))),
        (yy < yt[None, :], yy, np.broadcast_to(yt[None, :], (h, w)), xx),
        (yy > yb[None, :], h - 1 - yy, np.broadcast_to(yb[None, :], (h, w)), xx),
    ]
    best = np.full((h, w), big, np.int32)
    sy, sx = np.zeros((h, w), np.int32), np.zeros((h, w), np.int32)
    side = np.full((h, w), -1, np.int8)
    for k, (valid, dist, cy, cx) in enumerate(cands):
        better = valid & (dist < best) & (mask > 0)
        best = np.where(better, dist, best)
        sy = np.where(better, cy, sy)
        sx = np.where(better, cx, sx)
        side[better] = k
    # only stretch real lines (ink continuing inward), never text tips touching the frame
    J = mm(ppm, JUNCTION_MM)
    src_dark = dark[sy, sx]
    run_l = np.array([dark[y, xl[y]:xl[y] + J].mean() for y in range(h)])
    run_r = np.array([dark[y, max(0, xr[y] - J + 1):xr[y] + 1].mean() for y in range(h)])
    run_t = np.array([dark[yt[x]:yt[x] + J, x].mean() for x in range(w)])
    run_b = np.array([dark[max(0, yb[x] - J + 1):yb[x] + 1, x].mean() for x in range(w)])
    is_line = np.zeros((h, w), bool)
    is_line |= (side == 0) & (run_l[:, None] >= 0.85)
    is_line |= (side == 1) & (run_r[:, None] >= 0.85)
    is_line |= (side == 2) & (run_t[None, :] >= 0.85)
    is_line |= (side == 3) & (run_b[None, :] >= 0.85)
    # widen by 1 px across the line so its anti-aliased edge comes along too
    is_line = cv2.dilate(is_line.astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    use = (best < big) & is_line & (mask > 0)
    # corners: always plain background
    c = mm(ppm, CORNER_MM)
    corner = np.zeros((h, w), bool)
    corner[:c, :c] = corner[:c, w - c:] = corner[h - c:, :c] = corner[h - c:, w - c:] = True
    use &= ~corner
    out[use] = rgb[sy[use], sx[use]]

    # Lines that run on for a while: take their full-thickness profile from
    # LINE_SAMPLE_MM inside the card and use it all the way to the edge
    # (artwork lines often taper / get thinner right next to the old frame).
    L = mm(ppm, LINE_SAMPLE_MM)
    grow = mm(ppm, LINE_WIDEN_MM)

    def rows_with_long_line(run):
        r = (run >= 0.85).astype(np.uint8)[:, None]
        r = cv2.dilate(r, np.ones((2 * grow + 1, 1), np.uint8))[:, 0] > 0
        r[:c] = r[len(r) - c:] = False                       # not in the corners
        return np.nonzero(r)[0]

    long_l = np.array([dark[y, xl[y]:xl[y] + L].mean() if xl[y] + L < w else 0 for y in range(h)])
    long_r = np.array([dark[y, max(0, xr[y] - L + 1):xr[y] + 1].mean() if xr[y] - L >= 0 else 0 for y in range(h)])
    long_t = np.array([dark[yt[x]:yt[x] + L, x].mean() if yt[x] + L < h else 0 for x in range(w)])
    long_b = np.array([dark[max(0, yb[x] - L + 1):yb[x] + 1, x].mean() if yb[x] - L >= 0 else 0 for x in range(w)])
    for y in rows_with_long_line(long_l):
        src = min(xl[y] + L, w - 1)
        out[y, :src] = rgb[y, src]
    for y in rows_with_long_line(long_r):
        src = max(xr[y] - L, 0)
        out[y, src + 1:] = rgb[y, src]
    for x in rows_with_long_line(long_t):
        src = min(yt[x] + L, h - 1)
        out[:src, x] = rgb[src, x]
    for x in rows_with_long_line(long_b):
        src = max(yb[x] - L, 0)
        out[src + 1:, x] = rgb[src, x]
    return out


def process_page_flat(page):
    """Fallback: clean the whole rendered page (used when there is no background image)."""
    ppm = RENDER_DPI / 25.4
    rgb = render_page(page, RENDER_DPI)
    mask, keep = border_mask(rgb, ppm)
    clean = edge_extend(rgb, mask, ppm)
    if keep.any():
        h, w = mask.shape
        # outside a kept table edge, table lines must not run on to the card edge
        med = cv2.medianBlur(clean, 21)
        n, _, stats, _ = cv2.connectedComponentsWithStats(keep, connectivity=8)
        pad = mm(ppm, 0.5)
        for x, y, bw, bh, _a in stats[1:]:
            region = np.zeros((h, w), bool)
            if bh >= bw:                                   # vertical segment: left / right
                ys = slice(max(0, y - pad), y + bh + pad)
                if x < w // 2:
                    region[ys, :x] = True
                else:
                    region[ys, x + bw:] = True
            else:                                          # horizontal segment: top / bottom
                xs = slice(max(0, x - pad), x + bw + pad)
                if y < h // 2:
                    region[:y, xs] = True
                else:
                    region[y + bh:, xs] = True
            region &= mask > 0
            clean[region] = med[region]
        # put table-edge segments back ("darken" blend, so no white gap comes back)
        kd = cv2.dilate(keep, np.ones((5, 5), np.uint8)) > 0
        clean[kd] = np.minimum(clean[kd], rgb[kd])
    return Image.fromarray(clean).resize((OUT_W, OUT_H), Image.LANCZOS)


def find_background(page):
    """The big image that carries the card artwork + border (covers most of the page)."""
    best = None
    for img in page.get_images(full=True):
        xref = img[0]
        for r in page.get_image_rects(xref):
            r = r * page.rotation_matrix                      # -> visible page coordinates
            if r.get_area() >= 0.8 * page.rect.get_area():
                if best is None or r.get_area() > best[1].get_area():
                    best = (xref, r)
    return best


def clean_background(doc, page, xref, rect):
    """Remove the frame from the background image only and put it back into the PDF.
    Text, photo, QR and table are separate PDF objects, so they are never touched."""
    pix = pymupdf.Pixmap(doc, xref)
    if pix.n - pix.alpha != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
    # upscale so the mm-based tuning works at the same detail level as a 600 dpi render
    card_w_mm = max(rect.width, rect.height) / 72 * 25.4
    scale = max(1.0, RENDER_DPI / 25.4 / (max(pix.width, pix.height) / card_w_mm))
    rgb = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    ppm = max(rgb.shape[:2]) / card_w_mm
    mask, _keep = border_mask(rgb, ppm)
    # nothing but artwork here -> remove a slightly wider strip to kill every halo pixel
    k = 2 * mm(ppm, BG_EXTRA_MM) + 1
    mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    clean = edge_extend(rgb, mask, ppm)
    ok, png = cv2.imencode(".png", cv2.cvtColor(clean, cv2.COLOR_RGB2BGR))
    page.replace_image(xref, stream=png.tobytes())


def process_page(doc, page):
    bg = find_background(page)
    if bg is None:
        print("    (no background image found -> cleaning the flat render)")
        return process_page_flat(page)
    xref, rect = bg
    clean_background(doc, page, xref, rect)
    # crop exactly to the card artwork (drops the bare white page strips around it)
    inset = BG_CROP_INSET_PT
    clip = pymupdf.Rect(rect.x0 + inset, rect.y0 + inset, rect.x1 - inset, rect.y1 - inset)
    pix = page.get_pixmap(dpi=RENDER_DPI, clip=clip, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return img.resize((OUT_W, OUT_H), Image.LANCZOS)


# ===================================================================== Voter ID (e-EPIC)
# The e-EPIC PDF is one A4 sheet with the front (left) and back (right) card side by side,
# each inside a thin rectangular border. We only cut the card out just inside that border:
# nothing is extended or redrawn. Then it is scaled to the same size as the DL exports.
CARD_ASPECT = OUT_W / OUT_H
SHEET_MIN_WIDTH_MM = 120       # a page wider than this is a "sheet" with cards on it
VOTER_PHOTO_BRIGHTNESS = 1.05  # main (left) photo on the voter front: +5% brightness
                               # (the small black & white photo is left as it is)


def find_main_photo(card):
    """The big portrait photo on the left of the voter front: a dark rectangle inside the
    light photo frame. Returns a mask (uint8 0/255) or None."""
    h, w = card.shape[:2]
    gray = cv2.cvtColor(card, cv2.COLOR_RGB2GRAY)
    light = (gray > 200).astype(np.uint8)
    # the frame is printed with micro-text -> close the tiny gaps first
    light = cv2.morphologyEx(light, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cnts, hier = cv2.findContours(light, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for i, c in enumerate(cnts):
        if hier[0][i][3] < 0:                      # we want holes inside the frame
            continue
        x, y, bw, bh = cv2.boundingRect(c)
        if not (0.12 * w < bw < 0.45 * w and 1.1 < bh / bw < 1.7):
            continue                               # portrait photo-sized only
        if x + bw / 2 > 0.5 * w:                   # left half only (not the small photo)
            continue
        if cv2.contourArea(c) < 0.85 * bw * bh:    # must be a solid rectangle
            continue
        if best is None or bw * bh > best[2] * best[3]:
            best = (x, y, bw, bh)
    if best is None:
        return None
    x, y, bw, bh = best
    mask = np.zeros((h, w), np.uint8)
    mask[y:y + bh, x:x + bw] = 255
    return mask


def brighten(card, mask, factor):
    """Brighten only inside mask (soft 1 px edge so there is no visible seam)."""
    soft = cv2.GaussianBlur(mask, (3, 3), 0).astype(np.float32)[..., None] / 255
    bright = np.clip(card.astype(np.float32) * factor, 0, 255)
    return (card * (1 - soft) + bright * soft).round().astype(np.uint8)


def page_pixels(doc, page):
    """Best-quality pixels of a page: the embedded scan if the page is one full-page image,
    otherwise a 600 dpi render."""
    imgs = page.get_images(full=True)
    if len(imgs) == 1 and not page.get_drawings():
        xref = imgs[0][0]
        rects = page.get_image_rects(xref)
        if rects and (rects[0] * page.rotation_matrix).get_area() >= 0.95 * page.rect.get_area():
            pix = pymupdf.Pixmap(doc, xref)
            if pix.n - pix.alpha != 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            if pix.alpha:
                pix = pymupdf.Pixmap(pix, 0)
            rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
            if page.rotation:
                rgb = np.rot90(rgb, -page.rotation // 90).copy()
            return rgb
    return render_page(page, RENDER_DPI)


def find_card_boxes(rgb):
    """Card-shaped rectangles drawn on the sheet, outermost only, in reading order."""
    h, w = rgb.shape[:2]
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    dark = (gray < 160).astype(np.uint8)
    cnts, _ = cv2.findContours(dark, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in cnts:
        x, y, bw, bh = cv2.boundingRect(c)
        if bw < 0.25 * w or bh < 0.08 * h:
            continue
        if abs(bw / bh - CARD_ASPECT) > 0.12:
            continue
        # must really be a rectangle outline: dark along all four sides
        sides = [dark[y, x:x + bw].mean(), dark[y + bh - 1, x:x + bw].mean(),
                 dark[y:y + bh, x].mean(), dark[y:y + bh, x + bw - 1].mean()]
        if min(sides) < 0.8:
            continue
        boxes.append((x, y, bw, bh))
    # drop boxes inside another box (inner edge of the same border line)
    outer = [b for b in boxes if not any(
        o != b and o[0] <= b[0] and o[1] <= b[1] and o[0] + o[2] >= b[0] + b[2]
        and o[1] + o[3] >= b[1] + b[3] for o in boxes)]
    outer = list(dict.fromkeys(outer))
    outer.sort(key=lambda b: (round(b[1] / (0.05 * h)), b[0]))   # rows, then left->right
    return outer


def inside_border(gray, box):
    """Shrink a box past its border line on each side (no fixed guess of thickness)."""
    x, y, bw, bh = box
    mx, my = bw // 10, bh // 10
    def depth(strip_fn):
        last_dark = 0
        for i in range(0, 25):
            if strip_fn(i) < 200:
                last_dark = i
            elif i > last_dark + 1:
                break
        return last_dark + 2          # +1 past the line, +1 anti-alias pixel
    t = depth(lambda i: gray[y + i, x + mx:x + bw - mx].mean())
    b = depth(lambda i: gray[y + bh - 1 - i, x + mx:x + bw - mx].mean())
    l = depth(lambda i: gray[y + my:y + bh - my, x + i].mean())
    r = depth(lambda i: gray[y + my:y + bh - my, x + bw - 1 - i].mean())
    return x + l, y + t, x + bw - r, y + bh - b


def sheet_card_images(doc):
    """Cards laid out on a sheet (e-EPIC voter ID). Returns [(side, image), ...]."""
    out = []
    for page in doc:
        rgb = page_pixels(doc, page)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        for box in find_card_boxes(rgb):
            x0, y0, x1, y1 = inside_border(gray, box)
            crop = rgb[y0:y1, x0:x1].copy()
            side = PAGE_NAMES[len(out)] if len(out) < len(PAGE_NAMES) else f"Card{len(out) + 1}"
            print(f"    {side}: card found at {box[:2]}, {x1 - x0}x{y1 - y0} px inside the border")
            if side == "Front" and VOTER_PHOTO_BRIGHTNESS != 1:
                photo = find_main_photo(crop)
                if photo is None:
                    print("    ! main photo not found - brightness left unchanged")
                else:
                    crop = brighten(crop, photo, VOTER_PHOTO_BRIGHTNESS)
                    ys, xs = np.nonzero(photo)
                    print(f"    photo brightened {round((VOTER_PHOTO_BRIGHTNESS - 1) * 100)}% "
                          f"({xs.max() - xs.min() + 1}x{ys.max() - ys.min() + 1} px)")
            card = Image.fromarray(crop)
            out.append((side, card.resize((OUT_W, OUT_H), Image.LANCZOS)))
            if len(out) >= len(PAGE_NAMES):
                return out
    if not out:
        if not any(pg.get_text().strip() for pg in doc):
            raise ValueError("no card found, and this PDF has no real text (it is only a picture). "
                             "If it is an Aadhaar, use the original e-Aadhaar PDF from UIDAI "
                             "(locked or unlocked) - a scanned/flattened copy can't be read")
        raise ValueError("no card rectangles found on the page")
    return out


# ===================================================================== Aadhaar (e-Aadhaar letter)
# The UIDAI e-Aadhaar PDF is a letter page; at the bottom are the two "cut-out" cards
# (front left, back right) separated by a dashed divider and topped by a scissors line.
# The PVC card = exactly those two cut-outs, without the frame / divider / scissors line,
# trimmed to the PVC shape (no stretching) and rendered sharp from the PDF's vector text.
AADHAAR_NAMES = {"Front": "AADHAR_PVC_FRONT", "Back": "AADHAR_PVC_BACK"}


def is_aadhaar(doc):
    page = doc[0]
    if page.rect.width / 72 * 25.4 < SHEET_MIN_WIDTH_MM:      # a card-sized page is not the letter
        return False
    text = page.get_text().lower()
    return "aadhaar" in text or "uidai" in text or "unique identification" in text


def aadhaar_layout(page):
    """Finds the two cut-out card rectangles from the page structure (no fixed coords)."""
    drawings = page.get_drawings()
    # outer frame: the big stroked rectangle
    frames = [d for d in drawings if d.get("color") is not None and d.get("width")
              and d["rect"].width > 0.6 * page.rect.width and d["rect"].height > 0.5 * page.rect.height]
    if not frames:
        raise ValueError("Aadhaar layout not recognised (no page frame)")
    fr = max(frames, key=lambda d: d["rect"].get_area())
    half = (fr.get("width") or 1) / 2 + 0.3
    fx0, fx1, fy1 = fr["rect"].x0 + half, fr["rect"].x1 - half, fr["rect"].y1 - half
    # divider: thin, tall filled bars in the middle
    bars = [d["rect"] for d in drawings if d.get("fill") is not None and d["rect"].width < 3
            and d["rect"].height > 0.5 * fr["rect"].height]
    if not bars:
        raise ValueError("Aadhaar layout not recognised (no divider)")
    div_l = min(b.x0 for b in bars) - 0.3
    div_r = max(b.x1 for b in bars) + 0.3
    # scissors cut line: a thin wide image used on both halves, in the lower part of the page
    top = None
    for img in page.get_images(full=True):
        rects = [r * page.rotation_matrix for r in page.get_image_rects(img[0])]
        if len(rects) >= 2 and all(r.height < 10 and r.width > 0.3 * fr["rect"].width
                                   and r.y0 > page.rect.height * 0.5 for r in rects):
            top = max(r.y1 for r in rects) + 0.2
    if top is None:
        raise ValueError("Aadhaar layout not recognised (no cut line)")
    front = pymupdf.Rect(fx0, top, div_l, fy1)
    back = pymupdf.Rect(div_r, top, fx1, fy1)
    return [fit_aspect(front), fit_aspect(back)]


def fit_aspect(r):
    """Trim (never stretch) a rect to the PVC aspect, keeping it centred."""
    if r.width / r.height > CARD_ASPECT:
        d = (r.width - r.height * CARD_ASPECT) / 2
        return pymupdf.Rect(r.x0 + d, r.y0, r.x1 - d, r.y1)
    d = (r.height - r.width / CARD_ASPECT) / 2
    return pymupdf.Rect(r.x0, r.y0 + d, r.x1, r.y1 - d)


def aadhaar_card_images(doc):
    """PVC card in your design (templates/), filled with this PDF's details."""
    import aadhaar_pvc
    return aadhaar_pvc.generate(doc, aadhaar_layout(doc[0]))


def aadhaar_cutout_images(doc):
    """Plain cut-outs of the e-Aadhaar letter (not used by default)."""
    page = doc[0]
    out = []
    for side, rect in zip(PAGE_NAMES, aadhaar_layout(page)):
        pix = page.get_pixmap(dpi=RENDER_DPI, clip=rect, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        print(f"    {side}: cut-out {rect.width / 72 * 25.4:.1f} x {rect.height / 72 * 25.4:.1f} mm")
        out.append((side, img.resize((OUT_W, OUT_H), Image.LANCZOS)))
    return out


# ===================================================================== entry points
CARD_TYPES = {"auto": "Auto-detect", "dl": "Driving Licence", "voter": "Voter ID (e-EPIC)",
              "aadhaar": "Aadhaar (PVC)", "aadhaar_lam": "Aadhaar Laminated (A4)",
              "pan": "PAN Card", "ration": "Ration Card", "rc": "Vehicle RC", "abha": "ABHA Card",
              "pmjay": "Ayushman Card (PM-JAY)"}


class PasswordNeeded(ValueError):
    pass


def open_pdf(pdf_source, password=None, filename=None):
    """Open a PDF; for locked PDFs try the given password, then the file name
    (e-Aadhaar files are usually saved under their password, e.g. SUBR1992.pdf)."""
    if isinstance(pdf_source, (bytes, bytearray)):
        doc = pymupdf.open(stream=pdf_source, filetype="pdf")
    else:
        doc = pymupdf.open(pdf_source)
        filename = filename or Path(pdf_source).name
    if doc.needs_pass:
        tries = [password] if password else []
        if filename:
            stem = Path(filename).stem.strip()
            tries += [stem, stem.upper()]
        if not any(t and doc.authenticate(t) for t in tries):
            raise PasswordNeeded("this PDF is password-protected - enter its password")
        print("    unlocked with password")
    return doc


def detect_type(doc):
    import abha_card
    import pan_rc
    if abha_card.is_abha(doc):          # before the size check below: the ABHA page is wide like a voter sheet
        return "abha"
    import pmjay_card
    if pmjay_card.is_pmjay(doc):        # same: the PM-JAY page (front + back side by side) is wide too
        return "pmjay"
    if pan_rc.is_rc(doc):
        return "rc"
    if pan_rc.card_pictures(doc):       # front + back as two card pictures on A4, no text (new RC print)
        return "rc"
    if is_aadhaar(doc):
        return "aadhaar"
    if pan_rc.is_pan(doc):
        return "pan"
    w_mm = doc[0].rect.width / 72 * 25.4
    return "voter" if w_mm >= SHEET_MIN_WIDTH_MM else "dl"


def export_name(card_type, stem, side, details=None, pattern=None):
    """Export file name (without extension), e.g. PAN_C-REDDAMMA_017N_FRONT."""
    import card_details
    if details is None:
        details = {"type_label": card_details.TYPE_LABEL.get(card_type, card_type.upper())}
    return card_details.export_name(pattern or card_details.DEFAULT_PATTERN, details, side, stem)


def process_document_full(pdf_source, card_type="auto", password=None, filename=None, rc_no=None, epds_text=None):
    """Returns (card_type, [(side, PIL.Image), ...], details) - details: holder name / ID."""
    import card_details
    doc = open_pdf(pdf_source, password, filename)
    if card_type == "ration":
        import ration_card
        if not is_aadhaar(doc):
            raise ValueError("Ration Card needs the head of family's e-Aadhaar PDF")
        if not rc_no:
            raise ValueError("Enter the rice card (RC) number")
        data, details, _ = ration_card.build_data(rc_no, doc, epds_text)
        front, back = ration_card.render(data)
        import avatar
        details["_photo"] = avatar.holder_photo(doc, "aadhaar")
        details["_ration"] = data                              # kept so names can be edited later
        details["_photo_box"] = ration_card.photo_box() if data.get("photo") is not None else None
        print(f"    ration card: {details['name']}  RC {details['id']}")
        return "ration", [("Front", front), ("Back", back)], details
    if filename is None and not isinstance(pdf_source, (bytes, bytearray)):
        filename = Path(pdf_source).name
    if card_type == "auto":
        card_type = detect_type(doc)
        print(f"    type: {CARD_TYPES[card_type]}")
    layout = None
    if card_type in ("aadhaar", "aadhaar_lam"):
        try:
            layout = aadhaar_layout(doc[0])
        except Exception:
            pass
    details = card_details.read(doc, card_type, filename, layout)
    print(f"    holder: {details['name'] or '?'}  {details['id_label']}: "
          f"{('....' + details['id4']) if details['id4'] else '?'}")
    ctype, cards = _make_cards(doc, card_type)
    import avatar
    details["_photo"] = avatar.holder_photo(doc, ctype, cards[0][1] if cards else None)
    try:                                      # where the photo is on the front (for the Adjust editor)
        details["_photo_box"] = avatar.photo_box(doc, ctype, cards[0][1]) if cards else None
    except Exception as e:
        print(f"    (photo position not found: {e})")
    return ctype, cards, details


def process_document(pdf_source, card_type="auto", password=None, filename=None):
    """Returns (card_type, [(side, PIL.Image), ...])."""
    t, cards, _ = process_document_full(pdf_source, card_type, password, filename)
    return t, cards


# Backs of DL / RC: their text runs almost to the card edge (DL "Form 7 Rule 16(2)" 0.6 mm, RC
# "Registration Authority" 0.7 mm) and gets cut / touches the edge on a borderless PVC print.
# The back is shrunk to BACK_SCALE and centred; the margin is filled with the card's own background.
BACK_SCALE = {"dl": 0.93, "rc": 0.93}          # 0.93 -> +3.0 mm left/right, +1.9 mm top/bottom


LINE_DARK = 110               # a pixel darker than this belongs to a line / text


def _row_background(small):
    """The card's own background colour for every row, to continue it seamlessly into the margin.
    The DL / RC backs are shaded top-to-bottom but even from left to right, so each row's typical
    colour (median across the whole width) is its background - text, lines and their grey soft edges
    are left out, and a white QR box or a white strip at one edge is too small a part of the row to
    pull the colour off. Light smoothing between rows only removes noise; the shading is kept."""
    sh = small.shape[0]
    a = small[:, 3:-3].astype(np.float32)
    lum, sat = a.mean(axis=2), a.max(axis=2) - a.min(axis=2)
    ink = (lum < 150) | ((sat < 20) & (lum < 235))      # text, lines and their grey soft edges (background is bluish)
    a[ink] = np.nan
    with np.errstate(all="ignore"):
        col = np.nanmedian(a, axis=1)                                    # (rows, 3)
    ok = ~np.isnan(col).any(axis=1) & ((~ink).mean(axis=1) > 0.25)       # rows that are mostly a line: take neighbours
    ok[:4] = ok[-4:] = False                                             # outermost rows: leftovers of the old frame
    if not ok.any():
        return np.repeat(np.float32([[235, 242, 247]]), sh, axis=0)
    idx = np.arange(sh)
    # rows suddenly darker than the rows around them = a table line's soft edge across the card -> neighbours
    lum_rows = np.interp(idx, idx[ok], np.nan_to_num(col[:, :].mean(axis=1))[ok])
    trend = cv2.medianBlur(np.clip(lum_rows, 0, 255).astype(np.uint8).reshape(sh, 1), 15).reshape(sh).astype(np.float32)
    ok &= ~(lum_rows < trend - 4)
    col = np.stack([np.interp(idx, idx[ok], col[ok, c]) for c in range(3)], axis=1).astype(np.float32)
    return cv2.GaussianBlur(col.reshape(sh, 1, 3), (0, 0), sigmaX=0.1, sigmaY=2.0).reshape(sh, 3)


def add_margin(img, scale):
    """Shrink the card image to `scale`, centred. The freed border gets an even background colour
    (following the card's vertical gradient), and lines that run to the card's edge (the black line
    under "DL No") are continued through the border to the edge in one solid colour."""
    a = np.asarray(img.convert("RGB"))
    h, w = a.shape[:2]
    sw, sh = round(w * scale), round(h * scale)
    small = cv2.resize(a, (sw, sh), interpolation=cv2.INTER_AREA)
    x0, y0 = (w - sw) // 2, (h - sh) // 2
    rows = _row_background(small)
    full_rows = rows[np.clip(np.arange(h) - y0, 0, sh - 1)]              # margin above / below: first / last row
    out = np.repeat(full_rows[:, None, :], w, axis=1).round().clip(0, 255).astype(np.uint8)
    out[y0:y0 + sh, x0:x0 + sw] = small                                  # the card itself is untouched
    # continue edge-to-edge lines: rows whose ink touches the edge and runs on inward; every row of the
    # line (incl. its soft top / bottom rows) is continued in that row's own colour -> same thickness & shade
    lum = small.mean(axis=2)
    for side in ("left", "right"):
        band = lum[:, :40] if side == "left" else lum[:, -40:][:, ::-1]
        core = np.nonzero((band[:, :3] < LINE_DARK).all(axis=1) & ((band < LINE_DARK).mean(axis=1) > 0.9))[0]
        paint = {}
        for r in core:
            for rr in range(max(0, r - 3), min(sh, r + 4)):
                px = small[rr, 3:40] if side == "left" else small[rr, -40:-3]
                colour = np.median(px, axis=0)
                if rows[rr].mean() - colour.mean() > 20:                 # clearly part of the line
                    paint[rr] = colour.round().astype(np.uint8)
        for rr, colour in paint.items():
            if side == "left":
                out[y0 + rr, :x0 + 3] = colour
            else:
                out[y0 + rr, x0 + sw - 3:] = colour
    return Image.fromarray(out)


# Cards whose design runs to the very edge (rice card): BOTH sides are shrunk and centred, and the card's
# own edge colours are carried out to the new edge on all 4 sides (same spacing as the DL / RC backs:
# 0.93 -> +3.0 mm left/right, +1.9 mm top/bottom). The rice card applies it in ration_card.render.
# Aadhaar PVC is NOT padded any more: it prints at full size with its original template edges
# (to pad it again: add "aadhaar": 0.93 here and "aadhaar": 15 to EDGE_SMOOTH).
EDGE_SCALE = {"ration": 0.93}
# Side colours smoothed over this many rows, so a thin line at the edge does not become a solid bar in
# the margin (the rice card's red line under the header SHOULD run on: no smoothing)
EDGE_SMOOTH = {}
EDGE_STRIP = (2, 26)          # edge colour = median of this band of pixels in from each edge (skips the
                              # anti-aliased outermost pixel; wide enough that small text tips don't count)


def pad_edges(img, scale, smooth=0):
    """Shrink the card to `scale`, centred, and fill the freed border by extending the card's edge colours
    straight outwards (Photoshop-style edge extend): each row continues its left / right edge colour,
    each column its top / bottom edge colour, corners their corner colour. Colour bands and lines that
    reach the edge (yellow header, red bands, gradients) therefore run on to the new edge unbroken."""
    a = np.asarray(img.convert("RGB"))
    h, w = a.shape[:2]
    sw, sh = round(w * scale), round(h * scale)
    small = cv2.resize(a, (sw, sh), interpolation=cv2.INTER_AREA)
    s0, s1 = EDGE_STRIP
    edge = small.copy()
    edge[:, 0] = np.median(small[:, s0:s1], axis=1)
    edge[:, -1] = np.median(small[:, sw - s1:sw - s0], axis=1)
    if smooth:
        k = smooth | 1
        for c in (0, -1):
            edge[:, c] = cv2.medianBlur(np.ascontiguousarray(edge[:, c])[:, None], k)[:, 0]
    top = np.median(small[s0:s1 // 2], axis=0)
    bot = np.median(small[sh - s1 // 2:sh - s0], axis=0)
    # top / bottom: smooth along the edge so a letter or emblem tip near the edge leaves no streak
    edge[0] = cv2.medianBlur(top.astype(np.uint8)[None], 31)[0]
    edge[-1] = cv2.medianBlur(bot.astype(np.uint8)[None], 31)[0]
    for (y, x) in ((0, 0), (0, -1), (-1, 0), (-1, -1)):
        edge[y, x] = (edge[y, 1 if x == 0 else -2].astype(int) + edge[1 if y == 0 else -2, x]) // 2
    x0, y0 = (w - sw) // 2, (h - sh) // 2
    out = cv2.copyMakeBorder(edge, y0, h - sh - y0, x0, w - sw - x0, cv2.BORDER_REPLICATE)
    out[y0:y0 + sh, x0:x0 + sw] = small                                  # the card itself is untouched
    return Image.fromarray(out)


def scale_box(box, scale, size=(OUT_W, OUT_H)):
    """A box on the full-size card -> where it is after pad_edges(scale)."""
    w, h = size
    x0, y0 = (w - round(w * scale)) // 2, (h - round(h * scale)) // 2
    return [round(x0 + box[0] * scale), round(y0 + box[1] * scale),
            round(x0 + box[2] * scale), round(y0 + box[3] * scale)]


def _make_cards(doc, card_type):
    t, cards = _make_cards_raw(doc, card_type)
    if t in BACK_SCALE:
        cards = [(s, add_margin(im, BACK_SCALE[t]) if s == "Back" else im) for s, im in cards]
    if t in EDGE_SCALE and t != "ration":
        cards = [(s, pad_edges(im, EDGE_SCALE[t], EDGE_SMOOTH.get(t, 0))) for s, im in cards]
    return t, cards


def _make_cards_raw(doc, card_type):
    if card_type == "aadhaar_lam":
        if not is_aadhaar(doc):
            raise ValueError("this is not an e-Aadhaar PDF - Aadhaar Laminated needs the UIDAI e-Aadhaar letter")
        import aadhaar_laminate_pdf as V
        sheets = V.build_all(doc)                     # print-quality vector PDF per sheet
        out, combined = [], __import__("pymupdf").open()
        for s, sdoc in sheets.items():
            img = V.to_image(sdoc)                    # preview / JPEG rendered from that PDF
            out.append((s.capitalize(), img))
            combined.insert_pdf(sdoc)
        if out:
            out[0][1].info["cps_pdf"] = combined.tobytes(deflate=True, garbage=3)
        return card_type, out
    if card_type == "aadhaar":
        return card_type, aadhaar_card_images(doc)
    if card_type == "abha":
        import abha_card
        return card_type, abha_card.card_images(doc)
    if card_type == "pmjay":
        import pmjay_card
        return card_type, pmjay_card.card_images(doc)
    if card_type in ("pan", "rc"):
        import pan_rc
        return card_type, (pan_rc.pan_card_images(doc) if card_type == "pan" else pan_rc.rc_card_images(doc))
    if card_type == "voter":
        return card_type, sheet_card_images(doc)
    out = []
    for i, page in enumerate(doc):
        if i >= len(PAGE_NAMES):
            print(f"    (ignoring extra page {i + 1})")
            break
        out.append((PAGE_NAMES[i], process_page(doc, page)))
    return card_type, out


def card_images(pdf_source, card_type="auto", password=None, filename=None):
    """pdf_source: path or bytes. Returns [(side_name, PIL.Image), ...] (Front, Back)."""
    return process_document(pdf_source, card_type, password, filename)[1]


def save_jpeg(img, path_or_file):
    img.save(path_or_file, "JPEG", quality=JPEG_QUALITY, dpi=(OUT_DPI, OUT_DPI), subsampling=0)


_USED_THIS_RUN = set()


def process_pdf(pdf_path: Path):
    print(f"> {pdf_path.name}")
    card_type, cards, details = process_document_full(pdf_path)
    details.pop("_photo", None)
    for side, img in cards:
        name = export_name(card_type, pdf_path.stem, side, details)
        out_path, n = EXPORT_DIR / f"{name}.jpeg", 2
        while out_path in _USED_THIS_RUN:          # two Aadhaar PDFs in one run: don't overwrite
            out_path, n = EXPORT_DIR / f"{name} ({n}).jpeg", n + 1
        _USED_THIS_RUN.add(out_path)
        save_jpeg(img, out_path)
        print(f"    -> export/{out_path.name}")


def main():
    EXPORT_DIR.mkdir(exist_ok=True)
    files = [Path(a) for a in sys.argv[1:]] or sorted(RAW_DIR.glob("*.pdf"))
    if not files:
        print("No PDFs found in raw/")
        return
    for f in files:
        try:
            process_pdf(f)
        except Exception as e:
            print(f"    ERROR: {e}")
    print("Done.")


if __name__ == "__main__":
    main()
