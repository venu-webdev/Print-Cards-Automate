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
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
from collections import OrderedDict
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file

import avatar
import make_cards

ROOT = Path(__file__).resolve().parent
SETTINGS_FILE = ROOT / "settings.json"
DEFAULT_SETTINGS = {
    "save_folder": str(ROOT / "export"),
    "auto_save": False,
    "name_pattern": "{type}_{name}_{id4}_{side}",
    "extension": ".jpeg",
}
CARD_TYPES = make_cards.CARD_TYPES
MAX_JOBS = 100

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # 60 MB per upload
app.json.sort_keys = False      # keep card types in our order (Auto first)

JOBS = OrderedDict()          # job_id -> {"source": str, "images": {side: jpeg bytes}}
JOBS_LOCK = threading.Lock()
PROCESS_LOCK = threading.Lock()   # one PDF at a time (CPU heavy)


# ---------------------------------------------------------------- settings
def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    if s.get("name_pattern") in ("{name}-{side}", "", None):    # old default -> new clear names
        s["name_pattern"] = DEFAULT_SETTINGS["name_pattern"]
    return s


def save_settings(s):
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")


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


def get_image(job_id, side):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or side not in job["images"]:
        abort(404)
    return job["images"][side]


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
    return jsonify(load_settings() | {"card_types": CARD_TYPES})


@app.post("/api/settings")
def api_set_settings():
    s = load_settings()
    body = request.get_json(force=True) or {}
    for k in DEFAULT_SETTINGS:
        if k in body:
            s[k] = body[k]
    if s.get("extension") not in (".jpeg", ".jpg"):
        s["extension"] = ".jpeg"
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
                data, card_type, password, f.filename, request.form.get("rc_no") or None)
    except make_cards.PasswordNeeded:
        msg = "Wrong password - try again" if password else "This PDF is locked - enter its password"
        return jsonify({"error": msg, "needs_password": True}), 401
    except Exception as e:  # bad / unexpected PDF
        return jsonify({"error": f"Could not process this PDF: {e}"}), 422
    if not pages:
        return jsonify({"error": "The PDF has no pages"}), 422

    images = {}
    pdf = None
    for side, img in pages:
        pdf = pdf or img.info.get("cps_pdf")
        buf = io.BytesIO()
        make_cards.save_jpeg(img, buf)
        images[side] = buf.getvalue()

    photo = details.pop("_photo", None)
    ration = details.pop("_ration", None)
    ref = avatar.new_ref()                       # public reference, unrelated to job_id
    job_id = uuid.uuid4().hex
    with JOBS_LOCK:
        JOBS[job_id] = {"source": stem, "images": images, "photo": photo, "pdf": pdf, "ration": ration}
        while len(JOBS) > MAX_JOBS:
            JOBS.popitem(last=False)

    return jsonify({"job": job_id, "source": stem, "sides": list(images), "type": used_type,
                    "type_name": CARD_TYPES.get(used_type, used_type), "details": details,
                    "ref": ref, "has_photo": photo is not None, "has_pdf": pdf is not None,
                    "ration": {"head_name": ration["head_name"], "permanent_address": ration["permanent_address"],
                               "members": [{k: m.get(k, "") for k in ("name", "name_en", "dob_age", "relation", "head")}
                                           for m in ration["members"]]}
                    if ration else None,
                    "done_at": time.strftime("%Y-%m-%dT%H:%M:%S")})


@app.get("/api/image/<job_id>/<side>")
def api_image(job_id, side):
    data = get_image(job_id, side)
    resp = send_file(io.BytesIO(data), mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "max-age=3600"
    return resp


@app.get("/api/pdf/<job_id>")
def api_pdf(job_id):
    """Print-quality PDF (A4 laminated sheets: Full, Both, Small as pages)."""
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or not job.get("pdf"):
        abort(404)
    name = clean_name(request.args.get("name") or job["source"], ".pdf")
    return send_file(io.BytesIO(job["pdf"]), mimetype="application/pdf", as_attachment=True, download_name=name)


@app.post("/api/ration/<job_id>")
def api_ration_edit(job_id):
    """Change the Telugu names / address of a ration card and render it again."""
    import ration_card
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or not job.get("ration"):
        abort(404)
    body = request.get_json(force=True) or {}
    data = dict(job["ration"])
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
        job["ration"], job["images"] = data, images
    return jsonify({"ok": True})


@app.get("/api/photo/<job_id>")
def api_photo(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job or not job.get("photo"):
        abort(404)
    resp = send_file(io.BytesIO(job["photo"]), mimetype="image/jpeg")
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

    def enabled(self):
        return plugin_enabled(self.id)

    def get_image(self, job_id, side):
        return get_image(job_id, side)                      # 404s for unknown jobs

    def get_pdf(self, job_id):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job or not job.get("pdf"):
            abort(404)
        return job["pdf"]

    def job_info(self, job_id):
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        if not job:
            abort(404)
        return {"source": job["source"], "sides": list(job["images"])}

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


@app.errorhandler(413)
def too_big(_e):
    return jsonify({"error": "File is too large (max 60 MB)"}), 413


if __name__ == "__main__":
    url = "http://127.0.0.1:5000"
    print(f"\n  Card Print Studio running at {url}\n  (close this window to stop)\n")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=5000, debug=False, threaded=True)
