from typing import Dict, List
from flask import Blueprint, request
from loginFunctions.utils import send_response

from db import db
from oracle_db import ora_cursor as odb
from utils import *

bp = Blueprint("mes", __name__)
WHERE_PREFIX = "REGEXP_LIKE(p.PROCESS_NAME, '^\([LR][0-8][[:digit:]]{2}-[A-Z]?[[:digit:]]{2}\)') AND p.PROCESS_NAME NOT LIKE '%人工%' AND sm.ENABLED = 'Y' AND sm.EQM_ID <> 'NA'"
# PMS_PREFIX = "(SET_POINT IS NOT NULL OR REAL_POINT IS NOT NULL) AND PARAMETER_CONTROL = 'Y' AND (SET_ATTRIBUTE <> 'Y' OR SET_ATTRIBUTE IS NULL)"
# PMS_PREFIX = "PARAMETER_CONTROL = 'Y'"
MANAGEMENT_PREFIX = "PARAMETER_DESC IS NOT NULL AND PARAMETER_CONTROL = 'Y'"
MANUFACTURING_PREFIX = "(PARAM_COMPARE IS NOT NULL AND PARAM_COMPARE = 'Y' AND SET_ATTRIBUTE IS NOT NULL AND SET_ATTRIBUTE = 'Y')"

# --------- /projects ---------
@bp.get("/projects")
def projects():
    keyword = request.args.get("keyword")
    if not keyword:
        return send_response(401, True, "沒有搜尋條件", {"message": "請輸入關鍵字"})

    # projects are stored in MySQL rms_spec_flat
    with db(dict_cursor=True) as (_, cur):
        cur.execute("SELECT DISTINCT project FROM rms_spec_flat")
        all_projects = [r["project"] for r in cur.fetchall() if r.get("project")]

    out = {name: {"code": name[:3]} for name in all_projects if keyword.lower() in name.lower()}
    return send_response(200, True, "請求成功", {"projects": out})

# ---- helper: allowed codes by (project?, keyword?) mirroring groups-machines scope ----
@bp.get("/groups-machines")
def groups_machines():
    project  = request.args.get("project")
    itemType = request.args.get("item")
    specific = request.args.get("specific")
    keyword  = request.args.get("keyword")
    remove_spec_info = request.args.get("remove_spec_info", 'false')

    if project == None and specific == None and keyword == None and remove_spec_info == None:
        return send_response(400, False, "缺少參數", {"message": "請提供適用工程及關鍵字"})

    elif not any([project, itemType, specific]) and remove_spec_info.lower() == 'true':
        spec_map = None

    else:
        spec_map_by_project, project_info = get_spec_codes_by_project(project)
        spec_map_by_itemType, itemType_info = get_spec_codes_by_itemType(itemType, specific)

        # print(f"specific: {spec_map_by_project}")

        spec_map = None
        if project_info == "Success" and itemType_info == "Success":
            spec_map = set(spec_map_by_project) & set(spec_map_by_itemType)
        elif project_info == "Success" or itemType_info == "Success":
            spec_map = set(spec_map_by_project + spec_map_by_itemType)

        if "failed" in project_info:
            return send_response(400, False, "適用工程查詢失敗", {"message": "請提供正確的適用工程"})
        elif "failed" in itemType_info:
            return send_response(400, False, "品目查詢失敗", {"message": "請提供正確的品目"})
        elif specific != None and (project != None or specific != None) and (spec_map != None and specific not in spec_map):
            return send_response(400, False, "查詢失敗", {"message": "查詢條件互相衝突請重新確認"})
        elif spec_map != None and len(spec_map) == 0:
            return send_response(400, False, "請求成功", {"message": "製程查詢失敗"})
        
        if spec_map != None and len(spec_map) > 0:
            spec_map = list(spec_map) if len(spec_map) > 1 else spec_map.pop()
        elif spec_map == None and specific != None:
            spec_map = specific

    try:
        out = {}
        with odb(db_alias = "machine_db") as cur:
            specInfo = "" if remove_spec_info.lower() == 'true' else "p.PROCESS_DESC, p.PROCESS_NAME, "
            select_info = f"SELECT DISTINCT {specInfo}sm.MACHINE_CODE, sm.MACHINE_DESC, sm.BUILDING, mt.MACHINE_TYPE_NAME, mt.MACHINE_TYPE_DESC FROM SAJET.V_SFIS_MACHINE_PROCESS v"
            join_info = f"""
                JOIN SAJET.SYS_PROCESS p ON v.PROCESS_DESC = p.PROCESS_DESC
                JOIN SAJET.SYS_MACHINE sm ON v.MACHINE_CODE = sm.MACHINE_CODE
                JOIN SAJET.SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
                WHERE {WHERE_PREFIX}
            """
            sql = select_info + join_info
            if isinstance(spec_map, list):
                sql += " AND p.PROCESS_DESC IN ('" + "','".join(spec_map) + "')"

            elif isinstance(spec_map, str):
                sql += f" AND p.PROCESS_DESC = '{spec_map}'"
            
            if keyword != None:
                sql += f" AND sm.MACHINE_DESC LIKE '%{keyword}%'"

            cur.execute(sql)
            if remove_spec_info.lower() == 'false':
                for scode, sname, mcode, mname, mbuilding, gcode, gname in cur.fetchall():
                    machineInfo = {"name": mname, "building": mbuilding, "specifications": []}
                    if out.get(gcode) == None:
                        out[gcode] = {"name": gname, "machines": {}}
                        
                    if out[gcode]["machines"].get(mcode) == None:
                        out[gcode]["machines"][mcode] = machineInfo

                    out[gcode]["machines"][mcode]["specifications"].append({"code": scode, "name": sname})

            else:
                for mcode, mname, mbuilding, gcode, gname in cur.fetchall():
                    machineInfo = {"name": mname, "building": mbuilding}
                    if out.get(gcode) == None:
                        out[gcode] = {"name": gname, "machines": {}}
                        
                    if out[gcode]["machines"].get(mcode) == None:
                        out[gcode]["machines"][mcode] = machineInfo

    except Exception as e:
        print(f"error result: {e}")
        return send_response(500, True, "查詢失敗", {"message": "資料庫查詢失敗，請重新嘗試"})

    return send_response(200, True, "請求成功", {"groups": out})

@bp.get("/spec-groups-machines")
def spec_groups_machines():
    """
    根據製程帶出機檯群組和機台 (SFISDB Database SAJET Schema)
     -- 製程、機台群組、機台關係 由 View V_SFIS_MACHINE_PROCESS 帶出
     -- View 帶出的關係再由各表帶出相關資訊
     -- 將無 EQM_ID 資料篩選掉
    """
    raw_specs = request.args.getlist("specific") or request.args.getlist("specific[]")
    
    if len(raw_specs) == 0:
        return send_response(400, True, "查詢失敗", {"message": "請輸入至少一個適用工程"})

    try:
        out = {}
        with odb(db_alias = "machine_db") as cur:
            # sql = f"""
            #     SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME, sm.MACHINE_ID, sm.MACHINE_CODE, sm.MACHINE_DESC, mt.MACHINE_TYPE_NAME, mt.MACHINE_TYPE_DESC FROM SAJET.SYS_PROCESS p
            #     JOIN SAJET.SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
            #     JOIN SAJET.SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
            #     JOIN SAJET.SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
            #     WHERE {WHERE_PREFIX}
            # """
            sql = f"""
                SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME, sm.MACHINE_ID, sm.MACHINE_CODE, sm.MACHINE_DESC, mt.MACHINE_TYPE_NAME, mt.MACHINE_TYPE_DESC FROM SAJET.V_SFIS_MACHINE_PROCESS v
                JOIN SAJET.SYS_PROCESS p ON p.PROCESS_DESC = v.PROCESS_DESC
                JOIN SAJET.SYS_MACHINE sm ON v.MACHINE_CODE = sm.MACHINE_CODE
                JOIN SAJET.SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
                WHERE sm.EQM_ID <> 'NA'
            """
            sql += " AND p.PROCESS_DESC IN ('" + "','".join(raw_specs) + "')"

            cur.execute(f"SELECT PROCESS_DESC, MACHINE_CODE, MACHINE_DESC, MACHINE_TYPE_NAME, MACHINE_TYPE_DESC FROM ({sql}) ORDER BY MACHINE_ID")
            for scode, mcode, mname, gcode, gname in cur.fetchall():
                machineInfo = {"code": mcode, "name": mname}
                if out.get(scode) == None:
                    out[scode] = {}

                if out[scode].get(gcode) == None:
                    out[scode][gcode] = {"name": gname, "machines": []}

                out[scode][gcode]["machines"].append(machineInfo)

    except Exception as e:
        print(f"error result: {e}")
        return send_response(500, True, "查詢失敗", {"message": "資料庫查詢失敗，請重新嘗試"})

    return send_response(200, True, "請求成功", {"specGroups": out})

@bp.get("/spec-machines")
def spec_machines():
    raw_specs = request.args.getlist("specific") or request.args.getlist("specific[]")
    
    if len(raw_specs) == 0:
        return send_response(400, True, "查詢失敗", {"message": "請輸入至少一個適用工程"})

    try:
        out = {}
        with odb(db_alias = "machine_db") as cur:
            sql = f"""
                SELECT DISTINCT p.PROCESS_DESC, sm.MACHINE_ID, sm.MACHINE_CODE, sm.MACHINE_DESC, sm.BUILDING FROM SAJET.SYS_PROCESS p
                JOIN SAJET.SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
                JOIN SAJET.SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
                WHERE {WHERE_PREFIX}
            """

            sql += " AND p.PROCESS_DESC IN ('" + "','".join(raw_specs) + "')"

            cur.execute(f"SELECT PROCESS_DESC, MACHINE_CODE, MACHINE_DESC, BUILDING FROM ({sql}) ORDER BY MACHINE_ID")
            for scode, mcode, mname, mbuilding in cur.fetchall():
                machineInfo = {"code": mcode, "name": mname, "building": mbuilding}
                if out.get(scode) == None:
                    out[scode] = []

                out[scode].append(machineInfo)

    except Exception as e:
        print(f"error result: {e}")
        return send_response(500, True, "查詢失敗", {"message": "資料庫查詢失敗，請重新嘗試"})

    return send_response(200, True, "請求成功", {"specMachines": out})

# Use for machine list window
@bp.post("/filter-by-baseline")
def filter_by_baseline():
    """
    同參數比對
     -- 1. 利用機台帶出的 PMS 點位比對其他機台的點位
         -- 1.1 如果該機台無點位 -> 也篩選出無點位機台
     -- 2. 利用機台帶出的條件參數比對其他機台的條件參數
     -- 3. 回傳同參數機台 return { group_code: { gname, machines: { machine_code: { name, building, specification} }, ... }, ... }
    """
    body       = request.get_json(silent=True) or {}
    base_code  = body.get("machine_code")  # Oracle MACHINE_CODE
    project    = body.get("project")
    specific   = body.get("specific")
    keyword    = body.get("keyword")

    # User no give baseline machine then return error
    if base_code == None:
        return send_response(400, False, "缺少baseline", {"message": "缺少機台baseline"})

    # Get specifications based on project
    spec_map_by_project, project_info = get_spec_codes_by_project(project)
    if "failed" in project_info:
        return send_response(400, False, "適用工程查詢失敗", {"message": "請提供正確的適用工程"})

    # If specifications get successfully
    spec_map = None
    if project_info == "Success":
        spec_map = set(spec_map_by_project)

        if len(spec_map) == 0:  # There is no any specification in project
            return send_response(200, False, "查詢成功", {"groups": {}})

        elif specific != None and specific not in spec_map:  # Argument specification conflict with project
            return send_response(400, False, "查詢失敗", {"message": "查詢條件互相衝突請重新確認"})

        spec_map = list(spec_map) if len(spec_map) > 1 else spec_map.pop()
    
    # If no project input just get specific
    elif specific != None:
        spec_map = specific

    same_PMS_machines = []
    try:
        with ora_cursor(db_alias = "machine_db") as cur:
            sql = f"""
            WITH base AS (SELECT DISTINCT TRIM(SLOT_NAME) AS SLOT_NAME FROM SAJET.FLEX_PMS WHERE MACHINE_CODE = '{base_code}'),
                base_cnt AS ( SELECT COUNT(*) AS cnt FROM base ),
                cand AS (SELECT DISTINCT MACHINE_CODE, TRIM(SLOT_NAME) AS SLOT_NAME FROM SAJET.FLEX_PMS)
            SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME, sm.MACHINE_CODE, sm.MACHINE_DESC, sm.BUILDING, mt.MACHINE_TYPE_NAME, mt.MACHINE_TYPE_DESC FROM SAJET.V_SFIS_MACHINE_PROCESS v
            JOIN SAJET.SYS_PROCESS p ON v.PROCESS_DESC = p.PROCESS_DESC
            JOIN SAJET.SYS_MACHINE sm ON v.MACHINE_CODE = sm.MACHINE_CODE
            JOIN SAJET.SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID, base_cnt bc
            WHERE {WHERE_PREFIX} AND (
                (bc.cnt = 0 AND NOT EXISTS (SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE)) OR (
                    bc.cnt > 0 AND EXISTS (SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE) AND NOT EXISTS (
                        SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE AND NOT EXISTS (SELECT 1 FROM base b WHERE b.SLOT_NAME = cx.SLOT_NAME)
                    ) AND NOT EXISTS (
                        SELECT 1 FROM base b WHERE NOT EXISTS (SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE AND cx.SLOT_NAME = b.SLOT_NAME)
                    )
                )
            )
            """

            if isinstance(spec_map, list):
                sql += " AND p.PROCESS_DESC IN ('" + "','".join(spec_map) + "')"

            elif isinstance(spec_map, str):
                sql += f" AND p.PROCESS_DESC = '{spec_map}'"
            
            if keyword != None:
                sql += f" AND sm.MACHINE_DESC LIKE '%{keyword}%'"

            cur.execute(sql)
            same_PMS_machines = [_ for _ in cur.fetchall()]

    except Exception as e:
        print(f"error result: {e}")
        return send_response(400, True, "請求失敗", {"message": "Oracle資料庫查詢失敗，請重新嘗試"})
    
    same_PMS_machine_ids = [mi[2] for mi in same_PMS_machines]
    same_condition_machine_ids = []
    try:
        with db() as (conn, cur):
            join_command = " ".join([f"UNION ALL SELECT '{code}'" for code in same_PMS_machine_ids])
            sql = f"""
            WITH candidates AS (SELECT '{base_code}' AS machine_id {join_command}),
            sig AS (
                SELECT c.machine_id, COALESCE(GROUP_CONCAT(DISTINCT g.condition_id ORDER BY g.condition_id SEPARATOR ','), '') AS sig FROM candidates c
                LEFT JOIN rms_group_machines g ON g.machine_id = c.machine_id GROUP BY c.machine_id
            ),
            baseline AS (SELECT sig FROM sig WHERE machine_id = '{base_code}')
            SELECT s.machine_id FROM sig s
            JOIN baseline b ON s.sig = b.sig;
            """

            cur.execute(sql)
            same_condition_machine_ids = set([machine_code for (machine_code, ) in cur.fetchall()])

    except Exception as e:
        print(f"error result: {e}")
        return send_response(400, True, "請求失敗", {"message": "MySQL資料庫查詢失敗，請重新嘗試"})
    
    out = {}
    for mi in same_PMS_machines:
        if mi[2] not in same_condition_machine_ids:
            continue

        scode, sname, mcode, mname, mbuilding, gcode, gname = mi
        machineInfo = {"name": mname, "building": mbuilding, "specifications": []}
        if out.get(gcode) == None:
            out[gcode] = {"name": gname, "machines": {}}
            
        if out[gcode]["machines"].get(mcode) == None:
            out[gcode]["machines"][mcode] = machineInfo

        out[gcode]["machines"][mcode]["specifications"].append({"code": scode, "name": sname})

    return send_response(200, True, "請求成功", {"groups": out})

# Use for manufacturing block for specification document
@bp.post("/pms/filter-by-pms-baseline")
def filter_by_pms_baseline():
    data = request.json
    baseline_code = data.get("baseline_code")
    
    if not baseline_code:
        return send_response(400, True, "參數錯誤", {"message": "缺少 baseline_code"})

    try:
        valid_machines = []
        pms_table_rows = []

        with odb(db_alias = "machine_db") as cur:
            # === 第一步：純 SQL 高效篩選 ===
            # 修正 ORA-01427: 改用 IN (...)
            sql_filter = f"""
                WITH BASELINE_DATA AS (
                    SELECT SLOT_NAME, PARAMETER_DESC FROM SAJET.FLEX_PMS WHERE MACHINE_CODE = :b AND {MANUFACTURING_PREFIX}
                ),
                BASELINE_CNT AS (
                    SELECT COUNT(*) AS CNT FROM BASELINE_DATA
                )
                SELECT P.MACHINE_CODE FROM SAJET.FLEX_PMS P WHERE P.PARAM_COMPARE = 'Y' GROUP BY P.MACHINE_CODE
                HAVING COUNT(P.PMS_ID) = (SELECT CNT FROM BASELINE_CNT) AND COUNT(CASE WHEN (P.SLOT_NAME, P.PARAMETER_DESC) IN (SELECT SLOT_NAME, PARAMETER_DESC FROM BASELINE_DATA) THEN 1 END) = (SELECT CNT FROM BASELINE_CNT)
            """
            
            cur.execute(sql_filter, b=baseline_code)
            valid_machines = [row[0] for row in cur.fetchall()]
            # print(f"[PMS Filter] Valid Machines Count: {len(valid_machines)}")

            # === 第二步：取得基準機台表格資料 ===
            sql_data = f"SELECT SLOT_NAME, PARAMETER_DESC, UNIT FROM SAJET.FLEX_PMS WHERE MACHINE_CODE = :b AND {MANUFACTURING_PREFIX} ORDER BY PMS_ID"
            cur.execute(sql_data, b=baseline_code)
            rows_data = cur.fetchall()

            temp_pms_rows = []
            for index, r in enumerate(rows_data):
                slot, param, unit = r
                param = f"{param}({unit})" if unit != None and len(unit) > 0 else param
                row = [f"{index + 1}", slot, param, "", "", "", "", "", ""]
                temp_pms_rows.append(row)

            header = ["項次", "槽體", "管理項目", "規格下限(OOS-)", "操作下限(OOC-)", "設定值", "操作上限(OOC+)", "規格上限(OOS+)", "說明"]
            pms_table_rows = [header] + temp_pms_rows if len(temp_pms_rows) > 0 else []

        return send_response(200, True, "篩選成功", {"valid_machines": valid_machines, "pms_table_rows": pms_table_rows})

    except Exception as e:
        # === Debug Log: 輸出詳細錯誤 ===
        import traceback
        traceback.print_exc()
        print(f"[PMS Filter Error] {str(e)}")
        return send_response(500, True, "篩選失敗", {"message": str(e)})

placeholder = lambda x: ','.join(['%s'] * len(x))
list2SqlList = lambda l: "','".join(l)
@bp.post("get-scope-units")
def get_scope_units():
    body       = request.get_json(silent=True) or {}
    machines   = body.get("machines")

    if len(machines) == 0:
        return send_response(400, False, "缺少機台代碼", {"message": "缺少機台代碼"})
    
    departments = []
    try:
        with ora_cursor(db_alias = "machine_db") as cur:
            sql = f"""
                SELECT SD.DEPT_NAME FROM SAJET.SYS_MACHINE sm
                JOIN SAJET.SYS_PDLINE sp ON sm.PDLINE_ID = sp.PDLINE_ID  
                JOIN SAJET.SYS_DEPT sd ON sp.HCP_DEPT_ID || '00' = sd.DEPT_DESC 
                WHERE sm.MACHINE_CODE IN ('{list2SqlList(machines)}') AND sm.ENABLED = 'Y' AND sm.MACHINE_TYPE = 'EQP'
            """
            cur.execute(sql)
            departments = list(set([dept[0] for dept in cur.fetchall()]))

    except Exception as e:
        print(f"error result: {e}")
        return send_response(400, True, "請求失敗", {"message": "Oracle資料庫查詢失敗，請重新嘗試"})
    
    return send_response(200, True, "請求成功", {"data": departments})

# -------------------------------------------------------------------------------------------------

def _paginate(items, page, page_size):
    total = len(items)
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 20)))
    i0 = (page - 1) * page_size
    return {"items": items[i0:i0+page_size], "total": total, "page": page, "pageSize": page_size}

@bp.get("/engineering")
def list_engineering():
    """List projects from MySQL (distinct project). Supports keyword + pagination."""
    keyword = request.args.get("keyword", '')
    page     = request.args.get("page")
    pageSize = request.args.get("pageSize")

    keyword = f"(project LIKE '%{keyword}%') OR (spec_code LIKE '%{keyword}%') OR (spec_name LIKE '%{keyword}%')" if len(keyword) > 0 else '1=1'
    projects = []
    try:
        with db() as (_, cur):
            cur.execute(f"SELECT DISTINCT project FROM rms_spec_flat WHERE {keyword} ORDER BY project")
            projects = [p[0] for p in cur.fetchall()]
        
    except Exception as e:
        print(f"error result: {e}")
        return send_response(400, True, "查詢失敗", {"message": "請重新嘗試"})

    # Build rows expected by your UI: id, projectCode, projectName
    # Use the same string as both code/name unless you have a separate code.
    rows = [{"id": p, "projectCode": p, "projectName": p} for p in projects]
    return send_response(200, True, "OK", _paginate(rows, page, pageSize))

@bp.get("/engineering/processes")
def list_engineering_processes():
    project_id = request.args.get("project_id", "")
    keyword = request.args.get("keyword", "")
    keyword = f"(project LIKE '%{keyword}%') OR (spec_code LIKE '%{keyword}%') OR (spec_name LIKE '%{keyword}%')" if len(keyword) > 0 else '1=1'

    sql = f"SELECT DISTINCT spec_code, spec_name FROM rms_spec_flat WHERE project = '{project_id}' AND ({keyword}) ORDER BY spec_code"
    with db(dict_cursor=True) as (_, cur):
        cur.execute(sql)
        rows = [{"id": r["spec_code"], "specCode": r["spec_code"], "specName": r["spec_name"]} for r in cur.fetchall()]
    return send_response(200, True, "OK", rows)

@bp.get("/engineering/unassigned-processes")
def list_unassigned_processes():
    keyword  = request.args.get("keyword")
    page     = request.args.get("page")
    pageSize = request.args.get("pageSize")

    specification_dict = {}
    try:
        with odb(db_alias = "machine_db") as cur:
            sql = f"""
                SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME FROM SAJET.SYS_PROCESS p
                JOIN SAJET.SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
                JOIN SAJET.SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
                JOIN SAJET.SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
                WHERE {WHERE_PREFIX}
            """

            if keyword != None:
                sql += f" AND p.PROCESS_NAME LIKE '%{keyword}%'"

            cur.execute(sql)
            specification_dict = {r[0]: r[1] for r in cur.fetchall()}

    except Exception as e:
        print(f"error result: {e}")
        return send_response(400, True, "查詢失敗", {"message": "查詢 Oracle 資料庫失敗，請重新嘗試"})
    
    assign_specifications = []
    try:
        with db(dict_cursor = True) as (_, cur):
            cur.execute("SELECT DISTINCT spec_code FROM rms_spec_flat")
            assign_specifications = [r["spec_code"] for r in cur.fetchall() if r.get("spec_code")]

    except Exception as e:
        print(f"error result: {e}")
        return send_response(400, True, "查詢失敗", {"message": "查詢 MySQL 資料庫失敗，請重新嘗試"})

    unassign_specifications = set(specification_dict.keys()) - set(assign_specifications)
    unassigned = [{"id": spec_code, "specCode": spec_code, "specName": specification_dict[spec_code]} for spec_code in list(unassign_specifications)]

    # Stable sort
    unassigned.sort(key=lambda x: (x["specCode"], x["specName"]))
    return send_response(200, True, "OK", _paginate(unassigned, page, pageSize))

@bp.post("/engineering/<project_id>/processes")
def add_processes_to_engineering(project_id):
    """
    Add specs to a project by spec_code list. We DON'T write to Oracle.
    We insert minimal rows into MySQL mapping table:
      dept_code='NA', work_center_name='NA'
    """

    project = project_id
    payload = request.get_json(force=True) or {}
    process_ids = payload.get("processIds") or []  # list of spec_code

    if not process_ids:
        return send_response(400, False, "缺少 processIds", {"message": "請提供要加入的製程代碼陣列"})

    # Bulk insert IGNORE
    sql = "INSERT IGNORE INTO rms_spec_flat (dept_code, work_center_name, spec_code, spec_name, project) VALUES (%s, %s, %s, %s, %s)"
    vals = [("NA", "NA", process_id["code"], clean_desc_to_name(process_id["desc"]), project) for process_id in process_ids]

    with db(dict_cursor=True) as (conn, cur):
        cur.executemany(sql, vals)
        conn.commit()

    return send_response(200, True, "新增成功", {"added": len(vals)})

@bp.delete("/engineering/<project_id>/processes/<spec_code>")
def delete_process_from_engineering(project_id, spec_code):
    """Remove a mapping row(s) for this project/spec_code."""
    project = project_id
    spec_code = spec_code
    with db(dict_cursor=True) as (conn, cur):
        cur.execute("DELETE FROM rms_spec_flat WHERE project=%s AND spec_code=%s", (project, spec_code))
        conn.commit()
    return send_response(200, True, "移除成功", {"deletedSpec": spec_code})

# -------------------------------------- PMS --------------------------------------

HEADER_ROW = ['槽體', '管理項目', "定值項目", '規格下限(OOS-)', '操作下限(OOC-)', '設定值', '操作上限(OOC+)', '規格上限(OOS+)', '說明']
_nz = lambda v: (v or '').strip()

# Use for manufacturing block for instruction document
@bp.get("/pms/machine-parameters-set-attribute")
def get_machine_pms_parameters_set_attribute():
    """
    Query Oracle MES PMS rows for a machine and return:
      - items: list of dicts { slot_name, parameter_desc, unit, set_attribute }
      - table_rows: [[HEADER...], [槽體,管理項目,'','','','','',單位,'Y',''], ...]
    Filters:
      - MACHINE_CODE = :machine_id
      - PARAM_COMPARE = 'Y'
    """
    machine_id = _nz(request.args.get("machine_id"))
    if not machine_id:
        return send_response(400, True, "缺少機台代碼", {"message": "請提供 machine_id"})

    try:
        with ora_cursor(db_alias = "machine_db") as cur:
            cur.execute(
                f"""
                SELECT TRIM(SLOT_NAME) AS SLOT_NAME, TRIM(PARAMETER_DESC) AS PARAMETER_DESC, TRIM(UNIT) AS UNIT, TRIM(SET_ATTRIBUTE) AS SET_ATTRIBUTE FROM SAJET.FLEX_PMS
                WHERE MACHINE_CODE = :c AND {MANUFACTURING_PREFIX}
                ORDER BY SLOT_NUM, PMS_ID
                """,
                c=machine_id
            )
            rows = cur.fetchall()

        # Normalize to objects
        items: List[Dict[str, str]] = []
        for slot_name, parameter_desc, unit, set_attr in rows or []:
            items.append({"slot_name": _nz(slot_name), "parameter_desc": _nz(parameter_desc),"unit": _nz(unit), "set_attribute": _nz(set_attr) or 'Y'})

        # Build table_rows for frontend to drop into the TipTap table model easily
        # 表頭 + 每列填「槽體 / 管理項目 / 單位 / 參數下放」，中間 5 欄位與「說明」留空字串
        table_rows: List[List[str]] = []
        if items:
            table_rows.append(HEADER_ROW[:])  # header
            for it in items:
                unit = "(%s)" % it["unit"] if it["unit"] != None and len(it["unit"]) > 0 else ""
                table_rows.append([it["slot_name"], f'{it["parameter_desc"]}{unit}', '', '', '', '', '', ''])

        payload = {"items": items, "table_rows": table_rows}
        # 若為空，前端可據此顯示「無參數下放資料」
        return send_response(200, True, "請求成功", payload)

    except Exception as e:
        return send_response(500, True, "查詢失敗", {"message": f"Oracle 錯誤: {e}"})

# Use for management block of instruction document
@bp.get("/pms/machine-parameters")
def get_machine_pms_parameters():
    """
    Query Oracle MES PMS rows for a machine and return:
      - items: list of dicts { slot_name, parameter_desc, unit, set_attribute }
      - table_rows: 2D array for TipTap initial table content
    Filters:
      - MACHINE_CODE = :machine_id
      - PARAM_COMPARE = 'Y'
      - SET_ATTRIBUTE = 'Y'
      - (SET_POINT IS NOT NULL OR REAL_POINT IS NOT NULL)
      - de-duplicate by (SLOT_NAME, PARAMETER_DESC)
    """
    machine_id = _nz(request.args.get("machine_id"))
    if not machine_id:
        return send_response(400, True, "缺少機台代碼", {"message": "請提供 machine_id"})

    try:
        with ora_cursor(db_alias = "machine_db") as cur:
            cur.execute(
                f"""
                SELECT slot_name, parameter_desc, unit, set_attribute FROM (
                    SELECT
                        PMS_ID, SLOT_NUM, TRIM(SLOT_NAME) AS slot_name, TRIM(PARAMETER_DESC) AS parameter_desc, TRIM(UNIT) AS unit, TRIM(SET_ATTRIBUTE) AS set_attribute,
                        ROW_NUMBER() OVER (PARTITION BY TRIM(SLOT_NAME), TRIM(PARAMETER_DESC), TRIM(UNIT) ORDER BY TRIM(SLOT_NAME), TRIM(PARAMETER_DESC)) AS rn FROM SAJET.FLEX_PMS
                    WHERE MACHINE_CODE = :c AND {MANAGEMENT_PREFIX}
                )
                WHERE rn = 1
                ORDER BY SLOT_NUM, PMS_ID
                """,
                c=machine_id
            )
            rows = cur.fetchall()

        items: List[Dict[str, str]] = []
        for slot_name, parameter_desc, unit, set_attr in rows or []:
            items.append({"slot_name": _nz(slot_name), "parameter_desc": _nz(parameter_desc), "unit": _nz(unit), "set_attribute": _nz(set_attr) or 'Y'})

        # ---- 新版 header ----
        HEADER_ROW = ["項次", "槽體", "管理項目", "規格下限(OOS-)", "操作下限(OOC-)", "設定值", "操作上限(OOC+)", "規格上限(OOS+)", "檢查頻率", "檢查方式", "檢驗人員", "記錄", "備註/參考指示書"]
        table_rows: List[List[str]] = []
        if items:
            table_rows.append(HEADER_ROW[:])
            for idx, it in enumerate(items, start=1):
                unit = "(%s)" % it["unit"] if it["unit"] != None and len(it["unit"]) > 0 else ""
                table_rows.append([str(idx), it["slot_name"], f'{it["parameter_desc"]}{unit}', "", "", "", "", "", "", "", "", "", ""])

        payload = {"items": items, "table_rows": table_rows}
        return send_response(200, True, "請求成功", payload)

    except Exception as e:
        return send_response(500, True, "查詢失敗", {"message": f"Oracle 錯誤: {e}"})

# Use for process flow of instruction document
@bp.get("/pms/machine-process-flow")
def get_machine_process_flow():
    """
    取得某機台的「預設製程流程」欄位：
      - 從 SAJET.FLEX_PMS 取出 DISTINCT SLOT_NAME
      - 條件：
          MACHINE_CODE = :machine_id
          (REAL_POINT IS NOT NULL OR SET_POINT IS NOT NULL)
      - 以最小 PMS_ID 排序（流程前後順序）
    回傳：
      { success: True, data: { slots: ["熱水洗1", "剝膜1", ...] } }
    """
    machine_id = _nz(request.args.get("machine_id"))
    if not machine_id:
        return send_response(400, True, "缺少機台代碼", {"message": "請提供 machine_id"})

    try:
        with ora_cursor(db_alias = "machine_db") as cur:
            cur.execute(
                f"""
                SELECT slot_name FROM (
                    SELECT TRIM(SLOT_NAME) AS slot_name, MIN(PMS_ID) AS min_pms_id FROM SAJET.FLEX_PMS WHERE MACHINE_CODE = :c AND (SLOT_NAME NOT LIKE '%生產資訊%' AND SLOT_NAME NOT LIKE '%參數下放%') GROUP BY TRIM(SLOT_NAME)
                )
                ORDER BY min_pms_id
                """,
                c=machine_id,
            )
            rows = cur.fetchall()

        slots = [_nz(r[0]) for r in rows] if rows else []

        return send_response(200, True, "請求成功", {"slots": slots})
    except Exception as e:
        return send_response(500, True, "查詢失敗", {"message": f"Oracle 錯誤: {e}"})

@bp.get("/step1/specs")
def step1_specs():
    """
    製程選單：
      - keyword 模糊搜尋 PROCESS_NAME
      - 可選擇帶 machine / matnr 做額外過濾
    """
    keyword = (request.args.get("keyword") or "").strip().lower()
    machine = (request.args.get("machine") or "").strip()
    matnr   = (request.args.get("matnr") or "").strip()
    specifications = []

    if matnr:
        try:
            with ora_cursor(db_alias = 'item_db') as cur:
                # 基礎：從 SYS_PROCESS 出發
                table_name = 'EZFLEX."KKME_Table"'
                sql = f"SELECT DISTINCT STATION FROM {table_name} WHERE (CASE WHEN INSTR(ITEM, '-') > 0 THEN SUBSTR(ITEM, 1, INSTR(ITEM, '-') - 1) ELSE ITEM END) = :matnr"
                cur.execute(sql, {"matnr": matnr})
                specifications = [row[0] for row in cur.fetchall()]

                if len(specifications) == 0:
                    return send_response(400, True, "查詢失敗", {"message": "此品目無任何製程工站"})

        except Exception as e:
            print("Error /mes/step1/specs:", e)
            return send_response(500, True, "查詢失敗", {"message": str(e)})
    
    try:
        with ora_cursor(db_alias="machine_db") as cur:
            # 假設 WHERE_PREFIX 是全域變數，若無則預設 '1=1' 以方便串接 AND
            # 注意：p.PROCESS_DESC 應該是對應上面的 KTSCH
            base_sql = f"""
                SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME 
                FROM SAJET.SYS_PROCESS p
                JOIN SAJET.SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
                JOIN SAJET.SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
                WHERE {WHERE_PREFIX} 
            """
            # 如果你有特定的 WHERE_PREFIX，請加在 WHERE 之後，並確保以 AND 結尾或邏輯正確
            
            binds = {}

            # 1. 處理 Keyword
            if keyword:
                base_sql += " AND LOWER(p.PROCESS_NAME) LIKE :keyword"
                binds["keyword"] = f"%{keyword}%"

            # 2. 處理 Machine
            if machine:
                base_sql += " AND sm.MACHINE_CODE = :machine"
                binds["machine"] = machine

            # 3. [重點] 處理 Matnr 的 Specifications (IN Clause)
            if matnr:
                # 因為 specifications 是一個 List，我們需要動態產生 :s0, :s1, :s2...
                # 這樣做最安全，也能處理特殊字元
                bind_names = [f":spec_{i}" for i in range(len(specifications))]
                placeholders = ", ".join(bind_names)
                
                base_sql += f" AND p.PROCESS_DESC IN ({placeholders})"
                
                # 將值塞入 binds 字典
                for name, value in zip(bind_names, specifications):
                    # 注意：bind name 在字典key中不需要冒號 (視 library 而定，cx_Oracle/oracledb 通常不需要)
                    key = name.replace(":", "") 
                    binds[key] = value

            base_sql += " ORDER BY p.PROCESS_DESC"

            print(f"sql: {base_sql}") # Debug 用
            print(f"binds: {binds}")   # Debug 用

            cur.execute(base_sql, binds)
            rows = cur.fetchall()

        specifics = [{"code": r[0], "name": r[1]} for r in rows]
        
        return send_response(200, True, "請求成功", {"specifics": specifics})

    except Exception as e:
        print("Error /mes/step1/specs (SYS_PROCESS):", e)
        return send_response(500, True, "查詢失敗", {"message": str(e)})

# Use on management specification of instruction document 
@bp.post("/pms/pms-match")
def pms_match():
    body       = request.get_json(silent=True) or {}
    machine_id = body.get('machine_id')
    src_pms    = body.get("pmsData")
    
    if src_pms == None or len(src_pms) == 0:
        src_pms_item = {}
        src_custom_pms_items = []
    else:
        src_pms_item = {f"{row[1]}-{row[2]}": {"data": row[1:], "isPms": row[0]} for row in src_pms}
        src_custom_pms_items = [row[1:] for row in src_pms if row[0] == False]

    if not machine_id or len(machine_id) == 0:
        return send_response(400, True, "缺少機台代碼", {"message": "請提供 machine_id"})

    try:
        with ora_cursor(db_alias = "machine_db") as cur:
            cur.execute(f"SELECT SLOT_NAME, PARAMETER_DESC, UNIT FROM SAJET.FLEX_PMS WHERE MACHINE_CODE = :c AND {MANAGEMENT_PREFIX} ORDER BY SLOT_NUM, PMS_ID", c = machine_id)
            rows = cur.fetchall()

    except Exception as e:
        return send_response(500, True, "查詢失敗", {"message": f"Oracle 錯誤: {e}"})
    
    latestPMS = {}
    for slot_name, parameter_desc, unit in rows:
        unit = "(%s)" % unit if unit != None and len(unit) > 0 else ""
        key = f'{slot_name}-{parameter_desc}{unit}'
        latestPMS[key] = {"slotname": slot_name, "parameter_desc": f"{parameter_desc}{unit}"}

    # addList = [rowPMS for rowPMS in latestPMS if src_pms_item.get(rowPMS) == None or (src_pms_item.get(rowPMS) and src_pms_item.get(rowPMS)['isPms'] == False)]
    # delList = [rowPMS for rowPMS, PMS_info in src_pms_item.items() if PMS_info['isPms'] == True and latestPMS.get(rowPMS) == None]
    addList = [rowPMS for rowPMS in latestPMS if src_pms_item.get(rowPMS) == None]
    delList = [rowPMS for rowPMS, PMS_info in src_pms_item.items() if latestPMS.get(rowPMS) == None]

    rowIndex = 1
    HEADER_ROW = ["PMS", "項次", "槽體", "管理項目", "規格下限(OOS-)", "操作下限(OOC-)", "設定值", "操作上限(OOC+)", "規格上限(OOS+)", "檢查頻率", "檢查方式", "檢驗人員", "記錄", "備註/參考指示書"]
    newPMSData = [HEADER_ROW]
    for rowPMS in latestPMS:
        while len(src_custom_pms_items) > 0 and rowIndex >= int(src_custom_pms_items[0][0]):
            newPMSData.append([False, str(rowIndex)] + src_custom_pms_items.pop(0)[1:])
            rowIndex += 1

        if (src_pms_item.get(rowPMS) != None):
            newPMSData.append([True, str(rowIndex)] + src_pms_item[rowPMS]["data"])
        else:
            newPMSData.append([True, str(rowIndex), latestPMS[rowPMS]["slotname"], latestPMS[rowPMS]["parameter_desc"], "", "", "", "", "", "", "", "", "", ""])

        rowIndex += 1

    while len(src_custom_pms_items) > 0:
        newPMSData.append([False, str(rowIndex)] + src_custom_pms_items.pop(0))
        rowIndex += 1

    return send_response(200, True, "查詢成功", {"data": {"PMSData": newPMSData, "added": addList, "removed": delList}})

# Not use for frontend
@bp.post("/pms/pms-cond-match")
def pms_and_condition_match():
    body       = request.get_json(silent=True) or {}
    machine_id = body.get('machine_id')
    src_pms    = body.get("pmsData")
    src_cond   = body.get("condData")

    if not machine_id or len(machine_id) == 0:
        return send_response(400, True, "缺少機台代碼", {"message": "請提供 machine_id"})

    try:
        with ora_cursor(db_alias = "machine_db") as cur:
            cur.execute(f"SELECT SLOT_NAME, PARAMETER_DESC, UNIT FROM SAJET.FLEX_PMS WHERE MACHINE_CODE = :c AND {MANUFACTURING_PREFIX} ORDER BY PMS_ID", c = machine_id)
            pms_rows = cur.fetchall()

    except Exception as e:
        return send_response(500, True, "查詢失敗", {"message": f"Oracle 錯誤: {e}"})
    
    try:
        with db() as (_, cur):
            cur.execute("""
                SELECT t1.condition_name, t2.parameter_name FROM rms_conditions t1
                JOIN rms_condition_parameters t2 ON t1.condition_id = t2.condition_id
                JOIN rms_group_machines t3 ON t1.condition_id = t3.condition_id
                WHERE t3.machine_id = :c
            """, c = machine_id)
            cond_rows = cur.fetchall()
        
    except Exception as e:
        return send_response(500, True, "查詢失敗", {"message": f"MySQL 錯誤: {e}"})
    
    latestPMS = {}
    for slot_name, parameter_desc, unit in pms_rows:
        unit = "(%s)" % unit if unit != None and len(unit) > 0 else ""
        key = f'{slot_name}-{parameter_desc}{unit}'
        latestPMS[key] = {"slotname": slot_name, "parameter_desc": f"{parameter_desc}{unit}"}

    src_pms_item = {f"{row[0]}-{row[1]}": row for row in src_pms}
    addList = [rowPMS for rowPMS in latestPMS if src_pms_item.get(rowPMS) == None]
    delList = [rowPMS for rowPMS in src_pms_item if latestPMS.get(rowPMS) == None]

    HEADER_ROW = ["槽體", "管理項目", "規格下限(OOS-)", "操作下限(OOC-)", "設定值", "操作上限(OOC+)", "規格上限(OOS+)", "參數下放", "說明"]
    newPMSData = [HEADER_ROW]
    for rowPMS in latestPMS:
        row = src_pms_item[rowPMS]["data"] if (src_pms_item.get(rowPMS) != None) else [latestPMS[rowPMS]["slotname"], latestPMS[rowPMS]["parameter_desc"], "", "", "", "", "", "Y", ""]
        newPMSData.append(row)

    latestCond = {}
    for condition, parameter in cond_rows:
        if latestCond.get(condition) == None:
            latestCond[condition] = {"parameter": []}
        latestCond[condition]["parameter"].append(parameter)

    addList = [condition for condition in latestCond if src_cond.get(condition) == None]
    delList = [condition for condition in src_cond if latestCond.get(condition) == None]

    for condition, cond_info in latestCond.items():
        if src_cond.get(condition) and src_cond[condition] not in cond_info["parameter"]:
            latestCond[condition]["del_item"] = src_cond[condition]

    return send_response(200, True, "查詢成功", {"data": {"PMS": {"PMSData": newPMSData, "added": addList, "removed": delList}, "Cond": {"condData": latestCond, "added": addList, "removed": delList}}})

# Not use for frontend
@bp.get("/pms/get-latest-pms-and-condition")
def get_latest_pms_and_condition():
    machine_id = request.args.get("machine_id")

    if not machine_id or len(machine_id) == 0:
        return send_response(400, True, "缺少機台代碼", {"message": "請提供 machine_id"})

    try:
        with ora_cursor(db_alias = "machine_db") as cur:
            cur.execute(f"SELECT SLOT_NAME, PARAMETER_DESC, UNIT FROM SAJET.FLEX_PMS WHERE MACHINE_CODE = :c AND {MANUFACTURING_PREFIX} ORDER BY PMS_ID", c = machine_id)
            pms_rows = cur.fetchall()

    except Exception as e:
        return send_response(500, True, "查詢失敗", {"message": f"Oracle 錯誤: {e}"})
    
    try:
        with db() as (_, cur):
            cur.execute("""
                SELECT t1.condition_name, t2.parameter_name FROM rms_conditions t1
                JOIN rms_condition_parameters t2 ON t1.condition_id = t2.condition_id
                JOIN rms_group_machines t3 ON t1.condition_id = t3.condition_id
                WHERE t3.machine_id = :c
            """, c = machine_id)
            cond_rows = cur.fetchall()
        
    except Exception as e:
        return send_response(500, True, "查詢失敗", {"message": f"MySQL 錯誤: {e}"})

    return send_response(200, True, "查詢成功", {"data": {"PMS": pms_rows, "Cond": cond_rows}})

@bp.get("/pms/machine-all-templates")
def get_machine_all_templates():
    machine_id = request.args.get("machine_id", "").strip()
    if not machine_id:
        return send_response(400, False, "缺少機台代碼", {"message": "請提供 machine_id"})

    # 準備一個大包裝盒，用來裝你的四份資料
    payload = {
        "condTemplate": [],
        "paramTemplate": {"items": [], "table_rows": []},
        "pmsTemplate": {"items": [], "table_rows": []},
        "pfTemplate": {"slots": []}
    }

    # ==========================================
    # 1. 取得 Conditions 條件參數 (來自 MySQL)
    # ==========================================
    try:
        with db() as (conn, cur):
            like = f"{machine_id.lower()}"
            cur.execute("""
                SELECT DISTINCT rc.condition_id, rc.condition_name, rcp.parameter_name FROM rms_conditions rc
                INNER JOIN rms_condition_parameters rcp ON rc.condition_id = rcp.condition_id
                INNER JOIN rms_group_machines rgm ON rc.condition_id = rgm.condition_id
                WHERE LOWER(rgm.machine_id) = %s
                ORDER BY rc.condition_id;
            """, (like,))
            data = cur.fetchall()
            conditions_name_index_dict = {}
            for cid, cname, pname in data:
                if conditions_name_index_dict.get(cname) is None:
                    payload["condTemplate"].append({"name": cname, "id": cid, "parameters": []})
                    conditions_name_index_dict[cname] = len(payload["condTemplate"]) - 1
                
                payload["condTemplate"][conditions_name_index_dict[cname]]["parameters"].append(pname)
    except Exception as e:
        print(f"MySQL Error: {e}")
        # 若發生錯誤，可以選擇記錄並繼續執行 Oracle，或直接 return
        return send_response(500, False, "條件參數查詢失敗", {"message": str(e)})

    # ==========================================
    # 2. 取得 PMS, Params, Process Flow (來自 Oracle)
    # ==========================================
    try:
        # 只建立「一次」 Oracle 連線
        with ora_cursor(db_alias="machine_db") as cur:
            
            # (A) 取得 Process Flow (pfTemplate)
            cur.execute(f"""
                SELECT slot_name FROM (
                    SELECT TRIM(SLOT_NAME) AS slot_name, MIN(PMS_ID) AS min_pms_id 
                    FROM SAJET.FLEX_PMS 
                    WHERE MACHINE_CODE = :c AND (SLOT_NAME NOT LIKE '%生產資訊%' AND SLOT_NAME NOT LIKE '%參數下放%') 
                    GROUP BY TRIM(SLOT_NAME)
                ) ORDER BY min_pms_id
            """, c=machine_id)
            pf_rows = cur.fetchall()
            payload["pfTemplate"]["slots"] = [_nz(r[0]) for r in pf_rows] if pf_rows else []

            # (B) 取得 Manufacturing Params 參數下放 (paramTemplate)
            cur.execute(f"""
                SELECT TRIM(SLOT_NAME), TRIM(PARAMETER_DESC), TRIM(UNIT), TRIM(SET_ATTRIBUTE) 
                FROM SAJET.FLEX_PMS
                WHERE MACHINE_CODE = :c AND {MANUFACTURING_PREFIX}
                ORDER BY SLOT_NUM, PMS_ID
            """, c=machine_id)
            
            mfg_items = []
            mfg_table_rows = []
            mfg_rows = cur.fetchall()
            
            if mfg_rows:
                mfg_table_rows.append(HEADER_ROW[:]) # 插入前端所需表頭
                for slot_name, param_desc, unit, set_attr in mfg_rows:
                    mfg_items.append({"slot_name": _nz(slot_name), "parameter_desc": _nz(param_desc), "unit": _nz(unit), "set_attribute": _nz(set_attr) or 'Y'})
                    unit_str = f"({_nz(unit)})" if _nz(unit) else ""
                    mfg_table_rows.append([_nz(slot_name), f'{_nz(param_desc)}{unit_str}', '', '', '', '', '', ''])
            
            payload["paramTemplate"] = {"items": mfg_items, "table_rows": mfg_table_rows}

            # (C) 取得 Management Params 管理項目 (pmsTemplate)
            cur.execute(f"""
                SELECT slot_name, parameter_desc, unit, set_attribute FROM (
                    SELECT PMS_ID, SLOT_NUM, TRIM(SLOT_NAME) AS slot_name, TRIM(PARAMETER_DESC) AS parameter_desc, 
                           TRIM(UNIT) AS unit, TRIM(SET_ATTRIBUTE) AS set_attribute,
                           ROW_NUMBER() OVER (PARTITION BY TRIM(SLOT_NAME), TRIM(PARAMETER_DESC), TRIM(UNIT) ORDER BY TRIM(SLOT_NAME), TRIM(PARAMETER_DESC)) AS rn 
                    FROM SAJET.FLEX_PMS
                    WHERE MACHINE_CODE = :c AND {MANAGEMENT_PREFIX}
                ) WHERE rn = 1 ORDER BY SLOT_NUM, PMS_ID
            """, c=machine_id)
            
            mgmt_items = []
            mgmt_table_rows = []
            mgmt_rows = cur.fetchall()
            
            MGMT_HEADER = ["項次", "槽體", "管理項目", "定值項目", "規格下限(OOS-)", "操作下限(OOC-)", "設定值", "操作上限(OOC+)", "規格上限(OOS+)", "檢查頻率", "檢查方式", "檢驗人員", "記錄", "備註/參考指示書"]
            
            if mgmt_rows:
                mgmt_table_rows.append(MGMT_HEADER[:])
                for idx, (slot_name, param_desc, unit, set_attr) in enumerate(mgmt_rows, start=1):
                    mgmt_items.append({"slot_name": _nz(slot_name), "parameter_desc": _nz(param_desc), "unit": _nz(unit), "set_attribute": _nz(set_attr) or 'Y'})
                    unit_str = f"({_nz(unit)})" if _nz(unit) else ""
                    mgmt_table_rows.append([str(idx), _nz(slot_name), f'{_nz(param_desc)}{unit_str}', "", "", "", "", "", "", "", "", "", ""])

            payload["pmsTemplate"] = {"items": mgmt_items, "table_rows": mgmt_table_rows}

    except Exception as e:
        print(f"Oracle Error: {e}")
        return send_response(500, False, "Oracle 查詢失敗", {"message": str(e)})

    # 一次性回傳所有資料！
    return send_response(200, True, "請求成功", payload)

# Use for manufacturing block for specification document
@bp.post("/fetch-machine-spec-pms")
def fetch_machine_spec_pms():
    """
    根據選定的 機台群組 (groupCode) 與 基準機台 (machineCode)，
    獲取該機台的 PMS 參數表，並找出同群組內具備「完全相同 PMS」的機台清單。
    如果基準機台無 PMS，則只會配對同群組中同樣「無 PMS」的機台。
    """
    data = request.json or {}
    group_code = data.get("groupCode")
    machine_code = data.get("machineCode")
    
    if not group_code or not machine_code:
        return send_response(400, False, "參數錯誤", {"message": "缺少 groupCode 或 machineCode"})

    try:
        with odb(db_alias="machine_db") as cur:
            # === 第一步：取得基準機台的 PMS 表格資料 ===
            sql_data = f"SELECT SLOT_NAME, PARAMETER_DESC, UNIT FROM SAJET.FLEX_PMS WHERE MACHINE_CODE = :m AND {MANUFACTURING_PREFIX} ORDER BY PMS_ID"
            cur.execute(sql_data, m = machine_code)
            rows_data = cur.fetchall()

            temp_pms_rows = []
            for index, r in enumerate(rows_data):
                slot, param, unit = r
                param = f"{param}({unit})" if unit and len(unit) > 0 else param
                # 預留填寫數值的空欄位
                row = [slot, param, "", "", "", "", "", ""]
                temp_pms_rows.append(row)

            header = ["槽體", "管理項目", "定值項目", "規格下限(OOS-)", "操作下限(OOC-)", "設定值", "操作上限(OOC+)", "規格上限(OOS+)", "說明"]
            pms_table = [header] + temp_pms_rows if len(temp_pms_rows) > 0 else None

            # === 第二步：取得同群組內的相容機台 (Match Set) ===
            match_set = []

            if not temp_pms_rows:
                # ★ 狀況 A：如果基準機台沒有 PMS 資料 (N/A)
                # 使用 NOT EXISTS 確保只找出「同樣沒有 PMS」的機台
                sql_group_machines = f"""
                    SELECT sm.MACHINE_CODE FROM SAJET.SYS_MACHINE sm JOIN SAJET.SYS_MACHINE_TYPE mt ON sm.MACHINE_TYPE_ID = mt.MACHINE_TYPE_ID WHERE mt.MACHINE_TYPE_NAME = :g AND sm.EQM_ID <> 'NA'
                    AND NOT EXISTS ( SELECT 1 FROM SAJET.FLEX_PMS p WHERE p.MACHINE_CODE = sm.MACHINE_CODE AND {MANUFACTURING_PREFIX} )
                """
                cur.execute(sql_group_machines, g = group_code)
                match_set = [r[0] for r in cur.fetchall()]
            
            else:
                sql_filter = f"""
                    WITH BASELINE_DATA AS ( SELECT NVL(SLOT_NAME, '#NULL#') AS S_NAME, NVL(PARAMETER_DESC, '#NULL#') AS P_DESC FROM SAJET.FLEX_PMS WHERE MACHINE_CODE = :m AND {MANUFACTURING_PREFIX} ),
                    BASELINE_CNT AS ( SELECT COUNT(*) AS CNT FROM BASELINE_DATA ),
                    GROUP_MACHINES AS ( 
                        SELECT sm.MACHINE_CODE FROM SAJET.SYS_MACHINE sm 
                        JOIN SAJET.SYS_MACHINE_TYPE mt ON sm.MACHINE_TYPE_ID = mt.MACHINE_TYPE_ID 
                        WHERE mt.MACHINE_TYPE_NAME = :g AND sm.EQM_ID <> 'NA' 
                    )
                    SELECT GM.MACHINE_CODE FROM GROUP_MACHINES GM 
                    LEFT JOIN SAJET.FLEX_PMS P ON GM.MACHINE_CODE = P.MACHINE_CODE AND P.PARAM_COMPARE = 'Y'
                    GROUP BY GM.MACHINE_CODE HAVING COUNT(P.PMS_ID) = (SELECT CNT FROM BASELINE_CNT) AND COUNT(CASE WHEN (NVL(P.SLOT_NAME, '#NULL#'), NVL(P.PARAMETER_DESC, '#NULL#')) IN (SELECT S_NAME, P_DESC FROM BASELINE_DATA) THEN 1 END) = (SELECT CNT FROM BASELINE_CNT)
                """
                cur.execute(sql_filter, m = machine_code, g = group_code)
                match_set = [row[0] for row in cur.fetchall()]

            print(match_set)
        # 確保回傳結構符合前端期望: { pms, matchSet }
        return send_response(200, True, "獲取成功", {"pms": pms_table, "matchSet": match_set})

    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"[Fetch PMS Error] {str(e)}")
        return send_response(500, False, "獲取機台 PMS 失敗", {"message": str(e)})
    
# Use for manufacturing block loading for specification document
@bp.post("/fetch-manufacture-info")
def fetch_manufacture_info():
    """
    批次獲取多個 Block 的 PMS 樣板與相容機台清單 (matchSet)。
    使用 IN 語法優化第一階段的 PMS 查詢。
    """
    payload = request.json.get("payload", {})
    results = {}
    
    if not payload:
        return send_response(200, True, "無須獲取", {})

    try:
        # --- 1. 蒐集所有要查詢的基準機台 (去重) ---
        machine_to_blks = {} # machine_code -> list of (blk_id, group_code)
        for blk_id, req_data in payload.items():
            group_code = req_data.get("groupCode")
            machines = req_data.get("machines", [])
            results[blk_id] = {"pms": None, "matchSet": []} # 初始化預設回傳值
            
            if group_code and machines:
                machine_code = machines[0] # 取第一台作為基準
                if machine_code not in machine_to_blks:
                    machine_to_blks[machine_code] = []
                machine_to_blks[machine_code].append((blk_id, group_code))

        machine_codes = list(machine_to_blks.keys())
        if not machine_codes:
            return send_response(200, True, "獲取成功", results)

        with odb(db_alias="machine_db") as cur:
            # --- 2. 批量查詢所有基準機台的 PMS (IN 語法) ---
            # 動態產生綁定變數，例如 :m0, :m1, :m2
            bind_names = [f":m{i}" for i in range(len(machine_codes))]
            bind_dict = {f"m{i}": code for i, code in enumerate(machine_codes)}
            
            sql_pms = f"""
                SELECT MACHINE_CODE, SLOT_NAME, PARAMETER_DESC, UNIT 
                FROM SAJET.FLEX_PMS 
                WHERE MACHINE_CODE IN ({','.join(bind_names)}) 
                AND {MANUFACTURING_PREFIX} 
                ORDER BY MACHINE_CODE, PMS_ID
            """
            cur.execute(sql_pms, **bind_dict)
            rows_data = cur.fetchall()

            # 將結果依照 MACHINE_CODE 分類
            pms_by_machine = {code: [] for code in machine_codes}
            for r in rows_data:
                m_code, slot, param, unit = r
                param_str = f"{param}({unit})" if unit and len(unit) > 0 else param
                pms_by_machine[m_code].append([slot, param_str, "", "", "", "", "", ""])

            header = ["槽體", "管理項目", "規格下限(OOS-)", "操作下限(OOC-)", "設定值", "操作上限(OOC+)", "規格上限(OOS+)", "說明"]

            # --- 3. 處理每個 Block 的資料封裝與 MatchSet ---
            for m_code, blk_list in machine_to_blks.items():
                temp_pms_rows = pms_by_machine[m_code]
                pms_table = [header] + temp_pms_rows if temp_pms_rows else None

                for blk_id, group_code in blk_list:
                    results[blk_id]["pms"] = pms_table
                    
                    # Match Set 查詢 (因為邏輯涉及自身比對，保留單一查詢)
                    match_set = []
                    if not temp_pms_rows:
                        sql_group_machines = f"""
                            SELECT sm.MACHINE_CODE FROM SAJET.SYS_MACHINE sm JOIN SAJET.SYS_MACHINE_TYPE mt ON sm.MACHINE_TYPE_ID = mt.MACHINE_TYPE_ID WHERE mt.MACHINE_TYPE_NAME = :g AND sm.EQM_ID <> 'NA'
                            AND NOT EXISTS ( SELECT 1 FROM SAJET.FLEX_PMS p WHERE p.MACHINE_CODE = sm.MACHINE_CODE AND {MANUFACTURING_PREFIX} )
                        """
                        cur.execute(sql_group_machines, g=group_code)
                        match_set = [r[0] for r in cur.fetchall()]
                    else:
                        sql_filter = f"""
                            WITH BASELINE_DATA AS ( SELECT NVL(SLOT_NAME, '#NULL#') AS S_NAME, NVL(PARAMETER_DESC, '#NULL#') AS P_DESC FROM SAJET.FLEX_PMS WHERE MACHINE_CODE = :m AND {MANUFACTURING_PREFIX} ),
                            BASELINE_CNT AS ( SELECT COUNT(*) AS CNT FROM BASELINE_DATA ),
                            GROUP_MACHINES AS ( 
                                SELECT sm.MACHINE_CODE FROM SAJET.SYS_MACHINE sm 
                                JOIN SAJET.SYS_MACHINE_TYPE mt ON sm.MACHINE_TYPE_ID = mt.MACHINE_TYPE_ID 
                                WHERE mt.MACHINE_TYPE_NAME = :g AND sm.EQM_ID <> 'NA' 
                            )
                            SELECT GM.MACHINE_CODE FROM GROUP_MACHINES GM 
                            LEFT JOIN SAJET.FLEX_PMS P ON GM.MACHINE_CODE = P.MACHINE_CODE AND P.PARAM_COMPARE = 'Y'
                            GROUP BY GM.MACHINE_CODE HAVING COUNT(P.PMS_ID) = (SELECT CNT FROM BASELINE_CNT) AND COUNT(CASE WHEN (NVL(P.SLOT_NAME, '#NULL#'), NVL(P.PARAMETER_DESC, '#NULL#')) IN (SELECT S_NAME, P_DESC FROM BASELINE_DATA) THEN 1 END) = (SELECT CNT FROM BASELINE_CNT)
                        """
                        cur.execute(sql_filter, m=m_code, g=group_code)
                        match_set = [row[0] for row in cur.fetchall()]

                    results[blk_id]["matchSet"] = match_set

        return send_response(200, True, "獲取成功", results)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return send_response(500, False, "獲取機台 PMS 失敗", {"message": str(e)})