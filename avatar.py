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


def photo_box(doc, card_type, card_image):
    """Where the holder's photo sits on a finished card image -> [x0, y0, x1, y1] (px) or None.
    Used by the "Adjust" editor so brightness / contrast can be changed on the photo only.
    1) Exact: the card maker stored where it put the photo (card_image.info["cps_photo_box"]):
       Aadhaar PVC, PAN, DL, ABHA, Ayushman (that one + 4 px on purpose), Voter ID.
    2) Only if a maker could not tell: the PDF's own photo is searched on the card."""
    import cv2
    if card_type in ("rc", "aadhaar_lam") or card_image is None:
        return None
    if card_image.info.get("cps_photo_box"):
        return [int(v) for v in card_image.info["cps_photo_box"]]
    card = np.asarray(card_image.convert("L"))
    H, W = card.shape
    if card_type == "voter":
        import make_cards
        mask = make_cards.find_main_photo(np.asarray(card_image.convert("RGB")))
        if mask is None:
            return None
        ys, xs = np.nonzero(mask)
        return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1]
    try:
        if card_type == "pmjay":
            import pmjay_card
            photo = pmjay_card.photo(doc)
        else:
            photo = _from_pdf(doc)
    except Exception:
        photo = None
    if photo is None:
        return None
    t0 = np.asarray(photo.convert("L"))
    # 1) rough: half size, photo height as part of the card height, and a little narrower / wider than
    #    the stored picture (PDFs often draw the photo slightly squeezed, e.g. PAN: square picture shown
    #    184 x 192 px - keeping the picture's own shape made the area ~8 px too wide)
    f = 0.5
    g = cv2.resize(card, (round(W * f), round(H * f)), interpolation=cv2.INTER_AREA)
    best = None
    for frac in np.linspace(0.18, 0.8, 32):
        for asp in (0.88, 0.94, 1.0, 1.06):
            th = round(g.shape[0] * frac)
            tw = round(th * t0.shape[1] / t0.shape[0] * asp)
            if th < 12 or tw < 12 or tw >= g.shape[1] or th >= g.shape[0]:
                continue
            res = cv2.matchTemplate(g, cv2.resize(t0, (tw, th), interpolation=cv2.INTER_AREA), cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(res)
            if best is None or mx > best[0]:
                best = (mx, loc[0] / f, loc[1] / f, tw / f, th / f)
    if best is None or best[0] < 0.5:
        return None
    # 2) exact: full size, width and height tuned separately around the rough answer
    _, x, y, w, h = best
    m = 14
    x0, y0 = max(0, int(x) - m), max(0, int(y) - m)
    win = card[y0:min(H, int(y + h) + m), x0:min(W, int(x + w) + m)]
    fine = None
    for dh in range(-8, 9, 2):
        for dw in range(-12, 13, 2):
            tw, th = round(w) + dw, round(h) + dh
            if tw < 12 or th < 12 or tw > win.shape[1] or th > win.shape[0]:
                continue
            res = cv2.matchTemplate(win, cv2.resize(t0, (tw, th), interpolation=cv2.INTER_AREA), cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(res)
            if fine is None or mx > fine[0]:
                fine = (mx, x0 + loc[0], y0 + loc[1], tw, th)
    _, x, y, w, h = fine if fine and fine[0] >= best[0] - 0.02 else (0, round(x), round(y), round(w), round(h))
    return [int(x), int(y), int(x + w), int(y + h)]


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
        elif card_type == "pmjay":                   # its photo is stored sideways in the PDF
            import pmjay_card
            img = pmjay_card.photo(doc)
        else:
            img = _from_pdf(doc)
    except Exception as e:
        print(f"    (no holder photo: {e})")
    if img is None:
        return None
    buf = io.BytesIO()
    _square_face(img).save(buf, "JPEG", quality=88)
    return buf.getvalue()
