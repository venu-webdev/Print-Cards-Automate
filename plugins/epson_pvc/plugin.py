"""Epson PVC card printing - Card Print Studio plugin (remove this folder to remove it)."""
import io
import re
import sys
import threading
from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import winprint  # noqa: E402

DEFAULT = {
    "printer": None,          # printer name (auto: the L8050 / first Epson)
    "devmode": None,          # card settings chosen in Epson's Printing Preferences
    "card_w_mm": 85.6,        # CR80 / PVC ID card
    "card_h_mm": 54.0,
    "placement": "center",
    "offset_x_mm": 0.0,       # + moves the print right
    "offset_y_mm": 0.0,       # + moves the print down
    "scale_pct": 100.0,
    "mode": "auto",           # auto = Photo+-compatible card settings, custom = saved Epson preferences
    "borderless": True,       # media 452 (borderless) or 451 (with border), as in Photo+
    "rotation": 90,
    "tray_x_mm": 0.0,         # Photo+'s 150 mm tray square starts at the A4 page corner (measured from a real Photo+ job)
    "tray_y_mm": 0.0,
    "bleed_mm": 0.25,         # borderless: image spills this far past each card edge (measured in Photo+'s job)
    "slot": 1,                # card position on the tray for single cards (Photo+ has 2)
    "slot2_dx_mm": 0.0,       # extra fine-tune for slot 2 only (+ right / + down), after an alignment test
    "slot2_dy_mm": 0.0,
    "mirror": False,
}
NUM = {"slot2_dx_mm": (-5, 5), "slot2_dy_mm": (-5, 5), "tray_x_mm": (-20, 80),"tray_y_mm": (-20, 160), "bleed_mm": (0, 1.5), "slot": (1, 2), "card_w_mm": (40, 120), "card_h_mm": (30, 90), "offset_x_mm": (-20, 20),
       "offset_y_mm": (-20, 20), "scale_pct": (90, 110)}
PRINT_LOCK = threading.Lock()


def register(host):
    bp = Blueprint("epson_pvc", __name__, url_prefix="/api/plugins/epson_pvc")

    @bp.before_request
    def _guard():
        if not host.enabled():
            abort(404)

    def cfg():
        return host.load_config(DEFAULT)

    @bp.get("/status")
    def status():
        st = winprint.status()
        c = cfg()
        if st.get("ok"):
            if c["printer"] not in st["printers"]:
                c["printer"] = winprint.guess_printer(st["printers"])
        out = {k: v for k, v in c.items() if k != "devmode"}
        out.update(service=st, card_setup=bool(c.get("devmode")))
        return jsonify(out)

    @bp.post("/config")
    def config():
        c, body = cfg(), request.get_json(force=True) or {}
        if "printer" in body and body["printer"] != c.get("printer"):
            c["printer"], c["devmode"] = body["printer"], None      # settings belong to one printer
        for k, (lo, hi) in NUM.items():
            if k in body:
                try:
                    c[k] = min(hi, max(lo, float(body[k])))
                except (TypeError, ValueError):
                    pass
        for k in ("mirror", "borderless"):
            if k in body:
                c[k] = bool(body[k])
        if body.get("mode") in ("auto", "custom"):
            c["mode"] = body["mode"]
        if str(body.get("rotation")) in ("0", "90", "180", "270"):
            c["rotation"] = int(body["rotation"])
        if body.get("placement") in ("center", "topleft"):
            c["placement"] = body["placement"]
        host.save_config(c)
        return status()

    @bp.get("/detect")
    def detect():
        """What the installed driver offers for Photo+-style card printing."""
        c = cfg()
        if not c.get("printer"):
            return jsonify({"error": "No printer chosen"}), 400
        try:
            _, info = winprint.card_settings(c["printer"], c.get("borderless", True))
        except Exception as e:
            return jsonify({"error": f"Could not read the printer driver: {e}"}), 500
        return jsonify(info)

    @bp.post("/setup")
    def setup():
        c = cfg()
        if not c.get("printer"):
            return jsonify({"error": "Choose the printer first"}), 400
        try:
            dm = winprint.open_setup_dialog(c["printer"], c.get("devmode"), host.dir)
        except Exception as e:
            return jsonify({"error": str(e)}), 500
        if dm is None:
            return jsonify({"saved": False})
        c["devmode"] = dm
        host.save_config(c)
        return jsonify({"saved": True})

    def _img(job, side):
        return Image.open(io.BytesIO(host.get_image(job, side)))

    @bp.post("/print")
    def do_print():
        """One tray pass per copy. Body:
             {"cards": [{"slot": 1, "job": .., "side": "Front"}, {"slot": 2, "job": .., "side": "Front"}], "copies": n}
           or the old single-card form {"job": .., "side": ..}, which uses the configured slot."""
        body = request.get_json(force=True) or {}
        c = cfg()
        if not c.get("printer"):
            return jsonify({"error": "No printer chosen"}), 400
        cards = body.get("cards") or [{"slot": int(c.get("slot", 1)), "job": body.get("job"), "side": body.get("side", "Front")}]
        page = {}
        for card in cards[:2]:
            slot, job, side = int(card.get("slot", 1)), str(card.get("job", "")), card.get("side", "Front")
            if slot not in (1, 2) or slot in page:
                return jsonify({"error": "Each tray slot can hold one card"}), 400
            if side not in host.job_info(job)["sides"]:
                abort(404)
            page[slot] = _img(job, side)
        if not page:
            return jsonify({"error": "Nothing to print"}), 400
        copies = max(1, min(50, int(body.get("copies", 1) or 1)))
        name = re.sub(r"[^\w\- ]", "", str(body.get("name") or "card"))[:60]
        sides = "+".join(sorted({str(x.get("side", "Front")) for x in cards[:2]}))
        try:
            with PRINT_LOCK:
                winprint.print_images(c["printer"], c.get("devmode"), c, [page] * copies,
                                      f"PVC card - {name} - {sides}" + (" - 2 slots" if len(page) > 1 else ""))
        except winprint.PrintError as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True, "printed": copies, "cards": copies * len(page), "slots": sorted(page)})

    @bp.post("/test")
    def test():
        c = cfg()
        if not c.get("printer"):
            return jsonify({"error": "No printer chosen"}), 400
        slots = [s for s in (request.get_json(force=True, silent=True) or {}).get("slots", []) if s in (1, 2)]
        page = {s: winprint.alignment_card(c, label=f"SLOT {s}") for s in slots} if slots else winprint.alignment_card(c)
        try:
            with PRINT_LOCK:
                winprint.print_images(c["printer"], c.get("devmode"), c, [page], "PVC card - alignment test")
        except winprint.PrintError as e:
            return jsonify({"error": str(e)}), 500
        return jsonify({"ok": True})

    @bp.get("/test.png")
    def test_png():
        buf = io.BytesIO()
        winprint.alignment_card(cfg()).save(buf, "PNG", dpi=(300, 300))
        buf.seek(0)
        return send_file(buf, mimetype="image/png", as_attachment=True, download_name="PVC-alignment-test.png")

    @bp.get("/pdf/<job>")
    def pdf(job):
        info = host.job_info(job)
        data = winprint.card_pdf([_img(job, s) for s in info["sides"]], cfg())
        name = re.sub(r"[^\w\-]", "_", request.args.get("name") or info["source"])[:80]
        return send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=True,
                         download_name=f"{name}_CARD.pdf")

    host.app.register_blueprint(bp)
