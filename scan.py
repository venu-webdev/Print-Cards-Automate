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
DEFAULTS = {"rot": 0, "trim": 2, "auto": True, "br": 0, "ct": 0, "sat": 0, "sharp": 25}


def open_upright(data):
    """Photo bytes -> RGB image the right way up (phones store the turn in EXIF), not huge."""
    import io
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im).convert("RGB")
    if max(im.size) > MAX_SOURCE_PX:
        k = MAX_SOURCE_PX / max(im.size)
        im = im.resize((round(im.width * k), round(im.height * k)), Image.LANCZOS)
    return im


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


def find_card(img):
    """-> (corners 4x2 in img px, found: bool). Two ways, the best-shaped answer wins:
    1) edges (card outline against the table / scanner lid), 2) colour: what differs from the photo's border."""
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
    best = None
    for q, fill in cands:
        sc, oq = _quad_score(q, area)
        sc *= min(1.0, fill) ** 2                     # the outline must really be filled like a card
        if best is None or sc > best[0]:
            best = (sc, oq)
    if best is None or best[0] < 0.18:
        m = 0.03                                      # not found: the whole photo (a scan that is only the card)
        q = np.array([[W * m, H * m], [W * (1 - m), H * m], [W * (1 - m), H * (1 - m)], [W * m, H * (1 - m)]], np.float32)
        return order_corners(q), False
    q = best[1] / k
    q = _fit_edges(a, q, int(12 / k) + 6)            # wide search first (rounded corners pull the rough answer in)
    return _fit_edges(a, q, int(3 / k) + 4), True      # then tight around the lines found


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


def make(img, state, size=(CARD_W, CARD_H)):
    """Everything for one side: straighten, then colours."""
    st = {**DEFAULTS, **(state or {})}
    return enhance(warp(img, st["corners"], st["rot"], st["trim"], size), st)
