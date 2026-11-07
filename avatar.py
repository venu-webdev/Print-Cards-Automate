"""
Holder photo for the exports list (an avatar so people are recognised at a glance) and a
public job reference that is NOT the internal job id.
"""
import io
import secrets

import numpy as np
import pymupdf
from PIL import Image

AVATAR_PX = 160
_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"     # no 0/O, 1/I/L - easy to read out


def new_ref():
    """e.g. JOB-7K4Q-2XM9 : random, unguessable, unrelated to the internal id."""
    s = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"JOB-{s[:4]}-{s[4:]}"


def _pil(doc, xref):
    pix = pymupdf.Pixmap(doc, xref)
    if pix.n - pix.alpha != 3:
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if pix.alpha:
        pix = pymupdf.Pixmap(pix, 0)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def _looks_like_photo(img):
    a = np.asarray(img.convert("RGB").resize((64, 64)), np.int16)
    g = a.mean(2)
    if ((g < 40) | (g > 215)).mean() > 0.7:          # QR codes / logos are mostly black & white
        return False
    sat = (a.max(2) - a.min(2)).mean()
    return sat > 12 and g.std() > 18               # colour photo with real tonal range


def _from_pdf(doc):
    best = None
    for page in doc:
        for img in page.get_images(full=True):
            xref, w, h = img[0], img[2], img[3]
            if w < 60 or h < 60 or not (0.6 <= w / h <= 1.05):
                continue
            rects = page.get_image_rects(xref)
            if not rects or rects[0].get_area() > 0.2 * page.rect.get_area():
                continue
            try:
                im = _pil(doc, xref)
            except Exception:
                continue
            if _looks_like_photo(im) and (best is None or w * h > best.width * best.height):
                best = im
        if best is not None:
            break
    return best


def _square_face(img):
    """Square crop around where the face sits on an ID photo (upper middle)."""
    w, h = img.size
    side = min(w, int(h * 0.82))
    x = (w - side) // 2
    y = max(0, min(h - side, int(h * 0.04)))
    return img.crop((x, y, x + side, y + side)).resize((AVATAR_PX, AVATAR_PX), Image.LANCZOS)


def holder_photo(doc, card_type, front_image=None):
    """-> JPEG bytes of the holder's face, or None (e.g. vehicle RC)."""
    if card_type == "rc":
        return None
    img = None
    try:
        if card_type == "voter" and front_image is not None:
            import make_cards
            arr = np.asarray(front_image.convert("RGB"))
            mask = make_cards.find_main_photo(arr)
            if mask is not None:
                ys, xs = np.nonzero(mask)
                img = Image.fromarray(arr[ys.min():ys.max() + 1, xs.min():xs.max() + 1])
        else:
            img = _from_pdf(doc)
    except Exception as e:
        print(f"    (no holder photo: {e})")
    if img is None:
        return None
    buf = io.BytesIO()
    _square_face(img).save(buf, "JPEG", quality=88)
    return buf.getvalue()
