"""A4 office printing (e.g. Canon iR 5500) - Card Print Studio plugin. Delete the folder to remove."""
import io
import threading

import pymupdf
from flask import Blueprint, abort, jsonify, request, send_file
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

DEFAULT = {"printer": None, "color": True, "dpi": 600,
           "trays": {}}          # printer name -> chosen tray name (missing = Multi-purpose Tray)
LOCK = threading.Lock()
DM_PAPERSIZE, DM_ORIENTATION, DM_COPIES, DM_COLOR, DM_DEFAULTSOURCE, DM_DUPLEX = 0x2, 0x1, 0x100, 0x800, 0x200, 0x1000
DMDUP_SIMPLEX = 1      # the Canons default to 2-sided - laminated sheets must be 1 page = 1 sheet
DC_BINS, DC_BINNAMES = 6, 12
# the laminated Aadhaar sheets are fed by hand -> prefer the bypass / multi-purpose tray
PREFERRED_TRAY = ("multi-purpose", "multipurpose", "multi purpose", "bypass", "manual")
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


def trays(printer):
    """Paper sources the printer's driver offers: [{"id": bin number, "name": "Drawer 1"}, ...]."""
    if not HAVE_WIN32 or not printer:
        return []
    try:
        h = win32print.OpenPrinter(printer)
        try:
            port = win32print.GetPrinter(h, 2)["pPortName"]
        finally:
            win32print.ClosePrinter(h)
        ids = win32print.DeviceCapabilities(printer, port, DC_BINS) or []
        names = win32print.DeviceCapabilities(printer, port, DC_BINNAMES) or []
    except Exception:
        return []
    return [{"id": int(i), "name": str(n).strip()} for i, n in zip(ids, names)]


def default_tray(tray_list):
    for t in tray_list:
        if any(k in t["name"].lower() for k in PREFERRED_TRAY):
            return t["name"]
    return tray_list[0]["name"] if tray_list else None


def pick_tray(cfg, printer, wanted=None):
    """(tray list, chosen tray dict or None) - the wanted / saved tray if this printer has it,
    otherwise the multi-purpose tray."""
    tl = trays(printer)
    names = {t["name"]: t for t in tl}
    for n in (wanted, (cfg.get("trays") or {}).get(printer), default_tray(tl)):
        if n in names:
            return tl, names[n]
    return tl, None


def print_pdf(printer, pdf_bytes, pages, copies, color, max_dpi, doc_name, output_file=None, tray_id=None):
    """Print the given PDF pages at 100% scale (A4 sheet = A4 paper), one page per sheet,
    from the given paper tray (bin number from trays(); None = driver default)."""
    h = win32print.OpenPrinter(printer)
    try:
        dm = win32print.GetPrinter(h, 2)["pDevMode"]
        dm.PaperSize, dm.Orientation, dm.Copies = 9, 1, copies              # A4 portrait
        dm.Color = 2 if color else 1
        dm.Duplex = DMDUP_SIMPLEX                                            # never print on the back
        dm.Fields |= DM_PAPERSIZE | DM_ORIENTATION | DM_COPIES | DM_COLOR | DM_DUPLEX
        if tray_id is not None:
            dm.DefaultSource = int(tray_id)
            dm.Fields |= DM_DEFAULTSOURCE
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


MM = 72 / 25.4


def scale_test_pdf():
    """A4 test page: if the printer keeps 100% scale, every length printed on it measures true."""
    doc = pymupdf.open()
    pg = doc.new_page(width=210 * MM, height=297 * MM)
    k = (0, 0, 0)

    def line(x0, y0, x1, y1, w=0.25):
        pg.draw_line((x0 * MM, y0 * MM), (x1 * MM, y1 * MM), color=k, width=w * MM)

    def text(x, y, t, size=9):
        pg.insert_text((x * MM, y * MM), t, fontsize=size, color=k)

    text(20, 22, "Card Print Studio - A4 scale test (must print at 100%)", 13)
    text(20, 29, "Measure with a ruler. Every number is the true length on paper.", 9)
    # 100 x 100 mm square
    pg.draw_rect(pymupdf.Rect(20 * MM, 40 * MM, 120 * MM, 140 * MM), color=k, width=0.3 * MM)
    text(22, 46, "100 x 100 mm square")
    # card outline (85.6 x 54 mm, ISO ID-1) with 3.18 mm corners
    pg.draw_rect(pymupdf.Rect(20 * MM, 155 * MM, 105.6 * MM, 209 * MM), color=k, width=0.3 * MM, radius=3.18 / 54)
    text(23, 162, "Card size 85.6 x 54 mm")
    text(23, 168, "A real PVC / ID card should fit")
    text(23, 173, "exactly on this outline.")
    # horizontal ruler 150 mm
    y = 230
    line(20, y, 170, y, 0.3)
    for i in range(151):
        h = 5 if i % 10 == 0 else (3.5 if i % 5 == 0 else 2)
        line(20 + i, y, 20 + i, y - h, 0.15)
        if i % 10 == 0:
            text(20 + i - (1 if i < 10 else 2), y - 6.5, str(i), 7)
    text(20, y + 6, "150 mm ruler (0 to 150)")
    # vertical ruler 250 mm along the right edge
    x = 190
    line(x, 25, x, 275, 0.3)
    for i in range(251):
        w = 5 if i % 10 == 0 else (3.5 if i % 5 == 0 else 2)
        line(x, 25 + i, x - w, 25 + i, 0.15)
        if i % 50 == 0:
            text(x + 1.5, 25 + i + 1.2, str(i), 7)
    text(135, 280, "250 mm ruler (top to bottom)", 8)
    # corner marks 5 mm from each paper edge
    for cx, cy, dx, dy in ((5, 5, 1, 1), (205, 5, -1, 1), (5, 292, 1, -1), (205, 292, -1, -1)):
        line(cx, cy, cx + 8 * dx, cy, 0.3)
        line(cx, cy, cx, cy + 8 * dy, 0.3)
    text(20, 250, "Corner marks are 5 mm in from each paper edge (missing marks = printer's margin).", 8)
    text(20, 256, "If the square is not 100 mm: turn OFF 'Fit to page' / scaling in the printer settings.", 8)
    return doc.tobytes()


# ---------------------------------------------------------------- shared "Small" sheet
# The laminated Small sheet uses only the top strip of the A4 page. Here one A4 sheet takes up to
# SMALL_ROWS customers' strips (same size), filled one by one: print a row now (the same sheet is
# fed again each time) or collect rows and print them together.
import json
import time

import aadhaar_laminate as LAM

PX = 72 / 300                                                   # sheet px @300 dpi -> PDF points
A4_PX = LAM.A4
STRIP_TOP = LAM.STRIP[0] + LAM.STRIP_COPY_DY + LAM.SMALL_DY     # where the strip sits on the Small sheet (94 px)
STRIP_H = LAM.STRIP[2] - LAM.STRIP[0]                           # 666 px = 56.4 mm
SMALL_ROWS = 5
ROW_TOP0, ROW_PITCH = 62, 680       # row tops 5.2 mm from the paper edge, 1.2 mm gap; last row ends 5.1 mm above the bottom
CLIP_PAD = 8                        # px kept around the strip (anti-aliased cut lines)


def row_top(i):
    return ROW_TOP0 + i * ROW_PITCH


def place_strip(pg, src, page_no, top_px):
    """Copy the Small strip of src[page_no] onto pg with its top at top_px (same size, stays vector)."""
    W = A4_PX[0] * PX
    y0, y1 = (STRIP_TOP - CLIP_PAD) * PX, (STRIP_TOP + STRIP_H + CLIP_PAD) * PX
    dy = (top_px - STRIP_TOP) * PX
    pg.show_pdf_page(pymupdf.Rect(0, y0 + dy, W, y1 + dy), src, page_no, clip=pymupdf.Rect(0, y0, W, y1))


def small_sheet_pdf(entries):
    """entries: [(row, pdf bytes, index of the Small page)] -> 1-page A4 PDF with those strips in their rows."""
    out = pymupdf.open()
    pg = out.new_page(width=A4_PX[0] * PX, height=A4_PX[1] * PX)
    for row, pdf, page_no in entries:
        place_strip(pg, pymupdf.open(stream=pdf, filetype="pdf"), page_no, row_top(row))
    return out


# "Share pages": a Big (Full) sheet's printing ends at 2623 px (222 mm), leaving 75 mm - room for one
# Small strip (56 mm) underneath with a 4.8 mm cutting gap; it then ends 13.6 mm above the paper edge.
UNDER_FULL_TOP = 2680


def pack_pages(entries, open_src):
    """entries: [(job, sheet)] in print order (copies already repeated). Returns a PDF where
    - every Full / Both sheet is its own page,
    - each Full page also gets one Small strip underneath (in order),
    - the remaining Small strips share pages, SMALL_ROWS per A4.
    open_src(job) -> (pymupdf doc, sides)."""
    pages, smalls = [], []                              # smalls: card ids, in order
    for job, sheet in entries:
        if sheet == "Small":
            smalls.append(job)
        else:
            pages.append({"base": (job, sheet), "strips": []})
    for p in pages:
        if smalls and p["base"][1] == "Full":
            p["strips"].append((smalls.pop(0), UNDER_FULL_TOP))
    while smalls:
        chunk, smalls = smalls[:SMALL_ROWS], smalls[SMALL_ROWS:]
        pages.append({"base": None, "strips": [(job, row_top(i)) for i, job in enumerate(chunk)]})
    out = pymupdf.open()
    for p in pages:
        pg = out.new_page(width=A4_PX[0] * PX, height=A4_PX[1] * PX)
        if p["base"]:
            src, sides = open_src(p["base"][0])
            pg.show_pdf_page(pg.rect, src, sides.index(p["base"][1]))
        for job, top in p["strips"]:
            src, sides = open_src(job)
            place_strip(pg, src, sides.index("Small"), top)
    return out


def small_strip_source(host, job):
    info = host.job_info(job)
    if "Small" not in info["sides"]:
        raise ValueError(f"{info['name']}: this card has no Small sheet")
    return host.get_pdf(job), info["sides"].index("Small")


def register(host):
    bp = Blueprint("office_print", __name__, url_prefix="/api/plugins/office_print")
    sheet_file = lambda: host.data_dir / "small_sheet.json"

    def load_sheet():
        try:
            s = json.loads(sheet_file().read_text(encoding="utf-8"))
            rows = (s.get("rows") or [])[:SMALL_ROWS]
        except (OSError, ValueError):
            s, rows = {}, []
        rows += [None] * (SMALL_ROWS - len(rows))
        for i, r in enumerate(rows):             # a waiting card deleted from the history frees its row
            if r and r.get("status") == "waiting" and not host.job_exists(r.get("job")):
                rows[i] = None
        return {"rows": rows, "started": s.get("started") or time.strftime("%Y-%m-%dT%H:%M:%S")}

    def save_sheet(s):
        f = sheet_file()
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(s, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(f)

    def sheet_view(s):
        rows = s["rows"]
        return {"rows": rows, "size": SMALL_ROWS, "started": s["started"],
                "free": sum(r is None for r in rows), "waiting": sum(bool(r) and r["status"] == "waiting" for r in rows),
                "printed": sum(bool(r) and r["status"] == "printed" for r in rows)}

    def print_waiting(s, b):
        """Print every waiting row on one page (rows already printed stay blank on this pass)."""
        c = cfg()
        printer = b.get("printer") or c.get("printer")
        if not printer:
            raise ValueError("Choose a printer first")
        waiting = [i for i, r in enumerate(s["rows"]) if r and r["status"] == "waiting"]
        if not waiting:
            raise ValueError("No rows are waiting to be printed")
        entries = [(i, *small_strip_source(host, s["rows"][i]["job"])) for i in waiting]
        _, tray = pick_tray(c, printer, b.get("tray"))
        color = bool(b.get("color", c.get("color", True)))
        names = ", ".join(s["rows"][i]["name"] for i in waiting)
        print_pdf(printer, small_sheet_pdf(entries).tobytes(), [0], 1, color, c.get("dpi", 600),
                  f"Aadhaar Small sheet - row {', '.join(str(i + 1) for i in waiting)} - {names}"[:120],
                  tray_id=tray["id"] if tray else None)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        for i in waiting:
            s["rows"][i].update(status="printed", at=now)
        return {"printed_rows": [i + 1 for i in waiting], "printer": printer, "tray": tray["name"] if tray else None}

    def fail(e):
        if HAVE_WIN32 and isinstance(e, pywintypes.error):
            msg = ("The Windows print service (Print Spooler) is not running - start it and try again."
                   if e.winerror in (1722, 1726) else f"Printing failed: {e.strerror}")
            return jsonify({"error": msg}), 500
        if isinstance(e, ValueError):
            return jsonify({"error": str(e)}), 400
        return jsonify({"error": f"Printing failed: {e}"}), 500

    @bp.get("/small")
    def small_state():
        return jsonify(sheet_view(load_sheet()))

    @bp.post("/small/add")
    def small_add():
        """{"jobs": [...], "print": true} -> put the cards in the next free rows; print now or keep waiting."""
        b = request.get_json(force=True) or {}
        jobs = [str(j) for j in b.get("jobs", [])]
        with LOCK:
            s = load_sheet()
            free = [i for i, r in enumerate(s["rows"]) if r is None]
            if not jobs:
                return jsonify({"error": "No card chosen"}), 400
            if len(jobs) > len(free):
                return jsonify({"error": f"Only {len(free)} row{'s' if len(free) != 1 else ''} left on this sheet – "
                                         f"print it and start a new sheet, or add fewer cards"}), 400
            added = []
            try:
                for job, i in zip(jobs, free):
                    small_strip_source(host, job)                       # 404 / no Small sheet -> refuse early
                    s["rows"][i] = {"job": job, "name": host.job_info(job)["name"], "status": "waiting",
                                    "at": time.strftime("%Y-%m-%dT%H:%M:%S")}
                    added.append(i + 1)
                result = print_waiting(s, b) if b.get("print") else {}
            except Exception as e:
                return fail(e)                                          # nothing saved: the sheet is unchanged
            save_sheet(s)
        return jsonify(dict(sheet_view(s), added_rows=added, **result))

    @bp.post("/small/print")
    def small_print():
        b = request.get_json(force=True) or {}
        with LOCK:
            s = load_sheet()
            try:
                result = print_waiting(s, b)
            except Exception as e:
                return fail(e)
            save_sheet(s)
        return jsonify(dict(sheet_view(s), **result))

    @bp.post("/small/remove")
    def small_remove():
        """Take a waiting card off the sheet (printed rows are on the paper and stay)."""
        i = int((request.get_json(force=True) or {}).get("row", 0)) - 1
        with LOCK:
            s = load_sheet()
            if not (0 <= i < SMALL_ROWS) or not s["rows"][i] or s["rows"][i]["status"] != "waiting":
                return jsonify({"error": "Only a waiting row can be removed"}), 400
            s["rows"][i] = None
            save_sheet(s)
        return jsonify(sheet_view(s))

    @bp.post("/small/new")
    def small_new():
        """Start a fresh sheet (the old one is full or was used up)."""
        with LOCK:
            s = {"rows": [None] * SMALL_ROWS, "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
            save_sheet(s)
        return jsonify(sheet_view(s))

    @bp.get("/small/preview.png")
    def small_preview():
        """Picture of the sheet: printed rows greyed, waiting rows in colour, free rows outlined."""
        from PIL import ImageDraw
        s = load_sheet()
        entries, state = [], {}
        for i, r in enumerate(s["rows"]):
            if r:
                try:
                    entries.append((i, *small_strip_source(host, r["job"])))
                    state[i] = r["status"]
                except Exception:
                    state[i] = "missing"
        dpi = 40
        pix = small_sheet_pdf(entries)[0].get_pixmap(dpi=dpi, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
        k = dpi / 300
        over = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        for i in range(SMALL_ROWS):
            box = [round(150 * k), round(row_top(i) * k), round((A4_PX[0] - 150) * k), round((row_top(i) + STRIP_H) * k)]
            st = state.get(i)
            if st == "printed":
                d.rectangle(box, fill=(255, 255, 255, 150))
            elif st == "waiting":
                d.rectangle(box, outline=(234, 106, 31, 255), width=2)
            else:
                d.rectangle(box, outline=(160, 160, 160, 255), width=1)
            d.text((box[0] + 4, box[1] + 3), f"{i + 1}", fill=(90, 90, 90, 255))
        img.alpha_composite(over)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "PNG")
        buf.seek(0)
        resp = send_file(buf, mimetype="image/png")
        resp.headers["Cache-Control"] = "no-store"
        return resp

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
        tl, t = pick_tray(c, c["printer"]) if st.get("ok") else ([], None)
        return jsonify(dict(c, service=st, tray_list=tl, tray=t["name"] if t else None))

    @bp.get("/trays")
    def tray_list():
        """Trays of any printer (the UI asks when another printer is picked)."""
        printer = request.args.get("printer") or cfg().get("printer")
        tl, t = pick_tray(cfg(), printer)
        return jsonify({"printer": printer, "tray_list": tl, "tray": t["name"] if t else None})

    @bp.post("/config")
    def config():
        c, b = cfg(), request.get_json(force=True) or {}
        if "printer" in b:
            c["printer"] = b["printer"]
        if "color" in b:
            c["color"] = bool(b["color"])
        if b.get("dpi") in (300, 600, "300", "600"):
            c["dpi"] = int(b["dpi"])
        if b.get("tray") and c.get("printer"):
            if any(t["name"] == b["tray"] for t in trays(c["printer"])):
                c["trays"] = dict(c.get("trays") or {}, **{c["printer"]: b["tray"]})
        host.save_config(c)
        return status()

    @bp.post("/print")
    def do_print():
        """Body: {"job": id} for one card or {"jobs": [id, ...]} for several (one print job),
        "sheets": ["Full", ...] (which A4 sheets of each card), copies, color, tray."""
        b, c = request.get_json(force=True) or {}, cfg()
        printer = b.get("printer") or c.get("printer")
        if not printer:
            return jsonify({"error": "Choose a printer first"}), 400
        # per-card choices: "items": [{"job", "sheets": [...], "copies": n}, ...]  (each person different)
        # or the same for every card: "jobs"/"job" + "sheets" + "copies"
        if b.get("items"):
            items = [{"job": str(it.get("job", "")), "sheets": it.get("sheets") or [],
                      "copies": max(1, min(99, int(it.get("copies", 1) or 1)))} for it in b["items"]][:50]
            copies = 1                                  # copies are already in the pages
        else:
            copies = max(1, min(99, int(b.get("copies", 1) or 1)))
            per = copies if b.get("share") else 1      # sharing: 3 copies of a Small = 3 strips on one page
            items = [{"job": str(x), "sheets": b.get("sheets", []), "copies": per}
                     for x in (b.get("jobs") or [b.get("job", "")])][:50]
            if b.get("share"):
                copies = 1
        srcs = {}

        def open_src(job):                             # each card's sheets.pdf, opened once
            if job not in srcs:
                srcs[job] = (pymupdf.open(stream=host.get_pdf(job), filetype="pdf"), host.job_info(job)["sides"])
            return srcs[job]

        entries, want, ids = [], [], []
        for it in items:
            sides = open_src(it["job"])[1]
            sel = [s for s in it["sheets"] if s in sides]
            if not sel:
                if b.get("items"):
                    continue                            # nothing ticked for this person
                sel = sides[:1]
            for _ in range(it["copies"]):              # this person's set, collated
                entries += [(it["job"], s) for s in sel]   # sheets in the card's own order
            want += [s for s in sel if s not in want]
            ids.append(it["job"])
        if not entries:
            return jsonify({"error": "Tick at least one sheet"}), 400
        if b.get("share"):                             # Smalls under Bigs / together -> less paper
            out = pack_pages(entries, open_src)
        else:
            out = pymupdf.open()
            for job, s in entries:
                src, sides = open_src(job)
                out.insert_pdf(src, from_page=sides.index(s), to_page=sides.index(s))
        pdf, pages = out.tobytes(), list(range(out.page_count))
        color = bool(b.get("color", c.get("color", True)))
        _, tray = pick_tray(c, printer, b.get("tray"))
        try:
            with LOCK:
                print_pdf(printer, pdf, pages, copies, color, c.get("dpi", 600),
                          f"Aadhaar A4 - {b.get('name') or 'sheet'} - {', '.join(want)}"[:120],
                          tray_id=tray["id"] if tray else None)
        except pywintypes.error as e:
            msg = ("The Windows print service (Print Spooler) is not running - start it and try again."
                   if e.winerror in (1722, 1726) else f"Printing failed: {e.strerror}")
            return jsonify({"error": msg}), 500
        except Exception as e:
            return jsonify({"error": f"Printing failed: {e}"}), 500
        return jsonify({"ok": True, "printed": want, "cards": len(ids), "sheets": len(pages), "copies": copies,
                        "printer": printer, "tray": tray["name"] if tray else None})

    @bp.post("/pvc")
    def print_pvc():
        """PVC cards on A4: {"jobs": [...], "copies", "color", "tray", "printer"} - each card's front and back
        side by side at real size with cut marks, 4 cards per page, printed at 100%."""
        b, c = request.get_json(force=True) or {}, cfg()
        printer = b.get("printer") or c.get("printer")
        if not printer:
            return jsonify({"error": "Choose a printer first"}), 400
        jobs = [str(x) for x in (b.get("jobs") or [])][:50]
        if not jobs:
            return jsonify({"error": "No card chosen"}), 400
        copies = max(1, min(99, int(b.get("copies", 1) or 1)))
        color = bool(b.get("color", c.get("color", True)))
        _, tray = pick_tray(c, printer, b.get("tray"))
        try:
            pdf = host.pvc_a4_pdf(jobs)
            pages = list(range(pymupdf.open(stream=pdf, filetype="pdf").page_count))
            with LOCK:
                print_pdf(printer, pdf, pages, copies, color, c.get("dpi", 600),
                          f"PVC cards A4 - {b.get('name') or f'{len(jobs)} cards'}"[:120],
                          tray_id=tray["id"] if tray else None)
        except Exception as e:
            if getattr(e, "code", None) == 400:                         # abort(400) from the A4 builder
                return jsonify({"error": "Laminated Aadhaar sheets are already A4 - choose PVC cards"}), 400
            return fail(e)
        return jsonify({"ok": True, "cards": len(jobs), "sheets": len(pages), "copies": copies,
                        "printer": printer, "tray": tray["name"] if tray else None})

    @bp.get("/test.pdf")
    def test_pdf():
        return send_file(io.BytesIO(scale_test_pdf()), mimetype="application/pdf",
                         as_attachment=True, download_name="A4-scale-test.pdf")

    @bp.post("/test")
    def test_print():
        b, c = request.get_json(force=True) or {}, cfg()
        printer = b.get("printer") or c.get("printer")
        if not printer:
            return jsonify({"error": "Choose a printer first"}), 400
        _, tray = pick_tray(c, printer, b.get("tray"))
        try:
            with LOCK:
                print_pdf(printer, scale_test_pdf(), [0], 1, False, c.get("dpi", 600), "A4 scale test",
                          tray_id=tray["id"] if tray else None)
        except pywintypes.error as e:
            return jsonify({"error": f"Printing failed: {e.strerror}"}), 500
        except Exception as e:
            return jsonify({"error": f"Printing failed: {e}"}), 500
        return jsonify({"ok": True, "printer": printer, "tray": tray["name"] if tray else None})

    host.app.register_blueprint(bp)
