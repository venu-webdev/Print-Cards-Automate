"""
Who is on the card: holder name + ID number, read from the PDF text.

Used for the exports list (name, last 4 of the ID) and for clear, unique export names like
    PAN_C-REDDAMMA_017N_FRONT.jpeg
    AADHAR_PVC_HOLDER-NAME_1234_BACK.jpeg
"""
import re
from pathlib import Path

import pymupdf

TYPE_LABEL = {"dl": "DL", "voter": "VOTER", "aadhaar": "AADHAR_PVC", "aadhaar_lam": "AADHAR_LAM", "pan": "PAN", "ration": "RATION_CARD", "rc": "RC", "abha": "ABHA", "pmjay": "AYUSHMAN"}
ID_LABEL = {"dl": "DL No", "voter": "EPIC No", "aadhaar": "Aadhaar", "aadhaar_lam": "Aadhaar", "pan": "PAN", "ration": "RC No", "rc": "Regn No", "abha": "ABHA No", "pmjay": "PM-JAY ID"}

DL_RE = re.compile(r"\b([A-Z]{2}\d{2}\s?\d{11,12})\b")
PAN_RE = re.compile(r"\b([A-Z]{5}\d{4}[A-Z])\b")
AADHAAR_RE = re.compile(r"(?<![\d ])(\d{4} \d{4} \d{4})(?! ?\d)")
RC_RE = re.compile(r"\b([A-Z]{2}\d{1,2}[A-Z]{0,3}\d{4})\b")
EPIC_RE = re.compile(r"([A-Z]{3}\d{7})")
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .']{1,60}$")


def _lines(page):
    """[(bbox, text)] of horizontal text lines."""
    out = []
    rot = pymupdf.Matrix(page.rotation)              # reading direction as displayed
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            d = pymupdf.Point(l["dir"]) * rot
            if abs(d.x - 1) > 1e-3:
                continue
            t = " ".join("".join(s["text"] for s in l["spans"]).split())
            if t:
                out.append((pymupdf.Rect(l["bbox"]) * page.rotation_matrix, t))
    return out


def _value_right_of(lines, label):
    """Text on the same row, to the right of a label like 'Name'."""
    labs = [bb for bb, t in lines if t.rstrip(" :").lower() == label.lower()]
    for lb in labs:
        cy = (lb.y0 + lb.y1) / 2
        cands = [(bb.x0, t) for bb, t in lines
                 if bb.x0 > lb.x1 and bb.y0 - 2 <= cy <= bb.y1 + 2 and t.strip(" :")]
        if cands:
            return min(cands)[1].strip(" :")
    return None


def _value_below(lines, label):
    labs = [bb for bb, t in lines if t.rstrip(" :").lower() == label.lower()]
    for lb in labs:
        cands = [(bb.y0, t) for bb, t in lines
                 if bb.y0 >= lb.y1 - 1 and bb.y0 - lb.y1 < 14 and abs(bb.x0 - lb.x0) < 20]
        if cands:
            return min(cands)[1]
    return None


def _clean_name(n):
    n = " ".join((n or "").split())
    return n if n and NAME_RE.match(n) else None


def read(doc, card_type, filename=None, layout=None):
    """-> {"name", "id", "id_label", "id4", "type_label"} (missing values are None)."""
    name = ident = None
    try:
        if card_type == "dl":
            lines = _lines(doc[0])
            name = _clean_name(_value_right_of(lines, "Name"))
            m = DL_RE.search(" ".join(t for _, t in lines))
            ident = m.group(1).replace(" ", "") if m else None
        elif card_type == "rc":
            lines = _lines(doc[0])
            name = _clean_name(_value_below(lines, "Owner Name"))
            reg = _value_below(lines, "Regn No")
            m = RC_RE.search(reg or " ".join(t for _, t in lines))
            if not m:                              # picture-only RC print: the Regn No is in the file name
                m = RC_RE.search(re.sub(r"[^A-Z0-9]", " ", Path(filename or "").stem.upper()))
            ident = m.group(1) if m else None
        elif card_type == "pan":
            page = doc[0]
            text = page.get_text()
            m = PAN_RE.search(text)
            ident = m.group(1) if m else None
            lines = _lines(page)
            # on the printable card: the first name-like line below the PAN number
            pan_lines = [bb for bb, t in lines if ident and t == ident]
            if pan_lines:
                card_pan = max(pan_lines, key=lambda r: r.y0)       # the one on the card
                below = sorted((bb.y0, t) for bb, t in lines
                               if bb.y0 > card_pan.y1 - 1 and bb.y0 - card_pan.y1 < 80)
                for _, t in below:
                    if _clean_name(t) and not PAN_RE.fullmatch(t):
                        name = _clean_name(t)
                        break
        elif card_type in ("aadhaar", "aadhaar_lam"):
            page = doc[0]
            text = page.get_text()
            m = AADHAAR_RE.search(text)
            ident = m.group(1) if m else None
            if layout:
                front = layout[0]
                for bb, t in sorted(_lines(page), key=lambda x: x[0].y0):
                    if front.contains(bb) and _clean_name(t) and t.upper() == t and len(t) > 2:
                        name = _clean_name(t)
                        break
                if not name:
                    for bb, t in sorted(_lines(page), key=lambda x: x[0].y0):
                        if front.contains(bb) and _clean_name(t) and "aadhaar" not in t.lower():
                            name = _clean_name(t)
                            break
        elif card_type == "abha":
            import abha_card
            name, ident = abha_card.details(doc)
            name = _clean_name(name)
        elif card_type == "pmjay":
            import pmjay_card
            name, ident = pmjay_card.details(doc)
            name = _clean_name(name)
        elif card_type == "voter":
            # e-EPIC PDFs are scanned pictures (no text) - use the EPIC no. in the file name
            m = EPIC_RE.search(Path(filename or "").stem.upper())
            ident = m.group(1) if m else None
    except Exception as e:                         # details are a convenience, never fatal
        print(f"    (could not read card details: {e})")
    digits = re.sub(r"\s", "", ident or "")
    return {"name": name, "id": ident, "id_label": ID_LABEL.get(card_type, "ID"),
            "id4": digits[-4:] if digits else None,
            "type_label": TYPE_LABEL.get(card_type, card_type.upper())}


# ------------------------------------------------------------------ export names
DEFAULT_PATTERN = "{type}_{name}_{id4}_{side}"


def _slug(s, upper=True):
    s = re.sub(r"[^A-Za-z0-9]+", "-", (s or "").strip()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return (s.upper() if upper else s)[:28].strip("-")


def export_name(pattern, details, side, file_stem, date=""):
    """Fill a name pattern. Empty parts are dropped cleanly (no '__')."""
    name = _slug(details.get("name")) or _slug(file_stem, upper=False)
    vals = {"{type}": details.get("type_label") or "", "{name}": name,
            "{id4}": details.get("id4") or "", "{id}": _slug(details.get("id")),
            "{side}": side.upper(), "{file}": _slug(file_stem, upper=False), "{date}": date}
    out = pattern or DEFAULT_PATTERN
    for k, v in vals.items():
        out = out.replace(k, v)
    out = re.sub(r"([_\-. ])\1+", r"\1", out)          # collapse doubled separators
    out = re.sub(r"[_\- ]+([_\-])", r"\1", out).strip("_- .")
    return out or f"card_{side.upper()}"
