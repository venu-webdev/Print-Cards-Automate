"""Photo printing - Card Print Studio plugin. Delete the folder to remove.

Sends the passport 8-up sheet straight onto 4x6 (10x15cm) photo paper on a photo printer such as a
Canon G2010. The 4x6 paper size is chosen on the printer by its driver name; the sheet is drawn to fill
the whole sheet (borderless look). The glossy paper TYPE, borderless mode and print QUALITY are the
printer's own driver settings - set them once in the printer's Preferences and this plugin keeps them.
"""
import io
import re
import threading

from flask import Blueprint, jsonify, request
from PIL import Image

try:
    import pywintypes
    import win32gui
    import win32print
    import win32ui
    from PIL import ImageWin
    HAVE_WIN32 = True
except Exception:
    HAVE_WIN32 = False

DEFAULT = {"printer": None, "copies": 1}
LOCK = threading.Lock()
DM_PAPERSIZE, DM_ORIENTATION, DM_COPIES, DM_COLOR = 0x2, 0x1, 0x100, 0x800
LOGPIXELSX, LOGPIXELSY = 88, 90
PHYSICALWIDTH, PHYSICALHEIGHT, HORZRES, VERTRES, PHYSICALOFFSETX, PHYSICALOFFSETY = 110, 111, 8, 10, 112, 113
DC_PAPERS, DC_PAPERNAMES = 2, 16
# printers that are usually photo / inkjet printers (preferred when guessing)
PHOTO_HINTS = ("g1010", "g2010", "g2012", "g3010", "g3060", "pixma", "canon g", "ts3", "ts7", "ts8",
               "l805", "l810", "l813", "l850", "l1800", "l8050", "photo", "inkjet", "stylus")
# a 4x6 / 10x15 cm paper name in the driver ("4\"x6\" 10x15cm", "10 x 15 cm (4 x 6 in)", ...)
PAPER_RX = re.compile(r'(10\s*[x×]\s*15)|(15\s*[x×]\s*10)|(4\s*"?\s*[x×]\s*"?\s*6)|(6\s*"?\s*[x×]\s*"?\s*4)', re.I)


def printers():
    if not HAVE_WIN32:
        return {"ok": False, "error": "Printing needs the pywin32 package."}
    try:
        names = [p[2] for p in win32print.EnumPrinters(
            win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 1)]
        try:
            default = win32print.GetDefaultPrinter()
        except Exception:
            default = None
        return {"ok": True, "printers": names, "default": default}
    except pywintypes.error as e:
        return {"ok": False, "error": "The Windows print service (Print Spooler) is not running."
                if e.winerror in (1722, 1726) else str(e.strerror)}


def guess(names, default):
    for n in names:                                   # a real photo / inkjet printer
        if any(k in n.lower() for k in PHOTO_HINTS):
            return n
    skip = ("pdf", "xps", "onenote", "fax", "rustdesk")
    return next((n for n in names if not any(s in n.lower() for s in skip)), default)


def _port(name):
    h = win32print.OpenPrinter(name)
    try:
        return win32print.GetPrinter(h, 2)["pPortName"]
    finally:
        win32print.ClosePrinter(h)


def find_4x6(name):
    """The driver's paper-size code for 4x6 / 10x15 cm, or (None, None)."""
    try:
        port = _port(name)
        papers = win32print.DeviceCapabilities(name, port, DC_PAPERS) or []
        names = win32print.DeviceCapabilities(name, port, DC_PAPERNAMES) or []
    except Exception:
        return None, None
    best = None
    for code, nm in zip(papers, names):
        s = str(nm).strip()
        if "scaled" in s.lower() or not PAPER_RX.search(s):
            continue
        rank = 0 if re.search(r'10\s*[x×]\s*15', s, re.I) else 1   # prefer the "10x15" named one
        if best is None or rank < best[0]:
            best = (rank, int(code), s)
    return (best[1], best[2]) if best else (None, None)


def has_4x6(name):
    return find_4x6(name)[0] is not None


def print_image(printer, img, copies=1, paper_code=None, output_file=None):
    """Print a PIL image onto the printer's 4x6 paper, filling the whole sheet. output_file: print to that
    file instead (for a virtual 'print to file' printer, used by the self-test). Returns the paper name."""
    if not HAVE_WIN32:
        raise RuntimeError("Printing needs the pywin32 package.")
    paper_name = None
    if paper_code is None:
        paper_code, paper_name = find_4x6(printer)
        if paper_code is None:
            raise ValueError(f"{printer} has no 4x6 / 10x15cm paper size - load 4x6 paper and pick that size in the driver")
    h = win32print.OpenPrinter(printer)
    try:
        dm = win32print.GetPrinter(h, 2)["pDevMode"]     # inherits glossy / borderless / quality from the default
        portrait = img.height >= img.width
        dm.PaperSize = int(paper_code)
        dm.Orientation = 1 if portrait else 2
        dm.Copies = max(1, int(copies))
        dm.Color = 2
        dm.Fields |= DM_PAPERSIZE | DM_ORIENTATION | DM_COPIES | DM_COLOR
        win32print.DocumentProperties(0, h, printer, dm, dm, 2 | 8)
    finally:
        win32print.ClosePrinter(h)
    dc = win32ui.CreateDCFromHandle(win32gui.CreateDC("WINSPOOL", printer, dm))
    try:
        physW, physH = dc.GetDeviceCaps(PHYSICALWIDTH), dc.GetDeviceCaps(PHYSICALHEIGHT)
        ox, oy = dc.GetDeviceCaps(PHYSICALOFFSETX), dc.GetDeviceCaps(PHYSICALOFFSETY)
        if physW <= 0 or physH <= 0:
            physW, physH = dc.GetDeviceCaps(HORZRES), dc.GetDeviceCaps(VERTRES)
            ox = oy = 0
        im = img.convert("RGB")
        if (im.width >= im.height) != (physW >= physH):   # make the photo's orientation match the sheet
            im = im.rotate(90, expand=True)
        im = im.resize((physW, physH), Image.LANCZOS)      # fill the whole sheet (borderless look)
        dc.StartDoc("Passport photos", output_file) if output_file else dc.StartDoc("Passport photos")
        dc.StartPage()
        ImageWin.Dib(im).draw(dc.GetHandleOutput(), (-ox, -oy, physW - ox, physH - oy))
        dc.EndPage()
        dc.EndDoc()
    except Exception:
        try:
            dc.AbortDoc()
        except Exception:
            pass
        raise
    finally:
        dc.DeleteDC()
    return paper_name


def register(host):
    bp = Blueprint("photo_print", __name__, url_prefix="/api/plugins/photo_print")

    def cfg():
        return host.load_config(DEFAULT)

    def fail(e):
        if HAVE_WIN32 and isinstance(e, pywintypes.error):
            msg = ("The Windows print service (Print Spooler) is not running - start it and try again."
                   if e.winerror in (1722, 1726) else f"Printing failed: {e.strerror}")
            return jsonify({"error": msg}), 500
        if isinstance(e, (ValueError, RuntimeError)):
            return jsonify({"error": str(e)}), 400
        return jsonify({"error": f"Printing failed: {e}"}), 500

    @bp.get("/status")
    def status():
        st, c = printers(), cfg()
        out = dict(c, service=st)
        if st.get("ok"):
            if c.get("printer") not in st["printers"]:
                c["printer"] = guess(st["printers"], st.get("default"))
                out["printer"] = c["printer"]
            # which listed printers can do 4x6, and the chosen one's paper
            out["printers_4x6"] = [n for n in st["printers"] if has_4x6(n)]
            code, name = find_4x6(c["printer"]) if c.get("printer") else (None, None)
            out["paper"] = name
            out["ready"] = code is not None
        return jsonify(out)

    @bp.post("/config")
    def config():
        c, b = cfg(), request.get_json(force=True) or {}
        if "printer" in b:
            c["printer"] = b["printer"] or None
        if str(b.get("copies", "")).isdigit():
            c["copies"] = max(1, min(99, int(b["copies"])))
        host.save_config(c)
        return status()

    @bp.post("/print")
    def do_print():
        b = request.get_json(force=True) or {}
        c = cfg()
        printer = b.get("printer") or c.get("printer")
        if not printer:
            return jsonify({"error": "Choose a photo printer first"}), 400
        job = str(b.get("job", ""))
        info = host.job_info(job)                         # 404s if the job is gone
        if info.get("type") != "passport":
            return jsonify({"error": "Only the passport-photo sheet prints this way"}), 400
        copies = int(b.get("copies") or c.get("copies") or 1)
        try:
            img = Image.open(io.BytesIO(host.get_image(job, "Sheet")))
            with LOCK:
                paper = print_image(printer, img, copies)
        except Exception as e:
            return fail(e)
        return jsonify({"printer": printer, "paper": paper, "copies": copies, "name": info.get("name")})

    host.app.register_blueprint(bp)
