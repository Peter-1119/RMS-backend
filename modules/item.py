# modules/item.py
from flask import Blueprint, request, jsonify
from oracle_db import ora_cursor

bp = Blueprint("item", __name__, url_prefix="/item")

WHERE_PREFIX = "REGEXP_LIKE(p.PROCESS_NAME, '^\([LR][0-8][[:digit:]]{2}-[A-Z]?[[:digit:]]{2}\)') AND p.PROCESS_NAME NOT LIKE '%人工%' AND sm.ENABLED = 'Y' AND sm.EQM_ID <> 'NA'"
@bp.get("/search")
def search():
    specific = request.args.get("specific")
    machine  = request.args.get("machine")
    keyword  = request.args.get("keyword")

    print(f"specific: {specific}, machine: {machine}, keyword: {keyword}")

    if not any([specific, machine, keyword]):
        return jsonify({"success": False, "error": "keyword, specific, or machine is required"}), 400

    target_specs = []
    
    # 1. 如果有指定機台，先找出該機台對應的「製程代碼 (PROCESS_DESC)」
    if machine:
        try:
            with ora_cursor(db_alias="machine_db") as cur:
                # [修正 1] 改為查詢 PROCESS_DESC
                # EZFLEX_ROUTING 中的 KTSCH 通常是對應製程代碼 (字串, 如 'L260-38')，而非 ID (數字)
                
                # [修正 2] 移除 t.SFHNR 和 t.WERKS 篩選
                # SAJET.SYS_TERMINAL 通常是機台配置表，不會有 SFHNR (式樣) 或 WERKS (廠區) 這些品目欄位
                # 這些條件應該保留在下方的 EZFLEX_TOOL 查詢中
                
                # [修正 3] 補上機台篩選條件 sm.MACHINE_CODE
                sql = f"""
                    SELECT DISTINCT p.PROCESS_DESC FROM SAJET.SYS_PROCESS p
                    JOIN SAJET.SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
                    JOIN SAJET.SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
                    WHERE {WHERE_PREFIX} 
                    AND sm.MACHINE_CODE = :m_code
                """
                
                cur.execute(sql, m_code=machine)
                target_specs = [row[0] for row in cur.fetchall()]

                if not target_specs:
                    return jsonify({"success": False, "data": {"message": "該機台目前沒有設定適用製程"}}), 404
                    
        except Exception as e:
             print("Error finding machine specs:", e)
             return jsonify({"success": False, "error": "Database error checking machine"}), 500

    # 2. 查詢品目 (Items)
    try:
        with ora_cursor(db_alias = "item_db") as cur:
            # 基礎 SQL
            sql = """
                SELECT DISTINCT 
                    CASE 
                        WHEN INSTR(t.MATNR, '-') > 0 THEN SUBSTR(t.MATNR, 1, INSTR(t.MATNR, '-') - 1) 
                        ELSE t.MATNR 
                    END AS MATNR_BASE 
                FROM IDBUSER.EZFLEX_ROUTING r
                JOIN IDBUSER.EZFLEX_TOOL t ON r.MATNR = t.MATNR AND r.REVLV = t.REVLV AND r.VORNR = t.VORNR
                WHERE t.SFHNR LIKE '%-ST%' AND t.WERKS = '1011'
            """
            
            conditions = []
            binds = {}

            # 條件：指定製程 (specific)
            if specific:
                # [修正 4] 使用 bind variable 避免 SQL Injection
                conditions.append("r.KTSCH = :specific")
                binds['specific'] = specific

            # 條件：指定機台 (machine) -> 轉為限制 KTSCH 範圍
            if machine:
                if target_specs:
                    # [修正 5] 修正 IN 語法錯誤
                    # Python list 不能直接 f-string 進 SQL，需轉為 'A','B','C' 格式
                    specs_str = "'" + "','".join(target_specs) + "'"
                    conditions.append(f"r.KTSCH IN ({specs_str})")
                else:
                    # 如果機台有指定但找不到製程，理論上這裡不該發生(上面已擋)，但作為保險
                    conditions.append("1=0") 

            # 條件：關鍵字 (keyword)
            if keyword:
                conditions.append("t.MATNR LIKE :keyword")
                binds['keyword'] = f"%{keyword}%"

            # 組合 SQL
            if conditions:
                sql += " AND " + " AND ".join(conditions)

            sql += " ORDER BY MATNR_BASE"

            cur.execute(sql, binds)
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
                SELECT SUBSTR(T.SFHNR, 1, LENGTH(T.SFHNR) - 1) AS STYLE_NO, MAX(SUBSTR(T.SFHNR, -1, 1)) AS VERSION FROM IDBUSER.EZFLEX_TOOL T
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

        # --- 新增的排序邏輯 ---
        def get_st_number(item):
            try:
                # 切割 '-ST' 並取後面的部分轉為整數
                suffix = item['styleNo'].split('-ST')[1]
                return int(suffix)
            except (IndexError, ValueError):
                # 如果資料異常(沒有 -ST 或後方不是純數字)，預設回傳 0 排在最前面
                return 0

        # 針對 styles 陣列，依照提取出的數值由小到大排序
        styles.sort(key=get_st_number)

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
                SELECT DISTINCT P.PROCESS_NAME, R.KTSCH AS PROCESS_DESC, COUNT(DISTINCT T.VORNR) AS VORNR_COUNT FROM IDBUSER.EZFLEX_ROUTING R
                JOIN IDBUSER.EZFLEX_TOOL T ON R.MATNR = T.MATNR AND R.REVLV = T.REVLV AND R.VORNR = T.VORNR
                JOIN IDBUSER.RMS_SYS_PROCESS P ON P.PROCESS_DESC = R.KTSCH
                WHERE 
                    T.SFHNR = :sfhnr 
                    AND T.SFHNR LIKE '%-ST%' 
                    AND (CASE WHEN INSTR(T.MATNR, '-') > 0 THEN SUBSTR(T.MATNR, 1, INSTR(T.MATNR, '-') - 1) ELSE T.MATNR END) = :matnr 
                    AND T.WERKS = '1011'
                    -- ★ 關鍵過濾：只選取「版本號」最大的 MATNR
                    AND SUBSTR(T.MATNR, INSTR(T.MATNR, '-') + 1, 2) = (
                        SELECT MAX(SUBSTR(T2.MATNR, INSTR(T2.MATNR, '-') + 1, 2))
                        FROM IDBUSER.EZFLEX_TOOL T2
                        WHERE T2.SFHNR = :sfhnr 
                        AND (CASE WHEN INSTR(T2.MATNR, '-') > 0 THEN SUBSTR(T2.MATNR, 1, INSTR(T2.MATNR, '-') - 1) ELSE T2.MATNR END) = :matnr
                    )
                GROUP BY P.PROCESS_NAME, R.KTSCH
                ORDER BY R.KTSCH
            """
            cur.execute(sql, {"matnr": matnr, "sfhnr": sfhnr})

            specification = []
            for process_name, process_desc, step_count in cur:
                specification.append({"code": process_desc, "name": process_name, "step_count": step_count})

        return jsonify({"success": True, "data": {"specification": specification}})
    except Exception as e:
        print("Error in /item/processes:", e)
        return jsonify({"success": False, "error": str(e)}), 500


# @bp.get("/search")
# def search():
#     specific = request.args.get("specific")
#     machine  = request.args.get("machine")
#     keyword  = request.args.get("keyword")

#     print(f"specific: {specific}, machine: {machine}, keyword: {keyword}")

#     if not any([specific, machine, keyword]):
#         return jsonify({"success": False, "error": "keyword, specific, or machine is required"}), 400

#     target_specs = []
    
#     # 1. 如果有指定機台，先找出該機台對應的「製程代碼 (PROCESS_DESC)」
#     if machine:
#         try:
#             with ora_cursor(db_alias="machine_db") as cur:
#                 # [修正 1] 改為查詢 PROCESS_DESC
#                 # EZFLEX_ROUTING 中的 KTSCH 通常是對應製程代碼 (字串, 如 'L260-38')，而非 ID (數字)
                
#                 # [修正 2] 移除 t.SFHNR 和 t.WERKS 篩選
#                 # SAJET.SYS_TERMINAL 通常是機台配置表，不會有 SFHNR (式樣) 或 WERKS (廠區) 這些品目欄位
#                 # 這些條件應該保留在下方的 EZFLEX_TOOL 查詢中
                
#                 # [修正 3] 補上機台篩選條件 sm.MACHINE_CODE
#                 sql = f"""
#                     SELECT DISTINCT p.PROCESS_DESC FROM SAJET.SYS_PROCESS p
#                     JOIN SAJET.SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
#                     JOIN SAJET.SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
#                     WHERE {WHERE_PREFIX} 
#                     AND sm.MACHINE_CODE = :m_code
#                 """
                
#                 cur.execute(sql, m_code=machine)
#                 target_specs = [row[0] for row in cur.fetchall()]

#                 if not target_specs:
#                     return jsonify({"success": False, "data": {"message": "該機台目前沒有設定適用製程"}}), 404
                    
#         except Exception as e:
#              print("Error finding machine specs:", e)
#              return jsonify({"success": False, "error": "Database error checking machine"}), 500

#     # 2. 查詢品目 (Items)
#     try:
#         with ora_cursor(db_alias = "item_db") as cur:
#             table_name = 'EZFLEX."KKME_Table"'
#             sql = f"SELECT DISTINCT CASE WHEN INSTR(ITEM, '-') > 0 THEN SUBSTR(ITEM, 1, INSTR(ITEM, '-') - 1) ELSE ITEM END AS MATNR FROM {table_name} WHERE 1=1"
            
#             conditions = []
#             binds = {}

#             # 條件：指定製程 (specific)
#             if specific:
#                 # [修正 4] 使用 bind variable 避免 SQL Injection
#                 conditions.append("STATION = :specific")
#                 binds['specific'] = specific

#             # 條件：指定機台 (machine) -> 轉為限制 KTSCH 範圍
#             if machine:
#                 if target_specs:
#                     # [修正 5] 修正 IN 語法錯誤
#                     # Python list 不能直接 f-string 進 SQL，需轉為 'A','B','C' 格式
#                     specs_str = "'" + "','".join(target_specs) + "'"
#                     conditions.append(f"STATION IN ({specs_str})")
#                 else:
#                     # 如果機台有指定但找不到製程，理論上這裡不該發生(上面已擋)，但作為保險
#                     conditions.append("1=0") 

#             # 條件：關鍵字 (keyword)
#             if keyword:
#                 conditions.append("ITEM LIKE :keyword")
#                 binds['keyword'] = f"%{keyword}%"

#             # 組合 SQL
#             if conditions:
#                 sql += " AND " + " AND ".join(conditions)

#             sql += " ORDER BY MATNR"

#             print(f"sql: {sql}")
#             cur.execute(sql, binds)
#             items = [item[0] for item in cur.fetchall()]

#         return jsonify({"success": True, "data": {"items": items}})

#     except Exception as e:
#         print("Error in /item/search:", e)
#         return jsonify({"success": False, "error": str(e)}), 500

# @bp.get("/styles")
# def list_styles():
#     matnr = (request.args.get("matnr") or "").strip()

#     if not matnr:
#         return jsonify({"success": False, "error": "matnr is required"}), 400

#     try:
#         with ora_cursor(db_alias = "item_db") as cur:
#             # sql = """
#             #     SELECT SUBSTR(T.SFHNR, 1, LENGTH(T.SFHNR) - 1) AS STYLE_NO, MAX(SUBSTR(T.SFHNR, -1, 1)) AS VERSION FROM IDBUSER.EZFLEX_TOOL T
#             #     WHERE
#             #         (CASE
#             #             WHEN INSTR(T.MATNR, '-') > 0 THEN
#             #                 SUBSTR(T.MATNR, 1, INSTR(T.MATNR, '-') - 1)
#             #             ELSE
#             #                 T.MATNR
#             #          END) = :matnr
#             #       AND T.SFHNR LIKE '%-ST%' AND T.WERKS = '1011'
#             #     GROUP BY SUBSTR(T.SFHNR, 1, LENGTH(T.SFHNR) - 1)
#             #     ORDER BY STYLE_NO
#             # """
#             table_name = 'EZFLEX."KKME_Table"'
#             sql = f"SELECT SUBSTR(BOOK, 1, LENGTH(BOOK) - 1) AS STYLE_NO, MAX(SUBSTR(BOOK, -1, 1)) AS VERSION FROM {table_name} WHERE (CASE WHEN INSTR(ITEM, '-') > 0 THEN SUBSTR(ITEM, 1, INSTR(ITEM, '-') - 1) ELSE ITEM END) = :matnr GROUP BY SUBSTR(BOOK, 1, LENGTH(BOOK) - 1) ORDER BY STYLE_NO"
#             print(f"sql: {sql}")
#             cur.execute(sql, {"matnr": matnr})

#             styles = []
#             for style_no, version in cur:
#                 full_sfhnr = f"{style_no}{version}"
#                 styles.append({
#                     "sfhnr": full_sfhnr,   # 完整 SFHNR（含版本）
#                     "styleNo": style_no,   # 去掉最後一碼
#                     "version": version,    # 最後一碼
#                 })

#         return jsonify({"success": True, "data": {"styles": styles}})
#     except Exception as e:
#         print("Error in /item/styles:", e)
#         return jsonify({"success": False, "error": str(e)}), 500

# @bp.get("/processes")
# def list_processes():
#     matnr = (request.args.get("matnr") or "").strip()
#     sfhnr = (request.args.get("sfhnr") or "").strip()

#     if not matnr or not sfhnr:
#         return jsonify({"success": False, "error": "matnr and sfhnr are required"}), 400

#     try:
#         with ora_cursor(db_alias = "item_db") as cur:
#             # table_name = 'EZFLEX."KKME_Table"'
#             # sql = f"SELECT DISTINCT STATION_CH, CASE WHEN INSTR(STATION, '(') > 0 THEN SUBSTR(STATION, 1, INSTR(STATION, '(') - 1) ELSE STATION END AS STATION, COUNT(DISTINCT SEQ) AS VORNR_COUNT FROM {table_name} WHERE BOOK = :sfhnr AND (CASE WHEN INSTR(ITEM, '-') > 0 THEN SUBSTR(ITEM, 1, INSTR(ITEM, '-') - 1) ELSE ITEM END) = :matnr GROUP BY STATION_CH, STATION ORDER BY STATION_CH"
#             sql = """
#                 SELECT DISTINCT 
#                     STATION_CH, 
#                     CASE WHEN INSTR(STATION, '(') > 0 THEN SUBSTR(STATION, 1, INSTR(STATION, '(') - 1) ELSE STATION END AS STATION, 
#                     COUNT(DISTINCT SEQ) AS VORNR_COUNT 
#                 FROM EZFLEX."KKME_Table"
#                 WHERE 
#                     BOOK = :sfhnr 
#                     AND (CASE WHEN INSTR(ITEM, '-') > 0 THEN SUBSTR(ITEM, 1, INSTR(ITEM, '-') - 1) ELSE ITEM END) = :matnr
#                     AND SUBSTR(ITEM, INSTR(ITEM, '-') + 1, 2) = (
#                         SELECT MAX(SUBSTR(T2.ITEM, INSTR(T2.ITEM, '-') + 1, 2))
#                         FROM EZFLEX."KKME_Table" T2
#                         WHERE T2.BOOK = :sfhnr 
#                         AND (CASE WHEN INSTR(T2.ITEM, '-') > 0 THEN SUBSTR(T2.ITEM, 1, INSTR(T2.ITEM, '-') - 1) ELSE T2.ITEM END) = :matnr
#                     )
#                 GROUP BY STATION_CH, STATION 
#                 ORDER BY STATION_CH
#             """
#             cur.execute(sql, {"matnr": matnr, "sfhnr": sfhnr})

#             specification = []
#             for process_name, process_desc, step_count in cur:
#                 specification.append({"code": process_desc, "name": f"({process_desc}){process_name}", "step_count": step_count})

#         return jsonify({"success": True, "data": {"specification": specification}})
#     except Exception as e:
#         print("Error in /item/processes:", e)
#         return jsonify({"success": False, "error": str(e)}), 500
