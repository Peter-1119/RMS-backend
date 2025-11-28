# modules/item.py
from flask import Blueprint, request, jsonify
from oracle_db import ora_cursor

bp = Blueprint("item", __name__, url_prefix="/item")

@bp.get("/search")
def search():
    specific = request.args.get("specific")
    machine  = request.args.get("machine")
    keyword = request.args.get("keyword")

    print(f"specific: {specific}, machine: {machine}, keyword: {keyword}")

    if not any([specific, machine, keyword]):
        return jsonify({"success": False, "error": "keyword is required"}), 400

    try:
        with ora_cursor() as cur:
            sql = """
                SELECT DISTINCT CASE WHEN INSTR(t.MATNR, '-') > 0 THEN SUBSTR(t.MATNR, 1, INSTR(t.MATNR, '-') - 1) ELSE t.MATNR END AS MATNR_BASE FROM IDBUSER.EZFLEX_ROUTING r
                JOIN IDBUSER.EZFLEX_TOOL t ON r.MATNR = t.MATNR AND r.REVLV = t.REVLV AND r.VORNR = t.VORNR
                JOIN IDBUSER.RMS_SYS_PROCESS p ON r.KTSCH = p.PROCESS_DESC
                JOIN IDBUSER.RMS_SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
                JOIN IDBUSER.RMS_SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
                WHERE REGEXP_LIKE(p.PROCESS_NAME, '^\([LR][0-8][[:digit:]]{2}-[[:digit:]]{2}\)') AND p.PROCESS_NAME NOT LIKE '%人工%' AND sm.ENABLED = 'Y' AND sm.EQM_ID <> 'NA' AND t.SFHNR LIKE '%-ST%' AND t.WERKS = '1011'
            """

            if specific != None:
                sql += f" AND p.PROCESS_DESC = '{specific}'"

            if machine != None:
                sql += f" AND sm.MACHINE_CODE = '{machine}'"

            if keyword != None:
                sql += f" AND t.MATNR LIKE '%{keyword}%'"

            sql += " ORDER BY MATNR_BASE"
            cur.execute(sql)

            items = [item[0] for item in cur.fetchall()]

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
                  AND T.SFHNR LIKE '%-ST%' AND T.WERKS = '1011'
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
                SELECT DISTINCT P.PROCESS_NAME, R.KTSCH AS PROCESS_DESC FROM IDBUSER.EZFLEX_ROUTING R
                JOIN IDBUSER.EZFLEX_TOOL T ON R.MATNR = T.MATNR AND R.REVLV = T.REVLV AND R.VORNR = T.VORNR
                JOIN IDBUSER.RMS_SYS_PROCESS P ON P.PROCESS_DESC = R.KTSCH
                WHERE T.SFHNR = :sfhnr AND T.SFHNR LIKE '%-ST%' AND (CASE WHEN INSTR(T.MATNR, '-') > 0 THEN SUBSTR(T.MATNR, 1, INSTR(T.MATNR, '-') - 1) ELSE T.MATNR END) = :matnr AND T.WERKS = '1011'
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
