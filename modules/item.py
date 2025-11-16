# modules/item.py
from flask import Blueprint, request, jsonify
from oracle_db import ora_cursor  # 依照你實際的檔名調整

bp = Blueprint("item", __name__, url_prefix="/item")


@bp.get("/search")
def search():
    keyword = (request.args.get("keyword") or "").strip()
    item    = (request.args.get("item") or "").strip()
    specific = (request.args.get("specific") or "").strip()
    machine  = (request.args.get("machine") or "").strip()

    if not keyword and not item:
        return jsonify({"success": False, "error": "keyword is required"}), 400

    try:
        with ora_cursor() as cur:
            base_sql = """
                SELECT
                  P.PROCESS_NAME,
                  R.KTSCH AS PROCESS_DESC,
                  T.MATNR,
                  T.REVLV,
                  T.VORNR,
                  T.SFHNR
                FROM IDBUSER.EZFLEX_ROUTING R
                JOIN IDBUSER.EZFLEX_TOOL   T
                  ON R.MATNR = T.MATNR
                 AND R.REVLV = T.REVLV
                 AND R.VORNR = T.VORNR
                JOIN IDBUSER.RMS_SYS_PROCESS P
                  ON P.PROCESS_DESC = R.KTSCH
                WHERE T.SFHNR LIKE '%-ST%'
            """
            binds = {}

            if item:
                base_sql += " AND T.MATNR = :item"
                binds["item"] = item
            else:
                base_sql += " AND T.MATNR LIKE :kw"
                binds["kw"] = f"%{keyword}%"

            # 若指定製程，只留下該製程的 routing
            if specific:
                base_sql += " AND R.KTSCH = :spc"
                binds["spc"] = specific

            # 若指定機台，透過 process → terminal → machine 過濾掉跟該機台無關的製程
            if machine:
                base_sql += """
                  AND EXISTS (
                    SELECT 1
                    FROM IDBUSER.RMS_SYS_PROCESS p
                    JOIN IDBUSER.RMS_SYS_TERMINAL t
                      ON t.PROCESS_ID = p.PROCESS_ID
                    JOIN IDBUSER.RMS_SYS_MACHINE m
                      ON m.PDLINE_ID = t.PDLINE_ID
                    WHERE p.PROCESS_DESC = R.KTSCH
                      AND m.MACHINE_CODE = :mcode
                  )
                """
                binds["mcode"] = machine

            base_sql += " ORDER BY T.MATNR, R.KTSCH"

            cur.execute(base_sql, binds)

            items_map = {}
            for process_name, process_desc, matnr, revlv, vornr, sfhnr in cur:
                if matnr not in items_map:
                    items_map[matnr] = {"matnr": matnr, "specifications": []}
                spec_obj = {
                    "code": process_desc,
                    "name": process_name,
                    "sfhnr": sfhnr[:-1],
                    "version": sfhnr[-1],
                }
                if spec_obj not in items_map[matnr]["specifications"]:
                    items_map[matnr]["specifications"].append(spec_obj)

            items = list(items_map.values())

        return jsonify({"success": True, "data": {"items": items}})
    except Exception as e:
        print("Error in /item/search:", e)
        return jsonify({"success": False, "error": str(e)}), 500
