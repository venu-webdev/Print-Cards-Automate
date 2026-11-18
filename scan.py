"""
Card scan: a phone photo or flatbed scan of a customer's physical card -> a straight PVC card image.

  find_card(img)        where the card is in the photo: 4 corners (TL, TR, BR, BL), the long side on top
  warp(img, corners)    perspective crop to 1016 x 638 px (85.6 x 54 mm @ 300 dpi), optional edge trim
  enhance(card, p)      auto colour (levels / white balance), brightness, contrast, colour, sharpness

The card is only copied - nothing on it is changed or replaced.
"""
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

CARD_W, CARD_H = 1016, 638
CARD_RATIO = CARD_W / CARD_H            # 1.586 (ID-1)
DETECT_PX = 1000                        # detection works on a copy this big (long side)
MAX_SOURCE_PX = 4200                    # bigger photos are scaled down when uploaded
DEFAULTS = {"rot": 0, "trim": 2, "auto": True, "br": 0, "ct": 0, "sat": 0, "sharp": 25, "deframe": False}


MAX_PDF_PAGES = 15                      # render at most this many pages of a PDF
PDF_DPI = 300                           # render PDFs at this resolution (then capped by MAX_SOURCE_PX)


def is_pdf(data):
    return data[:5] == b"%PDF-" or data[:1024].lstrip()[:4] == b"%PDF"


def _fit(im):
    if max(im.size) > MAX_SOURCE_PX:
        k = MAX_SOURCE_PX / max(im.size)
        im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    return im


def open_upright(data):
    """Photo bytes -> RGB image the right way up (phones store the turn in EXIF), not huge."""
    import io
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im).convert("RGB")
    return _fit(im)


def render_pdf(data):
    """Any PDF (flattened scan, embedded image, or selectable text / vector) -> one RGB image per page.
    Rendering the page is the one path that handles every kind - what you see is what you crop."""
    import pymupdf
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = []
    for i, page in enumerate(doc):
        if i >= MAX_PDF_PAGES:
            break
        pix = page.get_pixmap(dpi=PDF_DPI, alpha=False)
        pages.append(_fit(Image.frombytes("RGB", (pix.width, pix.height), pix.samples)))
    return pages


def load_any(data):
    """Any input (image or PDF) -> list of RGB page images (1 for an image)."""
    if is_pdf(data):
        return render_pdf(data)
    return [open_upright(data)]


# ---------------------------------------------------------------- finding the card
def order_corners(pts):
    """4 points -> TL, TR, BR, BL (clockwise from top-left), then turned so the top edge is a long side."""
    pts = np.asarray(pts, np.float32).reshape(4, 2)
    c = pts.mean(0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]                       # clockwise in image coordinates (y down)
    start = np.argmin(pts.sum(1))                    # top-left = smallest x + y
    pts = np.roll(pts, -start, 0)
    side = lambda a, b: float(np.hypot(*(pts[a] - pts[b])))
    if side(0, 1) + side(2, 3) < side(1, 2) + side(3, 0):   # card stands upright in the photo
        pts = np.roll(pts, -1, 0)                    # -> lay it down (turn 180° later if it is upside down)
    return pts


def _quad_score(q, img_area):
    import cv2
    q = order_corners(q)
    long_ = (np.hypot(*(q[0] - q[1])) + np.hypot(*(q[2] - q[3]))) / 2
    short = (np.hypot(*(q[1] - q[2])) + np.hypot(*(q[3] - q[0]))) / 2
    if short < 10:
        return 0, q
    area = cv2.contourArea(q.astype(np.float32))
    frac = area / img_area
    if frac < 0.06 or frac > 0.995:
        return 0, q
    ratio = long_ / short
    aspect = np.exp(-((ratio - CARD_RATIO) / 0.32) ** 2)       # photos at an angle are a bit off the true shape
    if not cv2.isContourConvex(q.astype(np.float32).reshape(-1, 1, 2)):
        return 0, q
    return frac ** 0.5 * (0.35 + 0.65 * aspect), q


def _quads_from_contour(c, img_area):
    import cv2
    out = []
    if cv2.contourArea(c) < 0.05 * img_area:
        return out
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    for eps in (0.01, 0.02, 0.03, 0.045, 0.06, 0.08):
        ap = cv2.approxPolyDP(hull, eps * peri, True)
        if len(ap) == 4:
            out.append((ap.reshape(4, 2).astype(np.float32), cv2.contourArea(hull) / max(1, cv2.contourArea(ap))))
            break
    box = cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)        # rounded / broken outlines
    out.append((box, 0.92 * cv2.contourArea(hull) / max(1, cv2.contourArea(box))))
    return out


def _scored_quads(img):
    """All candidate card quads on the page, best first. Two ways: 1) edges (the card outline against the table /
    scanner lid / page), 2) colour (what differs from the border). -> (full-res array, scale k, [(score, quad_small, fill), ...])."""
    import cv2
    a = np.asarray(img.convert("RGB"))
    H, W = a.shape[:2]
    k = min(1.0, DETECT_PX / max(H, W))
    s = cv2.resize(a, (round(W * k), round(H * k)), interpolation=cv2.INTER_AREA) if k < 1 else a.copy()
    h, w = s.shape[:2]
    area = h * w
    cands = []
    gray = cv2.GaussianBlur(cv2.cvtColor(s, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    for lo, hi in ((10, 30), (20, 60), (35, 110), (60, 180)):
        e = cv2.dilate(cv2.Canny(gray, lo, hi), np.ones((3, 3), np.uint8), iterations=2)
        e = cv2.morphologyEx(e, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
        cnts, _ = cv2.findContours(e, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:12]:
            cands += _quads_from_contour(c, area)
    # colour: the background is what the photo's border looks like
    lab = cv2.cvtColor(s, cv2.COLOR_RGB2LAB).astype(np.float32)
    bw = max(4, int(min(h, w) * 0.03))
    border = np.concatenate([lab[:bw].reshape(-1, 3), lab[-bw:].reshape(-1, 3), lab[:, :bw].reshape(-1, 3), lab[:, -bw:].reshape(-1, 3)])
    bg = np.median(border, 0)
    d = np.linalg.norm(lab - bg, axis=2)
    d8 = np.clip(d * (255 / max(1.0, np.percentile(d, 99))), 0, 255).astype(np.uint8)
    t, _ = cv2.threshold(d8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    for thr in (max(t, 25), max(t * 0.6, 18)):
        m = (d8 > thr).astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:3]:
            cands += _quads_from_contour(c, area)
    scored = []
    for q, fill in cands:
        sc, oq = _quad_score(q, area)
        sc *= min(1.0, fill) ** 2                     # the outline must really be filled like a card
        if sc > 0:
            scored.append((sc, oq, fill))
    scored.sort(key=lambda t: -t[0])
    return a, k, area, scored


def _refine(a, q_small, k):
    q = np.asarray(q_small, np.float32) / k
    q = _fit_edges(a, q, int(12 / k) + 6)             # wide search first (rounded corners pull the rough answer in)
    return _fit_edges(a, q, int(3 / k) + 4)           # then tight around the lines found


def find_card(img):
    """-> (corners 4x2 in img px, found: bool). The single best-shaped card on the page."""
    a, k, area, scored = _scored_quads(img)
    if not scored or scored[0][0] < 0.18:
        H, W = a.shape[:2]
        m = 0.03                                      # not found: the whole photo (a scan that is only the card)
        q = np.array([[W * m, H * m], [W * (1 - m), H * m], [W * (1 - m), H * (1 - m)], [W * m, H * (1 - m)]], np.float32)
        return order_corners(q), False
    return _refine(a, scored[0][1], k), True


def _bbox(q):
    q = np.asarray(q, float)
    return q[:, 0].min(), q[:, 1].min(), q[:, 0].max(), q[:, 1].max()


def _iou(b1, b2):
    ix0, iy0 = max(b1[0], b2[0]), max(b1[1], b2[1])
    ix1, iy1 = min(b1[2], b2[2]), min(b1[3], b2[3])
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    ua = (b1[2] - b1[0]) * (b1[3] - b1[1]) + (b2[2] - b2[0]) * (b2[3] - b2[1]) - inter
    return inter / ua if ua > 0 else 0.0


def has_frame(img, corners, min_edges=3):
    """True if the card at `corners` is wrapped in a thin printed border line (a web-print DL / RC frame):
    on at least `min_edges` sides there is an outermost thin full-width dark line with lighter content just inside."""
    card = warp(img, corners, size=(254, 160))
    a = np.asarray(card).astype(np.float32)
    band = max(4, int(0.07 * min(a.shape[:2])))

    def edge_has(strip):
        lum = strip[:band].mean(2)
        mid = slice(int(0.15 * lum.shape[1]), int(0.85 * lum.shape[1]))
        for dd in range(lum.shape[0] - 3):
            if (lum[dd, mid] < 185).mean() > 0.5 and lum[dd + 3, mid].mean() > lum[dd, mid].mean() + 12:
                return True
        return False
    n = (edge_has(a) + edge_has(a[::-1]) + edge_has(np.transpose(a[:, :band], (1, 0, 2)))
         + edge_has(np.transpose(a[:, ::-1][:, :band], (1, 0, 2))))
    return n >= min_edges


def find_cards(img, max_n=2):
    """Up to `max_n` separate cards printed on one page (e.g. a DL / RC front + back together), ordered
    top-to-bottom then left-to-right. Each: {corners 4x2 in img px, framed: bool (has a printed border line)}."""
    import cv2
    a, k, area, scored = _scored_quads(img)
    picks, boxes, first_area = [], [], None
    for sc, oq, fill in scored:
        if sc < 0.18:
            break
        b = _bbox(oq)
        if any(_iou(b, pb) > 0.3 for pb in boxes):     # the same card found more than once
            continue
        ar = cv2.contourArea(np.asarray(oq, np.float32))
        if first_area is None:
            first_area = ar
        elif not (0.6 * first_area <= ar <= 1.7 * first_area):   # a 2nd card = front+back of one card => same size
            continue
        picks.append(oq)
        boxes.append(b)
        if len(picks) >= max_n:
            break
    out = [{"corners": _refine(a, oq, k)} for oq in picks]
    for c in out:
        c["framed"] = has_frame(img, c["corners"])
    out.sort(key=lambda c: (round(np.asarray(c["corners"])[:, 1].mean() / 40), np.asarray(c["corners"])[:, 0].mean()))
    return out


def _fit_edges(a, q, search):
    """Exact corners at full size: along the middle of each edge, find the strongest light/dark step across it,
    fit a straight line through those points, and cross neighbouring lines. Rounded card corners are skipped
    this way, so the corners land on the card's true (sharp) corners, not inside the curve."""
    import cv2
    g = cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY), (3, 3), 0).astype(np.float32)
    gx, gy = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    H, W = g.shape
    q = order_corners(q)
    lines = []
    offs = np.arange(-search, search + 1, dtype=np.float32)
    for i in range(4):
        P, Q = q[i], q[(i + 1) % 4]
        L = float(np.hypot(*(Q - P)))
        u = (Q - P) / L
        n = np.array([-u[1], u[0]], np.float32)
        pts = []
        for t in np.linspace(0.14, 0.86, 60):
            base = P + u * L * t
            xs = np.rint(base[0] + n[0] * offs).astype(int)
            ys = np.rint(base[1] + n[1] * offs).astype(int)
            ok = (xs >= 0) & (xs < W) & (ys >= 0) & (ys < H)
            if ok.sum() < len(offs) // 2:
                continue
            gv = np.zeros(len(offs), np.float32)
            gv[ok] = np.abs(gx[ys[ok], xs[ok]] * n[0] + gy[ys[ok], xs[ok]] * n[1])
            top = gv.max()
            if top <= 12:
                continue
            strong = np.nonzero(gv >= 0.55 * top)[0]
            j = int(strong[0])                              # outermost strong step (n points into the card):
            pts.append(base + n * offs[j])                   # the card's own edge, not print inside it
        if len(pts) < 15:
            return q
        vx, vy, x0, y0 = cv2.fitLine(np.array(pts, np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0]), np.array([vx, vy])))
    out = []
    for i in range(4):                                   # corner i = edge i-1 crossed with edge i
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        den = d1[0] * d2[1] - d1[1] * d2[0]
        if abs(den) < 1e-6:
            return q
        s = ((p2[0] - p1[0]) * d2[1] - (p2[1] - p1[1]) * d2[0]) / den
        out.append(p1 + d1 * s)
    out = np.array(out, np.float32)
    short = min(np.hypot(*(q[i] - q[(i + 1) % 4])) for i in range(4))
    if np.hypot(*(out - q).T).max() > 0.2 * short:        # something else was caught - keep the rough answer
        return q
    return order_corners(out)


# ---------------------------------------------------------------- straightening + colours
def warp(img, corners, rot=0, trim=0, size=(CARD_W, CARD_H)):
    """Perspective crop of the card to `size`. trim: px (of the final card) cut off every edge - takes away a
    thin line of table / scanner lid. rot: 180 turns an upside-down card."""
    import cv2
    W, H = size
    a = np.asarray(img.convert("RGB"))
    t = float(trim) * W / CARD_W
    S = 2                                                # warp at 2x, then shrink: no jagged edges / moire
    dst = np.array([[-t, -t], [W + t, -t], [W + t, H + t], [-t, H + t]], np.float32) * S
    M = cv2.getPerspectiveTransform(np.asarray(corners, np.float32).reshape(4, 2), dst)
    big = cv2.warpPerspective(a, M, (W * S, H * S), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    out = Image.fromarray(cv2.resize(big, (W, H), interpolation=cv2.INTER_AREA))
    return out.rotate(180) if int(rot) % 360 == 180 else out


def auto_colour(card, strength=0.8):
    """Levels per colour channel (dark 0.5 %, light 99.5 %): fixes a yellow / blue cast and a grey, flat photo."""
    a = np.asarray(card, np.float32)
    out = np.empty_like(a)
    for ch in range(3):
        lo, hi = np.percentile(a[..., ch], (0.5, 99.5))
        if hi - lo < 30:
            out[..., ch] = a[..., ch]
            continue
        out[..., ch] = (a[..., ch] - lo) * (255.0 / (hi - lo))
    out = a + (out - a) * strength
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def enhance(card, p):
    p = {**DEFAULTS, **(p or {})}
    if p.get("auto"):
        card = auto_colour(card)
    if p["br"]:
        card = ImageEnhance.Brightness(card).enhance(1 + p["br"] / 100)
    if p["ct"]:
        card = ImageEnhance.Contrast(card).enhance(1 + p["ct"] / 100)
    if p["sat"]:
        card = ImageEnhance.Color(card).enhance(1 + p["sat"] / 100)
    if p["sharp"]:
        card = card.filter(ImageFilter.UnsharpMask(radius=1.6, percent=int(p["sharp"] * 2), threshold=2))
    return card


def clean_frame(card, band_frac=0.05):
    """Remove the thin printed border line that web-print cards (e.g. an arivahan.gov.in DL / RC) draw around
    the card. Per edge: find the outermost thin full-width dark line (the frame) and fill the strip from the
    card edge up to it with the colour just inside the line - so a coloured header stays that colour, white
    stays white, and the text / photo / emblem further in are never touched. An edge with no such line (a real
    card photographed on a table) is left exactly as it was."""
    a = np.asarray(card.convert("RGB")).astype(np.uint8).copy()
    H, W = a.shape[:2]
    band = max(6, int(band_frac * min(H, W)))

    def line_dist(strip):                                 # strip[d] = the row at distance d in from the edge
        lum = strip[:band].mean(2)
        mid = slice(int(0.15 * lum.shape[1]), int(0.85 * lum.shape[1]))
        for d in range(lum.shape[0] - 4):                 # the OUTERMOST full-width dark line is the frame
            if (lum[d, mid] < 185).mean() > 0.5 and lum[d + 4, mid].mean() > lum[d, mid].mean() + 12:
                return d
        return -1

    d = line_dist(a)                                      # top
    if d >= 0: a[:d + 2, :] = a[d + 2:d + 3, :]
    d = line_dist(a[::-1])                                # bottom
    if d >= 0: a[H - d - 2:, :] = a[H - d - 3:H - d - 2, :]
    d = line_dist(np.transpose(a[:, :band], (1, 0, 2)))   # left
    if d >= 0: a[:, :d + 2] = a[:, d + 2:d + 3]
    d = line_dist(np.transpose(a[:, ::-1][:, :band], (1, 0, 2)))   # right
    if d >= 0: a[:, W - d - 2:] = a[:, W - d - 3:W - d - 2]
    return Image.fromarray(a)


def make(img, state, size=(CARD_W, CARD_H)):
    """Everything for one side: straighten, (optionally) remove a printed frame, then colours."""
    st = {**DEFAULTS, **(state or {})}
    card = warp(img, st["corners"], st["rot"], st["trim"], size)
    if st.get("deframe"):
        card = clean_frame(card)
    return enhance(card, st)
