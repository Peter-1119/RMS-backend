# media.py
from flask import Blueprint, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import os, tempfile, uuid, subprocess
from datetime import datetime
from config import UPLOAD_ROOT_DIR, ALLOWED_EXTS, DRAWIO_CLI_PATH
from db import db

bp = Blueprint("uploads", __name__)

def allowed_file(fn): 
    return '.' in fn and fn.rsplit('.',1)[1].lower() in ALLOWED_EXTS

@bp.get("/<path:filename>")
def serve_file(filename):
    full_path = os.path.join(UPLOAD_ROOT_DIR, filename)
    if not os.path.exists(full_path):
        return "File Not Found", 404
    return send_from_directory(UPLOAD_ROOT_DIR, filename)

@bp.post("/image")
def upload_image():
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file part"}), 400
    f = request.files['file']
    if not f or f.filename == '':
        return jsonify({"success": False, "message": "No selected file"}), 400
    if not allowed_file(f.filename):
        return jsonify({"success": False, "message": "File type not allowed"}), 400

    token = (request.args.get("token") or "").strip()
    original = secure_filename(f.filename)
    ext = '.' + original.rsplit('.', 1)[1].lower() if '.' in original else ''
    subdir = os.path.join(UPLOAD_ROOT_DIR, 'temp', datetime.now().strftime('%Y/%m/%d'))
    os.makedirs(subdir, exist_ok=True)

    fd, abs_path = tempfile.mkstemp(dir=subdir, suffix=ext); os.close(fd)
    f.save(abs_path)

    abstract_path = os.path.relpath(abs_path, UPLOAD_ROOT_DIR).replace(os.sep,'/')
    browser_url   = f"/uploads/{abstract_path}"

    asset_id = str(uuid.uuid4())
    try:
        with db() as (conn, cur):
            cur.execute("""
              INSERT INTO rms_assets (asset_id, storage_key, mime_type, byte_size, created_at)
              VALUES (%s,%s,%s,%s,NOW())
            """, (asset_id, f"uploads/{abstract_path}", f.mimetype, os.path.getsize(abs_path)))
            if token:
                cur.execute("""
                  INSERT INTO rms_asset_links (asset_id, document_token, content_id, created_at)
                  VALUES (%s, %s, %s, NOW())
                """, (asset_id, token, None))
    except Exception:
        asset_id = None

    payload = {"success": True, "url": browser_url, "path_to_save": abstract_path}
    if asset_id: payload["asset_id"] = asset_id
    return jsonify(payload), 200

@bp.post("/drawio")
def upload_drawio_and_convert():
    # accept either 'file' or 'drawioFile'
    f = request.files.get('file') or request.files.get('drawioFile')
    if not f:
        return jsonify({"success": False, "message": "No drawio file"}), 400
    if not f.filename.lower().endswith(".drawio"):
        return jsonify({"success": False, "message": "Not a .drawio file"}), 400

    token = (request.args.get("token") or "").strip()

    original = secure_filename(f.filename)
    ext = ('.' + original.rsplit('.', 1)[1].lower()) if '.' in original else ''
    daydir = os.path.join(UPLOAD_ROOT_DIR, 'temp', datetime.now().strftime('%Y/%m/%d'))
    os.makedirs(daydir, exist_ok=True)

    # 1) save input .drawio
    fd, in_path = tempfile.mkstemp(dir=daydir, suffix=".drawio")
    os.close(fd)
    f.save(in_path)

    # 2) choose an output name (DO NOT pre-create a file)
    fd, out_path = tempfile.mkstemp(dir=daydir, suffix=".png")
    os.close(fd)

    try:
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.run(
            [DRAWIO_CLI_PATH, "-x", in_path, "--format", "png", "--output", out_path],
            check=True, capture_output=True, text=True, creationflags=no_window
        )
    except FileNotFoundError:
        try: os.remove(in_path)
        except: pass
        return jsonify({"success": False, "message": "DRAWIO_CLI_PATH not found. Check config.DRAWIO_CLI_PATH"}), 500
    except subprocess.CalledProcessError as e:
        try: os.remove(in_path)
        except: pass
        return jsonify({"success": False, "message": f"Draw.io convert failed: {e.stderr or e}"}), 500

    # keep only the PNG; input can be removed
    try: os.remove(in_path)
    except: pass
    if not os.path.exists(out_path):
        return jsonify({"success": False, "message": "Conversion produced no PNG"}), 500

    # 3) persist PNG as an asset
    abstract_path = os.path.relpath(out_path, UPLOAD_ROOT_DIR).replace(os.sep, '/')
    browser_url   = f"/uploads/{abstract_path}"
    asset_id = str(uuid.uuid4())
    try:
        with db() as (conn, cur):
            cur.execute("""
              INSERT INTO rms_assets (asset_id, storage_key, mime_type, byte_size, created_at)
              VALUES (%s,%s,%s,%s,NOW())
            """, (asset_id, f"uploads/{abstract_path}", "image/png", os.path.getsize(out_path)))
            if token:
                cur.execute("""
                  INSERT INTO rms_asset_links (asset_id, document_token, content_id, created_at)
                  VALUES (%s, %s, %s, NOW())
                """, (asset_id, token, None))
    except Exception:
        asset_id = None

    payload = {"success": True, "url": browser_url, "path_to_save": abstract_path}
    if asset_id: payload["asset_id"] = asset_id
    return jsonify(payload), 200
