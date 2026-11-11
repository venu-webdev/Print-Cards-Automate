"""
Ayushman Bharat PM-JAY card (Andhra Pradesh: "Dr. NTR Vaidya Seva") -> PVC card front & back.

The card PDF from beneficiary.nha.gov.in / the Ayushman app (made with JasperReports) is one page,
stored rotated, with the card's front and back side by side: each side is a card-sized background
picture with the holder's details written on top as text. Each side is cut out exactly along its
picture and rendered sharp from the PDF at the same size as the other exports (1016 x 638 px =
85.6 x 54 mm @ 300 dpi). Nothing on the card is redrawn.

The card is shown 108 x 54 mm in the PDF (aspect 2.0), much wider than a PVC card (85.6 x 54 mm,
1.59). Its background picture is stored at about the PVC shape (1354 x 872) and only drawn stretched
by the PDF, so the shop's reference (references/ashuyman - this type for front.png) fills the card
edge to edge: each side is squeezed sideways to exactly the PVC shape (SQUEEZE = None) - the logos
become round again, no bands are added. The holder's photo is NOT squeezed: it is put back at its
real shape, centred on where it sits. Photo / card colours are tuned per card in the website's
"Adjust photo" editor.
"""
import re

import cv2
import numpy as np
import pymupdf
from PIL import Image

from abha_card import _to_card_shape

RENDER_DPI = 600
OUT_W, OUT_H = 1016, 638
# sideways squeeze of the 108 mm wide card: None = exactly to the PVC shape, edge to edge (the shop's
# choice); a number = that share of the width, the rest made up by extending the top / bottom edge
# (0.88 was the earlier in-between look, 1.0 keeps the PDF's shape)
SQUEEZE = None
PHOTO_BOX_PAD = 4         # px added around the photo for the "Adjust photo" area, so no photo edge is left out
ABHA_RE = re.compile(r"\b\d{2}-\d{4}-\d{4}-\d{4}\b")              # 14-digit ABHA number
# PM-JAY ID, e.g. MTQ5QNSUB: 9 capitals AND digits mixed (so an address word like ANNAMAYYA is not one)
PMJAY_RE = re.compile(r"\b(?=[A-Z0-9]*\d)(?=[A-Z0-9]*[A-Z])[A-Z0-9]{9}\b")


class PmjayError(ValueError):
    pass


def is_pmjay(doc):
    """Most labels on this card are drawn as shapes, not text: recognise it by what IS text -
    the 'Card Generated on' line plus an ABHA number and a 9-character PM-JAY ID."""
    if doc.page_count != 1:
        return False
    text = doc[0].get_text()
    return "Card Generated on" in text and bool(ABHA_RE.search(text)) and bool(PMJAY_RE.search(text))


def card_rects(page):
    """Front and back = the two card-sized background pictures (as shown, i.e. after the page's
    rotation), left one first."""
    rects = []
    for img in page.get_images(full=True):
        for r in page.get_image_rects(img[0]):
            r = r * page.rotation_matrix
            if r.width > 0.3 * page.rect.width and 1.3 < r.width / r.height < 2.3:   # shown 108 x 54 mm (2.0)
                rects.append(r)
    rects = sorted({(round(r.x0, 1), round(r.y0, 1)): r for r in rects}.values(), key=lambda r: (r.x0, r.y0))
    if len(rects) < 2:
        raise PmjayError("PM-JAY card layout not recognised (front / back not found)")
    return rects[:2]


def card_images(doc):
    """-> [("Front", img), ("Back", img)], each 1016 x 638."""
    if not is_pmjay(doc):
        raise PmjayError("this is not an Ayushman Bharat PM-JAY card PDF")
    page = doc[0]
    ph = photo_rect(page)
    face = photo(doc) if ph else None                     # real shape, before the photo is hidden below
    if ph:                                                # background: a copy of the PDF without the photo
        bg_doc = pymupdf.open(stream=doc.tobytes(), filetype="pdf")
        hide = pymupdf.Pixmap(pymupdf.csRGB, 1, 1, bytes(4), True)
        bg_doc[0].replace_image(ph[1], pixmap=hide)
        bg_page = bg_doc[0]
    else:
        bg_page = page
    out = []
    for side, clip in zip(("Front", "Back"), card_rects(page)):
        pix = bg_page.get_pixmap(dpi=RENDER_DPI, clip=clip, alpha=False)
        a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, 3).copy()
        h, w = a.shape[:2]
        sw = round(h * OUT_W / OUT_H) if SQUEEZE is None else round(w * SQUEEZE)
        sq = sw / w
        a = cv2.resize(a, (sw, h), interpolation=cv2.INTER_AREA)
        kept = face is not None and clip.contains(ph[0])
        if kept:                                          # photo at its real shape, centred on its spot
            r, k = ph[0], pix.width / clip.width          # k = px per pt
            fh = round(r.height * k)
            fw = round(fh * face.width / face.height)
            im = np.asarray(face.resize((fw, fh), Image.LANCZOS))
            cx, y0 = ((r.x0 + r.x1) / 2 - clip.x0) * k * sq, round((r.y0 - clip.y0) * k)
            x0 = max(0, round(cx - fw / 2))
            y1, x1 = min(h, y0 + fh), min(sw, x0 + fw)
            a[y0:y1, x0:x1] = im[:y1 - y0, :x1 - x0]
        shaped = _to_card_shape(a)
        print(f"    {side}: {clip.width / 72 * 25.4:.1f} x {clip.height / 72 * 25.4:.1f} mm on the PDF -> PVC card "
              f"(squeezed to {sq:.0%}{', photo at its real shape' if kept else ''})")
        card = Image.fromarray(shaped).resize((OUT_W, OUT_H), Image.LANCZOS)
        if kept:                                          # exact photo spot for the "Adjust photo" editor
            H2, W2 = shaped.shape[:2]
            top, left = (H2 - h) // 2, (W2 - sw) // 2     # what _to_card_shape added (extra // 2 each side)
            fx, fy, p = OUT_W / W2, OUT_H / H2, PHOTO_BOX_PAD
            card.info["cps_photo_box"] = [max(0, round((x0 + left) * fx) - p), max(0, round((y0 + top) * fy) - p),
                                          min(OUT_W, round((x1 + left) * fx) + p), min(OUT_H, round((y1 + top) * fy) + p)]
        out.append((side, card))
    return out


def photo(doc):
    """The holder's photo at its REAL shape. The picture is stored sideways and the PDF shows it
    stretched wider than it is (a 3:4 photo drawn at about 0.91); it is rendered as shown (so it
    is the right way up) and then brought back to the picture's own proportions."""
    page = doc[0]
    ph = photo_rect(page)
    if ph is None:
        return None
    r, xref, (iw, ih) = ph
    pix = page.get_pixmap(dpi=RENDER_DPI, clip=r, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    shown_portrait = r.height >= r.width
    aspect = min(iw, ih) / max(iw, ih) if shown_portrait else max(iw, ih) / min(iw, ih)   # w / h as it should be
    return img.resize((max(1, round(img.height * aspect)), img.height), Image.LANCZOS)


def photo_rect(page):
    """The holder's photo on the front -> (rect as shown, after rotation; xref; stored size (w, h)) or None."""
    front = card_rects(page)[0]
    best = None
    for img in page.get_images(full=True):
        if img[2] < 60 or img[3] < 60:
            continue
        for r in page.get_image_rects(img[0]):
            r = r * page.rotation_matrix
            if front.contains(r) and 0.6 <= r.width / r.height <= 1.05 and r.get_area() < 0.3 * front.get_area():
                if best is None or r.get_area() > best[0].get_area():
                    best = (r, img[0], (img[2], img[3]))
    return best


def details(doc):
    """Holder name + PM-JAY ID for the exports list / file names. The name is the biggest text on
    the card (7 pt; the other details are 5 pt)."""
    page = doc[0]
    spans = [s for b in page.get_text("dict")["blocks"] for l in b.get("lines", []) for s in l["spans"]
             if s["text"].strip() and "Card Generated" not in s["text"]]
    name = max(spans, key=lambda s: s["size"])["text"].strip() if spans else None
    m = PMJAY_RE.search(page.get_text())
    return name, (m.group(0) if m else None)
