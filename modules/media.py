# media.py
from flask import Blueprint, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import os, tempfile, uuid, subprocess, platform
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

# @bp.post("/image")
# def upload_image():
#     if 'file' not in request.files:
#         return jsonify({"success": False, "message": "No file part"}), 400
#     f = request.files['file']
#     if not f or f.filename == '':
#         return jsonify({"success": False, "message": "No selected file"}), 400
#     if not allowed_file(f.filename):
#         return jsonify({"success": False, "message": "File type not allowed"}), 400

#     token = (request.args.get("token") or "").strip()
#     original = secure_filename(f.filename)
#     ext = '.' + original.rsplit('.', 1)[1].lower() if '.' in original else ''
#     subdir = os.path.join(UPLOAD_ROOT_DIR, 'temp', datetime.now().strftime('%Y/%m/%d'))
#     os.makedirs(subdir, exist_ok=True)

#     fd, abs_path = tempfile.mkstemp(dir=subdir, suffix=ext); os.close(fd)
#     f.save(abs_path)

#     abstract_path = os.path.relpath(abs_path, UPLOAD_ROOT_DIR).replace(os.sep,'/')
#     browser_url   = f"/uploads/{abstract_path}"

#     asset_id = str(uuid.uuid4())
#     try:
#         with db() as (conn, cur):
#             cur.execute("""
#               INSERT INTO rms_assets (asset_id, storage_key, mime_type, byte_size, created_at)
#               VALUES (%s,%s,%s,%s,NOW())
#             """, (asset_id, f"uploads/{abstract_path}", f.mimetype, os.path.getsize(abs_path)))
#             if token:
#                 cur.execute("""
#                   INSERT INTO rms_asset_links (asset_id, document_token, content_id, created_at)
#                   VALUES (%s, %s, %s, NOW())
#                 """, (asset_id, token, None))
#     except Exception:
#         asset_id = None

#     payload = { "success": True, "url": browser_url, "path_to_save": abstract_path, "download_url": browser_url,}
#     if asset_id:
#         payload["asset_id"] = asset_id
#     return jsonify(payload), 200

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

    abstract_path = os.path.relpath(abs_path, UPLOAD_ROOT_DIR).replace(os.sep, '/')
    browser_url   = f"/uploads/{abstract_path}"
    download_url  = f"/uploads/download/{abstract_path}"   # ★ 專用下載路徑

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

    payload = {"success": True, "url": browser_url, "path_to_save": abstract_path, "download_url": download_url}
    if asset_id:
        payload["asset_id"] = asset_id
    return jsonify(payload), 200

@bp.get("/download/<path:filename>")
def download_file(filename):
    full_path = os.path.join(UPLOAD_ROOT_DIR, filename)
    if not os.path.exists(full_path):
        return "File Not Found", 404
    return send_from_directory(UPLOAD_ROOT_DIR, filename, as_attachment=True)


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

    # 2) choose an output name
    fd, out_path = tempfile.mkstemp(dir=daydir, suffix=".png")
    os.close(fd)

    try:
        if platform.system() == "Windows":
            print("Window drawio to png")
            subprocess.run(
                [DRAWIO_CLI_PATH, "-x", in_path, "--format", "png", "--output", out_path],
                check=True, capture_output=True, text=True, creationflags=0
            )
        elif platform.system() == "Linux":
            print("Ubuntu drawio to png")
            subprocess.run(
                ["xvfb-run", DRAWIO_CLI_PATH, "-x", in_path, "--format", "png", "--output", out_path, "--no-sandbox"],
                check=True, capture_output=True, text=True
            )
    except FileNotFoundError:
        # 轉檔失敗也不要亂刪，讓管理員可以排查
        return jsonify({"success": False, "message": "DRAWIO_CLI_PATH not found. Check config.DRAWIO_CLI_PATH"}), 500
    except subprocess.CalledProcessError as e:
        return jsonify({"success": False, "message": f"Draw.io convert failed: {e.stderr or e}"}), 500

    if not os.path.exists(out_path):
        return jsonify({"success": False, "message": "Conversion produced no PNG"}), 500

    # 3) 計算路徑（PNG + 原始 drawio 都保留）
    abstract_path = os.path.relpath(out_path, UPLOAD_ROOT_DIR).replace(os.sep, '/')
    browser_url   = f"/uploads/{abstract_path}"
    download_url  = f"/uploads/download/{abstract_path}"  # 使用同一個下載端點

    source_abstract_path = os.path.relpath(in_path, UPLOAD_ROOT_DIR).replace(os.sep, '/')
    source_url           = f"/uploads/download/{source_abstract_path}"

    # 4) persist PNG as an asset（你如果也想把 .drawio 存進 rms_assets 可以再加一組 INSERT）
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

    payload = {"success": True, "url": browser_url, "path_to_save": abstract_path, "download_url": source_url}
    # payload = {
    #     "success": True,
    #     "url": browser_url,                # PNG 圖片給 preview 用
    #     "path_to_save": abstract_path,
    #     "source_url": source_url,          # 原始 .drawio 的下載 URL
    #     "source_path": source_abstract_path,
    # }
    if asset_id:
        payload["asset_id"] = asset_id
    return jsonify(payload), 200
