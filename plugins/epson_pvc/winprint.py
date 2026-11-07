"""
Windows printing through the installed printer driver (no Epson internals used).

The card-tray settings (paper source "Disc/ID Card Tray", paper type, quality) are chosen
by the user once in Epson's own Printing Preferences window; we keep that DEVMODE and use
it only for card jobs, so normal paper printing on the same printer is not affected.
"""
import base64
import io
import json
import subprocess
import sys

from PIL import Image, ImageDraw

try:
    import pywintypes
    import win32con
    import win32gui
    import win32print
    import win32ui
    from PIL import ImageWin
    HAVE_WIN32 = True
except Exception:                       # not Windows / pywin32 missing
    HAVE_WIN32 = False

DM_FIELDS = ["Orientation", "PaperSize", "PaperLength", "PaperWidth", "Scale", "Copies", "DefaultSource",
             "PrintQuality", "Color", "Duplex", "YResolution", "TTOption", "Collate", "FormName",
             "ICMMethod", "ICMIntent", "MediaType", "DitherType", "Fields"]

# GetDeviceCaps indexes
LOGPIXELSX, LOGPIXELSY = 88, 90
PHYSICALWIDTH, PHYSICALHEIGHT, PHYSICALOFFSETX, PHYSICALOFFSETY = 110, 111, 112, 113


class PrintError(Exception):
    pass


def status():
    if not HAVE_WIN32:
        return {"ok": False, "error": "Printing needs the pywin32 package (pip install pywin32)."}
    try:
        flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
        printers = [p[2] for p in win32print.EnumPrinters(flags, None, 1)]
        default = None
        try:
            default = win32print.GetDefaultPrinter()
        except Exception:
            pass
        return {"ok": True, "printers": printers, "default": default}
    except pywintypes.error as e:
        msg = str(e.strerror or e)
        if e.winerror in (1722, 1726, 1753) or "RPC" in msg:
            msg = "The Windows print service (Print Spooler) is not running on this PC."
        return {"ok": False, "error": msg}


def guess_printer(printers):
    for p in printers:
        if "L8050" in p.upper():
            return p
    for p in printers:
        if "EPSON" in p.upper():
            return p
    return printers[0] if printers else None


# ---------------------------------------------------------------- DEVMODE <-> JSON
def devmode_to_dict(dm):
    d = {}
    for f in DM_FIELDS:
        try:
            d[f] = getattr(dm, f)
        except Exception:
            pass
    try:
        d["DriverData"] = base64.b64encode(bytes(dm.DriverData)).decode()
    except Exception:
        pass
    return d


def _base_devmode(printer):
    h = win32print.OpenPrinter(printer)
    try:
        dm = win32print.GetPrinter(h, 2)["pDevMode"]
        if dm is None:
            raise PrintError("The printer driver returned no settings.")
        return h, dm
    except Exception:
        win32print.ClosePrinter(h)
        raise


def dict_to_devmode(printer, saved):
    """Fresh DEVMODE from the driver with the saved card settings applied and validated."""
    h, dm = _base_devmode(printer)
    try:
        for f, v in (saved or {}).items():
            if f == "DriverData":
                try:
                    dm.DriverData = base64.b64decode(v)
                except Exception:
                    pass
            elif f in DM_FIELDS:
                try:
                    setattr(dm, f, v)
                except Exception:
                    pass
        try:   # let the driver merge / validate
            win32print.DocumentProperties(0, h, printer, dm, dm,
                                          win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER)
        except Exception:
            pass
        return dm
    finally:
        win32print.ClosePrinter(h)


SETUP_SCRIPT = r'''
import sys, json, base64, win32print, win32con, win32gui
name = sys.argv[1]; saved = json.loads(sys.argv[2])
sys.path.insert(0, sys.argv[3]); import winprint
# start from the card tray settings, so Epson's window opens on the PVC card setup
dm = winprint.card_settings(name, True, saved or None)[0]
h = win32print.OpenPrinter(name)
hwnd = win32gui.GetForegroundWindow()
r = win32print.DocumentProperties(hwnd, h, name, dm, dm,
        win32con.DM_IN_PROMPT | win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER)
win32print.ClosePrinter(h)
print(json.dumps({"ok": r == 1, "devmode": winprint.devmode_to_dict(dm) if r == 1 else None}))
'''


def open_setup_dialog(printer, saved, plugin_dir):
    """Shows the driver's own Printing Preferences; returns the chosen settings (or None)."""
    if not HAVE_WIN32:
        raise PrintError("pywin32 is not installed")
    r = subprocess.run([sys.executable, "-c", SETUP_SCRIPT, printer, json.dumps(saved or {}), str(plugin_dir)],
                       capture_output=True, text=True, timeout=900)
    try:
        out = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        raise PrintError((r.stderr or "The printer settings window could not be opened").strip()[-300:])
    return out["devmode"] if out.get("ok") else None


# ---------------------------------------------------------------- Photo+-compatible card mode
# Learned from Epson Photo+ (EPPlus.exe, PrintManager): it prints ID cards with the
# disc/ID card tray as paper feeder, media type 451 (plastic ID card, border) or
# 452 (borderless), on a portrait card page (template "IC": 764 x 1218 @ 360 dpi).
# We set the same things through the standard DEVMODE fields; the exact tray / paper
# numbers are asked from the installed driver by name (DeviceCapabilities).
MEDIATYPE_PLASTIC_IDCARD_BORDER = 451
MEDIATYPE_PLASTIC_IDCARD_BORDERLESS = 452
CARD_PAGE_MM = (764 / 360 * 25.4, 1218 / 360 * 25.4)       # 53.9 x 85.9 mm, portrait
# Photo+ ID card layout (EPPlus AppConfig, 1/360 inch): a 2124 x 2124 tray square with two
# portrait card slots. Slot 1 at (151.5, 549.5); slot 2 one card + 291 further right.
U = 25.4 / 360
TRAY_SQUARE_MM = 2124 * U                                  # 149.9 mm
CARD_SLOT_MM = (765 * U, 1219 * U)                         # 54.0 x 86.0 mm (portrait)
SLOT_POS_MM = {1: (151.5 * U, 549.5 * U), 2: ((151.5 + 765 + 291) * U, 549.5 * U)}
DC_PAPERS, DC_PAPERSIZE, DC_BINS, DC_BINNAMES, DC_PAPERNAMES = 2, 3, 6, 12, 16
DC_MEDIATYPENAMES, DC_MEDIATYPES = 34, 35
DM_ORIENTATION, DM_PAPERSIZE, DM_PAPERLENGTH, DM_PAPERWIDTH = 0x1, 0x2, 0x4, 0x8
DM_DEFAULTSOURCE, DM_MEDIATYPE = 0x200, 0x2000000
DMPAPER_USER = 256
TRAY_WORDS = ("id card", "disc", "cd/dvd", "cd-r", "card tray")
MEDIA_WORDS = ("id card", "plastic", "pvc")


def _caps(printer, port, cap, dm=None):
    try:
        if dm is not None:
            return list(win32print.DeviceCapabilities(printer, port, cap, dm) or [])
        return list(win32print.DeviceCapabilities(printer, port, cap) or [])
    except Exception:
        return []


def card_settings(printer, borderless=True, base=None):
    """Build the Photo+-style card DEVMODE. Returns (devmode, info).
    base: saved Epson preferences (quality, PhotoEnhance scene, ...) to start from."""
    h, dm = _base_devmode(printer)
    if base:
        dm = dict_to_devmode(printer, base)
    try:
        port = win32print.GetPrinter(h, 2)["pPortName"]
    finally:
        win32print.ClosePrinter(h)
    info = {"tray": None, "media": None, "paper": None, "missing": []}

    bins, bin_names = _caps(printer, port, DC_BINS), _caps(printer, port, DC_BINNAMES)
    for b, n in zip(bins, bin_names):
        if any(w in str(n).lower() for w in TRAY_WORDS):
            dm.DefaultSource = int(b); dm.Fields |= DM_DEFAULTSOURCE; info["tray"] = str(n).strip("\x00 ")
            break
    else:
        info["missing"].append("Disc/ID Card Tray")

    mts, mt_names = _caps(printer, port, DC_MEDIATYPES), _caps(printer, port, DC_MEDIATYPENAMES)
    want = MEDIATYPE_PLASTIC_IDCARD_BORDERLESS if borderless else MEDIATYPE_PLASTIC_IDCARD_BORDER
    pick = None
    if want in mts:
        pick = want
    else:
        for m, n in zip(mts, mt_names):
            if any(w in str(n).lower() for w in MEDIA_WORDS):
                pick = int(m); break
    if pick is None and not mts:
        pick = want                                   # driver did not list media; use Photo+'s id
    if pick is not None:
        dm.MediaType = pick; dm.Fields |= DM_MEDIATYPE
        names = dict(zip(mts, mt_names))
        info["media"] = str(names.get(pick, f"type {pick}")).strip("\x00 ")
    else:
        info["missing"].append("PVC ID card paper type")

    if info["tray"]:
        # The L8050 driver uses an A4 page for the Disc/ID Card Tray (measured); the card is
        # placed on it like Photo+ does. Media types for this tray are listed only with it set.
        mts, mt_names = _caps(printer, port, DC_MEDIATYPES, dm), _caps(printer, port, DC_MEDIATYPENAMES, dm)
        if want in mts:
            dm.MediaType = want; dm.Fields |= DM_MEDIATYPE
            info["media"] = str(dict(zip(mts, mt_names)).get(want, want)).strip("\x00 ")
            info["missing"] = [m for m in info["missing"] if m != "PVC ID card paper type"]
        dm.PaperSize = 9; dm.Fields |= DM_PAPERSIZE; info["paper"] = "A4 tray page (card placed like Photo+)"
        dm.Orientation = 1; dm.Fields |= DM_ORIENTATION
        h = win32print.OpenPrinter(printer)
        try:
            win32print.DocumentProperties(0, h, printer, dm, dm, win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER)
        finally:
            win32print.ClosePrinter(h)
        return dm, info

    papers, paper_names = _caps(printer, port, DC_PAPERS), _caps(printer, port, DC_PAPERNAMES)
    sizes = _caps(printer, port, DC_PAPERSIZE)
    chosen = None
    for i, (p, n) in enumerate(zip(papers, paper_names)):
        nl = str(n).lower()
        if "card" in nl or "pvc" in nl:
            try:
                sz = sizes[i]; w, l = (sz["x"], sz["y"]) if isinstance(sz, dict) else (sz[0], sz[1])
                if not (480 <= min(w, l) <= 600 and 820 <= max(w, l) <= 900):
                    continue                          # 0.1 mm units: must be card-sized
            except Exception:
                pass
            chosen = (int(p), str(n).strip("\x00 ")); break
    if chosen:
        dm.PaperSize = chosen[0]; dm.Fields |= DM_PAPERSIZE; info["paper"] = chosen[1]
    else:
        dm.PaperSize = DMPAPER_USER
        dm.PaperWidth, dm.PaperLength = round(CARD_PAGE_MM[0] * 10), round(CARD_PAGE_MM[1] * 10)
        dm.Fields |= DM_PAPERSIZE | DM_PAPERWIDTH | DM_PAPERLENGTH
        info["paper"] = "Custom 53.9 x 85.9 mm"
    dm.Orientation = 1; dm.Fields |= DM_ORIENTATION       # portrait, like Photo+'s ID card page

    h = win32print.OpenPrinter(printer)
    try:
        win32print.DocumentProperties(0, h, printer, dm, dm, win32con.DM_IN_BUFFER | win32con.DM_OUT_BUFFER)
    except Exception:
        pass
    finally:
        win32print.ClosePrinter(h)
    return dm, info


# ---------------------------------------------------------------- printing
def _rotate(img, deg):
    return {90: img.transpose(Image.ROTATE_270), 180: img.transpose(Image.ROTATE_180),
            270: img.transpose(Image.ROTATE_90)}.get(deg % 360, img)      # clockwise degrees


def _place_tray(dc, cfg, slot):
    """Card rect in device px for Photo+-style tray printing (A4 page, tray square at
    cfg tray_x_mm / tray_y_mm from the page's top-left, card in slot 1 or 2)."""
    dx, dy = dc.GetDeviceCaps(LOGPIXELSX), dc.GetDeviceCaps(LOGPIXELSY)
    ox, oy = dc.GetDeviceCaps(PHYSICALOFFSETX), dc.GetDeviceCaps(PHYSICALOFFSETY)
    sx, sy = SLOT_POS_MM.get(slot, SLOT_POS_MM[1])
    s = cfg.get("scale_pct", 100) / 100
    w, h = CARD_SLOT_MM[0] * s, CARD_SLOT_MM[1] * s
    bleed = cfg.get("bleed_mm", 0.25) if cfg.get("borderless", True) else 0.0
    x = cfg.get("tray_x_mm", 0.0) + sx + (CARD_SLOT_MM[0] - w) / 2 + cfg.get("offset_x_mm", 0) - bleed
    y = cfg.get("tray_y_mm", 0.0) + sy + (CARD_SLOT_MM[1] - h) / 2 + cfg.get("offset_y_mm", 0) - bleed
    w, h = w + 2 * bleed, h + 2 * bleed
    px = lambda v, d: v / 25.4 * d
    return (int(round(px(x, dx) - ox)), int(round(px(y, dy) - oy)),
            int(round(px(x + w, dx) - ox)), int(round(px(y + h, dy) - oy)))


def _place(dc, cfg, rotated):
    """Card rectangle in device pixels."""
    dx, dy = dc.GetDeviceCaps(LOGPIXELSX), dc.GetDeviceCaps(LOGPIXELSY)
    pw, ph = dc.GetDeviceCaps(PHYSICALWIDTH), dc.GetDeviceCaps(PHYSICALHEIGHT)
    ox, oy = dc.GetDeviceCaps(PHYSICALOFFSETX), dc.GetDeviceCaps(PHYSICALOFFSETY)
    s = cfg.get("scale_pct", 100) / 100
    cw, ch = (cfg["card_h_mm"], cfg["card_w_mm"]) if rotated else (cfg["card_w_mm"], cfg["card_h_mm"])
    w = round(cw * s / 25.4 * dx)
    h = round(ch * s / 25.4 * dy)
    if cfg.get("placement", "center") == "center":
        x = (pw - w) / 2
        y = (ph - h) / 2
    else:                                          # top-left of the physical page
        x = y = 0
    x += cfg.get("offset_x_mm", 0) / 25.4 * dx - ox
    y += cfg.get("offset_y_mm", 0) / 25.4 * dy - oy
    return int(round(x)), int(round(y)), int(round(x + w)), int(round(y + h))


def print_images(printer, saved_devmode, cfg, pages, doc_name="Card Print Studio"):
    """pages: list of PIL images, each printed on its own page (one card).
    cfg['mode'] 'auto' = Photo+-compatible card settings, 'custom' = saved Epson preferences."""
    if not HAVE_WIN32:
        raise PrintError("pywin32 is not installed")
    try:
        if cfg.get("mode", "auto") == "auto":
            dm, _ = card_settings(printer, cfg.get("borderless", True), saved_devmode)
        else:
            dm = dict_to_devmode(printer, saved_devmode) if saved_devmode else None
        hdc = win32gui.CreateDC("WINSPOOL", printer, dm)
    except pywintypes.error as e:
        raise PrintError(f"Could not open the printer: {e.strerror}")
    dc = win32ui.CreateDCFromHandle(hdc)
    rot = int(cfg.get("rotation", 90)) % 360
    try:
        dc.StartDoc(doc_name)
        for img in pages:
            dc.StartPage()
            img = _rotate(img.convert("RGB"), rot)
            if cfg.get("mirror"):
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            rect = (_place_tray(dc, cfg, int(cfg.get("slot", 1))) if cfg.get("mode", "auto") == "auto"
                    else _place(dc, cfg, rot in (90, 270)))
            ImageWin.Dib(img).draw(dc.GetHandleOutput(), rect)
            dc.EndPage()
        dc.EndDoc()
    except Exception as e:
        try:
            dc.AbortDoc()
        except Exception:
            pass
        raise PrintError(f"Printing failed: {e}")
    finally:
        dc.DeleteDC()


def alignment_card(cfg, dpi=300):
    """Card-size test pattern: outline, 2 mm safe line, centre cross, mm ticks."""
    w, h = round(cfg["card_w_mm"] / 25.4 * dpi), round(cfg["card_h_mm"] / 25.4 * dpi)
    im = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(im)
    mm = dpi / 25.4
    d.rectangle([0, 0, w - 1, h - 1], outline=(0, 0, 0), width=3)
    d.rectangle([2 * mm, 2 * mm, w - 2 * mm, h - 2 * mm], outline=(234, 106, 31), width=2)
    d.line([w / 2, 0, w / 2, h], fill=(120, 120, 120), width=1)
    d.line([0, h / 2, w, h / 2], fill=(120, 120, 120), width=1)
    for i in range(0, int(cfg["card_w_mm"]) + 1, 5):
        L = 3 * mm if i % 10 == 0 else 1.5 * mm
        d.line([i * mm, 0, i * mm, L], fill=(0, 0, 0), width=2)
        d.line([i * mm, h, i * mm, h - L], fill=(0, 0, 0), width=2)
    for i in range(0, int(cfg["card_h_mm"]) + 1, 5):
        L = 3 * mm if i % 10 == 0 else 1.5 * mm
        d.line([0, i * mm, L, i * mm], fill=(0, 0, 0), width=2)
        d.line([w, i * mm, w - L, i * mm], fill=(0, 0, 0), width=2)
    d.polygon([(w / 2 - 4 * mm, 9 * mm), (w / 2 + 4 * mm, 9 * mm), (w / 2, 5 * mm)], fill=(234, 106, 31))
    d.text((w / 2 - 6 * mm, 10 * mm), "TOP EDGE", fill=(0, 0, 0))
    d.text((6 * mm, 6 * mm), "Card Print Studio - alignment test", fill=(0, 0, 0))
    return im


def card_pdf(images, cfg):
    """Front/back as a PDF with pages exactly card-sized (for Photo+ or any software)."""
    import pymupdf
    doc = pymupdf.open()
    wpt, hpt = cfg["card_w_mm"] / 25.4 * 72, cfg["card_h_mm"] / 25.4 * 72
    for img in images:
        page = doc.new_page(width=wpt, height=hpt)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=95, dpi=(300, 300))
        page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)
    return doc.tobytes()
