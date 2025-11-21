# modules/item.py
from flask import Blueprint, request, jsonify
from oracle_db import ora_cursor

bp = Blueprint("item", __name__, url_prefix="/item")


@bp.get("/search")
def search():
    keyword = (request.args.get("keyword") or "").strip()
    # 保留 item/specific/machine 參數，但這支只看 keyword
    # item    = (request.args.get("item") or "").strip()
    # specific = (request.args.get("specific") or "").strip()
    # machine  = (request.args.get("machine") or "").strip()

    if not keyword:
        return jsonify({"success": False, "error": "keyword is required"}), 400

    try:
        with ora_cursor() as cur:
            sql = """
                SELECT DISTINCT
                    CASE WHEN INSTR(MATNR, '-') > 0 THEN SUBSTR(MATNR, 1, INSTR(MATNR, '-') - 1) ELSE MATNR END AS MATNR_BASE
                FROM IDBUSER.EZFLEX_TOOL
                WHERE MATNR LIKE :kw AND SFHNR LIKE '%-ST%'
                ORDER BY MATNR_BASE
            """
            cur.execute(sql, {"kw": f"%{keyword}%"})

            items = []
            for (matnr_base,) in cur:
                items.append({
                    "matnr": matnr_base,
                })

        return jsonify({"success": True, "data": {"items": items}})
    except Exception as e:
        print("Error in /item/search:", e)
        return jsonify({"success": False, "error": str(e)}), 500

@bp.get("/styles")
def list_styles():
    matnr = (request.args.get("matnr") or "").strip()

    if not matnr:
        return jsonify({"success": False, "error": "matnr is required"}), 400

    try:
        with ora_cursor() as cur:
            sql = """
                SELECT
                    SUBSTR(T.SFHNR, 1, LENGTH(T.SFHNR) - 1) AS STYLE_NO,
                    MAX(SUBSTR(T.SFHNR, -1, 1)) AS VERSION
                FROM IDBUSER.EZFLEX_TOOL T
                WHERE
                    (CASE
                        WHEN INSTR(T.MATNR, '-') > 0 THEN
                            SUBSTR(T.MATNR, 1, INSTR(T.MATNR, '-') - 1)
                        ELSE
                            T.MATNR
                     END) = :matnr
                  AND T.SFHNR LIKE '%-ST%'
                GROUP BY SUBSTR(T.SFHNR, 1, LENGTH(T.SFHNR) - 1)
                ORDER BY STYLE_NO
            """
            cur.execute(sql, {"matnr": matnr})

            styles = []
            for style_no, version in cur:
                full_sfhnr = f"{style_no}{version}"
                styles.append({
                    "sfhnr": full_sfhnr,   # 完整 SFHNR（含版本）
                    "styleNo": style_no,   # 去掉最後一碼
                    "version": version,    # 最後一碼
                })

        return jsonify({"success": True, "data": {"styles": styles}})
    except Exception as e:
        print("Error in /item/styles:", e)
        return jsonify({"success": False, "error": str(e)}), 500

@bp.get("/processes")
def list_processes():
    matnr = (request.args.get("matnr") or "").strip()
    sfhnr = (request.args.get("sfhnr") or "").strip()

    if not matnr or not sfhnr:
        return jsonify({"success": False, "error": "matnr and sfhnr are required"}), 400

    try:
        with ora_cursor() as cur:
            sql = """
                SELECT DISTINCT
                    P.PROCESS_NAME,
                    R.KTSCH AS PROCESS_DESC
                FROM IDBUSER.EZFLEX_ROUTING R
                JOIN IDBUSER.EZFLEX_TOOL   T
                  ON R.MATNR = T.MATNR
                 AND R.REVLV = T.REVLV
                 AND R.VORNR = T.VORNR
                JOIN IDBUSER.RMS_SYS_PROCESS P
                  ON P.PROCESS_DESC = R.KTSCH
                WHERE T.SFHNR = :sfhnr
                  AND T.SFHNR LIKE '%-ST%'
                  AND (CASE
                        WHEN INSTR(T.MATNR, '-') > 0 THEN
                            SUBSTR(T.MATNR, 1, INSTR(T.MATNR, '-') - 1)
                        ELSE
                            T.MATNR
                      END) = :matnr
                ORDER BY R.KTSCH
            """
            cur.execute(sql, {"matnr": matnr, "sfhnr": sfhnr})

            specification = []
            for process_name, process_desc in cur:
                specification.append({
                    "code": process_desc,
                    "name": process_name,
                })

        return jsonify({"success": True, "data": {"specification": specification}})
    except Exception as e:
        print("Error in /item/processes:", e)
        return jsonify({"success": False, "error": str(e)}), 500
