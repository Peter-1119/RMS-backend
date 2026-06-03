# modules/item.py
import io
import datetime
import xlsxwriter
from flask import Blueprint, request, jsonify, send_file
from oracle_db import ora_cursor
from db import db

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
    
    # 1. 取得機台對應的製程代碼 (這段 SAJET 查詢邏輯維持原樣)
    if machine:
        try:
            with ora_cursor(db_alias="machine_db") as cur:
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

    # 2. 查詢品目 (Items) - 👉 全面替換為新 Table 與欄位
    try:
        with ora_cursor(db_alias = "item_db") as cur:
            # 基礎 SQL (MATNR 換成 WIP_ID)
            sql = """
                SELECT DISTINCT 
                    CASE 
                        WHEN INSTR(r.WIP_ID, '-') > 0 THEN SUBSTR(r.WIP_ID, 1, INSTR(r.WIP_ID, '-') - 1) 
                        ELSE r.WIP_ID 
                    END AS MATNR_BASE 
                FROM IDBUSER.PM_WIPPATH r
                JOIN IDBUSER.PM_WIPMOLD t 
                  ON r.FACTORY_ID = t.FACTORY AND r.WIP_ID = t.WIP_ID AND r.PROC_SCHL_SEQ = t.PROC_ITEM_SEQ AND r.SUB_WIP = t.SUB_WIP
                WHERE t.MTRL_ID LIKE '%-ST%' AND r.FACTORY_ID = '1011'
            """
            
            conditions = []
            binds = {}

            # 條件：指定製程 (KTSCH 換成 PROC_ID)
            if specific:
                conditions.append("r.PROC_ID = :specific")
                binds['specific'] = specific

            # 條件：指定機台 -> 轉為限制 PROC_ID 範圍
            if machine:
                if target_specs:
                    specs_str = "'" + "','".join(target_specs) + "'"
                    conditions.append(f"r.PROC_ID IN ({specs_str})")
                else:
                    conditions.append("1=0") 

            # 條件：關鍵字 (MATNR 換成 WIP_ID)
            if keyword:
                conditions.append("r.WIP_ID LIKE :keyword")
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
        # ==========================================
        # 1. Oracle Search item type and style no
        # ==========================================
        with ora_cursor() as cur:
            # 👉 轉換成 PM_WIPMOLD 與 PM_WIPPATH，並移除對 RMS_SYS_PROCESS 的依賴
            sql = """
                WITH BASE_T AS (
                    -- 1. 先抓出符合 WIP_ID (料號) 的所有 Mold/Tool
                    SELECT T.WIP_ID AS MATNR,
                           T.MTRL_ID AS SFHNR,
                           SUBSTR(T.MTRL_ID, 1, LENGTH(T.MTRL_ID) - 1) AS STYLE_BASE,
                           SUBSTR(T.MTRL_ID, -1, 1) AS STYLE_VER,
                           T.FACTORY,
                           T.PROC_ITEM_SEQ,
                           T.SUB_WIP
                    FROM IDBUSER.PM_WIPMOLD T
                    WHERE T.FACTORY = '1011'
                      AND T.MTRL_ID LIKE '%-ST%'
                      AND (CASE WHEN INSTR(T.WIP_ID, '-') > 0 THEN SUBSTR(T.WIP_ID, 1, INSTR(T.WIP_ID, '-') - 1) ELSE T.WIP_ID END) = :matnr
                ),
                MAX_MATNR AS (
                    -- 2. 直接針對每一個獨立的 MTRL_ID (包含舊版與新版)，找出最大的料號版本
                    SELECT SFHNR, MAX(SUBSTR(MATNR, INSTR(MATNR, '-') + 1, 2)) AS MAX_M_VER
                    FROM BASE_T
                    GROUP BY SFHNR
                ),
                FINAL_TOOLS AS (
                    -- 3. 只留下每個 MTRL_ID 的最新料號版本 Mold/Tool
                    SELECT B.SFHNR, B.STYLE_BASE, B.STYLE_VER, B.MATNR, B.FACTORY, B.PROC_ITEM_SEQ, B.SUB_WIP
                    FROM BASE_T B
                    JOIN MAX_MATNR M ON B.SFHNR = M.SFHNR AND SUBSTR(B.MATNR, INSTR(B.MATNR, '-') + 1, 2) = M.MAX_M_VER
                )
                -- 4. 與 Path 進行 JOIN，直接取得 PROC_NAME (不需再 JOIN 第三張表)
                SELECT
                    F.SFHNR,
                    F.STYLE_BASE,
                    F.STYLE_VER,
                    R.PROC_NAME AS PROCESS_NAME,
                    R.PROC_ID AS PROCESS_DESC,
                    COUNT(DISTINCT R.PROC_SCHL_SEQ) AS VORNR_COUNT
                FROM FINAL_TOOLS F
                JOIN IDBUSER.PM_WIPPATH R
                  ON R.FACTORY_ID = F.FACTORY AND R.WIP_ID = F.MATNR AND R.PROC_SCHL_SEQ = F.PROC_ITEM_SEQ AND R.SUB_WIP = F.SUB_WIP
                GROUP BY F.SFHNR, F.STYLE_BASE, F.STYLE_VER, R.PROC_NAME, R.PROC_ID
            """
            cur.execute(sql, {"matnr": matnr})

            # 💡 下面的 Python 封裝邏輯與原本 100% 相同，所以前端完全不需要修改！
            styles_dict = {}
            for sfhnr, style_base, style_ver, process_name, process_desc, vornr_count in cur:
                if sfhnr not in styles_dict:
                    styles_dict[sfhnr] = {
                        "sfhnr": sfhnr,
                        "styleNo": style_base,
                        "version": style_ver,
                        "processes": [],
                        "process_names": [] 
                    }
                styles_dict[sfhnr]["processes"].append({
                    "code": process_desc,
                    "name": process_name,
                    "step_count": vornr_count
                })
                styles_dict[sfhnr]["process_names"].append(process_name)

            styles = list(styles_dict.values())

        # ==========================================
        # 2. MySQL Match to find exists style no
        # 👉 這一段完全不變！因為上面整理出來的 styles 結構一模一樣
        # ==========================================
        if styles:
            sfhnr_list = [s['sfhnr'] for s in styles]
            format_strings = ','.join(['%s'] * len(sfhnr_list))
            
            with db() as (conn, cur):
                mysql_query = f"""
                    SELECT JSON_UNQUOTE(JSON_EXTRACT(attribute, '$.styleNo')) AS locked_style, author FROM rms_document_attributes 
                    WHERE document_type = 1 AND JSON_UNQUOTE(JSON_EXTRACT(attribute, '$.styleNo')) IN ({format_strings})
                """
                cur.execute(mysql_query, tuple(sfhnr_list))
                locked_drafts = {row[0]: row[1] for row in cur.fetchall()}

            # 3. 合併資料：打上 isLocked 標記
            for style in styles:
                if style['sfhnr'] in locked_drafts:
                    style['isLocked'] = True
                    style['lockedBy'] = locked_drafts[style['sfhnr']]
                else:
                    style['isLocked'] = False

        # 排序邏輯維持不變
        def get_st_number(item):
            try:
                suffix = item['styleNo'].split('-ST')[1]
                return int(suffix)
            except (IndexError, ValueError):
                return 0

        styles.sort(key=get_st_number)

        return jsonify({"success": True, "data": {"styles": styles}})
        
    except Exception as e:
        print("Error in /item/styles:", e)
        return jsonify({"success": False, "error": str(e)}), 500

@bp.get("/processesAndMachines")
def list_processes_and_machines():
    matnr = (request.args.get("matnr") or "").strip()
    sfhnr = (request.args.get("sfhnr") or "").strip()

    if not matnr or not sfhnr:
        return jsonify({"success": False, "error": "matnr and sfhnr are required"}), 400

    try:
        specification = []
        spec_groups = {}
        process_codes = []

        # ==========================================
        # 第一階段：取得 適用工程 (Processes)
        # 來源 DB: IDBUSER (ora_cursor)
        # ==========================================
        with ora_cursor() as cur:
            # 👉 轉換成 PM_WIPMOLD 與 PM_WIPPATH，並直接取用 R.PROC_NAME，不再 JOIN 第三張表
            sql_processes = """
                SELECT R.PROC_NAME AS PROCESS_NAME, R.PROC_ID AS PROCESS_DESC, COUNT(DISTINCT R.PROC_SCHL_SEQ) AS VORNR_COUNT FROM IDBUSER.PM_WIPPATH R
                JOIN IDBUSER.PM_WIPMOLD T ON R.FACTORY_ID = T.FACTORY AND R.WIP_ID = T.WIP_ID AND R.PROC_SCHL_SEQ = T.PROC_ITEM_SEQ AND R.SUB_WIP = T.SUB_WIP
                WHERE T.MTRL_ID = :sfhnr AND T.MTRL_ID LIKE '%-ST%' AND T.FACTORY = '1011' 
                  AND (CASE WHEN INSTR(T.WIP_ID, '-') > 0 THEN SUBSTR(T.WIP_ID, 1, INSTR(T.WIP_ID, '-') - 1) ELSE T.WIP_ID END) = :matnr 
                  -- 取得該 MATNR Base 對應的最大版本號
                  AND SUBSTR(T.WIP_ID, INSTR(T.WIP_ID, '-') + 1, 2) = (
                      SELECT MAX(SUBSTR(T2.WIP_ID, INSTR(T2.WIP_ID, '-') + 1, 2)) FROM IDBUSER.PM_WIPMOLD T2
                      WHERE T2.MTRL_ID = :sfhnr AND (CASE WHEN INSTR(T2.WIP_ID, '-') > 0 THEN SUBSTR(T2.WIP_ID, 1, INSTR(T2.WIP_ID, '-') - 1) ELSE T2.WIP_ID END) = :matnr
                  )
                GROUP BY R.PROC_NAME, R.PROC_ID 
                ORDER BY R.PROC_ID
            """
            cur.execute(sql_processes, {"matnr": matnr, "sfhnr": sfhnr})

            for process_name, process_desc, step_count in cur:
                specification.append({"code": process_desc, "name": process_name, "step_count": step_count})
                process_codes.append(process_desc) # 收集查出來的代碼給第二階段用

        # ==========================================
        # 第二階段：取得 機檯群組與機台 (Spec Groups & Machines)
        # 來源 DB: SAJET (machine_db)
        # 👉 這裡因為 SAJET 架構沒變，直接沿用舊的邏輯即可！
        # ==========================================
        # 防呆：只有在第一階段有查到製程時，才去查機台，避免 IN () 語法報錯
        if process_codes:
            with ora_cursor(db_alias="machine_db") as cur_mach:
                in_clause = "'" + "','".join(process_codes) + "'"
                
                sql_machines = f"""
                    SELECT PROCESS_DESC, MACHINE_CODE, MACHINE_DESC, MACHINE_TYPE_NAME, MACHINE_TYPE_DESC FROM (
                        SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME, sm.MACHINE_ID, sm.MACHINE_CODE, sm.MACHINE_DESC, mt.MACHINE_TYPE_NAME, mt.MACHINE_TYPE_DESC FROM SAJET.V_SFIS_MACHINE_PROCESS v
                        JOIN SAJET.SYS_PROCESS p ON p.PROCESS_DESC = v.PROCESS_DESC
                        JOIN SAJET.SYS_MACHINE sm ON v.MACHINE_CODE = sm.MACHINE_CODE
                        JOIN SAJET.SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
                        WHERE sm.EQM_ID <> 'NA' AND p.PROCESS_DESC IN ({in_clause})
                    ) ORDER BY MACHINE_ID
                """
                
                cur_mach.execute(sql_machines)
                
                for scode, mcode, mname, gcode, gname in cur_mach.fetchall():
                    machine_info = {"code": mcode, "name": mname}
                    
                    if scode not in spec_groups:
                        spec_groups[scode] = {}

                    if gcode not in spec_groups[scode]:
                        spec_groups[scode][gcode] = {"name": gname, "machines": []}

                    spec_groups[scode][gcode]["machines"].append(machine_info)

        # ==========================================
        # 統一回傳組合後的資料
        # ==========================================
        return jsonify({
            "success": True, 
            "data": {"specification": specification, "specGroups": spec_groups}
        })

    except Exception as e:
        print("Error in /item/processesAndMachines:", e)
        return jsonify({"success": False, "error": str(e)}), 500

_SPEC_STATUS_TEXT_MAP = {
    1: "草稿",
    2: "已公告",
    3: "已下載",
}

_ORACLE_IN_CHUNK_SIZE = 1000  # Oracle pre-23c 的 IN list 上限


def _chunked_in_clause(column, values, param_prefix, params, chunk_size=_ORACLE_IN_CHUNK_SIZE):
    """
    把 `column IN (大列表)` 拆成 `(col IN (...) OR col IN (...) ...)` 避開 ORA-01795。
    - values 為空回傳 "1=0" (永遠不命中)。
    - 參數寫入傳入的 params dict (採 :prefix_idx 形式)。
    - 回傳含外層括號的 WHERE fragment。
    """
    if not values:
        return "1=0"
    chunks = []
    for batch_start in range(0, len(values), chunk_size):
        batch = values[batch_start:batch_start + chunk_size]
        placeholders = []
        for i, v in enumerate(batch):
            ph = f"{param_prefix}_{batch_start + i}"
            placeholders.append(f":{ph}")
            params[ph] = v
        chunks.append(f"{column} IN ({','.join(placeholders)})")
    return "(" + " OR ".join(chunks) + ")"


def _query_spec_matched_styles(document_id, department, doc_status, start_date=None, end_date=None):
    """
    依 documentId / department / status (+ 日期區間) 過濾 rms_document_attributes，
    回傳符合條件的 styleNo 清單 (對應 Oracle MTRL_ID)。
    status:
        "草稿"  -> status = 1
        "已公告" -> status = 2
        "已下載" -> status = 3
        其餘 / "全部" / None -> 不限制 (但仍排除 status = 0)
    日期 (YYYYMMDD 格式)：比對 attr.issue_date
        - 因為 Oracle 必須先有對應料號 MySQL 文件才會存在，
          所以對 issue_date 做相同區間過濾不會誤殺資料，
          且能讓回傳的 styleNo 數量縮小、避免 Oracle IN list 爆掉。
    """
    conditions = ["attr.document_type = 1", "attr.status <> 0"]
    sql_params = {}

    if document_id:
        conditions.append("attr.document_id LIKE %(document_id)s")
        sql_params['document_id'] = f"%{document_id}%"
    if department:
        conditions.append("attr.department LIKE %(department)s")
        sql_params['department'] = f"%{department}%"
    if doc_status == "草稿":
        conditions.append("attr.status = 1")
    elif doc_status == "已公告":
        conditions.append("attr.status = 2")
    elif doc_status == "已下載":
        conditions.append("attr.status = 3")
    if start_date:
        conditions.append("attr.issue_date >= STR_TO_DATE(%(start_date)s, '%%Y%%m%%d')")
        sql_params['start_date'] = start_date
    if end_date:
        # 含當日：< end_date + 1 day
        conditions.append("attr.issue_date < DATE_ADD(STR_TO_DATE(%(end_date)s, '%%Y%%m%%d'), INTERVAL 1 DAY)")
        sql_params['end_date'] = end_date

    sql = f"""
        SELECT DISTINCT JSON_UNQUOTE(JSON_EXTRACT(attr.attribute, '$.styleNo')) AS style_no
        FROM rms_document_attributes attr
        WHERE {" AND ".join(conditions)}
    """

    styles = []
    try:
        with db(dict_cursor=True) as (_, cur):
            cur.execute(sql, sql_params)
            for row in cur.fetchall():
                sn = row.get('style_no')
                if sn:
                    styles.append(sn)
    except Exception as e:
        print(f"MySQL spec-list filter error: {e}")
    return styles


# ============================================================
# /spec-list 系列共用 helpers (給 list / export-count / export)
# ============================================================
_SPEC_LIST_BASE_SELECT = """
    SELECT r.WIP_ID, r.PROC_ID, r.PROC_NAME, t.MTRL_ID, r.INS_DT
    FROM IDBUSER.PM_WIPPATH r
    JOIN IDBUSER.PM_WIPMOLD t
      ON r.FACTORY_ID = t.FACTORY
     AND r.WIP_ID = t.WIP_ID
     AND r.PROC_SCHL_SEQ = t.PROC_ITEM_SEQ
     AND r.SUB_WIP = t.SUB_WIP
"""

_SPEC_LIST_BASE_COUNT = """
    SELECT COUNT(1)
    FROM IDBUSER.PM_WIPPATH r
    JOIN IDBUSER.PM_WIPMOLD t
      ON r.FACTORY_ID = t.FACTORY
     AND r.WIP_ID = t.WIP_ID
     AND r.PROC_SCHL_SEQ = t.PROC_ITEM_SEQ
     AND r.SUB_WIP = t.SUB_WIP
"""


def _parse_spec_list_args():
    """讀 request.args，回傳 spec-list 系列共用的參數 dict。"""
    return {
        'page': int(request.args.get('page', 1)),
        'page_size': int(request.args.get('pageSize', 10)),
        'start_date': request.args.get('startDate', '20260401').replace('-', ''),
        'end_date': request.args.get('endDate', '').replace('-', ''),
        'item': request.args.get('item', '').strip(),
        'station': request.args.get('station', '').strip(),
        'document_id': request.args.get('document_id', '').strip(),
        'department': request.args.get('department', '').strip(),
        'doc_status': request.args.get('status', '').strip(),
    }


def _prepare_spec_list_filter(p):
    """
    依參數建立 Oracle WHERE 與 binds。
    回傳 (where_sql, ora_params, mysql_empty)
      - mysql_empty=True 代表 MySQL 預篩無結果，呼叫端可直接回空
    """
    ora_params = {"start_date": p['start_date']}
    where_clauses = [
        "r.FACTORY_ID = '1011'",
        "t.MTRL_ID LIKE '%-ST%'",
        "r.INS_DT >= :start_date",
    ]

    if p['end_date']:
        where_clauses.append("r.INS_DT <= :end_date")
        ora_params["end_date"] = p['end_date']
    if p['item']:
        where_clauses.append("r.WIP_ID LIKE :item")
        ora_params["item"] = f"%{p['item']}%"
    if p['station']:
        where_clauses.append("(r.PROC_ID LIKE :station OR r.PROC_NAME LIKE :station)")
        ora_params["station"] = f"%{p['station']}%"

    has_mysql_filter = (
        bool(p['document_id'])
        or bool(p['department'])
        or (p['doc_status'] and p['doc_status'] != "全部")
    )
    if has_mysql_filter:
        matched_styles = _query_spec_matched_styles(
            p['document_id'], p['department'], p['doc_status'],
            start_date=p['start_date'], end_date=p['end_date'] or None,
        )
        if not matched_styles:
            return "", ora_params, True
        where_clauses.append(
            _chunked_in_clause("t.MTRL_ID", matched_styles, "sn", ora_params)
        )

    return " AND ".join(where_clauses), ora_params, False


def _fetch_doc_map(style_nos, chunk_size=1000):
    """
    批量查 MySQL rms_document_attributes，回傳 {style_no: doc_info_dict}。
    每個 style_no 只保留最新版 (依 document_version DESC, issue_date DESC)。
    style_nos 過長時自動分批避免 SQL 太大。
    """
    doc_map = {}
    if not style_nos:
        return doc_map

    distinct_sns = list({sn for sn in style_nos if sn})
    if not distinct_sns:
        return doc_map

    try:
        with db(dict_cursor=True) as (_, cur_m):
            for i in range(0, len(distinct_sns), chunk_size):
                batch = distinct_sns[i:i + chunk_size]
                format_strings = ','.join(['%s'] * len(batch))
                attr_sql = f"""
                    SELECT document_token, document_id, document_name, document_version,
                           author, approver, change_summary, status, department,
                           JSON_UNQUOTE(JSON_EXTRACT(attribute, '$.styleNo')) AS style_no
                    FROM rms_document_attributes
                    WHERE document_type = 1
                      AND status <> 0
                      AND JSON_UNQUOTE(JSON_EXTRACT(attribute, '$.styleNo')) IN ({format_strings})
                    ORDER BY document_version DESC, issue_date DESC
                """
                cur_m.execute(attr_sql, tuple(batch))
                for r in cur_m.fetchall():
                    sn = r['style_no']
                    if sn not in doc_map:  # 保留第一筆 (= 最新版)
                        doc_map[sn] = r
    except Exception as e:
        print(f"MySQL doc lookup error: {e}")
    return doc_map


def _format_ins_dt(ins_dt):
    """Oracle 的 INS_DT 是 YYYYMMDD INT，轉成 YYYY-MM-DD 字串"""
    if ins_dt is None:
        return ""
    return f"{ins_dt // 10000}-{(ins_dt // 100) % 100:02d}-{ins_dt % 100:02d}"


@bp.get('/spec-list')
def get_spec_list():
    try:
        p = _parse_spec_list_args()
        where_sql, ora_params, mysql_empty = _prepare_spec_list_filter(p)
        if mysql_empty:
            return jsonify({"success": True, "data": {"items": [], "total": 0}})

        offset = (p['page'] - 1) * p['page_size']
        end_row = offset + p['page_size']

        count_sql = f"{_SPEC_LIST_BASE_COUNT} WHERE {where_sql}"
        data_sql = f"""
            SELECT * FROM (
                SELECT a.*, ROWNUM rnum FROM (
                    {_SPEC_LIST_BASE_SELECT}
                    WHERE {where_sql}
                    ORDER BY r.INS_DT DESC
                ) a WHERE ROWNUM <= :end_row
            ) WHERE rnum > :offset
        """

        paged_params = {**ora_params, "offset": offset, "end_row": end_row}

        oracle_items = []
        with ora_cursor("item_db") as cur_o:
            cur_o.execute(count_sql, ora_params)
            total_records = cur_o.fetchone()[0]

            cur_o.execute(data_sql, paged_params)
            for row in cur_o.fetchall():
                wip_id, proc_id, proc_name, mtrl_id, ins_dt, _rnum = row
                oracle_items.append({
                    "MATNR": wip_id,
                    "KTSCH": proc_id,
                    "LTXA1": proc_name,
                    "SFHNR": mtrl_id,
                    "EDATE": _format_ins_dt(ins_dt),
                })

        if not oracle_items:
            return jsonify({"success": True, "data": {"items": [], "total": total_records}})

        # 補上 MySQL 文件資訊
        doc_map = _fetch_doc_map([it["SFHNR"] for it in oracle_items])

        for it in oracle_items:
            doc_info = doc_map.get(it["SFHNR"])
            if doc_info:
                it.update({
                    "doc_id": doc_info["document_id"],
                    "doc_name": doc_info["document_name"],
                    "doc_version": doc_info["document_version"],
                    "author": doc_info["author"],
                    "approver": doc_info["approver"],
                    "change_summary": doc_info["change_summary"],
                    "department": doc_info["department"],
                    "status_text": _SPEC_STATUS_TEXT_MAP.get(doc_info['status'], "未知狀態"),
                })
            else:
                it.update({
                    "doc_id": None, "doc_name": None, "doc_version": None,
                    "author": None, "approver": None, "change_summary": None,
                    "department": None,
                    "status_text": "無文件",
                })

        return jsonify({
            "success": True,
            "data": {"items": oracle_items, "total": total_records},
        })

    except Exception as e:
        print(f"Error in spec-list: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# /spec-list/export-count : 給前端做匯出前的警示判斷
# ============================================================
@bp.get('/spec-list/export-count')
def get_spec_list_export_count():
    try:
        p = _parse_spec_list_args()
        where_sql, ora_params, mysql_empty = _prepare_spec_list_filter(p)
        if mysql_empty:
            return jsonify({"success": True, "data": {"count": 0}})

        count_sql = f"{_SPEC_LIST_BASE_COUNT} WHERE {where_sql}"
        with ora_cursor("item_db") as cur_o:
            cur_o.execute(count_sql, ora_params)
            count = cur_o.fetchone()[0]

        return jsonify({"success": True, "data": {"count": count}})

    except Exception as e:
        print(f"Error in spec-list/export-count: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# /spec-list/export : 匯出 xlsx (用 BytesIO + xlsxwriter，不落磁碟)
# ============================================================
_EXPORT_HEADERS = [
    '項次', '品目', '製程', '製程名稱', '式樣書編號', '建立時間',
    '文件編號', '文件名稱', '版本', '制定者', '變更要點', '文件狀態',
]
_EXPORT_COL_WIDTHS = [6, 18, 12, 28, 22, 14, 18, 32, 8, 12, 35, 12]


@bp.get('/spec-list/export')
def get_spec_list_export():
    try:
        p = _parse_spec_list_args()
        where_sql, ora_params, mysql_empty = _prepare_spec_list_filter(p)

        # Pass 1: 串流撈 Oracle (不分頁)，同時收集 styleNo
        oracle_rows = []  # 每筆 (wip_id, proc_id, proc_name, mtrl_id, formatted_date)
        if not mysql_empty:
            data_sql = f"""
                {_SPEC_LIST_BASE_SELECT}
                WHERE {where_sql}
                ORDER BY r.INS_DT DESC
            """
            with ora_cursor("item_db") as cur_o:
                # 一次抓 5000 筆，減少網路 round trip
                cur_o.arraysize = 5000
                cur_o.execute(data_sql, ora_params)
                for row in cur_o:
                    wip_id, proc_id, proc_name, mtrl_id, ins_dt = row
                    oracle_rows.append((
                        wip_id, proc_id, proc_name, mtrl_id, _format_ins_dt(ins_dt),
                    ))

        # Pass 2: 一次撈 MySQL doc 資訊
        doc_map = _fetch_doc_map([r[3] for r in oracle_rows])

        # Pass 3: 寫 xlsx 到 BytesIO
        output = io.BytesIO()
        workbook = xlsxwriter.Workbook(output, {'in_memory': True})
        worksheet = workbook.add_worksheet('式樣書清單')

        header_fmt = workbook.add_format({
            'bold': True, 'bg_color': '#D9E1F2',
            'border': 1, 'align': 'center', 'valign': 'vcenter',
        })
        for col, h in enumerate(_EXPORT_HEADERS):
            worksheet.write(0, col, h, header_fmt)
        for col, w in enumerate(_EXPORT_COL_WIDTHS):
            worksheet.set_column(col, col, w)

        # 凍結首列 + 開啟篩選
        worksheet.freeze_panes(1, 0)
        worksheet.autofilter(0, 0, max(len(oracle_rows), 1), len(_EXPORT_HEADERS) - 1)

        for idx, (wip_id, proc_id, proc_name, mtrl_id, date_str) in enumerate(oracle_rows, start=1):
            doc_info = doc_map.get(mtrl_id)
            if doc_info:
                doc_id = doc_info['document_id'] or ''
                doc_name = doc_info['document_name'] or ''
                dv = doc_info['document_version']
                doc_version = float(dv) if dv is not None else ''
                author = doc_info['author'] or ''
                change_summary = doc_info['change_summary'] or ''
                status_text = _SPEC_STATUS_TEXT_MAP.get(doc_info['status'], '未知狀態')
            else:
                doc_id = doc_name = author = change_summary = ''
                doc_version = ''
                status_text = '無文件'

            worksheet.write_row(idx, 0, [
                idx,                # 項次
                wip_id or '',       # 品目
                proc_id or '',      # 製程
                proc_name or '',    # 製程名稱
                mtrl_id or '',      # 式樣書編號
                date_str,           # 建立時間
                doc_id,             # 文件編號
                doc_name,           # 文件名稱
                doc_version,        # 版本
                author,             # 制定者
                change_summary,     # 變更要點
                status_text,        # 文件狀態
            ])

        workbook.close()
        output.seek(0)

        filename = f"式樣書清單_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename,
        )

    except Exception as e:
        print(f"Error in spec-list/export: {e}")
        return jsonify({"success": False, "error": str(e)}), 500