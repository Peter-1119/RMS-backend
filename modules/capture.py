# modules/capture.py
import os, json, uuid, datetime
from flask import Blueprint, request, jsonify, send_file, abort

bp_capture = Blueprint("capture", __name__)

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "_captures", "docs"))
os.makedirs(BASE_DIR, exist_ok=True)

def _id() -> str:
    return f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

def _dir(payload_id: str) -> str:
    d = os.path.join(BASE_DIR, payload_id)
    os.makedirs(d, exist_ok=True)
    return d

@bp_capture.post("/capture-request")
def capture_request():
    """
    Save request arguments (JSON or form) to a timestamped folder:
      - meta.json  (headers and mode)
      - payload.json (attribute/content/reference)
      - files/*     (optional if multipart)
    Returns: { ok: true, payload_id }
    """
    try:
        payload_id = _id()
        d = _dir(payload_id)

        meta = {
            "payload_id": payload_id,
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "content_type": request.content_type,
            "mode": "json" if request.is_json else "form",
            "headers": {k: v for k, v in request.headers.items()},
        }
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        if request.is_json:
            body = request.get_json(silent=True) or {}
            # Expecting {attribute, content, reference}
            with open(os.path.join(d, "payload.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "attribute": body.get("attribute", []),
                    "content":   body.get("content", []),
                    "reference": body.get("reference", []),
                    # files are not in JSON mode
                }, f, ensure_ascii=False, indent=2)

        else:
            # Form-data mode (optional you may use later)
            import shutil
            attribute = json.loads(request.form.get("attribute", "[]"))
            content   = json.loads(request.form.get("content", "[]"))
            reference = json.loads(request.form.get("reference", "[]"))
            files_meta = {}
            if request.files:
                files_dir = os.path.join(d, "files")
                os.makedirs(files_dir, exist_ok=True)
                for key, fs in request.files.items():
                    name = fs.filename or key
                    safe = f"{key}__{name}"
                    dst = os.path.join(files_dir, safe)
                    fs.save(dst)
                    files_meta[key] = {"filename": name, "path": f"files/{safe}"}
            with open(os.path.join(d, "payload.json"), "w", encoding="utf-8") as f:
                json.dump({
                    "attribute": attribute,
                    "content":   content,
                    "reference": reference,
                    "files":     files_meta
                }, f, ensure_ascii=False, indent=2)

        return jsonify({"ok": True, "payload_id": payload_id})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@bp_capture.get("/capture/<payload_id>")
def get_capture(payload_id):
    d = os.path.join(BASE_DIR, payload_id)
    p = os.path.join(d, "payload.json")
    if not os.path.isfile(p):
        return jsonify({"ok": False, "error": "not found"}), 404
    with open(p, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))

@bp_capture.get("/captures")
def list_captures():
    items = []
    for name in sorted(os.listdir(BASE_DIR)):
        meta_path = os.path.join(BASE_DIR, name, "meta.json")
        if os.path.isfile(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            items.append(meta)
    return jsonify({"ok": True, "items": items[-100:]})
