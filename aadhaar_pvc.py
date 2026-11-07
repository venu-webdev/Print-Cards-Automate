"""
Aadhaar PVC card generator.

Builds the PVC-style front & back (your design in templates/) and fills in the details
of any UIDAI e-Aadhaar PDF:

  front: photo, small B&W photo + number, regional/English name, DOB, gender (rendered
         straight from the PDF so fonts/script are exact), mobile, "Aadhaar No. Issued",
         Aadhaar number
  back : address block (rendered from the PDF), QR code, "Details As On", Aadhaar number

All positions/sizes below were measured from the reference cards
(templates/aadhaar_pvc_*_reference.png) so the output matches that design.
"""
import re
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
TPL = ROOT / "templates"
REF_FRONT = TPL / "aadhaar_pvc_front_reference.png"
REF_BACK = TPL / "aadhaar_pvc_back_reference.png"
BLANK_FRONT = TPL / "aadhaar_pvc_front_blank.png"
BLANK_BACK = TPL / "aadhaar_pvc_back_blank.png"
PHOTO_CURVE = TPL / "aadhaar_photo_curve.json"   # brightness/colour edit learned from the reference

W, H = 1016, 638
ARIAL = "C:/Windows/Fonts/arial.ttf"
ARIAL_BOLD = "C:/Windows/Fonts/arialbd.ttf"

# ------------------------------------------------------------------ layout (card pixels)
# areas holding personal data in the reference -> erased when building the blank template
FRONT_ERASE = [(18, 125, 41, 423),     # vertical "Aadhaar No. Issued: ..."
               (283, 160, 700, 342),   # name / DOB / gender block
               (288, 340, 600, 377),   # Mob: ...
               (858, 320, 975, 337),   # number under the small photo
               (362, 502, 740, 551)]   # big Aadhaar number
BACK_ERASE = [(18, 182, 41, 421),      # vertical "Details As On: ..."
              (44, 150, 676, 414),     # address block
              (320, 502, 695, 551)]    # big Aadhaar number
QR_BOX = (697, 177, 982, 462)          # QR modules area (white box around it stays)

PHOTO_BOX = (51, 185, 276, 465)        # main photo (x0, y0, x1, y1), x1/y1 exclusive
SMALL_PHOTO_BOX = (865, 195, 970, 320) # small black & white photo
QR_MODULES = (699, 179, 981, 461)      # where the QR modules go
SMALL_PHOTO_STRETCH = True             # small photo: fit whole photo (True) or crop to fit

# PDF text blocks are pasted at these scales; anchor = first line's left x and baseline
NAME_SCALE, NAME_ANCHOR = 4.32, (290.3, 194.9)
ADDR_SCALE, ADDR_ANCHOR = 4.58, (48.6, 179.8)
ADDR_MAX_RIGHT = 680                   # keep the address clear of the QR box

# typed texts (Arial): font size in px and the top-left of the ink (sub-pixel), tuned
# against the reference. Vertical texts read bottom -> top; their y is the ink bottom.
MOB = {"font": "regular", "size": 34.4, "x": 295.0, "y": 346.0}
BIG_NUM_FRONT = {"font": "bold", "size": 49.9, "x": 372.0, "y": 509.75}
BIG_NUM_BACK = {"font": "bold", "size": 49.9, "x": 329.0, "y": 509.75}
SMALL_NUM = {"font": "regular", "size": 13.9, "cx": 916.0, "y": 323.25}
V_ISSUED = {"font": "regular", "size": 19.9, "x": 24.0, "bottom": 418.0}
V_DETAILS = {"font": "regular", "size": 19.65, "x": 24.25, "bottom": 415.75}
SQUARE_CORNERS = True                  # fill the rounded corners -> square card
SS = 4                                 # text is drawn 4x larger then reduced (sub-pixel)


# ------------------------------------------------------------------ helpers
def _flatten(path):
    im = Image.open(path).convert("RGBA")
    return im


def _erase(rgba, boxes):
    """Remove text inside boxes and fill from the surrounding background (inpainting)."""
    rgb = np.array(rgba.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.int16)
    # background estimate: grey closing removes thin dark strokes
    bg = cv2.morphologyEx(gray.astype(np.uint8), cv2.MORPH_CLOSE, np.ones((17, 17), np.uint8)).astype(np.int16)
    mask = np.zeros(gray.shape, np.uint8)
    for x0, y0, x1, y1 in boxes:
        sub = (gray[y0:y1, x0:x1] < bg[y0:y1, x0:x1] - 18)
        mask[y0:y1, x0:x1] = sub.astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8))
    clean = cv2.inpaint(rgb, mask, 4, cv2.INPAINT_TELEA)
    out = np.array(rgba)
    out[..., :3] = clean
    return Image.fromarray(out, "RGBA")


def build_templates(force=False):
    """Make the blank (no personal data) templates from the reference cards."""
    if BLANK_FRONT.exists() and BLANK_BACK.exists() and not force:
        return
    front = _erase(_flatten(REF_FRONT), FRONT_ERASE)
    back = _erase(_flatten(REF_BACK), BACK_ERASE)
    b = np.array(back)
    x0, y0, x1, y1 = QR_BOX
    b[y0:y1, x0:x1, :3] = 255
    back = Image.fromarray(b, "RGBA")
    if SQUARE_CORNERS:
        front, back = _square_corners(front), _square_corners(back)
    back.save(BLANK_BACK)
    front.save(BLANK_FRONT)


def _square_corners(rgba):
    """The reference has transparent rounded corners: fill them with the card's own
    background so the card is a full rectangle (everything else looks unchanged)."""
    a = np.array(rgba)
    alpha = a[..., 3]
    flat = Image.new("RGB", rgba.size, (255, 255, 255))
    flat.paste(rgba, mask=rgba.split()[3])                  # same look as before
    rgb = np.array(flat)
    h, w = alpha.shape
    c = 40
    mask = np.zeros((h, w), np.uint8)
    for ys, xs in [(slice(0, c), slice(0, c)), (slice(0, c), slice(w - c, w)),
                   (slice(h - c, h), slice(0, c)), (slice(h - c, h), slice(w - c, w))]:
        mask[ys, xs] = (alpha[ys, xs] < 250).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8))       # include the soft edge pixels
    rgb = cv2.inpaint(rgb, mask, 6, cv2.INPAINT_TELEA)
    out = np.dstack([rgb, np.full((h, w), 255, np.uint8)])
    return Image.fromarray(out, "RGBA")


def ink_layer(text, spec, fx=0.0, fy=0.0, rotate=False):
    """'Ink on white' grey layer of text, drawn at SS x size then reduced, with the ink's
    top-left placed at the sub-pixel offset (fx, fy) inside the returned layer."""
    path = ARIAL_BOLD if spec["font"] == "bold" else ARIAL
    f = ImageFont.truetype(path, spec["size"] * SS)
    l, t, r, b = f.getbbox(text)
    im = Image.new("L", (r - l + 8, b - t + 8), 255)
    ImageDraw.Draw(im).text((4 - l, 4 - t), text, font=f, fill=0)
    a = np.array(im)
    ys, xs = np.nonzero(a < 250)
    a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    if rotate:
        a = np.rot90(a)
    ox, oy = int(round(fx * SS)), int(round(fy * SS))
    h = -(-(a.shape[0] + oy) // SS) * SS
    w = -(-(a.shape[1] + ox) // SS) * SS
    canvas = np.full((h, w), 255, np.uint8)
    canvas[oy:oy + a.shape[0], ox:ox + a.shape[1]] = a
    return cv2.resize(canvas, (w // SS, h // SS), interpolation=cv2.INTER_AREA)


def put_text(card, text, spec, x, y, rotate=False):
    """Draw text with its ink top-left at (x, y) (floats)."""
    ix, iy = int(np.floor(x)), int(np.floor(y))
    _multiply(card, ink_layer(text, spec, x - ix, y - iy, rotate), ix, iy)


def ink_size(text, spec, rotate=False):
    a = ink_layer(text, spec, 0, 0, rotate)
    ys, xs = np.nonzero(a < 250)
    return xs.max() + 1, ys.max() + 1


def _multiply(card, layer, x, y):
    """Paste a grey/RGB 'ink on white' layer with multiply blending at integer x, y."""
    h, w = layer.shape[:2]
    x, y = int(round(x)), int(round(y))
    H_, W_ = card.shape[:2]
    cx0, cy0, cx1, cy1 = max(0, x), max(0, y), min(W_, x + w), min(H_, y + h)
    if cx1 <= cx0 or cy1 <= cy0:
        return
    lay = layer[cy0 - y:cy1 - y, cx0 - x:cx1 - x].astype(np.float32) / 255
    if lay.ndim == 2:
        lay = lay[..., None]
    region = card[cy0:cy1, cx0:cx1, :3].astype(np.float32)
    card[cy0:cy1, cx0:cx1, :3] = np.clip(region * lay, 0, 255).astype(np.uint8)


def _curves():
    import json
    try:
        c = json.loads(PHOTO_CURVE.read_text())
        return [np.array(l) for l in c["photo"]], np.array(c["small"])
    except (OSError, ValueError, KeyError):
        return None, None


def _apply_curve(img, luts):
    a = np.array(img)
    if a.ndim == 2:
        return Image.fromarray(np.clip(luts[a], 0, 255).round().astype(np.uint8))
    out = np.stack([luts[c][a[..., c]] for c in range(3)], -1)
    return Image.fromarray(np.clip(out, 0, 255).round().astype(np.uint8))


def _paste(card, img_rgb, box):
    x0, y0, x1, y1 = box
    card[y0:y1, x0:x1, :3] = np.array(img_rgb.resize((x1 - x0, y1 - y0), Image.LANCZOS))


def _cover(img, w, h):
    """Crop img to the w:h aspect (centre) - no stretching."""
    iw, ih = img.size
    if iw / ih > w / h:
        nw = int(round(ih * w / h)); x = (iw - nw) // 2
        return img.crop((x, 0, x + nw, ih))
    nh = int(round(iw * h / w)); y = (ih - nh) // 2
    return img.crop((0, y, iw, y + nh))


# ------------------------------------------------------------------ reading the PDF
def _pix_to_pil(doc, xref):
    pix = pymupdf.Pixmap(doc, xref)
    if pix.n - pix.alpha != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _images_in(page, rect):
    out = []
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            r = r * page.rotation_matrix
            if rect.contains(r):
                out.append((img[0], r))
    return out


def _text_lines(page, rect):
    """Horizontal text lines inside rect: [(baseline, bbox), ...] merged per baseline."""
    lines = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            if abs(l["dir"][0] - 1) > 1e-3:
                continue
            bb = pymupdf.Rect(l["bbox"])
            if not (rect.contains(pymupdf.Point(bb.x0 + 0.1, (bb.y0 + bb.y1) / 2))
                    and bb.y0 >= rect.y0 - 0.5 and bb.y1 <= rect.y1 + 0.5):
                continue
            text = "".join(s["text"] for s in l["spans"]).strip()
            if not text:
                continue
            lines.append((l["spans"][0]["origin"][1], bb, text))
    lines.sort(key=lambda t: t[0])
    merged = []
    for base, bb, text in lines:
        if merged and abs(merged[-1][0] - base) < 1.5:
            merged[-1] = (merged[-1][0], merged[-1][1] | bb, merged[-1][2] + " " + text)
        else:
            merged.append((base, bb, text))
    return merged


def _render_block(page, lines, scale, max_x=None):
    """Render the given text lines as 'ink on white'.
    Returns (RGB image, pdf x of image left, pdf y of image top)."""
    union = pymupdf.Rect(lines[0][1])
    for _, bb, _ in lines:
        union |= bb
    x1 = union.x1 + 1 if max_x is None else min(union.x1 + 1, max_x)
    clip = pymupdf.Rect(union.x0 - 1, union.y0 - 2, x1, union.y1 + 2)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip, alpha=False)
    rgb = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    return rgb.copy(), clip.x0, clip.y0


def _find(text_all, pattern):
    m = re.search(pattern, text_all, re.I)
    return m.group(1).strip() if m else None


# ------------------------------------------------------------------ main
def generate(doc, layout_rects):
    """layout_rects: [front_rect, back_rect] of the e-Aadhaar cut-outs (PDF coords).
    Returns [("Front", PIL RGB), ("Back", PIL RGB)]."""
    build_templates()
    page = doc[0]
    front_r, back_r = layout_rects
    all_text = page.get_text()

    number = _find(all_text, r"(?<![\d ])(\d{4} \d{4} \d{4})(?! ?\d)")
    issued = _find(all_text, r"Aadhaar\s*no\.?\s*issued\s*:?\s*(\d{2}/\d{2}/\d{4})")
    details = _find(all_text, r"Details\s*as\s*on\s*:?\s*(\d{2}/\d{2}/\d{4})")
    mobile = _find(all_text, r"Mobile\s*:?\s*(\d{10})")

    # ---------- FRONT
    card = np.array(Image.open(BLANK_FRONT).convert("RGBA"))
    imgs = _images_in(page, front_r)
    photos = [(x, r) for x, r in imgs if 0.65 < r.width / max(r.height, 1e-6) < 0.95]
    photo_rect = None
    if photos:
        xref, photo_rect = max(photos, key=lambda t: t[1].get_area())
        photo = _pix_to_pil(doc, xref)
        luts, small_lut = _curves()
        pw, ph = PHOTO_BOX[2] - PHOTO_BOX[0], PHOTO_BOX[3] - PHOTO_BOX[1]
        big = _cover(photo, pw, ph).resize((pw, ph), Image.LANCZOS)
        if luts is not None:
            big = _apply_curve(big, luts)
        _paste(card, big, PHOTO_BOX)
        sw, sh = SMALL_PHOTO_BOX[2] - SMALL_PHOTO_BOX[0], SMALL_PHOTO_BOX[3] - SMALL_PHOTO_BOX[1]
        small = (photo.resize((sw, sh), Image.LANCZOS) if SMALL_PHOTO_STRETCH
                 else _cover(photo, sw, sh).resize((sw, sh), Image.LANCZOS)).convert("L")
        if small_lut is not None:
            small = _apply_curve(small, small_lut)
        _paste(card, small.convert("RGB"), SMALL_PHOTO_BOX)
    else:
        print("    ! Aadhaar: photo not found")

    # name block: text right of the photo, above the disclaimer box
    disclaimer_top = min([r.y0 for x, r in imgs if r.width > 0.4 * front_r.width
                          and r.y0 > (photo_rect.y0 if photo_rect else front_r.y0)] or [front_r.y1])
    left = (photo_rect.x1 if photo_rect else front_r.x0 + 0.3 * front_r.width) + 1
    name_lines = _text_lines(page, pymupdf.Rect(left, front_r.y0, front_r.x1, disclaimer_top))
    if name_lines:
        block, bx, by = _render_block(page, name_lines, NAME_SCALE)
        x0, base0 = name_lines[0][1].x0, name_lines[0][0]
        _multiply(card, block, NAME_ANCHOR[0] + (bx - x0) * NAME_SCALE,
                  NAME_ANCHOR[1] + (by - base0) * NAME_SCALE)

    if mobile:
        put_text(card, f"Mob: {mobile}", MOB, MOB["x"], MOB["y"])
    if issued:
        _vertical(card, f"Aadhaar No. Issued: {issued}", V_ISSUED)
    if number:
        put_text(card, number, BIG_NUM_FRONT, BIG_NUM_FRONT["x"], BIG_NUM_FRONT["y"])
        w_, _ = ink_size(number, SMALL_NUM)
        put_text(card, number, SMALL_NUM, SMALL_NUM["cx"] - w_ / 2, SMALL_NUM["y"])
    front = card

    # ---------- BACK
    card = np.array(Image.open(BLANK_BACK).convert("RGBA"))
    imgs = _images_in(page, back_r)
    qrs = [(x, r) for x, r in imgs if abs(r.width / max(r.height, 1e-6) - 1) < 0.05
           and r.width > 0.25 * back_r.width]
    qr_rect = None
    if qrs:
        xref, qr_rect = max(qrs, key=lambda t: t[1].get_area())
        _paste_qr(card, _pix_to_pil(doc, xref))
    else:
        print("    ! Aadhaar: QR code not found")

    # address: text between the vertical "Details" text and the QR code
    num_top = min([bb.y0 for base, bb, t in _text_lines(page, back_r) if re.search(r"\d{4} \d{4} \d{4}", t)]
                  or [back_r.y1])
    right = qr_rect.x0 - 0.5 if qr_rect else back_r.x0 + 0.6 * back_r.width
    addr_lines = [l for l in _text_lines(page, pymupdf.Rect(back_r.x0 + 0.04 * back_r.width, back_r.y0,
                                                           right, num_top))]
    # skip header lines (they are part of the template): keep lines below the header images
    head_imgs = [r for x, r in imgs if r.y1 < back_r.y0 + 0.3 * back_r.height]
    head_bottom = max([r.y1 for r in head_imgs] or [back_r.y0])
    addr_lines = [l for l in addr_lines if l[1].y0 >= head_bottom - 0.5]
    if addr_lines:
        scale = ADDR_SCALE
        width_px = min(max(bb.x1 for _, bb, _ in addr_lines), right) - addr_lines[0][1].x0
        if ADDR_ANCHOR[0] + width_px * scale > ADDR_MAX_RIGHT:          # very long address
            scale = (ADDR_MAX_RIGHT - ADDR_ANCHOR[0]) / width_px
        block, bx, by = _render_block(page, addr_lines, scale, max_x=right)
        x0, base0 = addr_lines[0][1].x0, addr_lines[0][0]
        _multiply(card, block, ADDR_ANCHOR[0] + (bx - x0) * scale, ADDR_ANCHOR[1] + (by - base0) * scale)
    if details:
        _vertical(card, f"Details As On: {details}", V_DETAILS)
    if number:
        put_text(card, number, BIG_NUM_BACK, BIG_NUM_BACK["x"], BIG_NUM_BACK["y"])
    back = card

    out = []
    for side, arr in (("Front", front), ("Back", back)):
        rgba = Image.fromarray(arr, "RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.split()[3])          # rounded corners -> white
        out.append((side, flat))
    found = {"number": bool(number), "issued": bool(issued), "details": bool(details),
             "mobile": bool(mobile), "photo": photo_rect is not None, "qr": qr_rect is not None,
             "name lines": len(name_lines), "address lines": len(addr_lines)}
    print("    Aadhaar PVC:", ", ".join(f"{k}={v}" for k, v in found.items()))
    return out


def _vertical(card, text, spec):
    _, h_ = ink_size(text, spec, rotate=True)
    put_text(card, text, spec, spec["x"], spec["bottom"] - h_ + 1, rotate=True)


def _paste_qr(card, qr):
    """Redraw the QR module grid crisply at the card size (keeps it scannable when small)."""
    g = np.array(qr.convert("L"))
    ys, xs = np.nonzero(g < 128)                           # crop to the modules
    g = g[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    dark = g < 128
    # module size from the top-left finder pattern: its top edge is 7 modules of ink
    row = dark[1, :]
    run = int(np.argmax(~row)) if not row.all() else row.size
    n = dark.shape[1] / (run / 7)
    n = 17 + 4 * max(1, int(round((n - 17) / 4)))          # QR sizes are 17 + 4 * version
    m = dark.shape[1] / n
    centres = ((np.arange(n) + 0.5) * m).astype(int)
    grid = dark[np.ix_(centres, centres)]
    x0, y0, x1, y1 = QR_MODULES
    ex = np.round(np.linspace(0, x1 - x0, n + 1)).astype(int)
    ey = np.round(np.linspace(0, y1 - y0, n + 1)).astype(int)
    q = np.full((y1 - y0, x1 - x0), 255, np.uint8)
    for i in range(n):
        for j in np.nonzero(grid[i])[0]:
            q[ey[i]:ey[i + 1], ex[j]:ex[j + 1]] = 0
    card[y0:y1, x0:x1, :3] = q[..., None]
