"""
Print-quality (vector) PDF version of the laminated Aadhaar sheets.

Same action steps as aadhaar_laminate.build(), but applied to rectangles of the original PDF
page instead of pixels, so all text, lines and QR codes stay vector at any printer resolution.
Every fragment = (dst rect in canvas px @300 dpi, src rect in PDF points).
"""
import datetime
import io
import re

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFont

import aadhaar_laminate as L

PX2PT = 72 / 300


class _Frag:
    __slots__ = ("d", "s")

    def __init__(self, d, s):
        self.d, self.s = d, s

    def sub(self, x0, y0, x1, y1):
        (a0, b0, a1, b1), (p0, q0, p1, q1) = self.d, self.s
        kx, ky = (p1 - p0) / (a1 - a0), (q1 - q0) / (b1 - b0)
        return _Frag((x0, y0, x1, y1), (p0 + (x0 - a0) * kx, q0 + (y0 - b0) * ky,
                                        p0 + (x1 - a0) * kx, q0 + (y1 - b0) * ky))


def _split(frags, sel):
    """-> (outside, inside) fragments for the selection (top, left, bottom, right)."""
    t, l, b, r = sel
    out, ins = [], []
    for f in frags:
        x0, y0, x1, y1 = f.d
        ix0, iy0, ix1, iy1 = max(x0, l), max(y0, t), min(x1, r), min(y1, b)
        if ix0 >= ix1 or iy0 >= iy1:
            out.append(f)
            continue
        for rr in ((x0, y0, x1, iy0), (x0, iy1, x1, y1), (x0, iy0, ix0, iy1), (ix1, iy0, x1, iy1)):
            if rr[2] - rr[0] > 1e-6 and rr[3] - rr[1] > 1e-6:
                out.append(f.sub(*rr))
        ins.append(f.sub(ix0, iy0, ix1, iy1))
    return out, ins


def _xform(frags, sel, dx=0.0, dy=0.0, sx=100.0, sy=100.0):
    t, l, b, r = sel
    cx, cy, fx, fy = (l + r) / 2, (t + b) / 2, sx / 100, sy / 100
    mv = lambda v, c, f, d: c + (v - c) * f + d
    out, ins = _split(frags, sel)
    for f in ins:
        x0, y0, x1, y1 = f.d
        out.append(_Frag((mv(x0, cx, fx, dx), mv(y0, cy, fy, dy), mv(x1, cx, fx, dx), mv(y1, cy, fy, dy)), f.s))
    return out, (mv(t, cy, fy, dy), mv(l, cx, fx, dx), mv(b, cy, fy, dy), mv(r, cx, fx, dx))


def _shift(items, dy):
    return [(_Frag((f.d[0], f.d[1] + dy, f.d[2], f.d[3] + dy), f.s) if isinstance(f, _Frag) else f) for f in items]


_NUM = r"-?(?:\d+\.?\d*|\.\d+)"
_GRAY_OP = re.compile(rf"(?<![\w.\-/])({_NUM})(\s+)(g|G)(?![\w])")
_RGB_OP = re.compile(rf"(?<![\w.\-/])({_NUM})(\s+)({_NUM})(\s+)({_NUM})(\s+)(rg|RG)(?![\w])")


def _curve_colours(stream, lut):
    """Apply the Curves LUT to the vector fill / stroke colours (g G rg RG) of a content stream.
    Black text (0 0 0) stays exactly black, so text and QR stay sharp vector."""
    f = lambda v: f"{float(np.interp(min(max(float(v), 0), 1) * 255, np.arange(256), lut)) / 255:.4f}"
    s = _GRAY_OP.sub(lambda m: f"{f(m[1])}{m[2]}{m[3]}", stream)
    return _RGB_OP.sub(lambda m: f"{f(m[1])}{m[2]}{f(m[3])}{m[4]}{f(m[5])}{m[6]}{m[7]}", s)


def _set_image(doc, xref, lut):
    pix = pymupdf.Pixmap(doc, xref)
    if pix.n - pix.alpha != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
    buf = io.BytesIO()
    Image.fromarray(np.clip(lut[a], 0, 255).round().astype(np.uint8)).save(buf, "PNG")
    doc[0].replace_image(xref, stream=buf.getvalue())


def _enhanced_copy(doc):
    """Copy of the PDF with the action's colour steps done inside the PDF (everything stays in place):
      1. holder photo: brighter (Curves 113->143 + Levels gamma 1.24)
      2. inverse of the photo selection = the rest of the canvas: darker (Curves 159->105) -
         every picture (card artwork, logos, QR) and every vector line / shape colour.
    The thin strip of the photo selection around the photo is put back by _photo_overlays."""
    page = doc[0]
    cx = ((L.PHOTO_RECT[1] + L.PHOTO_RECT[3]) / 2 - L.BASE_TX) / L.BASE_SX
    cy = ((L.PHOTO_RECT[0] + L.PHOTO_RECT[2]) / 2 - L.BASE_TY) / L.BASE_SY
    target = None
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            if r.contains(pymupdf.Point(cx, cy)) and r.width < page.rect.width / 3:
                target = img[0]
    new = pymupdf.open(stream=doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_NONE), filetype="pdf")
    lut_photo = 255 * (L._spline_lut(L.PHOTO_CURVE) / 255) ** (1 / L.PHOTO_GAMMA)
    lut_rest = L._spline_lut(L.REST_CURVE)
    for xref in {img[0] for img in new[0].get_images(full=True)}:
        _set_image(new, xref, lut_photo if xref == target else lut_rest)
    forms = [x for x in range(1, new.xref_length())
             if "/Subtype/Form" in new.xref_object(x, compressed=True).replace(" ", "")]
    for xref in set(new[0].get_contents()) | set(forms):
        s = new.xref_stream(xref)
        if s:
            new.update_stream(xref, _curve_colours(s.decode("latin-1"), lut_rest).encode("latin-1"))
    if target is None:
        print("    ! Aadhaar laminated: photo not found - only the background was darkened")
    return new


def _photo_overlays(doc, frags):
    """Enhanced face (Curves + Levels from the action) at 600 dpi."""
    out = []
    lut = 255 * (L._spline_lut(L.PHOTO_CURVE) / 255) ** (1 / L.PHOTO_GAMMA)
    for f in _split(frags, L.PHOTO_RECT)[1]:
        clip = pymupdf.Rect(*f.s)
        zoom = max(1.0, (f.d[2] - f.d[0]) * 2 / max(clip.width, 1e-6))
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False, annots=False)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3)
        out.append((f.d, Image.fromarray(np.clip(lut[a], 0, 255).round().astype(np.uint8))))
    return out


def _mark_overlay(when):
    """Green tick 'Signature valid' block + timestamp, made like Photoshop does it: the date text
    is drawn at the block's own resolution (300 dpi), so it has the same softness as the rest."""
    mk = Image.open(L.MARK_IMG).convert("RGBA")
    flat = Image.new("RGBA", mk.size, (255, 255, 255, 255))     # solid block: no hole, no halo
    flat.alpha_composite(mk)
    text = when.strftime("%Y.%m.%d %H:%M:%S")
    sx, sy, cx, base_y = L.DATE_XFORM
    ss = 8                                                      # anti-aliasing only
    font = ImageFont.truetype(L.DATE_FONT, round(L.DATE_PT * ss))
    l, t, r, b = font.getbbox(text)
    asc = font.getmetrics()[0]
    big = Image.new("L", (r - l + 4 * ss, asc + 8 * ss), 0)
    ImageDraw.Draw(big).text((2 * ss - l, 0), text, font=font, fill=255)
    w, h = max(1, round(big.width * sx / ss)), max(1, round(big.height * sy / ss))
    small = big.resize((w, h), Image.BOX)                       # straight down to 300 dpi
    ink = Image.new("RGBA", small.size, (0, 0, 0, 255))
    ink.putalpha(small)
    flat.alpha_composite(ink, (round(cx - L.MARK_POS[0] - w / 2), round(base_y - L.MARK_POS[1] - asc * sy / ss)))
    x0, y0 = L.MARK_POS
    return ((x0, y0, x0 + mk.width, y0 + mk.height), flat.convert("RGB"))


def build_pdf(doc, sheet="full", when=None, enhance=True):
    """One A4 sheet as a print-quality PDF page. Returns a pymupdf Document (1 page)."""
    if sheet not in L.SHEETS:
        raise ValueError(f"unknown sheet {sheet!r}")
    src = doc
    if enhance:
        doc = _enhanced_copy(doc)
    W, H = doc[0].rect.width, doc[0].rect.height
    frags = [_Frag((L.BASE_TX, L.BASE_TY, L.BASE_TX + W * L.BASE_SX, L.BASE_TY + H * L.BASE_SY), (0, 0, W, H))]
    frags = _split(frags, (-1e9, -1e9, 0, 1e9))[0]                     # only what is on the canvas
    # the whole photo selection (photo + the thin strip around it) with the photo curve, as in the action
    photos = _photo_overlays(src, frags) if enhance else []
    if sheet in ("both", "small"):
        frags, _ = _xform(frags, (2294, 556, 2506, 1220), dx=-24.21)
    frags, _ = _xform(frags, (2145, 1285, 2414, 1314), dx=11.4)
    frags, sel = _xform(frags, (2105, 1288, 2432, 1871), dx=5, dy=-2)
    frags, _ = _xform(frags, sel, dx=9.5, dy=7, sx=96.75, sy=101.2)
    frags, _ = _xform(frags, (2636, 152, 2961, 2327), dy=-12)
    frags, _ = _split(frags, L.CUT_LINE)

    if sheet in ("both", "small"):
        strip = _split(frags, L.STRIP)[1]
        if sheet == "both":
            frags = frags + _shift(strip, L.STRIP_COPY_DY)
            photos = photos + [((d[0], d[1] + L.STRIP_COPY_DY, d[2], d[3] + L.STRIP_COPY_DY), im) for d, im in photos]
        else:
            dy = L.STRIP_COPY_DY + L.SMALL_DY
            frags = _shift(strip, dy)
            photos = [((d[0], d[1] + dy, d[2], d[3] + dy), im) for d, im in photos]
    overlays = photos + ([_mark_overlay(when or datetime.datetime.now())] if sheet != "small" else [])

    out = pymupdf.open()
    pg = out.new_page(width=L.A4[0] * PX2PT, height=L.A4[1] * PX2PT)
    for f in frags:
        x0, y0, x1, y1 = f.d
        if x1 - x0 < 0.5 or y1 - y0 < 0.5:
            continue
        pg.show_pdf_page(pymupdf.Rect(x0 * PX2PT, y0 * PX2PT, x1 * PX2PT, y1 * PX2PT), doc, 0,
                         clip=pymupdf.Rect(*f.s), keep_proportion=False)
    for (x0, y0, x1, y1), im in overlays:
        buf = io.BytesIO()
        im.save(buf, "PNG")
        pg.insert_image(pymupdf.Rect(x0 * PX2PT, y0 * PX2PT, x1 * PX2PT, y1 * PX2PT), stream=buf.getvalue())
    return out


def build_all(doc, when=None, enhance=True):
    """{sheet: 1-page Document} for Full, Both, Small (same timestamp on all)."""
    when = when or datetime.datetime.now()
    return {s: build_pdf(doc, s, when, enhance) for s in L.SHEETS}


# ---------------------------------------------------------------- face adjustments (website "Adjust photo")
# In a finished sheets PDF the holder's face is a small portrait picture (the e-Aadhaar photo, and on
# newer sheets also the photo-selection overlay), drawn wherever the card appears on that sheet.
# Adjusting = replacing just those pictures; text, QR and artwork are not touched.
def _is_face(info):
    w, h = info["width"], info["height"]
    b = info["bbox"]
    return info.get("xref") and 0.65 < w / max(h, 1) < 0.95 and (b[2] - b[0]) * 300 / 72 < 320


def face_boxes(page):
    """[(xref, bbox in points)] of the face pictures on one sheet page (visible ones only)."""
    seen, out = set(), []
    for inf in page.get_image_info(xrefs=True):
        r = pymupdf.Rect(inf["bbox"])
        if _is_face(inf) and r.intersects(page.rect):
            key = (inf["xref"], tuple(round(v) for v in r))
            if key not in seen:
                seen.add(key)
                out.append((inf["xref"], r))
    return out


def card_clip(page):
    """The front card around the face (for the Adjust preview), or the whole page if no face."""
    boxes = sorted((r for _, r in face_boxes(page)), key=lambda r: (r.y0, r.x0))
    if not boxes:
        return page.rect, None
    face = boxes[0]
    for r in boxes[1:]:                                  # photo + its overlay = one face
        if r.intersects(face):
            face |= r
    k = 72 / 300
    clip = pymupdf.Rect(face.x0 - 110 * k, face.y0 - 160 * k, face.x0 + 950 * k, face.y0 + 520 * k) & page.rect
    return clip, face


def adjust_faces(pdf_bytes, fn):
    """fn(PIL RGB) -> PIL RGB applied to every face picture of every sheet. Returns new PDF bytes."""
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    done = set()
    # every portrait picture object in the file = a face (the e-Aadhaar photo, its copies and the overlay).
    # Found by object, not via get_image_info: identical copies of the photo exist and that call can
    # name a different copy than the one actually drawn.
    for xref in range(1, doc.xref_length()):
        if doc.xref_get_key(xref, "Subtype")[1] != "/Image":
            continue
        try:
            w, h = int(doc.xref_get_key(xref, "Width")[1]), int(doc.xref_get_key(xref, "Height")[1])
        except ValueError:
            continue
        if not (0.65 < w / max(h, 1) < 0.95 and max(w, h) <= 1200):
            continue
        done.add(xref)
        pix = pymupdf.Pixmap(doc, xref)
        if pix.n - pix.alpha != 3:
            pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
        if pix.alpha:
            pix = pymupdf.Pixmap(pix, 0)
        img = fn(Image.frombytes("RGB", (pix.width, pix.height), pix.samples)).convert("RGB")
        # write the pixels straight into the image object: the face sits inside the placed card
        # artwork (a form XObject), where Page.replace_image does not reach
        doc.update_stream(xref, img.tobytes(), compress=True)
        for key, val in (("ColorSpace", "/DeviceRGB"), ("BitsPerComponent", "8"), ("Filter", "/FlateDecode"),
                         ("DecodeParms", "null"), ("ColorTransform", "null"), ("Decode", "null")):
            doc.xref_set_key(xref, key, val)
    if not done:
        raise ValueError("The photo was not found on these sheets")
    return doc.tobytes(deflate=True, garbage=3)


def to_image(sheet_doc, dpi=300):
    pix = sheet_doc[0].get_pixmap(dpi=dpi, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    w, h = round(L.A4[0] * dpi / 300), round(L.A4[1] * dpi / 300)
    return img.crop((0, 0, w, h)) if img.size != (w, h) else img
