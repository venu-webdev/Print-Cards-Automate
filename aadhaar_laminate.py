"""
Laminated Aadhaar A4 sheets - the shop's Photoshop actions (shop_actions_2026_.atn:
BIG_AADHAR -> "full", BOTH_AADHAR -> "both", SMALL_AADHAR -> "small") re-done in code.

Only the action steps that are switched ON in the .atn are reproduced; every number was
decoded from the action file, the base placement was measured against the action's output.
Canvas: A4 at 300 dpi (2480 x 3508), like FULL AADHAR.psd.
"""
import datetime
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
MARK_IMG = ROOT / "templates" / "aadhaar_lam_mark.png"   # "Signature valid" + green tick (from the PSD)
DATE_FONT = "C:/Windows/Fonts/ARIALN.TTF"                # Arial Narrow, as in the PSD text layer
A4 = (2480, 3508)
SHEETS = ("full", "both", "small")

# placed PDF: page at 96.11% x 97.28% of 300 dpi, page origin at (33, -342) px
BASE_SX, BASE_SY = 0.9611 * 300 / 72, 0.9728 * 300 / 72
BASE_TX, BASE_TY = 33, -342

PHOTO_RECT = (2115, 316, 2394, 537)                       # (top, left, bottom, right)
PHOTO_CURVE = [(0, 0), (113, 143), (255, 255)]
PHOTO_GAMMA = 1.24
REST_CURVE = [(0, 0), (159, 105), (255, 255)]
CUT_LINE = (2624, 152, 2949, 2327)                         # layer mask: hidden below the card strip
STRIP = (1971, 223, 2637, 2296)                            # the two cut-out cards
STRIP_COPY_DY = 686                                        # BOTH: second set of cards
SMALL_DY = -2563                                           # SMALL: copied strip to the top

MARK_POS = (380, 1377)
DATE_XFORM = (0.2458296678371003, 0.200168159619955, 487.53447685637383, 1474.5882592281962)
DATE_PT = 67.0


def _spline_lut(points):
    """Photoshop curve (natural cubic spline through the points) -> 256-entry LUT."""
    xs = np.array([p[0] for p in points], float); ys = np.array([p[1] for p in points], float)
    n, h = len(xs), np.diff(xs)
    a = np.zeros((n, n)); r = np.zeros(n); a[0, 0] = a[-1, -1] = 1
    for i in range(1, n - 1):
        a[i, i - 1], a[i, i], a[i, i + 1] = h[i - 1], 2 * (h[i - 1] + h[i]), h[i]
        r[i] = 3 * ((ys[i + 1] - ys[i]) / h[i] - (ys[i] - ys[i - 1]) / h[i - 1])
    c = np.linalg.solve(a, r)
    x = np.arange(256, dtype=float)
    k = np.clip(np.searchsorted(xs, x, side="right") - 1, 0, n - 2)
    dx = x - xs[k]
    b = (ys[k + 1] - ys[k]) / h[k] - h[k] * (2 * c[k] + c[k + 1]) / 3
    d = (c[k + 1] - c[k]) / (3 * h[k])
    return np.clip(ys[k] + b * dx + c[k] * dx ** 2 + d * dx ** 3, 0, 255)


def _place_pdf(page):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(BASE_SX, BASE_SY), alpha=True, annots=False)  # like Photoshop: no signature stamp
    img = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    layer = Image.new("RGBA", A4, (0, 0, 0, 0))
    layer.alpha_composite(img, (BASE_TX, 0), (0, -BASE_TY))
    return layer


def _xform(layer, rect, dx=0.0, dy=0.0, sx=100.0, sy=100.0):
    """Photoshop move/transform of a rectangular selection: lift pixels, scale about the
    selection centre, shift. Returns the selection rect after the operation."""
    t, l, b, r = rect
    piece = layer.crop((l, t, r, b))
    layer.paste(Image.new("RGBA", piece.size, (0, 0, 0, 0)), (l, t))
    w, h = round((r - l) * sx / 100), round((b - t) * sy / 100)
    if (w, h) != piece.size:
        piece = piece.resize((w, h), Image.BICUBIC)
    x0 = round((l + r) / 2 + dx - w / 2); y0 = round((t + b) / 2 + dy - h / 2)
    layer.alpha_composite(piece, (max(0, x0), max(0, y0)), (max(0, -x0), max(0, -y0)))
    return (y0, x0, y0 + h, x0 + w)


def _colour(layer):
    a = np.array(layer)
    idx = a[..., :3]
    photo = np.zeros(a.shape[:2], bool)
    t, l, b, r = PHOTO_RECT
    photo[t:b, l:r] = True
    lut_p = 255 * (_spline_lut(PHOTO_CURVE) / 255) ** (1 / PHOTO_GAMMA)
    lut_r = _spline_lut(REST_CURVE)
    a[..., :3] = np.clip(np.where(photo[..., None], lut_p[idx], lut_r[idx]), 0, 255).round().astype(np.uint8)
    return Image.fromarray(a, "RGBA")


def _mark(canvas, when):
    """Green tick 'Signature valid' block + current timestamp (what date.jsx writes)."""
    canvas.alpha_composite(Image.open(MARK_IMG).convert("RGBA"), MARK_POS)
    text = when.strftime("%Y.%m.%d %H:%M:%S")
    sx, sy, cx, base_y = DATE_XFORM
    ss = 4                                               # draw large, then squash like the PSD
    font = ImageFont.truetype(DATE_FONT, round(DATE_PT * ss))
    l, t, r, b = font.getbbox(text)
    asc = font.getmetrics()[0]
    big = Image.new("L", (r - l + 8, asc + 8 * ss), 0)
    ImageDraw.Draw(big).text((4 - l, 0), text, font=font, fill=255)
    w, h = max(1, round(big.width * sx / ss)), max(1, round(big.height * sy / ss))
    small = big.resize((w, h), Image.LANCZOS)
    x, y = round(cx - w / 2), round(base_y - asc * sy / ss)
    ink = Image.new("RGBA", small.size, (0, 0, 0, 255)); ink.putalpha(small)
    canvas.alpha_composite(ink, (x, y))


def build(doc, sheet="full", when=None, enhance=True):
    """sheet: 'full' (BIG_AADHAR), 'both' (BOTH_AADHAR) or 'small' (SMALL_AADHAR).
    enhance: the action's Curves/Levels (brighter face, richer colours)."""
    if sheet not in SHEETS:
        raise ValueError(f"unknown sheet {sheet!r}")
    layer = _place_pdf(doc[0])
    if sheet in ("both", "small"):
        _xform(layer, (2294, 556, 2506, 1220), dx=-24.21)          # front card: name / DOB block
    _xform(layer, (2145, 1285, 2414, 1314), dx=11.4)              # back card: "Details as on"
    if enhance:
        layer = _colour(layer)
    sel = _xform(layer, (2105, 1288, 2432, 1871), dx=5, dy=-2)    # back card: address block
    _xform(layer, sel, dx=9.5, dy=7, sx=96.75, sy=101.2)
    _xform(layer, (2636, 152, 2961, 2327), dy=-12)
    a = np.array(layer)                                           # mask: hide below the cut line
    t, l, b, r = CUT_LINE
    a[t:b, l:r, 3] = 0
    layer = Image.fromarray(a, "RGBA")

    if sheet in ("both", "small"):
        t, l, b, r = STRIP
        strip = layer.crop((l, t, r, b))
        if sheet == "both":
            layer.alpha_composite(strip, (l, t + STRIP_COPY_DY))
        else:
            layer = Image.new("RGBA", A4, (0, 0, 0, 0))
            layer.alpha_composite(strip, (l, t + STRIP_COPY_DY + SMALL_DY))

    sheet_img = Image.new("RGBA", A4, (255, 255, 255, 255))
    sheet_img.alpha_composite(layer)
    if sheet != "small":
        _mark(sheet_img, when or datetime.datetime.now())
    return sheet_img.convert("RGB")


def save(img, path):
    img.save(path, dpi=(300, 300))
