# modules/dcc.py
from __future__ import annotations
from flask import Blueprint, request, jsonify
from oracle_db import ora_cursor

bp = Blueprint("dcc", __name__)

def _fetch_dcc(keyword: str | None, page: int, page_size: int):
    """
    從 IDBUSER.RMS_DCC 取出 DCCNO, DCCNAME，支援：
      - keyword: 模糊搜尋 (DCCNO / DCCNAME, 不分大小寫)
      - page, page_size: 分頁
    回傳:
      {
        "rows": [ { "dccno": ..., "dccname": ... }, ... ],
        "total": 1234,
        "page": page,
        "page_size": page_size
      }
    """

    # 安全一點先修正 page / page_size 範圍
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 20), 200))  # 每次最多 200 筆，你可以自己調

    where_sql = " WHERE 1 = 1"
    params = {}

    if keyword:
        where_sql += " AND (LOWER(DCCNO) LIKE :kw OR LOWER(DCCNAME) LIKE :kw)"
        params["kw"] = f"%{keyword.lower()}%"

    # 1) 先算 total 筆數
    count_sql = f"""
        SELECT COUNT(*)
        FROM IDBUSER.RMS_DCC
        {where_sql}
    """

    with ora_cursor() as cur:
        cur.execute(count_sql, params)
        total_rows = cur.fetchone()[0]

        # 如果沒資料就直接回傳空
        if total_rows == 0:
            return {
                "rows": [],
                "total": 0,
                "page": page,
                "page_size": page_size,
            }

        # 2) 再查當前頁的資料（ROWNUM 分頁）
        #   start_row: 0-based index
        #   rnum: 1-based index
        start_row = (page - 1) * page_size
        end_row = page * page_size

        data_sql = f"""
            SELECT * FROM (
                SELECT q.*, ROWNUM AS rnum FROM (
                    SELECT DCCNO, DCCNAME
                    FROM IDBUSER.RMS_DCC
                    {where_sql}
                    ORDER BY DCCNO
                ) q
                WHERE ROWNUM <= :max_row
            )
            WHERE rnum > :min_row
        """

        data_params = dict(params)
        data_params["max_row"] = end_row
        data_params["min_row"] = start_row

        cur.execute(data_sql, data_params)
        rows = cur.fetchall()

    return {
        "rows": [
            {"dccno": r[0], "dccname": r[1]}
            for r in rows
        ],
        "total": total_rows,
        "page": page,
        "page_size": page_size,
    }


@bp.get("/forms")
def search_forms():
    """
    GET /dcc/forms?keyword=xxx&page=1&page_size=20
    """
    keyword = (request.args.get("keyword") or "").strip()
    page = request.args.get("page", "1")
    page_size = request.args.get("page_size", "20")

    try:
        result = _fetch_dcc(keyword or None, page, page_size)
        return jsonify({
            "success": True,
            "data": result["rows"],
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
        })
    except Exception as e:
        print("search_forms error:", e)
        return jsonify({"success": False, "error": str(e)}), 500


@bp.get("/docs")
def search_docs():
    """
    GET /dcc/docs?keyword=xxx&page=1&page_size=20
    """
    keyword = (request.args.get("keyword") or "").strip()
    page = request.args.get("page", "1")
    page_size = request.args.get("page_size", "20")

    try:
        result = _fetch_dcc(keyword or None, page, page_size)
        return jsonify({
            "success": True,
            "data": result["rows"],
            "total": result["total"],
            "page": result["page"],
            "page_size": result["page_size"],
        })
    except Exception as e:
        print("search_docs error:", e)
        return jsonify({"success": False, "error": str(e)}), 500
