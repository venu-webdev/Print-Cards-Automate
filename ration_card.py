"""
AP Rice Card (ration card) - front & back template, 1016 x 638 px @ 300 dpi (like the exports).

Background = the shop's export design with the person's data removed (templates/ration_*_bg.png).
Data is drawn back in at the positions / sizes measured from the export. Telugu is shaped by
PyMuPDF (HarfBuzz) with Nirmala UI, so conjuncts render correctly.

    import ration_card
    front, back = ration_card.render(data)      # PIL images
Data (dict) - see SAMPLE below for every field.
"""
import io
import json
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image

ROOT = Path(__file__).resolve().parent
TPL = ROOT / "templates"
FRONT_BG, BACK_BG = TPL / "ration_front_bg.png", TPL / "ration_back_bg.png"
SPEC_FILE = TPL / "ration_card_spec.json"
REF_FRONT, REF_BACK = ROOT / "export" / "ration_card_0001_front.jpg", ROOT / "export" / "ration_card_0000_back.jpg"
FONTS = str(TPL / "fonts") if (TPL / "fonts" / "Nirmala.ttf").exists() else "C:/Windows/Fonts"  # Nirmala UI
# The card's text is Nirmala UI drawn heavier (Photoshop faux-bold). Stroke thickening at 4x,
# measured against the export: Telugu 5 px, Latin / digits 3 px.
THICKEN_TE, THICKEN_LATIN = 5, 3.5
# Telugu values: Noto Sans Telugu (variable); TE_WGHT = its weight axis for normal values
TE_FONT = str(TPL / "fonts" / "NotoSansTelugu.ttf")
TE_WGHT = 540
# front values: Latin set wider than Nirmala's own shape (measured on the real card), bold RC number narrower
FRONT_HX, FRONT_HX_BOLD = 1.15, 0.89
BASE_EM = 1.3                               # baseline position in the text image (x font size)
SIZE = (1016, 638)
QR_URL = "https://aepos.ap.gov.in/Qcodesearch.jsp?rcno={rc_no}"

# Card fields. The values on the reference export (a real customer's card) are only needed to
# re-measure the template (build_template) and are kept out of git in templates/ration_sample.local.json.
SAMPLE = {k: "" for k in ("card_type", "rc_no", "head_name", "dob_age", "gender_en", "gender_te", "members_count",
                          "shop_id", "shop_address", "permanent_address", "tahsildar_office")}
SAMPLE.update({"photo": None,          # path or PIL image (portrait)
               "members": []})         # back table: {"name", "dob_age": "2001-01-01/25", "gender", "relation"}
SAMPLE_FILE = TPL / "ration_sample.local.json"

# field: (side, ink box measured in the export (x0, y0, x1, y1), bold)
FIELDS = {
    "card_type":         ("front", (350, 262, 488, 282), False),
    "rc_no":             ("front", (350, 298, 546, 325), True),
    "head_name":         ("front", (350, 344, 580, 376), False),
    "dob_age":           ("front", (351, 390, 621, 414), False),
    "gender_en":         ("front", (352, 431, 434, 455), False),
    "gender_te":         ("front", (479, 432, 530, 455), False),
    "members_count":     ("front", (352, 473, 360, 494), False),
    "shop_id":           ("front", (352, 511, 476, 533), False),
    "shop_address":      ("front", (350, 550, 473, 585), False),
    "permanent_address": ("back", (306, 444, 734, 468), False),
    "tahsildar_office":  ("back", (357, 526, 457, 550), False),
}
PHOTO_BOX = (745, 242, 939, 446)
QR_BOX = (792, 458, 909, 575)
VALUE_AREA = (335, 250, 765, 590)
SEAL_ROWS = (243, 588)                     # seal redrawn only between the title pill and the bottom band
BAND_TOP = 588
SEAL_CENTER = (491.5, 398.5)             # faint AP seal watermark behind the values          # front: where values sit over the watermark
TABLE = {"cols": [66, 131, 513, 726, 813, 949], "top": 165, "bottom": 419}
# back side, measured against real printed cards: table text regular weight and a bit smaller,
# address slightly lighter, the Tahsildar value as big and heavy as its label
TABLE_SCALE = 0.88
ADDR_SCALE = 1.0
ADDR_PITCH = 1.42                           # line pitch (x text size)
ADDR_TE = 0.97                              # Telugu runs in the address are set larger than the digits
ADDR_LIMIT = 450                            # px; the real cards wrap at about this width
TAHSILDAR_SCALE = 1.0
BACK_WEIGHT = {"table": 0.95, "table_te": 0.9, "permanent_address": 1.6, "tahsildar_office": 1.7}
TABLE_TE_COL = {1: 1.12, 3: 1.05, 4: 1.04}  # Telugu cells (name, gender, relation) vs the digits, measured
MAX_MEMBERS = 8                             # rows on the back (head of family is not listed)
SS = 4                                      # text drawn 4x larger, then reduced


# ------------------------------------------------------------------ text rendering
def _is_te(ch):
    return "ఀ" <= ch <= "౿" or ch in "‌‍"


def _runs(text):
    """Split into (is_telugu, text) runs; spaces / punctuation stay with the run before them."""
    out = []
    for ch in str(text):
        te = _is_te(ch) if (_is_te(ch) or ch.isalnum()) else (out[-1][0] if out else False)
        if out and out[-1][0] == te:
            out[-1][1] += ch
        else:
            out.append([te, ch])
    return out


_FACES = {}


def _face(path, wght=None):
    key = (path, wght)
    if key not in _FACES:
        import freetype
        import uharfbuzz as hb
        hbface = hb.Face(hb.Blob.from_file_path(path))
        font = hb.Font(hbface)
        font.scale = (hbface.upem, hbface.upem)
        ft = freetype.Face(path)
        if wght is not None:
            font.set_variations({"wght": wght})
            ft.set_var_design_coords((wght, 100.0))
        _FACES[key] = (font, ft, hbface.upem)
    return _FACES[key]


def _text_img(text, size_px, bold=False, weight=1.0, te_scale=1.0, hx=1.0):
    """Text as an 'ink' image (0 = paper, 255 = ink) at SS x size, baseline at BASE_EM * size.
    Shaped with HarfBuzz: Telugu in Noto Sans Telugu (as on the real cards, ra-vattu / conjuncts
    below the letter), Latin letters and digits in Nirmala UI (Nirmala Bold for bold values)."""
    import uharfbuzz as hb
    import freetype
    fs = size_px * SS
    runs = _runs(text)
    W = int(fs * (len(str(text)) * 1.2 + 4))
    H = int(fs * (BASE_EM + 1.0))
    base = BASE_EM * fs
    te_ink = np.zeros((H, W), np.uint8)
    la_ink = np.zeros((H, W), np.uint8)
    pen = fs * 0.5
    for te, chunk in runs:
        if te:
            wght = float(np.clip(400 + (TE_WGHT - 400) * weight + (300 if bold else 0), 100, 900))
            font, ft, upem = _face(TE_FONT, round(wght))
            canvas = te_ink
            rfs = fs * te_scale
        else:
            font, ft, upem = _face(str(Path(FONTS) / ("NirmalaB.ttf" if bold else "Nirmala.ttf")))
            canvas = la_ink
            rfs = fs
        sx = 1.0 if te else hx                      # horizontal scale of Latin runs (front values)
        ft.set_transform(freetype.Matrix(int(sx * 0x10000), 0, 0, 0x10000), freetype.Vector(0, 0))
        buf = hb.Buffer()
        buf.add_str(chunk)
        buf.guess_segment_properties()
        hb.shape(font, buf, {})
        ft.set_char_size(int(rfs * 64))
        k = rfs / upem
        for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
            ft.load_glyph(info.codepoint, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_NO_HINTING)
            g, bm = ft.glyph, ft.glyph.bitmap
            if bm.width and bm.rows:
                img = np.array(bm.buffer, np.uint8).reshape(bm.rows, bm.pitch)[:, :bm.width]
                x = int(round(pen + pos.x_offset * k * sx)) + g.bitmap_left
                y = int(round(base - pos.y_offset * k)) - g.bitmap_top
                x0, y0 = max(x, 0), max(y, 0)
                x1, y1 = min(x + bm.width, W), min(y + bm.rows, H)
                if x1 > x0 and y1 > y0:
                    reg = canvas[y0:y1, x0:x1]
                    np.maximum(reg, img[y0 - y:y1 - y, x0 - x:x1 - x], out=reg)
            pen += pos.x_advance * k * sx
    if not bold and la_ink.any():
        kk = max(0, round(THICKEN_LATIN * size_px / 30 * weight))     # measured at ~30 px
        if kk:
            la_ink = cv2.dilate(la_ink, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kk + 1, kk + 1)))
    return np.maximum(te_ink, la_ink)


def _esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _ink_bbox(a, thr=60):
    ys, xs = np.nonzero(a > thr)
    return (xs.min(), ys.min(), xs.max(), ys.max()) if len(xs) else None


def _fit(text, box, bold, hx=1.0):
    """Font size (px @300dpi) whose ink WIDTH matches the export (height for 1-2 characters);
    position = ink left edge and ink vertical centre of the export."""
    by_width = len(text.strip()) > 2
    want = (box[2] - box[0]) if by_width else (box[3] - box[1])
    lo, hi = 6.0, 60.0
    for _ in range(18):
        mid = (lo + hi) / 2
        bb = _ink_bbox(_text_img(text, mid, bold, hx=hx))
        v = ((bb[2] - bb[0]) if by_width else (bb[3] - bb[1])) / SS
        lo, hi = (mid, hi) if v < want else (lo, mid)
    size = (lo + hi) / 2
    bb = _ink_bbox(_text_img(text, size, bold, hx=hx))
    cy_ink = (bb[1] + bb[3]) / 2 / SS
    return {"size": round(size, 2), "x": box[0] - bb[0] / SS, "y": (box[1] + box[3]) / 2 - cy_ink, "hx": hx}


def _draw(card, text, spec, bold, max_w=None, weight=1.0, te_scale=1.0):
    if not text:
        return
    size = spec["size"]
    hx = spec.get("hx", 1.0)
    a = _text_img(text, size, bold, weight, te_scale, hx)
    bb = _ink_bbox(a)
    if bb is None:
        return
    if max_w:                                # shrink long values to fit their space
        w = (bb[2] - spec_x_ink(spec, bb)) / SS if False else (bb[2] - bb[0]) / SS
        if w > max_w:
            size = size * max_w / w
            a = _text_img(text, size, bold, weight, te_scale, hx)
            bb = _ink_bbox(a)
    a = a[:bb[3] + SS * 2, :bb[2] + SS * 2]
    h, w = a.shape
    small = cv2.resize(a.astype(np.float32), (max(1, w // SS), max(1, h // SS)), interpolation=cv2.INTER_AREA) / 255
    # keep the same baseline when the size was reduced
    x, y = spec["x"], spec["y"] + (spec["size"] - size) * BASE_EM
    _blend(card, small, x, y)


def _draw_line(card, text, size, x, cy, weight=1.0):
    """Draw text with its ink vertically centred on cy."""
    a = _text_img(text, size, False, weight)
    bb = _ink_bbox(a)
    if bb is None:
        return
    a = a[:bb[3] + SS * 2, :bb[2] + SS * 2]
    h, w = a.shape
    small = cv2.resize(a.astype(np.float32), (max(1, w // SS), max(1, h // SS)), interpolation=cv2.INTER_AREA) / 255
    _blend(card, small, x - bb[0] / SS, cy - (bb[1] + bb[3]) / 2 / SS)


def _width(text, size, bold=False, weight=1.0, te_scale=1.0):
    bb = _ink_bbox(_text_img(text, size, bold, weight, te_scale))
    return (bb[2] - bb[0]) / SS if bb else 0


def spec_x_ink(spec, bb):
    return bb[0]


def _blend(card, alpha, x, y, colour=(0, 0, 0)):
    xi, yi = int(round(x)), int(round(y))
    h, w = alpha.shape
    H, W = card.shape[:2]
    x0, y0, x1, y1 = max(0, xi), max(0, yi), min(W, xi + w), min(H, yi + h)
    if x1 <= x0 or y1 <= y0:
        return
    al = alpha[y0 - yi:y1 - yi, x0 - xi:x1 - xi][..., None]
    reg = card[y0:y1, x0:x1].astype(np.float32)
    card[y0:y1, x0:x1] = (reg * (1 - al) + np.array(colour, np.float32) * al).round().astype(np.uint8)


# ------------------------------------------------------------------ template build
def _clear(img, boxes_inpaint, boxes_white, seal=True):
    """Remove the old values. The values are grey/black; the watermark seal is green / yellow /
    orange. The whole seal is then redrawn from a high-res copy of the header emblem, so there are
    no patched / doubled rings and no pale halos where the old text was."""
    a = np.array(img.convert("RGB"))
    ai = a.astype(np.int16)
    g = ai.mean(2)
    sat = ai.max(2) - ai.min(2)
    mask = np.zeros(g.shape, np.uint8)
    for x0, y0, x1, y1 in boxes_inpaint:
        core = (g[y0:y1, x0:x1] < 205) & (sat[y0:y1, x0:x1] < 45)
        mask[y0:y1, x0:x1] = core.astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=2) > 0
    seal = _seal_model(a.shape[:2]) if seal else None          # the back has no watermark
    if seal is not None:
        rgb, inside = seal
        red = (ai[..., 0] > 150) & (ai[..., 1] < 120)                 # title pill / bottom band
        dark = (g < 150) & (sat < 40) & ~mask                         # labels' colons etc. stay
        rows = np.zeros(g.shape, bool)
        rows[SEAL_ROWS[0]:SEAL_ROWS[1]] = True
        put = inside & rows & ~red & ~dark
        a[put] = rgb[put]
        mask &= ~put
    if mask.any():
        a = cv2.inpaint(a, mask.astype(np.uint8) * 255, 3, cv2.INPAINT_TELEA)
    for x0, y0, x1, y1 in boxes_white:
        a[y0:y1, x0:x1] = 255
    return Image.fromarray(a)


def _seal_model(shape):
    """(RGB, inside-mask) of the faded AP seal, drawn from the header emblem of a high-res real
    card (templates/ration_seal_src.png). Position / size / fade fitted in ration_seal_fit.json."""
    fit_file, src_file = TPL / "ration_seal_fit.json", TPL / "ration_seal_src.png"
    if not fit_file.exists() or not src_file.exists():
        return None
    f = json.loads(fit_file.read_text())
    src = np.array(Image.open(src_file).convert("RGB")).astype(np.float32)
    src = cv2.GaussianBlur(src, (0, 0), 0.6)                     # mild pre-filter for the ~0.74x reduction
    H, W = shape
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    ux, uy = (xx - f["cx"]) / f["scale"], (yy - f["cy"]) / f["scale"]   # header-logo units
    mx = (f["raw_c"][0] + ux * f["k"]).astype(np.float32)
    my = (f["raw_c"][1] + uy * f["k"]).astype(np.float32)
    logo = np.stack([cv2.remap(src[..., c], mx, my, cv2.INTER_CUBIC, borderValue=255) for c in range(3)], -1)
    rl = np.hypot(ux, uy)
    inside = (rl <= 84.0) | ((uy > 55) & (rl <= 98.0) & (np.abs(ux) < 60))   # ring + the motto below it
    dist = np.linalg.norm(logo - np.array(f["bg"], np.float32), axis=2)   # header yellow -> paper
    keep = np.clip((dist - 30) / 45, 0, 1)
    keep = np.where(rl < 72, 1.0, keep)[..., None]               # inside the ring the yellow rays are real
    logo = logo * keep + 255 * (1 - keep)
    out = 255 - (255 - np.clip(logo, 0, 255)) * np.array(f["fade"], np.float32)
    return np.clip(out, 0, 255).round().astype(np.uint8), inside


def _fix_band(a):
    """Bottom red band: rebuild it from a clean column so old descenders leave no notch."""
    a = np.array(a)
    y0 = BAND_TOP - 2
    a[y0:, 120:720] = a[y0:, 730:731]
    return Image.fromarray(a)


def build_template():
    """Blank front/back from the export + measured field spec (run once)."""
    TPL.mkdir(exist_ok=True)
    pad = 4
    fx = [(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad) for s, b, _ in FIELDS.values() if s == "front"]
    bx = [(b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad) for s, b, _ in FIELDS.values() if s == "back"]
    front = _clear(Image.open(REF_FRONT), fx, [(PHOTO_BOX[0] - 3, PHOTO_BOX[1] - 3, PHOTO_BOX[2] + 3, PHOTO_BOX[3] + 3),
                                              (QR_BOX[0] - 4, QR_BOX[1] - 4, QR_BOX[2] + 4, QR_BOX[3] + 4)])
    back = _clear(Image.open(REF_BACK), [], bx, seal=False)
    front = _fix_band(front)
    front.save(FRONT_BG)
    back.save(BACK_BG)
    if not SAMPLE_FILE.exists():
        raise RuntimeError("templates/ration_sample.local.json (the reference card's values) is needed to rebuild")
    ref = json.loads(SAMPLE_FILE.read_text(encoding="utf-8"))
    spec = {k: _fit(ref[k], box, bold, (FRONT_HX_BOLD if bold else FRONT_HX) if side == "front" else 1.0)
            for k, (side, box, bold) in FIELDS.items()}
    # member table rows: same type size as the address line, 6 rows in the table body
    base = spec["permanent_address"]["size"]
    # member rows, measured from real cards: pitch 11.5% of the table body, text ~6.5% high
    body = TABLE["bottom"] - TABLE["top"]
    spec["_table"] = {"size": round(body * 0.065 / 0.72, 2), "row_h": body * 0.1153, "first_c": body * 0.090}
    SPEC_FILE.write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    return spec


def _spec():
    if not SPEC_FILE.exists() or not FRONT_BG.exists():
        return build_template()
    return json.loads(SPEC_FILE.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ render
def _qr(rc_no, size):
    import qrcode
    q = qrcode.QRCode(border=0, error_correction=qrcode.constants.ERROR_CORRECT_M)
    q.add_data(QR_URL.format(rc_no=rc_no))
    q.make(fit=True)
    return q.make_image(fill_color="black", back_color="white").get_image().convert("RGB").resize(size, Image.NEAREST)


def _photo(photo, box):
    img = photo if isinstance(photo, Image.Image) else Image.open(photo)
    img = img.convert("RGB")
    w, h = box[2] - box[0], box[3] - box[1]
    iw, ih = img.size
    if iw / ih > w / h:                       # crop to the frame, never stretch
        nw = int(ih * w / h); img = img.crop(((iw - nw) // 2, 0, (iw - nw) // 2 + nw, ih))
    else:
        nh = int(iw * h / w); img = img.crop((0, 0, iw, nh))
    return img.resize((w, h), Image.LANCZOS)


def render(data):
    """-> (front, back) PIL images, 1016 x 638."""
    spec = _spec()
    d = {**{k: "" for k in SAMPLE}, **(data or {})}
    front = np.array(Image.open(FRONT_BG).convert("RGB"))
    back = np.array(Image.open(BACK_BG).convert("RGB"))
    limits = {"head_name": 400, "dob_age": 400, "shop_address": 400, "card_type": 400,
              "permanent_address": 470, "tahsildar_office": 420}
    for k, (side, box, bold) in FIELDS.items():
        val = d.get(k, "")
        if k == "permanent_address" and val:
            # real cards: the text exactly as written (no spaces added after commas), wrapped only
            # at spaces into at most 3 lines; the Tahsildar row stays where it is
            sp = dict(spec[k], size=spec[k]["size"] * ADDR_SCALE)
            words, lines, cur = val.split(), [], ""
            for wd in words:
                trial = (cur + " " + wd).strip()
                if cur and _width(trial, sp["size"], te_scale=ADDR_TE) > ADDR_LIMIT and len(lines) < 2:
                    lines.append(cur); cur = wd
                else:
                    cur = trial
            lines.append(cur)
            for li, line in enumerate(lines):
                _draw(back, line, dict(sp, y=sp["y"] + li * sp["size"] * ADDR_PITCH), bold, ADDR_LIMIT,
                      weight=BACK_WEIGHT["permanent_address"], te_scale=ADDR_TE)
            continue
        if k == "tahsildar_office":                # real cards: as large and heavy as its label
            sp = spec[k]
            big = dict(sp, size=sp["size"] * TAHSILDAR_SCALE, y=sp["y"] - sp["size"] * (TAHSILDAR_SCALE - 1) * 0.55)
            _draw(back, val, big, bold, limits[k], weight=BACK_WEIGHT[k])
            continue
        _draw(front if side == "front" else back, val, spec[k], bold, limits.get(k))
    fimg = Image.fromarray(front)
    if d.get("photo") is not None:
        fimg.paste(_photo(d["photo"], PHOTO_BOX), PHOTO_BOX[:2])
    if d.get("rc_no"):
        fimg.paste(_qr(d["rc_no"], (QR_BOX[2] - QR_BOX[0], QR_BOX[3] - QR_BOX[1])), QR_BOX[:2])
    # back: member table
    t, cols = dict(spec["_table"]), TABLE["cols"]
    t["size"] *= TABLE_SCALE
    others = [m for m in (d.get("members") or []) if not m.get("head")]
    # column text offsets, measured on real cards (fraction of the column width); None = centred
    offs = [None, 0.042, 0.089, None, 0.092]
    for i, m in enumerate(others[:MAX_MEMBERS]):
        cy = TABLE["top"] + t["first_c"] + i * t["row_h"] - 1.0      # -1 px: measured on real cards
        cells = [f"{i + 2:02d}", m.get("name", ""), m.get("dob_age", ""), m.get("gender", ""), m.get("relation", "")]
        for c, val in enumerate(cells):
            if not val:
                continue
            c0, c1 = cols[c], cols[c + 1]
            te = any("ఀ" <= ch <= "౿" for ch in val)
            base = t["size"] * (TABLE_TE_COL.get(c, 1.0) if te else 1.0)
            wt = BACK_WEIGHT["table_te" if te else "table"]
            w = _width(val, base, weight=wt)
            size = base * min(1.0, (c1 - c0 - 10) / max(w, 1))
            w = w * size / base
            x = (c0 + c1) / 2 - w / 2 if offs[c] is None else c0 + (c1 - c0) * offs[c]
            _draw_line(back, val, size, x, cy, weight=wt)
    return fimg, Image.fromarray(back)


# ===================================================================== data from AePDS + Aadhaar
import datetime as _dt
import html as _html
import re as _re
import urllib.request as _url

AEPDS_URL = "https://aepos.ap.gov.in/smartepos/Qcodesearch.jsp?rcno={rc_no}"
REL_TE = {"SELF": "స్వయం", "HUSBAND": "భర్త", "WIFE": "భార్య", "SON": "కుమారుడు", "DAUGHTER": "కుమార్తె",
          "FATHER": "తండ్రి", "MOTHER": "తల్లి", "GRAND SON": "మనవడు", "GRAND DAUGHTER": "మనవరాలు",
          "BROTHER": "సోదరుడు", "SISTER": "సోదరి", "DAUGHTER IN LAW": "కోడలు", "SON IN LAW": "అల్లుడు",
          "FATHER IN LAW": "మామ", "MOTHER IN LAW": "అత్త", "GRAND FATHER": "తాత", "GRAND MOTHER": "అమ్మమ్మ/నాన్నమ్మ",
          "OTHERS": "ఇతరులు", "OTHER": "ఇతరులు", "GRAND SON IN LAW": "మనవడు", "NEPHEW": "మేనల్లుడు",
          "NIECE": "మేనకోడలు", "BROTHER IN LAW": "బావ/మరిది", "SISTER IN LAW": "వదిన/మరదలు"}
GENDER_TE = {"MALE": "మగ", "FEMALE": "ఆడ", "TRANSGENDER": "ట్రాన్స్‌జెండర్"}
GENDER_TE_TABLE = {"MALE": "మగ", "FEMALE": "ఆడ"}


class RationError(ValueError):
    pass


def fetch_rc(rc_no):
    """Member details for a rice card number from the AePDS public page."""
    rc_no = _re.sub(r"\D", "", str(rc_no))
    if not 8 <= len(rc_no) <= 12:
        raise RationError("Enter a valid rice card (RC) number")
    req = _url.Request(AEPDS_URL.format(rc_no=rc_no), headers={"User-Agent": "Mozilla/5.0 (CardPrintStudio)"})
    try:
        page = _url.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
    except Exception as e:
        raise RationError(f"Could not reach the AePDS site: {e}")
    clean = lambda t: _html.unescape(_re.sub(r"<[^>]+>", " ", t)).replace("\xa0", " ")
    i = page.find("Member Details")
    if i < 0:
        raise RationError(f"No details found on AePDS for RC {rc_no}")
    block = page[i:page.find("</table>", i)]
    head = clean(block)
    get = lambda k: (_re.search(k + r"\s*:\s*([^\s][^:]*?)\s{2,}", head + "   ") or [None, ""])[1].strip()
    info = {"rc_no": rc_no, "district": get("District"), "taluk": get("Taluk"), "fps_id": get("FPS Id"),
            "scheme": get("Scheme"), "shop_no": get("Shop No"), "new_rc_no": get("New RC No")}
    members = []
    for row in _re.findall(r"<tr>(.*?)</tr>", block, _re.S):
        cells = [clean(c).strip() for c in _re.findall(r"<td[^>]*>(.*?)</td>", row, _re.S)]
        if len(cells) >= 6 and cells[0].isdigit():
            members.append({"name": " ".join(cells[1].split()), "gender": cells[2], "age": cells[3],
                            "relation": cells[4], "status": cells[5]})
    if not members:
        raise RationError(f"No members listed on AePDS for RC {rc_no}")
    info["members"] = members
    return info


def _norm(s):
    return " ".join(_re.sub(r"[^a-z ]", " ", (s or "").lower()).split())


def _same_person(a, b):
    ta, tb = set(_norm(a).split()), set(_norm(b).split())
    return bool(ta and tb) and len(ta & tb) >= min(len(ta), len(tb), 2)


def aadhaar_facts(doc):
    """Telugu name, DOB, gender, address (Telugu + English parts) and photo from an e-Aadhaar."""
    import avatar
    import make_cards
    page = doc[0]
    front, back = make_cards.aadhaar_layout(page)
    lines = lambda r: [" ".join("".join(s["text"] for s in l["spans"]).split())
                       for b in page.get_text("dict", clip=r)["blocks"] for l in b.get("lines", [])
                       if abs(l["dir"][0] - 1) < 1e-3]
    fl = [t for t in lines(front) if t]
    f = {"name_te": "", "name_en": "", "dob": "", "gender": ""}
    for i, t in enumerate(fl):
        m = _re.search(r"(\d{2})/(\d{2})/(\d{4})", t)
        if "DOB" in t.upper() and m:
            f["dob"] = "-".join(m.groups())
            if i >= 2:
                f["name_te"], f["name_en"] = fl[i - 2], fl[i - 1]
        if _re.search(r"\b(MALE|FEMALE|TRANSGENDER)\b", t.upper()):
            f["gender"] = _re.search(r"\b(MALE|FEMALE|TRANSGENDER)\b", t.upper()).group(1)
    bl = [t for t in lines(back) if t]
    te, en, cur = [], [], None
    for t in bl:
        if t.startswith("చిరునామా"):
            cur = te; t = t.split(":", 1)[-1]
        elif t.lower().startswith("address"):
            cur = en; t = t.split(":", 1)[-1]
        if cur is not None and t.strip():
            cur.append(t.strip())
    split = lambda parts: [p.strip() for p in " ".join(parts).split(",") if p.strip()]
    f["addr_te"], f["addr_en"] = split(te), split(en)
    # photo: the portrait image on the Aadhaar
    f["photo"] = avatar._from_pdf(doc)
    return f


def _te_for(english, f):
    """Telugu spelling of a place, taken from the matching part of the Aadhaar address."""
    want = _norm(english)
    for i, e in enumerate(f.get("addr_en", [])):
        e2 = _norm(_re.sub(r"^(po|dist|vtc|sub district|state)\s*:", "", e.strip(), flags=_re.I))
        if e2 == want and i < len(f.get("addr_te", [])):
            return f["addr_te"][i]
    return english


def _permanent_address(f):
    """Telugu address without the C/O part and repeated place names."""
    parts = [p for p in f.get("addr_te", []) if not _re.match(r"^(సి/ఓ|C/O|S/O|D/O|W/O)", p, _re.I)]
    out = []
    for p in parts:
        if not out or p != out[-1]:
            out.append(p)
    return ", ".join(out)


def build_data(rc_no, aadhaar_doc):
    """Card data for render() from the AePDS page + the head of family's e-Aadhaar."""
    rc = fetch_rc(rc_no)
    f = aadhaar_facts(aadhaar_doc)
    head = next((m for m in rc["members"] if m["relation"].upper() == "SELF"), rc["members"][0])
    if f["name_en"] and not _same_person(f["name_en"], head["name"]):
        raise RationError(f"This Aadhaar is of {f['name_en']}, but the head of family on RC {rc['rc_no']} is "
                          f"{head['name']}. Upload the head of family's Aadhaar.")
    import te_translit
    known = te_translit.learn(f["name_en"], f["name_te"])     # e.g. surname spelled as on Aadhaar
    gender = (f["gender"] or head["gender"]).upper()
    age = head["age"]
    dob = f["dob"]
    rows = []
    for m in rc["members"]:
        g = m["gender"].upper()
        is_head = m is head
        try:
            est = f"{_dt.date.today().year - int(m['age'])}-01-01/{m['age']}"   # unknown DOB: year-01-01
        except ValueError:
            est = m["age"]
        rows.append({"head": is_head,
                     "name": f["name_te"] if is_head and f["name_te"] else te_translit.name(m["name"], known),
                     "name_en": m["name"],
                     "dob_age": est,
                     "gender": GENDER_TE_TABLE.get(g, m["gender"]),
                     "relation": REL_TE.get(m["relation"].upper(), m["relation"].title())})
    data = {
        "card_type": f"STATE/{rc['scheme'] or 'PHH'}",
        "rc_no": rc["rc_no"],
        "head_name": f["name_te"] or head["name"],
        "dob_age": f"{dob}/{age} సం." if dob else f"{age} సం.",
        "gender_en": gender.title() + "/",
        "gender_te": GENDER_TE.get(gender, ""),
        "members_count": str(len(rc["members"])),
        "shop_id": rc["fps_id"] or rc["shop_no"],
        "shop_address": _te_for(rc["taluk"], f),
        "permanent_address": _permanent_address(f),
        "tahsildar_office": _te_for(rc["district"], f),
        "photo": f["photo"],
        "members": rows,
    }
    details = {"name": head["name"], "id": rc["rc_no"], "id_label": "RC No", "id4": rc["rc_no"][-4:],
               "type_label": "RATION"}
    return data, details, rc
