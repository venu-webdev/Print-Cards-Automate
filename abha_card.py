"""
ABHA (Ayushman Bharat Health Account) card -> PVC card front & back.

The ABHA card PDF from the ABHA app / abha.abdm.gov.in ("ABHA-Card-xx-xxxx-xxxx-xxxx.pdf") is one
page with the card's front (top) and back (bottom), each a full-size card image with the details on
top of it and a thin grey frame line around it. Each side is cut out just inside that frame line and
rendered sharp from the PDF at the same size as the other exports (1016 x 638 px = 85.6 x 54 mm
@ 300 dpi). Nothing on the card is redrawn.

The ABHA card is a little wider than a PVC card (aspect 1.63 vs 1.59). Cutting the sides would bring
the back's long text lines to 0.5 mm from the edge, so instead the full width is kept and the card is
made a few pixels taller: the blue header continues up and the white body continues down.
"""
import re

import cv2
import numpy as np
import pymupdf
from PIL import Image

RENDER_DPI = 600
OUT_W, OUT_H = 1016, 638
ASPECT = OUT_W / OUT_H
FRAME_INSET_PT = 0.9          # grey frame line is 0.6 pt wide (measured) -> cut just inside it
NUM_RE = re.compile(r"\b\d{2}-\d{4}-\d{4}-\d{4}\b")


class AbhaError(ValueError):
    pass


def is_abha(doc):
    text = doc[0].get_text()
    return bool(NUM_RE.search(text)) and "@abdm" in text


def card_rects(page):
    """Front and back = the two card-wide background images, top one first."""
    rects = []
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            r = r * page.rotation_matrix
            if r.width > 0.8 * page.rect.width and r.height > 0.3 * r.width:
                rects.append(r)
    rects = sorted({(round(r.x0, 1), round(r.y0, 1)): r for r in rects}.values(), key=lambda r: r.y0)
    if len(rects) < 2:
        raise AbhaError("ABHA card layout not recognised (front / back not found)")
    return rects[:2]


def _to_card_shape(a):
    """Make the image the PVC card shape without cutting or stretching anything: a wider image gets
    its top and bottom edge rows continued (blue header up, white body down); a taller one its sides."""
    h, w = a.shape[:2]
    if w / h > ASPECT:
        extra = round(w / ASPECT) - h
        top = extra // 2
        return cv2.copyMakeBorder(a, top, extra - top, 0, 0, cv2.BORDER_REPLICATE)
    extra = round(h * ASPECT) - w
    left = extra // 2
    return cv2.copyMakeBorder(a, 0, 0, left, extra - left, cv2.BORDER_REPLICATE)


def card_images(doc):
    """-> [("Front", img), ("Back", img)], each 1016 x 638."""
    if not is_abha(doc):
        raise AbhaError("this is not an ABHA card PDF (download it from the ABHA app or abha.abdm.gov.in)")
    page = doc[0]
    out = []
    for side, r in zip(("Front", "Back"), card_rects(page)):
        k = FRAME_INSET_PT
        clip = pymupdf.Rect(r.x0 + k, r.y0 + k, r.x1 - k, r.y1 - k)            # just inside the grey frame
        pix = page.get_pixmap(dpi=RENDER_DPI, clip=clip, alpha=False)
        a = _to_card_shape(np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy())
        print(f"    {side}: {clip.width / 72 * 25.4:.1f} x {clip.height / 72 * 25.4:.1f} mm on the PDF -> PVC card")
        out.append((side, Image.fromarray(a).resize((OUT_W, OUT_H), Image.LANCZOS)))
    return out


def details(doc):
    """Holder name + ABHA number for the exports list / file names."""
    lines = [t.strip() for t in doc[0].get_text().splitlines() if t.strip()]
    m = NUM_RE.search(" ".join(lines))
    name = next((l for l in lines if re.fullmatch(r"[A-Za-z][A-Za-z .']{1,60}", l)), None)
    return name, (m.group(0) if m else None)
