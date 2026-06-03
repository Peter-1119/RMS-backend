# modules/dcc.py
from __future__ import annotations
import math
from flask import Blueprint, request, jsonify
from utils import send_response
from oracle_db import ora_cursor

bp = Blueprint("dcc", __name__)

base_condition = {"form": "^FM-", "document": "^W[WMQ]", "qua": "^WQ"}
@bp.get("/docs")
def search_docs():
    document_type = request.args.get("documentType")
    keyword = request.args.get("keyword", "").strip()
    page = int(request.args.get("page", 1))
    pageSize = int(request.args.get("pageSize", 10))
    getPages = request.args.get("getPages", "false").lower() == "true"

    print(f"document_type: {document_type}")
    print(f"keyword: {document_type}")
    print(f"page: {page}")
    print(f"pageSize: {pageSize}")
    print(f"getPages: {getPages}")

    if document_type is None:
        return jsonify({"success": False, "error": "Please input document type you want to search"}), 400

    if document_type not in base_condition:
        return jsonify({"success": False, "error": "Invalid document type"}), 400

    base = base_condition[document_type]

    # bind 變數（避免 SQL injection）
    params = {"base": base}
    kw = None
    if keyword:
        kw = f"%{keyword}%"
    params["kw"] = kw  # 讓 SQL 可以用 :kw IS NULL 判斷

    try:
        with ora_cursor() as cur:
            # 先算總筆數 / pages
            if getPages:
                sql_count = "SELECT COUNT(*) FROM IDBUSER.RMS_DCC t WHERE REGEXP_LIKE(t.DCCNO, :base) AND (:kw IS NULL OR t.DCCNO LIKE :kw OR t.DCCNAME LIKE :kw)"
                cur.execute(sql_count, params)
                return send_response(200, True, "查詢成功", {"pages": math.ceil(cur.fetchone()[0] / pageSize) if pageSize > 0 else 0})

            # 分頁查詢
            startRow = (page - 1) * pageSize + 1
            endRow = page * pageSize
            params.update({"startRow": startRow, "endRow": endRow})

            sql_page = """
                SELECT dccno, dccname FROM (
                  SELECT t.DCCNO AS dccno, t.DCCNAME AS dccname, ROW_NUMBER() OVER (ORDER BY t.DCCNO) AS rn FROM IDBUSER.RMS_DCC t
                  WHERE REGEXP_LIKE(t.DCCNO, :base) AND (:kw IS NULL OR t.DCCNO LIKE :kw OR t.DCCNAME LIKE :kw)
                )
                WHERE rn BETWEEN :startRow AND :endRow
                ORDER BY rn
            """
            cur.execute(sql_page, params)
            data = [{"dccno": r[0], "dccname": r[1]} for r in cur.fetchall()]
            return jsonify({"success": True, "data": data}), 200

    except Exception as e:
        print("DB error:", e)
        return send_response(500, False, "查詢失敗", {"message": "資料庫查詢失敗，請重新嘗試"})