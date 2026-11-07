"""A4 office printing (e.g. Canon iR 5500) - Card Print Studio plugin. Delete the folder to remove."""
import threading

import pymupdf
from flask import Blueprint, abort, jsonify, request
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

DEFAULT = {"printer": None, "color": True, "dpi": 600}
LOCK = threading.Lock()
DM_PAPERSIZE, DM_ORIENTATION, DM_COPIES, DM_COLOR = 0x2, 0x1, 0x100, 0x800
LOGPIXELSX, LOGPIXELSY = 88, 90
PHYSICALWIDTH, PHYSICALHEIGHT, PHYSICALOFFSETX, PHYSICALOFFSETY = 110, 111, 112, 113


def printers():
    if not HAVE_WIN32:
        return {"ok": False, "error": "Printing needs the pywin32 package."}
    try:
        names = [p[2] for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 1)]
        try:
            default = win32print.GetDefaultPrinter()
        except Exception:
            default = None
        return {"ok": True, "printers": names, "default": default}
    except pywintypes.error as e:
        return {"ok": False, "error": "The Windows print service (Print Spooler) is not running." if e.winerror in (1722, 1726) else str(e.strerror)}


def guess(names, default):
    for n in names:
        if "canon" in n.lower() or "ir-adv" in n.lower() or "ir " in n.lower():
            return n
    skip = ("pdf", "xps", "onenote", "fax", "rustdesk", "epson")
    return next((n for n in names if not any(s in n.lower() for s in skip)), default)


def print_pdf(printer, pdf_bytes, pages, copies, color, max_dpi, doc_name, output_file=None):
    """Print the given PDF pages at 100% scale (A4 sheet = A4 paper), one page per sheet."""
    h = win32print.OpenPrinter(printer)
    try:
        dm = win32print.GetPrinter(h, 2)["pDevMode"]
        dm.PaperSize, dm.Orientation, dm.Copies = 9, 1, copies              # A4 portrait
        dm.Color = 2 if color else 1
        dm.Fields |= DM_PAPERSIZE | DM_ORIENTATION | DM_COPIES | DM_COLOR
        win32print.DocumentProperties(0, h, printer, dm, dm, 2 | 8)           # DM_IN_BUFFER | DM_OUT_BUFFER
    finally:
        win32print.ClosePrinter(h)
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    dc = win32ui.CreateDCFromHandle(win32gui.CreateDC("WINSPOOL", printer, dm))
    try:
        dx, dy = dc.GetDeviceCaps(LOGPIXELSX), dc.GetDeviceCaps(LOGPIXELSY)
        ox, oy = dc.GetDeviceCaps(PHYSICALOFFSETX), dc.GetDeviceCaps(PHYSICALOFFSETY)
        dpi = min(max(dx, dy), max_dpi)
        dc.StartDoc(doc_name, output_file) if output_file else dc.StartDoc(doc_name)
        for i in pages:
            pg = doc[i]
            pix = pg.get_pixmap(dpi=dpi, alpha=False, colorspace=pymupdf.csRGB if color else pymupdf.csGRAY)
            img = Image.frombytes("RGB" if color else "L", (pix.width, pix.height), pix.samples).convert("RGB")
            w = round(pg.rect.width / 72 * dx)
            hh = round(pg.rect.height / 72 * dy)
            dc.StartPage()
            ImageWin.Dib(img).draw(dc.GetHandleOutput(), (-ox, -oy, w - ox, hh - oy))   # 1:1 with the paper
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


def register(host):
    bp = Blueprint("office_print", __name__, url_prefix="/api/plugins/office_print")

    @bp.before_request
    def _guard():
        if not host.enabled():
            abort(404)

    def cfg():
        return host.load_config(DEFAULT)

    @bp.get("/status")
    def status():
        st, c = printers(), cfg()
        if st.get("ok") and c["printer"] not in st["printers"]:
            c["printer"] = guess(st["printers"], st.get("default"))
        return jsonify(dict(c, service=st))

    @bp.post("/config")
    def config():
        c, b = cfg(), request.get_json(force=True) or {}
        if "printer" in b:
            c["printer"] = b["printer"]
        if "color" in b:
            c["color"] = bool(b["color"])
        if b.get("dpi") in (300, 600, "300", "600"):
            c["dpi"] = int(b["dpi"])
        host.save_config(c)
        return status()

    @bp.post("/print")
    def do_print():
        b, c = request.get_json(force=True) or {}, cfg()
        printer = b.get("printer") or c.get("printer")
        if not printer:
            return jsonify({"error": "Choose a printer first"}), 400
        pdf = host.get_pdf(str(b.get("job", "")))
        sides = host.job_info(str(b.get("job", "")))["sides"]
        want = [s for s in b.get("sheets", []) if s in sides] or sides[:1]
        pages = [sides.index(s) for s in want]
        copies = max(1, min(99, int(b.get("copies", 1) or 1)))
        color = bool(b.get("color", c.get("color", True)))
        try:
            with LOCK:
                print_pdf(printer, pdf, pages, copies, color, c.get("dpi", 600),
                          f"Aadhaar A4 - {b.get('name') or 'sheet'} - {', '.join(want)}")
        except pywintypes.error as e:
            msg = ("The Windows print service (Print Spooler) is not running - start it and try again."
                   if e.winerror in (1722, 1726) else f"Printing failed: {e.strerror}")
            return jsonify({"error": msg}), 500
        except Exception as e:
            return jsonify({"error": f"Printing failed: {e}"}), 500
        return jsonify({"ok": True, "printed": want, "copies": copies, "printer": printer})

    host.app.register_blueprint(bp)
