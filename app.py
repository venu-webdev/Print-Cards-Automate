"""
Card Print Studio - local website for the card automation.

Run:  python app.py          (or double-click "Start Website.bat")
Open: http://127.0.0.1:5000

Only listens on this PC (127.0.0.1) - nothing is uploaded to the internet.
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
from collections import OrderedDict
from pathlib import Path

from flask import Flask, abort, jsonify, redirect, request, send_file, session

import avatar
import make_cards

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / "settings.json"
DEFAULT_SETTINGS = {
    "save_folder": str(ROOT / "export"),
    "auto_save": False,
    "name_pattern": "{type}_{name}_{id4}_{side}",
    "extension": ".jpeg",
    "network": False,          # other PCs on the shop network may use the website (needs a restart)
    "network_pin": "",         # PIN other PCs must enter (this PC never needs it)
    "type_names": {},          # the shop's own names for the card types, e.g. {"pmjay": "Arogyasri"}
}
# PAN back hologram sticker brightness, A (scan as is) .. F (brightest). New cards start at
# pan_rc.HOLOGRAM_DEFAULT ("E"); each card is changed on its back side in the exports list.
HOLOGRAM_LEVELS = ("A", "B", "C", "D", "E", "F")       # same keys as pan_rc.HOLOGRAM_LEVELS
NETWORK_KEYS = ("network", "network_pin")      # changed only on this PC, through /api/network
PORT = 5000
CARD_TYPES = make_cards.CARD_TYPES

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # 60 MB per upload
app.json.sort_keys = False      # keep card types in our order (Auto first)

# Every finished card is kept on disk, so the exports list survives a refresh / restart:
#   history/<job_id>/meta.json        what the list shows (name, type, ref, file names, saved...)
#   history/<job_id>/<Side>.jpeg      the card images (Front, Back / Full, Both, Small)
#   history/<job_id>/photo.jpeg       holder photo (avatar)      sheets.pdf  A4 laminated print PDF
#   history/<job_id>/ration.json (+ ration_photo.png)  ration card data, so names can be edited later
HISTORY_DIR = ROOT / "history"
JOB_ID_RE = re.compile(r"[0-9a-f]{32}")
JOBS = OrderedDict()          # job_id -> meta dict (oldest first); images are read from disk
JOBS_LOCK = threading.Lock()
PROCESS_LOCK = threading.Lock()   # one PDF at a time (CPU heavy)


# ---------------------------------------------------------------- settings
# Three layers, later wins:
#   DEFAULT_SETTINGS                built in
#   shop_settings.json  (in git)    the shop's shared choices - every PC gets them with `git pull`
#   settings.json       (not in git) this PC's own changes only: save folder, network sharing / PIN, ...
SHOP_SETTINGS_FILE = ROOT / "shop_settings.json"
SHOP_KEYS = ("auto_save", "name_pattern", "extension", "pan_front", "type_names")   # never a PC path or the PIN


def _shop_defaults():
    s = dict(DEFAULT_SETTINGS)
    try:
        shop = json.loads(SHOP_SETTINGS_FILE.read_text(encoding="utf-8"))
        s.update({k: v for k, v in shop.items() if k in SHOP_KEYS})
    except (OSError, ValueError):
        pass
    return s


def load_settings():
    s = _shop_defaults()
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if s.get("name_pattern") in ("{name}-{side}", "", None):    # old default -> new clear names
        s["name_pattern"] = DEFAULT_SETTINGS["name_pattern"]
    # folder copied to another PC / drive: a save folder whose parent no longer exists -> our own export/
    try:
        if not Path(s["save_folder"]).expanduser().parent.exists():
            s["save_folder"] = DEFAULT_SETTINGS["save_folder"]
    except (TypeError, ValueError, OSError):
        s["save_folder"] = DEFAULT_SETTINGS["save_folder"]
    return s


def save_settings(s):
    """Only what differs from the shop's defaults is kept in this PC's settings.json, so a newer
    shop_settings.json (git pull) still reaches every PC for the things it did not change itself."""
    base = _shop_defaults()
    own = {k: v for k, v in s.items() if k not in base or v != base[k]}
    SETTINGS_FILE.write_text(json.dumps(own, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- helpers
BAD_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def clean_name(name, ext):
    name = BAD_CHARS.sub("_", (name or "").strip()).strip(" .")
    for e in (".jpeg", ".jpg"):
        if name.lower().endswith(e):
            name = name[: -len(e)]
    return (name or "card") + ext


def unique_path(folder: Path, filename: str) -> Path:
    p = folder / filename
    stem, ext = p.stem, p.suffix
    n = 2
    while p.exists():
        p = folder / f"{stem} ({n}){ext}"
        n += 1
    return p


# ---------------------------------------------------------------- history (jobs on disk)
def job_dir(job_id):
    if not JOB_ID_RE.fullmatch(str(job_id)):
        abort(404)
    return HISTORY_DIR / job_id


def get_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(str(job_id))
    if not job:
        abort(404)
    return job


def _write_meta(job_id, meta):
    d = job_dir(job_id)
    tmp = d / "meta.json.tmp"
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, d / "meta.json")              # never leaves a half-written file


def _write_images(job_id, images):
    d = job_dir(job_id)
    for side, data in images.items():
        (d / f"{side}.jpeg").write_bytes(data)


def _ration_summary(r):
    return {"head_name": r["head_name"], "permanent_address": r["permanent_address"],
            "members": [{k: m.get(k, "") for k in ("name", "name_en", "dob_age", "relation", "head")}
                        for m in r["members"]]}


def _save_ration(job_id, data):
    d = job_dir(job_id)
    data = dict(data)
    photo = data.pop("photo", None)
    if photo is not None:
        photo.save(d / "ration_photo.png")
    (d / "ration.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _load_ration(job_id):
    from PIL import Image
    d = job_dir(job_id)
    data = json.loads((d / "ration.json").read_text(encoding="utf-8"))
    p = d / "ration_photo.png"
    data["photo"] = Image.open(p).convert("RGB") if p.is_file() else None
    return data


def load_history():
    """Read every saved job back into the list (on startup)."""
    if not HISTORY_DIR.is_dir():
        return
    found = []
    for d in HISTORY_DIR.iterdir():
        if not (d.is_dir() and JOB_ID_RE.fullmatch(d.name)):
            continue
        try:
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            if meta.get("job") == d.name and all((d / f"{s}.jpeg").is_file() for s in meta["sides"]):
                found.append(meta)
        except (OSError, ValueError, KeyError):
            print(f"  (skipped damaged history entry {d.name})")
    found.sort(key=lambda m: m.get("done_at", ""))
    with JOBS_LOCK:
        for m in found:
            JOBS[m["job"]] = m
    print(f"  history: {len(found)} card{'s' if len(found) != 1 else ''} loaded from {HISTORY_DIR}")


def public(meta):
    """What the website gets for one job."""
    return {k: meta.get(k) for k in ("job", "source", "sides", "type", "type_name", "details", "ref", "has_photo",
                                     "has_pdf", "ration", "done_at", "names", "edited", "saved", "v",
                                     "photo_box", "adjust", "hologram", "pan_front", "scan")}


def get_image(job_id, side):
    job = get_job(job_id)
    if side not in job["sides"]:
        abort(404)
    try:
        return (job_dir(job_id) / f"{side}.jpeg").read_bytes()
    except OSError:
        abort(404)


def get_pdf(job_id):
    job = get_job(job_id)
    p = job_dir(job_id) / "sheets.pdf"
    if not job.get("has_pdf") or not p.is_file():
        abort(404)
    return p.read_bytes()


def write_items(items, folder, ext):
    folder = Path(folder).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    for it in items:
        data = get_image(it["job"], it["side"])
        path = unique_path(folder, clean_name(it.get("name"), ext))
        path.write_bytes(data)
        saved.append(path.name)
    return str(folder), saved


# ---------------------------------------------------------------- pages
@app.get("/")
def index():
    return send_file(ROOT / "web" / "index.html")


@app.get("/assets/<any('tools.css', 'tools.js'):name>")
def tool_asset(name):
    """Look + helpers shared by the tool pages (Passport photos, Card scan)."""
    return send_file(ROOT / "web" / name, max_age=0)


@app.get("/passport")
def passport_page():
    """Passport size photos: adjust one photo, then 8 of them on a 6 x 4 inch sheet."""
    return send_file(ROOT / "web" / "passport.html")


SITE_PAGES = {"home": "home.html", "login": "login.html", "register": "register.html"}


@app.get("/<any(home, login, register):page>")
def site_page(page):
    """Marketing + account pages (design preview only - the tool itself stays open at /)."""
    return send_file(ROOT / "web" / "site" / SITE_PAGES[page])


@app.get("/site/<any('site.css', 'site.js'):name>")
def site_asset(name):
    return send_file(ROOT / "web" / "site" / name)


@app.get("/img/<path:name>")
def img(name):
    """Logos used by the card type tiles (web/img)."""
    folder = (ROOT / "web" / "img").resolve()
    path = (folder / name).resolve()
    if folder not in path.parents or not path.is_file():
        abort(404)
    return send_file(path, max_age=86400)


@app.get("/api/settings")
def api_get_settings():
    s = {k: v for k, v in load_settings().items() if k != "network_pin"}      # the PIN never leaves this PC
    return jsonify(s | {"card_types": CARD_TYPES, "is_host": is_local()})


# ---------------------------------------------------------------- network sharing
# Off by default: the website only listens on this PC (127.0.0.1). With "network" on (Settings ->
# Network sharing, then restart) it listens on the shop network too. Other PCs must enter the PIN;
# this PC never does. Printing from another PC prints on THIS PC's printers.
import hashlib
import hmac
import secrets
import socket

BIND_HOST = "127.0.0.1"                      # what this run actually listens on (set at start)
HOST_ONLY = ("/api/browse", "/api/open-folder", "/api/network")   # dialogs on this PC / sharing settings
PIN_FAILS = {}                               # ip -> [time of failed tries]


def is_local():
    return request.remote_addr in ("127.0.0.1", "::1")


def _secret_key():
    p = HISTORY_DIR / "_secret.key"          # kept with the history (never in git)
    try:
        return p.read_bytes()
    except OSError:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        k = secrets.token_bytes(32)
        p.write_bytes(k)
        return k


def _pin_tag(pin):
    return hashlib.sha256(("cps:" + pin).encode()).hexdigest()[:24]


def lan_addresses():
    """[(name, ip)] of this PC on the networks it is connected to (no loopback / link-local)."""
    out = []
    try:
        import subprocess as sp
        r = sp.run(["powershell", "-NoProfile", "-Command",
                    "Get-NetIPAddress -AddressFamily IPv4 | ForEach-Object { $_.InterfaceAlias + '|' + $_.IPAddress }"],
                   capture_output=True, text=True, timeout=8)
        for line in r.stdout.splitlines():
            name, _, ip = line.strip().partition("|")
            if ip and not ip.startswith(("127.", "169.254.")):
                out.append((name, ip))
    except Exception:
        pass
    if not out:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("10.255.255.255", 1))
            out.append(("Network", s.getsockname()[0]))
            s.close()
        except OSError:
            pass
    return out


LOGIN_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Card Print Studio</title><style>
:root{--bg:#f5f6f8;--card:#fff;--text:#16181d;--muted:#5d6370;--line:#dfe2e8;--accent:#4f46e5;--err:#dc2626}
@media (prefers-color-scheme:dark){:root{--bg:#111318;--card:#1b1e25;--text:#eceef2;--muted:#9aa0ab;--line:#2c313b;--accent:#818cf8}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--text);font:15px/1.5 "Segoe UI",system-ui,sans-serif;padding:16px}
form{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px;width:min(360px,100%);display:grid;gap:14px}
h1{margin:0;font-size:20px}p{margin:0;color:var(--muted);font-size:13.5px}
input{font:inherit;font-size:22px;letter-spacing:.3em;text-align:center;padding:10px;border:1px solid var(--line);border-radius:10px;background:transparent;color:inherit}
button{font:inherit;font-weight:600;padding:10px;border:0;border-radius:10px;background:var(--accent);color:#fff;cursor:pointer}
.err{color:var(--err)}</style></head><body>
<form method="post"><h1>Card Print Studio</h1><p>This PC is using the shop's card website over the network. Enter the PIN set on the main PC.</p>
{msg}<input name="pin" type="password" inputmode="numeric" autocomplete="off" autofocus aria-label="PIN" required>
<input type="hidden" name="next" value="{next}"><button>Open</button></form></body></html>"""


@app.route("/lan-login", methods=["GET", "POST"])
def lan_login():
    s = load_settings()
    nxt = request.values.get("next") or "/"
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    msg = ""
    if request.method == "POST":
        ip, now = request.remote_addr, time.time()
        fails = PIN_FAILS[ip] = [t for t in PIN_FAILS.get(ip, []) if now - t < 600]
        if len(fails) >= 8:
            msg = '<p class="err">Too many wrong PINs - wait 10 minutes.</p>'
        elif s.get("network_pin") and hmac.compare_digest(request.form.get("pin", ""), s["network_pin"]):
            session.permanent = True
            session["pin"] = _pin_tag(s["network_pin"])
            PIN_FAILS.pop(ip, None)
            return redirect(nxt)
        else:
            fails.append(now)
            msg = '<p class="err">Wrong PIN.</p>'
    import html
    return LOGIN_PAGE.replace("{msg}", msg).replace("{next}", html.escape(nxt, quote=True))


@app.before_request
def lan_guard():
    """Requests from other PCs: only when sharing is on, only with the PIN, never host-only actions."""
    if is_local():
        return None
    s = load_settings()
    if not s.get("network"):
        abort(403)
    if request.path == "/lan-login":
        return None
    if not s.get("network_pin") or session.get("pin") != _pin_tag(s["network_pin"]):
        if request.path.startswith("/api/") or request.path.startswith("/plugins/"):
            return jsonify({"error": "Enter the PIN first (reload the page)"}), 401
        return redirect("/lan-login?next=" + (request.full_path.rstrip("?") or "/"))
    if request.path.startswith(HOST_ONLY):
        return jsonify({"error": "This works only on the main PC"}), 403
    return None


def _firewall_rule_exists():
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", "name=Card Print Studio"],
                           capture_output=True, text=True, timeout=8)
        return r.returncode == 0
    except Exception:
        return False


@app.get("/api/network")
def api_network():
    s = load_settings()
    return jsonify({"enabled": bool(s.get("network")), "pin_set": bool(s.get("network_pin")),
                    "listening_on_network": BIND_HOST != "127.0.0.1", "port": PORT,
                    "addresses": [{"name": n, "url": f"http://{ip}:{PORT}"} for n, ip in lan_addresses()],
                    "firewall": _firewall_rule_exists()})


@app.post("/api/network")
def api_network_set():
    body = request.get_json(force=True) or {}
    s = load_settings()
    if "pin" in body:
        pin = str(body["pin"]).strip()
        if pin and not re.fullmatch(r"\d{4,8}", pin):
            return jsonify({"error": "The PIN must be 4 to 8 digits"}), 400
        s["network_pin"] = pin
    if "enabled" in body:
        if body["enabled"] and not s.get("network_pin"):
            return jsonify({"error": "Set a PIN first - other PCs will need it"}), 400
        s["network"] = bool(body["enabled"])
    save_settings(s)
    return api_network()


@app.post("/api/network/firewall")
def api_network_firewall():
    """Ask Windows (admin prompt on this PC) to allow the website through the firewall on private networks."""
    args = (f'advfirewall firewall add rule name="Card Print Studio" dir=in action=allow protocol=TCP '
            f'localport={PORT} profile=private,domain')
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Start-Process netsh -ArgumentList '{args}' -Verb RunAs -Wait -WindowStyle Hidden"],
                       timeout=180)
    except Exception as e:
        return jsonify({"error": f"Could not open the Windows permission prompt: {e}"}), 500
    return api_network()


@app.post("/api/settings")
def api_set_settings():
    s = load_settings()
    body = request.get_json(force=True) or {}
    for k in DEFAULT_SETTINGS:
        if k in body and k not in NETWORK_KEYS:
            s[k] = body[k]
    if s.get("extension") not in (".jpeg", ".jpg"):
        s["extension"] = ".jpeg"
    s.pop("pan_hologram", None)                     # old setting (now chosen per card)
    names = s.get("type_names") if isinstance(s.get("type_names"), dict) else {}
    s["type_names"] = {k: " ".join(str(v).split())[:24] for k, v in names.items()
                       if (k in CARD_TYPES or k in ("passport", "scan")) and str(v).strip()}   # empty name = back to the standard one
    save_settings(s)
    return jsonify(s)


@app.post("/api/browse")
def api_browse():
    """Opens a normal Windows folder picker on this PC."""
    start = load_settings()["save_folder"]
    code = (
        "import tkinter as tk, tkinter.filedialog as fd, sys\n"
        "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
        "p = fd.askdirectory(initialdir=sys.argv[1], title='Choose where exports are saved')\n"
        "print(p or '')\n"
    )
    try:
        out = subprocess.run([sys.executable, "-c", code, start], capture_output=True,
                             text=True, timeout=600).stdout.strip()
    except subprocess.TimeoutExpired:
        out = ""
    return jsonify({"folder": os.path.normpath(out) if out else ""})


@app.post("/api/open-folder")
def api_open_folder():
    folder = Path(load_settings()["save_folder"])
    folder.mkdir(parents=True, exist_ok=True)
    os.startfile(folder)  # Windows Explorer
    return jsonify({"ok": True})


@app.post("/api/process")
def api_process():
    f = request.files.get("file")
    card_type = request.form.get("card_type", "auto")
    if card_type not in CARD_TYPES:
        return jsonify({"error": "Unknown card type"}), 400
    if not f or not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "Please upload a PDF file"}), 400
    data = f.read()
    stem = Path(f.filename).stem
    password = request.form.get("password") or None
    try:
        with PROCESS_LOCK:
            used_type, pages, details = make_cards.process_document_full(
                data, card_type, password, f.filename, request.form.get("rc_no") or None,
                request.form.get("epds") or None)
    except make_cards.PasswordNeeded:
        msg = "Wrong password - try again" if password else "This PDF is locked - enter its password"
        return jsonify({"error": msg, "needs_password": True}), 401
    except Exception as e:  # bad / unexpected PDF
        return jsonify({"error": f"Could not process this PDF: {e}"}), 422
    if not pages:
        return jsonify({"error": "The PDF has no pages"}), 422

    images = {}
    pdf = None
    hologram, holo_base = None, None
    pan_front, pf_layers = None, None
    for side, img in pages:
        pdf = pdf or img.info.get("cps_pdf")
        pf = img.info.get("cps_panfront")
        if pf:                                       # PAN front: name gaps + signature, changeable later
            import pan_rc
            params = pan_rc.front_params(load_settings().get("pan_front"))   # "use for new PAN cards"
            img = pan_rc.compose_front(pf[0], pf[1], params)
            pan_front = {"side": side, "params": params, "box": pf[1].get("box"), "aspect": pf[1].get("aspect"),
                         "parts": [k for k in ("name", "father", "sign") if pf[1].get(k) is not None]}
            pf_layers = pf
        holo = img.info.get("cps_holo")
        if holo:                                     # PAN back: sticker drawn at pan_rc.HOLOGRAM_DEFAULT ("E")
            import pan_rc
            hologram = {"side": side, "level": pan_rc.HOLOGRAM_DEFAULT, **holo["place"]}
            holo_base = holo["base"]
        buf = io.BytesIO()
        make_cards.save_jpeg(img, buf)
        images[side] = buf.getvalue()

    photo = details.pop("_photo", None)
    ration = details.pop("_ration", None)
    photo_box = details.pop("_photo_box", None)
    job_id = uuid.uuid4().hex
    meta = {"job": job_id, "source": stem, "sides": list(images), "type": used_type,
            "type_name": CARD_TYPES.get(used_type, used_type), "details": details,
            "ref": avatar.new_ref(),                 # public reference, unrelated to job_id
            "has_photo": photo is not None, "has_pdf": pdf is not None,
            "ration": _ration_summary(ration) if ration else None,
            "done_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "names": {}, "edited": {}, "saved": False, "v": 0,
            "photo_box": {"Front": photo_box} if photo_box else {}, "adjust": {}, "hologram": hologram,
            "pan_front": pan_front}
    try:
        job_dir(job_id).mkdir(parents=True, exist_ok=True)
        _write_images(job_id, images)
        if holo_base is not None:                    # card back without the sticker (brightness changes)
            holo_base.save(job_dir(job_id) / f"{hologram['side']}.holo_base.png")
        if pf_layers is not None:                    # PAN front layers (see pan_rc.pan_front_layers)
            _save_pf_layers(job_id, pan_front["side"], *pf_layers)
        if photo:
            (job_dir(job_id) / "photo.jpeg").write_bytes(photo)
        if pdf:
            (job_dir(job_id) / "sheets.pdf").write_bytes(pdf)
        if ration:
            _save_ration(job_id, ration)
        _write_meta(job_id, meta)                    # last: a job only counts once it is complete
    except OSError as e:
        shutil.rmtree(job_dir(job_id), ignore_errors=True)
        return jsonify({"error": f"Could not store the card on disk: {e}"}), 500
    with JOBS_LOCK:
        JOBS[job_id] = meta
    return jsonify(public(meta))


@app.get("/api/jobs")
def api_jobs():
    """The whole exports history, newest first."""
    with JOBS_LOCK:
        metas = list(JOBS.values())
    return jsonify([public(m) for m in reversed(metas)])


@app.post("/api/jobs/<job_id>")
def api_job_update(job_id):
    """Remember what the website changed: file names typed by hand, saved flag."""
    job = get_job(job_id)
    body = request.get_json(force=True) or {}
    with JOBS_LOCK:
        if isinstance(body.get("names"), dict):
            job["names"] = {s: str(v)[:200] for s, v in body["names"].items() if s in job["sides"]}
        if isinstance(body.get("edited"), dict):
            job["edited"] = {s: bool(v) for s, v in body["edited"].items() if s in job["sides"]}
        if "saved" in body:
            job["saved"] = bool(body["saved"])
        _write_meta(job_id, job)
    return jsonify({"ok": True})


@app.post("/api/jobs/delete")
def api_jobs_delete():
    """Delete cards from the history for good (files saved to the export folder are kept)."""
    ids = [str(i) for i in (request.get_json(force=True) or {}).get("jobs", []) if JOB_ID_RE.fullmatch(str(i))]
    deleted = []
    for job_id in ids:
        with JOBS_LOCK:
            found = JOBS.pop(job_id, None) is not None
        if found:
            shutil.rmtree(job_dir(job_id), ignore_errors=True)
            deleted.append(job_id)
    return jsonify({"deleted": deleted})


@app.get("/api/image/<job_id>/<side>")
def api_image(job_id, side):
    data = get_image(job_id, side)
    resp = send_file(io.BytesIO(data), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "max-age=3600"
    return resp


@app.get("/api/pdf/<job_id>")
def api_pdf(job_id):
    """Print-quality PDF (A4 laminated sheets: Full, Both, Small as pages)."""
    data = get_pdf(job_id)
    name = clean_name(request.args.get("name") or get_job(job_id)["source"], ".pdf")
    return send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=not request.args.get("inline"),
                     download_name=name)                 # inline=1: open in the browser (to print)


# ---------------------------------------------------------------- A4 PDF: PVC cards, front + back side by side
MM = 72 / 25.4
A4_PT = (210 * MM, 297 * MM)
CARD_MM = (85.6, 54.0)          # ID-1 / CR80 PVC card
A4_GAP_MM = 8.0                 # between front and back, and between rows
A4_TOP_MM = 15.0
A4_ROWS = 4                     # card pairs per A4 page
CROP_MM, CROP_OFF_MM = 4.0, 1.5  # cut marks: length, distance from the card corner


CARD_RADIUS_MM = 3.18          # ID-1 / CR80 corner radius (ISO/IEC 7810)
_ROUND_MASK = {}


def _round_mask(w, h):
    """Grey PNG mask for a w x h px card picture: white inside the rounded card, black in the corners."""
    if (w, h) not in _ROUND_MASK:
        from PIL import Image, ImageDraw
        k = 4                                                       # draw 4x and shrink: smooth arc
        r = CARD_RADIUS_MM / CARD_MM[0] * w * k
        m = Image.new("L", (w * k, h * k), 0)
        ImageDraw.Draw(m).rounded_rectangle((0, 0, w * k - 1, h * k - 1), radius=r, fill=255)
        buf = io.BytesIO()
        m.resize((w, h), Image.LANCZOS).save(buf, "PNG")
        _ROUND_MASK[(w, h)] = buf.getvalue()
    return _ROUND_MASK[(w, h)]


def a4_pdf(job_ids, rounded=True):
    """A4 portrait pages, each row = one card: Front left, Back right, at real PVC size.
    rounded: the cards get their real rounded corners (3.18 mm) and a thin rounded cutting line;
    otherwise square with cut marks. Rounded cards are flattened onto white and embedded as one plain
    JPEG per card (no soft mask) so the PDF opens in Adobe Reader too; square cards embed the JPEG as-is."""
    import pymupdf
    from PIL import Image
    cw, ch, gap = CARD_MM[0] * MM, CARD_MM[1] * MM, A4_GAP_MM * MM
    x_front = (A4_PT[0] - 2 * cw - gap) / 2
    out, n = pymupdf.open(), 0
    for job_id in job_ids:
        job = get_job(job_id)
        if job.get("has_pdf"):                       # A4 laminated sheets are already pages
            continue
        sides = [s for s in ("Front", "Back") if s in job["sides"]] or job["sides"][:2]
        if n % A4_ROWS == 0:
            page = out.new_page(width=A4_PT[0], height=A4_PT[1])
        y = A4_TOP_MM * MM + (n % A4_ROWS) * (ch + gap)
        for i, side in enumerate(sides[:2]):
            x = x_front + i * (cw + gap)
            r = pymupdf.Rect(x, y, x + cw, y + ch)
            data = get_image(job_id, side)
            if rounded:
                # bake the rounded corners onto white and embed one plain JPEG (no soft mask) - a separate
                # image mask here makes Adobe Reader show only the borders ("contact the creator").
                im = Image.open(io.BytesIO(data)).convert("RGB")
                w, h = im.size
                mask = Image.open(io.BytesIO(_round_mask(w, h))).convert("L")
                card = Image.new("RGB", (w, h), "white")
                card.paste(im, (0, 0), mask)
                buf = io.BytesIO()
                card.save(buf, "JPEG", quality=95, subsampling=0, dpi=(300, 300))
                page.insert_image(r, stream=buf.getvalue(), keep_proportion=False)
                page.draw_rect(r, color=(0.6, 0.6, 0.6), width=0.35, radius=CARD_RADIUS_MM / CARD_MM[1])
            else:
                page.insert_image(r, stream=data, keep_proportion=False)
                _crop_marks(page, r)
        n += 1
    if not n:
        abort(400, "No PVC cards to put on A4 (laminated Aadhaar sheets are already A4)")
    return out.tobytes(deflate=True, garbage=3)


def _crop_marks(page, r):
    L, o = CROP_MM * MM, CROP_OFF_MM * MM
    for cx, sx in ((r.x0, -1), (r.x1, 1)):
        for cy, sy in ((r.y0, -1), (r.y1, 1)):
            page.draw_line((cx + sx * o, cy), (cx + sx * (o + L), cy), color=(0, 0, 0), width=0.3)
            page.draw_line((cx, cy + sy * o), (cx, cy + sy * (o + L)), color=(0, 0, 0), width=0.3)


@app.route("/api/a4", methods=["GET", "POST"])
def api_a4():
    """?job=<id> (one card) or POST {"jobs": [...], "name": ...} (several cards, 4 per page)."""
    body = (request.get_json(force=True, silent=True) or {}) if request.method == "POST" else {}
    ids = [str(i) for i in (body.get("jobs") or request.args.getlist("job")) if JOB_ID_RE.fullmatch(str(i))]
    if not ids:
        abort(400)
    name = clean_name(body.get("name") or request.args.get("name") or "PVC-A4", ".pdf")
    rounded = not (body.get("square") or request.args.get("square"))      # square=1: old cut-mark style
    return send_file(io.BytesIO(a4_pdf(ids, rounded)), mimetype="application/pdf", as_attachment=True, download_name=name)


@app.get("/api/a4/preview")
def api_a4_preview():
    """?job=<id>: the first A4 page as a picture, for the A4 window."""
    import pymupdf
    ids = [i for i in request.args.getlist("job") if JOB_ID_RE.fullmatch(i)]
    if not ids:
        abort(400)
    page = pymupdf.open(stream=a4_pdf(ids), filetype="pdf")[0]
    resp = send_file(io.BytesIO(page.get_pixmap(dpi=110).tobytes("png")), mimetype="image/png")
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- passport photo sheet
# The passport page lays out the 8 photos (35 x 45 mm, 2 rows x 4 columns) on a 6 x 4 inch canvas at 300 dpi
# (1800 x 1200 px). "Add to exports" sends that sheet + the single photo; it becomes a normal entry in the
# exports list: side "Sheet" (JPEG, 300 dpi) + a 6 x 4 inch PDF for printing, the photo as its avatar.
PASSPORT_PX = (1800, 1200)
PASSPORT_PHOTO_PX = (413, 531)          # 35 x 45 mm @ 300 dpi


def _data_url_image(value, size):
    import base64
    from PIL import Image
    data = str(value or "")
    if "," in data:
        data = data.split(",", 1)[1]
    try:
        img = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
    except Exception:
        abort(400)
    if img.size != size:
        abort(400)
    return img


def _passport_pdf(sheet, portrait=False):
    import pymupdf
    jpg = io.BytesIO()
    make_cards.save_jpeg(sheet, jpg)
    doc = pymupdf.open()
    w, h = (4, 6) if portrait else (6, 4)                            # inch; portrait = turned for 4x6 paper fed upright
    page = doc.new_page(width=w * 72, height=h * 72)
    page.insert_image(page.rect, stream=jpg.getvalue())
    return doc.tobytes()


def _passport_orient(sheet, portrait):
    """The sheet as it should be saved / printed: turned 90 deg clockwise for a 4x6 portrait print."""
    from PIL import Image
    return sheet.transpose(Image.ROTATE_270) if portrait else sheet


# history/<job>/passport_src.<ext>   the customer's original photo, as uploaded (for "Edit" later)
#               passport.json         the editor settings: position / size / angle / flip, colours, border
PASSPORT_SRC_TYPES = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp", "BMP": ".bmp"}
PASSPORT_STATE_KEYS = {"cx": float, "cy": float, "s": float, "a": float, "flip": int, "br": float, "ct": float,
                       "sat": float, "warm": float, "bw": float, "color": str, "guide": bool}


def _passport_state(raw):
    out = {}
    for k, kind in PASSPORT_STATE_KEYS.items():
        if isinstance(raw, dict) and k in raw:
            try:
                out[k] = kind(raw[k])
            except (TypeError, ValueError):
                pass
    if "color" in out and not re.fullmatch(r"#[0-9a-fA-F]{6}", out["color"]):
        out.pop("color")
    if isinstance(raw, dict) and raw.get("orient") == "portrait":    # 4x6 paper fed upright (sheet turned 90 deg)
        out["orient"] = "portrait"
    return out


def _passport_source(value):
    """Original photo (data URL) -> (bytes, extension), checked to be a real picture."""
    import base64
    from PIL import Image
    data = str(value or "")
    if "," in data:
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data)
        with Image.open(io.BytesIO(raw)) as im:
            ext = PASSPORT_SRC_TYPES.get(im.format)
            im.verify()
    except Exception:
        abort(400)
    if not ext:
        abort(400)
    return raw, ext


def _passport_src_path(job_id):
    for p in job_dir(job_id).glob("passport_src.*"):
        return p
    return None


@app.post("/api/passport")
def api_passport():
    """{"image": sheet PNG data URL (1800x1200), "photo": the one photo (413x531), "name": customer / file name,
        "source": the original photo (data URL, may be left out when editing), "state": editor settings,
        "job": <id> to update that passport entry instead of adding a new one}"""
    body = request.get_json(force=True, silent=True) or {}
    sheet = _data_url_image(body.get("image"), PASSPORT_PX)
    photo = _data_url_image(body.get("photo"), PASSPORT_PHOTO_PX)
    src = _passport_source(body["source"]) if body.get("source") else None
    state = _passport_state(body.get("state"))
    portrait = state.get("orient") == "portrait"
    name = " ".join(str(body.get("name") or "").split())[:60] or "Passport photos"
    out_sheet = _passport_orient(sheet, portrait)
    buf = io.BytesIO()
    make_cards.save_jpeg(out_sheet, buf)
    ava = io.BytesIO()
    avatar._square_face(photo).save(ava, "JPEG", quality=88)
    pdf = _passport_pdf(out_sheet, portrait)

    def write_files(job_id):
        d = job_dir(job_id)
        _write_images(job_id, {"Sheet": buf.getvalue()})
        (d / "photo.jpeg").write_bytes(ava.getvalue())
        (d / "sheets.pdf").write_bytes(pdf)
        if src:
            for old in d.glob("passport_src.*"):
                old.unlink()
            (d / f"passport_src{src[1]}").write_bytes(src[0])
        (d / "passport.json").write_text(json.dumps(state), encoding="utf-8")

    job_id = str(body.get("job") or "")
    if job_id:                                       # "Edit": same entry, same job ref, new pictures
        job = get_job(job_id)
        if job.get("type") != "passport":
            abort(400)
        try:
            with JOBS_LOCK:
                write_files(job_id)
                job.update(source=name, details={**(job.get("details") or {}), "name": name},
                           v=int(job.get("v", 0)) + 1, saved=False)
                _write_meta(job_id, job)
        except OSError as e:
            return jsonify({"error": f"Could not store the photos on disk: {e}"}), 500
        return jsonify(public(job))

    if not src:
        return jsonify({"error": "The original photo is missing"}), 400
    job_id = uuid.uuid4().hex
    meta = {"job": job_id, "source": name, "sides": ["Sheet"], "type": "passport", "type_name": "Passport photos",
            "details": {"name": name, "type_label": "PASSPORT"}, "ref": avatar.new_ref(), "has_photo": True, "has_pdf": True, "ration": None,
            "done_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "names": {}, "edited": {}, "saved": False, "v": 0,
            "photo_box": {}, "adjust": {}, "hologram": None, "pan_front": None,
            "passport": {"photos": 8, "photo_mm": [35, 45], "sheet_in": [4, 6] if portrait else [6, 4],
                         "orient": "portrait" if portrait else "landscape"}}
    try:
        job_dir(job_id).mkdir(parents=True, exist_ok=True)
        write_files(job_id)
        _write_meta(job_id, meta)
    except OSError as e:
        shutil.rmtree(job_dir(job_id), ignore_errors=True)
        return jsonify({"error": f"Could not store the photos on disk: {e}"}), 500
    with JOBS_LOCK:
        JOBS[job_id] = meta
    return jsonify(public(meta))


@app.get("/api/passport/<job_id>")
def api_passport_get(job_id):
    """What the passport page needs to edit an entry again: name, editor settings, is the original there."""
    job = get_job(job_id)
    if job.get("type") != "passport":
        abort(404)
    try:
        state = json.loads((job_dir(job_id) / "passport.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    return jsonify({"job": job_id, "name": (job.get("details") or {}).get("name") or job["source"], "ref": job.get("ref"),
                    "state": _passport_state(state), "has_source": _passport_src_path(job_id) is not None})


@app.get("/api/passport/<job_id>/source")
def api_passport_source(job_id):
    job = get_job(job_id)
    p = _passport_src_path(job_id)
    if job.get("type") != "passport" or p is None:
        abort(404)
    resp = send_file(io.BytesIO(p.read_bytes()), mimetype={".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
                                                           ".bmp": "image/bmp"}.get(p.suffix, "application/octet-stream"))
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- card scan (photo / scan of a physical card)
# Upload -> history/_scans/<id>.jpg (upright, max 4200 px; removed after a day) -> corners found -> the page
# moves corners / colours with live previews -> "Add to exports": a normal PVC card job (Front [+ Back]).
# The job keeps the photos + settings (scan_<Side>.jpg, scan.json) so "Edit" can open it again.
SCAN_TMP = HISTORY_DIR / "_scans"
SCAN_CACHE = OrderedDict()                   # id -> PIL image (a few, so dragging corners stays quick)
SCAN_LOCK = threading.Lock()
SCAN_TYPES = ("scan", "aadhaar", "pan", "voter", "dl", "rc", "abha", "pmjay", "ration")
SCAN_SIDE_KEYS = {"rot": int, "trim": float, "auto": bool, "br": float, "ct": float, "sat": float, "sharp": float}


def _scan_clean_old():
    try:
        for p in SCAN_TMP.glob("*.jpg"):
            if time.time() - p.stat().st_mtime > 24 * 3600:
                p.unlink()
    except OSError:
        pass


def _scan_put(img):
    SCAN_TMP.mkdir(parents=True, exist_ok=True)
    _scan_clean_old()
    sid = uuid.uuid4().hex
    img.save(SCAN_TMP / f"{sid}.jpg", "JPEG", quality=95, subsampling=0)
    with SCAN_LOCK:
        SCAN_CACHE[sid] = img
        while len(SCAN_CACHE) > 6:
            SCAN_CACHE.popitem(last=False)
    return sid


def _scan_get(sid):
    from PIL import Image
    if not JOB_ID_RE.fullmatch(str(sid)):
        abort(404)
    with SCAN_LOCK:
        if sid in SCAN_CACHE:
            SCAN_CACHE.move_to_end(sid)
            return SCAN_CACHE[sid]
    p = SCAN_TMP / f"{sid}.jpg"
    if not p.is_file():
        abort(404)
    img = Image.open(p).convert("RGB")
    with SCAN_LOCK:
        SCAN_CACHE[sid] = img
        while len(SCAN_CACHE) > 6:
            SCAN_CACHE.popitem(last=False)
    return img


def _scan_state(raw, img):
    """A side's settings from the page, checked: 4 corners inside (around) the photo + the colour options."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for k, kind in SCAN_SIDE_KEYS.items():
        if k in raw:
            try:
                out[k] = kind(raw[k])
            except (TypeError, ValueError):
                pass
    out["rot"] = 180 if out.get("rot") == 180 else 0
    for k, lo, hi in (("trim", 0, 40), ("br", -60, 60), ("ct", -60, 60), ("sat", -100, 100), ("sharp", 0, 100)):
        if k in out:
            out[k] = max(lo, min(hi, out[k]))
    try:
        c = [[float(x), float(y)] for x, y in raw["corners"]]
        assert len(c) == 4
    except Exception:
        abort(400)
    W, H = img.size
    out["corners"] = [[max(-W, min(2 * W, x)), max(-H, min(2 * H, y))] for x, y in c]
    return out


def _scan_view(sid, img, corners=None, found=None):
    import scan
    if corners is None:
        q, found = scan.find_card(img)
        corners = q.tolist()
    return {"id": sid, "w": img.width, "h": img.height, "corners": corners, "found": found,
            "defaults": {k: v for k, v in scan.DEFAULTS.items()}}


@app.get("/scan")
def scan_page():
    """Card scan: a photo or scan of the customer's own card -> straight PVC card."""
    return send_file(ROOT / "web" / "scan.html")


@app.post("/api/scan/upload")
def api_scan_upload():
    """multipart "file" (JPG / PNG / WebP / BMP) -> {id, w, h, corners (found automatically), found}"""
    import scan
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "Choose a photo of the card"}), 400
    try:
        img = scan.open_upright(f.read())
    except Exception:
        return jsonify({"error": "That file is not a photo this app can read (use JPG or PNG)"}), 400
    sid = _scan_put(img)
    return jsonify(_scan_view(sid, img))


@app.post("/api/scan/detect/<sid>")
def api_scan_detect(sid):
    return jsonify(_scan_view(sid, _scan_get(sid)))


@app.get("/api/scan/src/<sid>")
def api_scan_src(sid):
    """The uploaded photo (upright), smaller for the page: ?max=1600"""
    from PIL import Image
    img = _scan_get(sid)
    m = max(200, min(3000, int(request.args.get("max", 1600))))
    if max(img.size) > m:
        k = m / max(img.size)
        img = img.resize((round(img.width * k), round(img.height * k)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    resp = send_file(io.BytesIO(buf.getvalue()), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@app.post("/api/scan/preview/<sid>")
def api_scan_preview(sid):
    """{corners, rot, trim, auto, br, ct, sat, sharp} -> the straightened card (JPEG)"""
    import scan
    img = _scan_get(sid)
    st = _scan_state(request.get_json(force=True, silent=True), img)
    full = request.args.get("full")
    card = scan.make(img, st) if full else scan.make(img, st, (scan.CARD_W // 2 * 2, scan.CARD_H // 2 * 2))
    buf = io.BytesIO()
    card.save(buf, "JPEG", quality=88)
    resp = send_file(io.BytesIO(buf.getvalue()), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/scan/add")
def api_scan_add():
    """{"sides": {"Front": {id, corners, ...}, "Back": {...} (optional)}, "type": card type, "name": ...,
        "job": <id> to update that scanned card instead of adding a new one}"""
    import scan
    body = request.get_json(force=True, silent=True) or {}
    sides_in = body.get("sides") or {}
    if not isinstance(sides_in, dict) or "Front" not in sides_in:
        return jsonify({"error": "Add the front of the card first"}), 400
    ctype = body.get("type") if body.get("type") in SCAN_TYPES else "scan"
    name = " ".join(str(body.get("name") or "").split())[:60]
    made, states, sources = {}, {}, {}
    for side in ("Front", "Back"):
        s = sides_in.get(side)
        if not s:
            continue
        img = _scan_get(s.get("id"))
        st = _scan_state(s, img)
        made[side] = scan.make(img, st)
        states[side] = st
        sources[side] = SCAN_TMP / f"{s['id']}.jpg"
    type_name = "Scanned card" if ctype == "scan" else CARD_TYPES.get(ctype, ctype)
    label = {"scan": "CARD", "aadhaar": "AADHAAR", "pan": "PAN", "voter": "VOTER", "dl": "DL", "rc": "RC",
             "abha": "ABHA", "pmjay": "AYUSHMAN", "ration": "RATION"}[ctype]
    details = {"name": name, "type_label": label}

    def store(job_id):
        d = job_dir(job_id)
        for p in d.glob("scan_*.jpg"):
            if p.stem[5:] not in sources:
                p.unlink()
        for side, src in sources.items():
            if src.is_file() and src.resolve() != (d / f"scan_{side}.jpg").resolve():
                shutil.copyfile(src, d / f"scan_{side}.jpg")
        (d / "scan.json").write_text(json.dumps(states), encoding="utf-8")

    job_id = str(body.get("job") or "")
    if job_id:                                       # "Edit": same entry, same job ref
        job = get_job(job_id)
        if not job.get("scan"):
            abort(400)
        try:
            with JOBS_LOCK:
                store(job_id)
                d = job_dir(job_id)
                for side in job["sides"]:
                    if side not in made:             # back taken away
                        for p in (d / f"{side}.jpeg", d / f"{side}.orig.jpeg"):
                            if p.is_file():
                                p.unlink()
                        (job.get("adjust") or {}).pop(side, None)
                for side, card in made.items():
                    _replace_side(job_id, job, side, card)   # keeps a colour adjustment made in the card window
                job.update(sides=list(made), type=ctype, type_name=type_name, source=name or "Scanned card",
                           details={**(job.get("details") or {}), **details})
                _write_meta(job_id, job)
        except OSError as e:
            return jsonify({"error": f"Could not store the card on disk: {e}"}), 500
        return jsonify(public(job))

    images = {}
    for side, card in made.items():
        buf = io.BytesIO()
        make_cards.save_jpeg(card, buf)
        images[side] = buf.getvalue()
    job_id = uuid.uuid4().hex
    meta = {"job": job_id, "source": name or "Scanned card", "sides": list(images), "type": ctype, "type_name": type_name,
            "details": details, "ref": avatar.new_ref(), "has_photo": False, "has_pdf": False, "ration": None,
            "done_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "names": {}, "edited": {}, "saved": False, "v": 0,
            "photo_box": {}, "adjust": {}, "hologram": None, "pan_front": None, "scan": True}
    try:
        job_dir(job_id).mkdir(parents=True, exist_ok=True)
        _write_images(job_id, images)
        store(job_id)
        _write_meta(job_id, meta)
    except OSError as e:
        shutil.rmtree(job_dir(job_id), ignore_errors=True)
        return jsonify({"error": f"Could not store the card on disk: {e}"}), 500
    with JOBS_LOCK:
        JOBS[job_id] = meta
    return jsonify(public(meta))


@app.get("/api/scan/job/<job_id>")
def api_scan_job(job_id):
    """Open a scanned card again: its photos go back into the scan store, with the settings used."""
    from PIL import Image
    job = get_job(job_id)
    if not job.get("scan"):
        abort(404)
    d = job_dir(job_id)
    try:
        states = json.loads((d / "scan.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        states = {}
    sides = {}
    for side in job["sides"]:
        p = d / f"scan_{side}.jpg"
        if not p.is_file():
            continue
        img = Image.open(p).convert("RGB")
        sid = _scan_put(img)
        st = states.get(side) or {}
        sides[side] = {**_scan_view(sid, img, st.get("corners"), True), "state": st}
    return jsonify({"job": job_id, "name": (job.get("details") or {}).get("name") or "", "type": job.get("type"),
                    "ref": job.get("ref"), "sides": sides})


@app.post("/api/epds/parse")
def api_epds_parse():
    """Read the EPDS rice card search page the shop copied (Ctrl+A, Ctrl+C) -> members with dates of birth."""
    import ration_card
    try:
        return jsonify(ration_card.parse_epds((request.get_json(force=True) or {}).get("text", "")))
    except ration_card.RationError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/ration/<job_id>")
def api_ration_edit(job_id):
    """Change the Telugu names / address of a ration card and render it again."""
    import ration_card
    job = get_job(job_id)
    if not job.get("ration"):
        abort(404)
    body = request.get_json(force=True) or {}
    try:
        data = _load_ration(job_id)
    except (OSError, ValueError):
        return jsonify({"error": "The ration card data for this job is missing - make the card again"}), 404
    names = body.get("members") or []
    old = data["members"]
    if names and isinstance(names[0], dict):
        # [{i: index in the current list, name, dob}] in the wanted order (head of family stays first)
        new = []
        for e in names:
            i = int(e.get("i", -1))
            if not 0 <= i < len(old):
                continue
            m = dict(old[i])
            if str(e.get("name", "")).strip():
                m["name"] = str(e["name"]).strip()
            dob = str(e.get("dob", "")).strip()
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", dob):
                m["dob_age"] = dob + "/" + m.get("dob_age", "/").split("/", 1)[-1]
            new.append(m)
        heads = [m for m in new if m.get("head")]
        data["members"] = heads + [m for m in new if not m.get("head")] if len(new) == len(old) else old
        names = [m["name"] for m in data["members"]]
    else:
        data["members"] = [dict(m, name=(names[i].strip() if i < len(names) and names[i].strip() else m["name"]))
                           for i, m in enumerate(old)]
    if body.get("head_name", "").strip():
        data["head_name"] = body["head_name"].strip()
        if data["members"]:
            data["members"][0]["name"] = data["members"][0]["name"] if names else data["head_name"]
    if body.get("permanent_address", "").strip():
        data["permanent_address"] = body["permanent_address"].strip()
    front, back = ration_card.render(data)
    images = {}
    for side, img in (("Front", front), ("Back", back)):
        buf = io.BytesIO()
        make_cards.save_jpeg(img, buf)
        images[side] = buf.getvalue()
    with JOBS_LOCK:
        _write_images(job_id, images)
        for side in images:                          # a re-drawn card starts without adjustments
            (job_dir(job_id) / f"{side}.orig.jpeg").unlink(missing_ok=True)
        _save_ration(job_id, data)
        job.update(ration=_ration_summary(data), v=int(job.get("v", 0)) + 1, saved=False, adjust={})
        _write_meta(job_id, job)
    return jsonify({"ok": True})


# ---------------------------------------------------------------- adjust (brightness / contrast ...)
# The first adjustment keeps the untouched card as <Side>.orig.jpeg; every change is made from that
# original (never on top of an earlier change), and Reset puts it back.
ADJ_RANGE = {"brightness": (-60, 60), "contrast": (-60, 60), "saturation": (-100, 100), "sharpness": (0, 100)}


def _adj_params(src):
    out = {}
    for k, (lo, hi) in ADJ_RANGE.items():
        try:
            out[k] = max(lo, min(hi, float(src.get(k, 0) or 0)))
        except (TypeError, ValueError):
            out[k] = 0.0
    return out


def _adj_box(v, size):
    """'x0,y0,x1,y1' or [x0, y0, x1, y1] -> clamped tuple, or None (= whole card)."""
    if not v or v == "card":
        return None
    try:
        x0, y0, x1, y1 = [int(round(float(n))) for n in (v.split(",") if isinstance(v, str) else v)]
    except (TypeError, ValueError):
        return None
    W, H = size
    x0, x1 = sorted((max(0, min(W, x0)), max(0, min(W, x1))))
    y0, y1 = sorted((max(0, min(H, y0)), max(0, min(H, y1))))
    return (x0, y0, x1, y1) if x1 - x0 >= 8 and y1 - y0 >= 8 else None


def apply_adjust(img, p, box=None):
    from PIL import ImageEnhance
    img = img.convert("RGB")
    part = img.crop(box) if box else img
    if p["brightness"]:
        part = ImageEnhance.Brightness(part).enhance(1 + p["brightness"] / 100)
    if p["contrast"]:
        part = ImageEnhance.Contrast(part).enhance(1 + p["contrast"] / 100)
    if p["saturation"]:
        part = ImageEnhance.Color(part).enhance(1 + p["saturation"] / 100)
    if p["sharpness"]:
        part = ImageEnhance.Sharpness(part).enhance(1 + p["sharpness"] / 50)
    if not box:
        return part
    out = img.copy()
    out.paste(part, box[:2])
    return out


def _adjust_source(job_id, side):
    """The untouched card image (original if it was adjusted before)."""
    from PIL import Image
    job = get_job(job_id)
    if side not in job["sides"] or job.get("has_pdf"):
        abort(404)                                   # A4 laminated sheets: see the _lam_ functions below
    orig = job_dir(job_id) / f"{side}.orig.jpeg"
    data = (orig if orig.is_file() else job_dir(job_id) / f"{side}.jpeg").read_bytes()   # no open file handle
    return Image.open(io.BytesIO(data))


# Laminated Aadhaar: the sheets are a vector PDF. Only the face pictures inside it are changed (same face
# on Full, Both and Small, so one adjustment covers every sheet); the untouched PDF is kept as
# sheets.orig.pdf and every change is made from it.
def _lam_source(job_id):
    d = job_dir(job_id)
    p = d / "sheets.orig.pdf"
    return (p if p.is_file() else d / "sheets.pdf").read_bytes()


def _lam_preview(job_id, side, p):
    """Front card of one sheet, rendered from the untouched PDF, with the face adjusted."""
    import pymupdf
    from PIL import Image
    job = get_job(job_id)
    if side not in job["sides"]:
        abort(404)
    page = pymupdf.open(stream=_lam_source(job_id), filetype="pdf")[job["sides"].index(side)]
    import aadhaar_laminate_pdf as V
    clip, face = V.card_clip(page)
    dpi = 220
    pix = page.get_pixmap(dpi=dpi, clip=clip, alpha=False)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    if face is not None:
        k = dpi / 72
        img = apply_adjust(img, p, tuple(round(v) for v in ((face.x0 - clip.x0) * k, (face.y0 - clip.y0) * k,
                                                            (face.x1 - clip.x0) * k, (face.y1 - clip.y0) * k)))
    return img


def _lam_save(job_id, job, body):
    import aadhaar_laminate_pdf as V
    import pymupdf
    d = job_dir(job_id)
    orig_pdf = d / "sheets.orig.pdf"
    if body.get("reset"):
        if orig_pdf.is_file():
            os.replace(orig_pdf, d / "sheets.pdf")
        for side in job["sides"]:
            o = d / f"{side}.orig.jpeg"
            if o.is_file():
                os.replace(o, d / f"{side}.jpeg")
        job["adjust"] = {}
        return
    p = _adj_params(body)
    try:
        pdf = V.adjust_faces(_lam_source(job_id), lambda im: apply_adjust(im, p))
    except ValueError as e:
        abort(422, str(e))
    if not orig_pdf.is_file():
        shutil.copyfile(d / "sheets.pdf", orig_pdf)
        for side in job["sides"]:
            shutil.copyfile(d / f"{side}.jpeg", d / f"{side}.orig.jpeg")
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    for i, side in enumerate(job["sides"]):           # the website / Save use these JPEGs
        buf = io.BytesIO()
        make_cards.save_jpeg(V.to_image(_one_page(doc, i)), buf)
        (d / f"{side}.jpeg").write_bytes(buf.getvalue())
    (d / "sheets.pdf").write_bytes(pdf)
    job["adjust"] = {s: dict(p, box=None) for s in job["sides"]}


def _one_page(doc, i):
    import pymupdf
    one = pymupdf.open()
    one.insert_pdf(doc, from_page=i, to_page=i)
    return one


@app.get("/api/adjust/<job_id>/<side>")
def api_adjust_preview(job_id, side):
    """Preview only (nothing saved): ?brightness=..&contrast=..&saturation=..&sharpness=..&box=x0,y0,x1,y1"""
    if get_job(job_id).get("has_pdf"):
        img = _lam_preview(job_id, side, _adj_params(request.args))
    else:
        src = _adjust_source(job_id, side)
        img = apply_adjust(src, _adj_params(request.args), _adj_box(request.args.get("box"), src.size))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    resp = send_file(io.BytesIO(buf.getvalue()), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/adjust/<job_id>")
def api_adjust_save(job_id):
    """{"side", "brightness", "contrast", "saturation", "sharpness", "box": [..] | null} or {"side", "reset": true}
    Laminated Aadhaar: the face on every sheet changes (box is ignored)."""
    body = request.get_json(force=True) or {}
    side = str(body.get("side", ""))
    job = get_job(job_id)
    if job.get("has_pdf"):
        with JOBS_LOCK:
            _lam_save(job_id, job, body)
            job.update(v=int(job.get("v", 0)) + 1, saved=False)
            _write_meta(job_id, job)
        return jsonify(public(job))
    src = _adjust_source(job_id, side)
    job, d = get_job(job_id), job_dir(job_id)
    orig, cur = d / f"{side}.orig.jpeg", d / f"{side}.jpeg"
    with JOBS_LOCK:
        if body.get("reset"):
            if orig.is_file():
                os.replace(orig, cur)
            job.setdefault("adjust", {}).pop(side, None)
        else:
            p, box = _adj_params(body), _adj_box(body.get("box"), src.size)
            if not orig.is_file():
                shutil.copyfile(cur, orig)
            buf = io.BytesIO()
            make_cards.save_jpeg(apply_adjust(src, p, box), buf)
            cur.write_bytes(buf.getvalue())
            job.setdefault("adjust", {})[side] = dict(p, box=list(box) if box else None)
        job.update(v=int(job.get("v", 0)) + 1, saved=False)
        _write_meta(job_id, job)
    return jsonify(public(job))


@app.post("/api/hologram/<job_id>")
def api_hologram(job_id):
    """{"level": "A".."F"} - redraw the PAN back's hologram sticker at that brightness.
    An adjusted back keeps its adjustment (it is applied again on top of the new sticker)."""
    from PIL import Image
    import pan_rc
    job = get_job(job_id)
    holo, level = job.get("hologram"), str((request.get_json(force=True) or {}).get("level", ""))
    if not holo or level not in HOLOGRAM_LEVELS:
        return jsonify({"error": "This card has no hologram to change" if not holo else "Unknown level"}), 400
    d, side = job_dir(job_id), holo["side"]
    try:
        base = Image.open(io.BytesIO((d / f"{side}.holo_base.png").read_bytes()))
    except OSError:
        return jsonify({"error": "This card was made before hologram levels existed - upload the PDF again"}), 409
    card = pan_rc.place_hologram(base, holo, level)
    with JOBS_LOCK:
        _replace_side(job_id, job, side, card)
        job["hologram"] = dict(holo, level=level)
        _write_meta(job_id, job)
    return jsonify(public(job))


def _replace_side(job_id, job, side, card):
    """A redrawn card side becomes the untouched card; an adjustment made before is applied again
    on top of it. Bumps the version (call with JOBS_LOCK held, then _write_meta)."""
    d = job_dir(job_id)
    adj = (job.get("adjust") or {}).get(side)
    if adj:
        buf = io.BytesIO(); make_cards.save_jpeg(card, buf); (d / f"{side}.orig.jpeg").write_bytes(buf.getvalue())
        card = apply_adjust(card, _adj_params(adj), tuple(adj["box"]) if adj.get("box") else None)
    buf = io.BytesIO(); make_cards.save_jpeg(card, buf); (d / f"{side}.jpeg").write_bytes(buf.getvalue())
    job.update(v=int(job.get("v", 0)) + 1, saved=False)


# ---------------------------------------------------------------- PAN front (name gaps, signature)
# history/<job>/<Side>.pf_base.png  card without name / father's name / signature
#               <Side>.pf_name.png  <Side>.pf_father.png  (alpha masks)   <Side>.pf_sign.png (RGBA ink)
PF_PARTS = ("base", "name", "father", "sign")


def _save_pf_layers(job_id, side, base, layers):
    d = job_dir(job_id)
    base.save(d / f"{side}.pf_base.png")
    for k in ("name", "father", "sign"):
        if layers.get(k) is not None:
            layers[k].save(d / f"{side}.pf_{k}.png")


def _load_pf(job_id):
    from PIL import Image
    job = get_job(job_id)
    pf = job.get("pan_front")
    if not pf:
        abort(404)
    d, side = job_dir(job_id), pf["side"]
    def load(k):
        p = d / f"{side}.pf_{k}.png"
        return Image.open(io.BytesIO(p.read_bytes())) if p.is_file() else None
    base = load("base")
    if base is None:
        abort(409)
    layers = {k: load(k) for k in ("name", "father", "sign")}
    layers.update(box=pf.get("box"), aspect=pf.get("aspect"))
    return job, pf, base, layers


@app.get("/api/panfront/<job_id>")
def api_panfront_preview(job_id):
    """Preview only (nothing saved): ?name_gap=..&father_gap=..&sign_size=..&sign_x=..&sign_y=..
    &sign_strength=..&sign_contrast=..&sign_dark=..&sign_color=original|black|blue"""
    import pan_rc
    job, pf, base, layers = _load_pf(job_id)
    img = pan_rc.compose_front(base, layers, request.args.to_dict())
    adj = (job.get("adjust") or {}).get(pf["side"])
    if adj:                                          # show it as it will be saved
        img = apply_adjust(img, _adj_params(adj), tuple(adj["box"]) if adj.get("box") else None)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    resp = send_file(io.BytesIO(buf.getvalue()), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/panfront/<job_id>")
def api_panfront_save(job_id):
    """{settings..., "as_default": bool} or {"reset": true} -> redraw the PAN front.
    as_default: new PAN cards start with these settings too."""
    import pan_rc
    body = request.get_json(force=True) or {}
    job, pf, base, layers = _load_pf(job_id)
    params = pan_rc.front_params({} if body.get("reset") else body)
    card = pan_rc.compose_front(base, layers, params)
    with JOBS_LOCK:
        _replace_side(job_id, job, pf["side"], card)
        job["pan_front"] = dict(pf, params=params)
        _write_meta(job_id, job)
    if body.get("as_default"):
        s = load_settings()
        s["pan_front"] = params
        save_settings(s)
    return jsonify(public(job) | {"default": pan_rc.front_params(load_settings().get("pan_front"))})


@app.get("/api/photo/<job_id>")
def api_photo(job_id):
    job = get_job(job_id)
    p = job_dir(job_id) / "photo.jpeg"
    if not job.get("has_photo") or not p.is_file():
        abort(404)
    resp = send_file(io.BytesIO(p.read_bytes()), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=3600"
    return resp


@app.get("/api/download/<job_id>/<side>")
def api_download(job_id, side):
    data = get_image(job_id, side)
    name = clean_name(request.args.get("name"), load_settings()["extension"])
    return send_file(io.BytesIO(data), mimetype="image/jpeg", as_attachment=True,
                     download_name=name)


@app.post("/api/zip")
def api_zip():
    body = request.get_json(force=True) or {}
    items = body.get("items", [])
    ext = load_settings()["extension"]
    buf = io.BytesIO()
    used = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for it in items:
            name = clean_name(it.get("name"), ext)
            base, n = name, 2
            while name.lower() in used:
                name = f"{Path(base).stem} ({n}){ext}"
                n += 1
            used.add(name.lower())
            z.writestr(name, get_image(it["job"], it["side"]))
    buf.seek(0)
    zip_name = clean_name(body.get("zip_name") or "card-exports", ".zip")
    return send_file(buf, mimetype="application/zip", as_attachment=True,
                     download_name=zip_name)


@app.post("/api/save")
def api_save():
    body = request.get_json(force=True) or {}
    s = load_settings()
    try:
        folder, saved = write_items(body.get("items", []), s["save_folder"], s["extension"])
    except OSError as e:
        return jsonify({"error": f"Could not save: {e}"}), 500
    with JOBS_LOCK:                                  # remember the "Saved" tick
        for job_id in {str(it.get("job")) for it in body.get("items", [])}:
            if job_id in JOBS and not JOBS[job_id].get("saved"):
                JOBS[job_id]["saved"] = True
                _write_meta(job_id, JOBS[job_id])
    return jsonify({"folder": folder, "saved": saved})


# ---------------------------------------------------------------- plugins
# Each folder in plugins/ with a plugin.json is an optional feature. Delete the folder to
# remove it; switch it on/off in Settings. Plugins only get the small HostAPI below.
PLUGIN_DIR = ROOT / "plugins"
PLUGINS = OrderedDict()          # id -> {"manifest": dict, "module": module | None, "error": str}


class HostAPI:
    def __init__(self, pid):
        self.id = pid
        self.dir = PLUGIN_DIR / pid
        self.app = app

    @property
    def data_dir(self):
        """Folder for a plugin's own data about customers' cards (kept with the history, never in git)."""
        d = HISTORY_DIR / "_plugins" / self.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def enabled(self):
        return plugin_enabled(self.id)

    def get_image(self, job_id, side):
        return get_image(job_id, side)                      # 404s for unknown jobs

    def get_pdf(self, job_id):
        return get_pdf(job_id)                              # 404s for unknown jobs

    def pvc_a4_pdf(self, job_ids):
        """A4 PDF with each PVC card's front + back side by side at real size (4 cards per page)."""
        return a4_pdf(job_ids)

    def job_info(self, job_id):
        job = get_job(job_id)
        return {"source": job["source"], "sides": list(job["sides"]), "type": job.get("type"),
                "name": (job.get("details") or {}).get("name") or job["source"]}

    def job_exists(self, job_id):
        with JOBS_LOCK:
            return str(job_id) in JOBS

    def load_config(self, default):
        try:
            return {**default, **json.loads((self.dir / "config.json").read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            return dict(default)

    def save_config(self, cfg):
        (self.dir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def plugin_enabled(pid):
    p = PLUGINS.get(pid)
    if not p or p.get("error"):
        return False
    return bool(load_settings().get("plugins", {}).get(pid, p["manifest"].get("enabled_by_default", True)))


def load_plugins():
    import importlib.util
    if not PLUGIN_DIR.is_dir():
        return
    for d in sorted(PLUGIN_DIR.iterdir()):
        mf = d / "plugin.json"
        if not mf.is_file():
            continue
        try:
            manifest = json.loads(mf.read_text(encoding="utf-8"))
            pid = manifest["id"]
            if not re.fullmatch(r"[a-z0-9_]+", pid) or pid != d.name:
                raise ValueError("plugin id must match its folder name (a-z, 0-9, _)")
            spec = importlib.util.spec_from_file_location(f"cps_plugin_{pid}", d / manifest.get("entry", "plugin.py"))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            mod.register(HostAPI(pid))
            PLUGINS[pid] = {"manifest": manifest, "module": mod, "error": ""}
            print(f"  plugin loaded: {manifest.get('name', pid)}")
        except Exception as e:                  # a broken plugin never stops the app
            pid = d.name
            PLUGINS[pid] = {"manifest": {"id": pid, "name": pid}, "module": None, "error": str(e)}
            print(f"  plugin {pid} failed to load: {e}")


@app.get("/api/plugins")
def api_plugins():
    out = []
    for pid, p in PLUGINS.items():
        m = p["manifest"]
        out.append({"id": pid, "name": m.get("name", pid), "description": m.get("description", ""),
                    "version": m.get("version", ""), "enabled": plugin_enabled(pid),
                    "ui": bool(m.get("ui")) and not p["error"], "error": p["error"]})
    return jsonify(out)


@app.post("/api/plugins/<pid>")
def api_plugin_toggle(pid):
    if pid not in PLUGINS:
        abort(404)
    s = load_settings()
    s.setdefault("plugins", {})[pid] = bool((request.get_json(force=True) or {}).get("enabled"))
    save_settings(s)
    return jsonify({"id": pid, "enabled": plugin_enabled(pid)})


@app.get("/plugins/<pid>/<path:name>")
def plugin_static(pid, name):
    """Front-end files a plugin ships (only the file named in its manifest 'ui')."""
    p = PLUGINS.get(pid)
    if not p or p["error"] or name != p["manifest"].get("ui") or not plugin_enabled(pid):
        abort(404)
    return send_file(PLUGIN_DIR / pid / name, max_age=0)


load_plugins()
load_history()
app.secret_key = _secret_key()                 # signs the "PIN entered" cookie of other PCs


@app.errorhandler(413)
def too_big(_e):
    return jsonify({"error": "File is too large (max 60 MB)"}), 413


if __name__ == "__main__":
    url = f"http://127.0.0.1:{PORT}"
    s = load_settings()
    if s.get("network") and s.get("network_pin"):
        BIND_HOST = "0.0.0.0"
        others = "\n".join(f"      {n}: http://{ip}:{PORT}" for n, ip in lan_addresses()) or "      (no network found)"
        print(f"\n  Card Print Studio running at {url}\n  Shared on the network - other PCs open (PIN needed):\n{others}"
              + ("" if _firewall_rule_exists() else
                 "\n  ! Windows Firewall may block other PCs: Settings -> Network sharing -> Allow through firewall")
              + "\n  (close this window to stop)\n")
    else:
        print(f"\n  Card Print Studio running at {url}  (this PC only)\n  (close this window to stop)\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host=BIND_HOST, port=PORT, debug=False, threaded=True)
