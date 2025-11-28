from typing import Dict, List
from flask import Blueprint, request
from loginFunctions.utils import send_response

from db import db
from oracle_db import ora_cursor as odb
from utils import *

bp = Blueprint("mes", __name__)

# --------- /projects ---------
@bp.get("/projects")
def projects():
    keyword = request.args.get("keyword")
    if not keyword:
        return send_response(401, True, "沒有搜尋條件", {"message": "請輸入關鍵字"})

    # projects are stored in MySQL rms_spec_flat
    with db(dict_cursor=True) as (_, cur):
        cur.execute("SELECT DISTINCT project FROM sfdb.rms_spec_flat")
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
        spec_map_by_itemType, itemType_info = get_spec_codes_by_itemType(itemType)

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
            return send_response(200, False, "請求成功", {"groups": out})
        
        if spec_map != None and len(spec_map) > 0:
            spec_map = list(spec_map) if len(spec_map) > 1 else spec_map.pop()
        elif spec_map == None and specific != None:
            spec_map = specific

    try:
        out = {}
        with odb() as cur:
            specInfo = "" if remove_spec_info.lower() == 'true' else "p.PROCESS_DESC, p.PROCESS_NAME, "
            select_info = f"SELECT DISTINCT {specInfo}sm.MACHINE_CODE, sm.MACHINE_DESC, sm.BUILDING, mt.MACHINE_TYPE_NAME, mt.MACHINE_TYPE_DESC FROM IDBUSER.RMS_SYS_PROCESS p"
            join_info = """
                JOIN IDBUSER.RMS_SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
                JOIN IDBUSER.RMS_SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
                JOIN IDBUSER.RMS_SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
                WHERE REGEXP_LIKE(p.PROCESS_NAME, '^\([LR][0-8][[:digit:]]{2}-[[:digit:]]{2}\)') AND p.PROCESS_NAME NOT LIKE '%人工%' AND sm.ENABLED = 'Y' AND sm.EQM_ID <> 'NA'
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
    raw_specs = request.args.getlist("specific") or request.args.getlist("specific[]")
    
    if len(raw_specs) == 0:
        return send_response(400, True, "查詢失敗", {"message": "請輸入至少一個適用工程"})

    try:
        out = {}
        with odb() as cur:
            sql = """
                SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME, sm.MACHINE_CODE, sm.MACHINE_DESC, sm.BUILDING, mt.MACHINE_TYPE_NAME, mt.MACHINE_TYPE_DESC FROM IDBUSER.RMS_SYS_PROCESS p
                JOIN IDBUSER.RMS_SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
                JOIN IDBUSER.RMS_SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
                JOIN IDBUSER.RMS_SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
                WHERE REGEXP_LIKE(p.PROCESS_NAME, '^\([LR][0-8][[:digit:]]{2}-[[:digit:]]{2}\)') AND p.PROCESS_NAME NOT LIKE '%人工%' AND sm.ENABLED = 'Y' AND sm.EQM_ID <> 'NA'
            """
            if len(raw_specs) > 1:
                sql += " AND p.PROCESS_DESC IN ('" + "','".join(raw_specs) + "')"

            elif len(raw_specs) == 1:
                sql += f" AND p.PROCESS_DESC = '{raw_specs[0]}'"

            cur.execute(sql)
            for scode, sname, mcode, mname, mbuilding, gcode, gname in cur.fetchall():
                machineInfo = {"code": mcode, "name": mname, "building": mbuilding}
                if out.get(scode) == None:
                    out[scode] = {}

                if out[scode].get(gcode) == None:
                    out[scode][gcode] = {"name": gname, "machines": []}

                out[scode][gcode]["machines"].append(machineInfo)

    except Exception as e:
        print(f"error result: {e}")
        return send_response(500, True, "查詢失敗", {"message": "資料庫查詢失敗，請重新嘗試"})

    return send_response(200, True, "請求成功", {"specGroups": out})

WHERE_PREFIX = "REGEXP_LIKE(p.PROCESS_NAME, '^\([LR][0-8][[:digit:]]{2}-[[:digit:]]{2}\)') AND p.PROCESS_NAME NOT LIKE '%人工%' AND sm.ENABLED = 'Y' AND sm.EQM_ID <> 'NA'"
@bp.post("/filter-by-baseline")
def filter_by_baseline():
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
            print("1")
            return send_response(200, False, "查詢成功", {"groups": {}})

        elif specific != None and specific not in spec_map:  # Argument specification conflict with project
            print("2")
            return send_response(400, False, "查詢失敗", {"message": "查詢條件互相衝突請重新確認"})

        spec_map = list(spec_map) if len(spec_map) > 1 else spec_map.pop()
    
    # If no project input just get specific
    elif specific != None:
        spec_map = specific

    same_PMS_machines = []
    try:
        with ora_cursor() as cur:
            sql = f"""
            WITH base AS (SELECT DISTINCT TRIM(SLOT_NAME) AS SLOT_NAME FROM IDBUSER.RMS_FLEX_PMS WHERE MACHINE_CODE = '{base_code}'),
                base_cnt AS ( SELECT COUNT(*) AS cnt FROM base ),
                cand AS (SELECT DISTINCT MACHINE_CODE, TRIM(SLOT_NAME) AS SLOT_NAME FROM IDBUSER.RMS_FLEX_PMS)
            SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME, sm.MACHINE_CODE, sm.MACHINE_DESC, sm.BUILDING, mt.MACHINE_TYPE_NAME, mt.MACHINE_TYPE_DESC FROM IDBUSER.RMS_SYS_PROCESS p
            JOIN IDBUSER.RMS_SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
            JOIN IDBUSER.RMS_SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
            LEFT JOIN IDBUSER.RMS_SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID, base_cnt bc
            WHERE REGEXP_LIKE(p.PROCESS_NAME, '^\([LR][0-8][[:digit:]]{{2}}-[[:digit:]]{{2}}\)') AND p.PROCESS_NAME NOT LIKE '%人工%' AND (
                (bc.cnt = 0 AND NOT EXISTS (SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE)) OR (
                    bc.cnt > 0 AND EXISTS (SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE) AND NOT EXISTS (
                        SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE AND NOT EXISTS (SELECT 1 FROM base b WHERE b.SLOT_NAME = cx.SLOT_NAME)
                    ) AND NOT EXISTS (
                        SELECT 1 FROM base b WHERE NOT EXISTS (SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE AND cx.SLOT_NAME = b.SLOT_NAME)
                    )
                )
            ) AND sm.EQM_ID <> 'NA' AND sm.ENABLED = 'Y'
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
                LEFT JOIN sfdb.rms_group_machines g ON g.machine_id = c.machine_id GROUP BY c.machine_id
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
    keyword = request.args.get("keyword", "")
    page     = request.args.get("page")
    pageSize = request.args.get("pageSize")

    projects = []
    try:
        with db(dict_cursor=True) as (_, cur):
            cur.execute("SELECT DISTINCT project FROM sfdb.rms_spec_flat ORDER BY project")
            projects = [r["project"] for r in cur.fetchall() if keyword in r["project"]]
        
    except Exception as e:
        print(f"error result: {e}")
        return send_response(400, True, "查詢失敗", {"message": "請重新嘗試"})

    # Build rows expected by your UI: id, projectCode, projectName
    # Use the same string as both code/name unless you have a separate code.
    rows = [{"id": p, "projectCode": p, "projectName": p} for p in projects]
    return send_response(200, True, "OK", _paginate(rows, page, pageSize))

@bp.get("/engineering/<project_id>/processes")
def list_engineering_processes(project_id):
    """List specs already assigned to this project (from MySQL)."""
    project = project_id
    sql = """
      SELECT DISTINCT spec_code, spec_name
      FROM sfdb.rms_spec_flat
      WHERE project=%s
      ORDER BY spec_code
    """
    with db(dict_cursor=True) as (_, cur):
        cur.execute(sql, (project,))
        rows = [{"id": r["spec_code"], "specCode": r["spec_code"], "specName": r["spec_name"]} for r in cur.fetchall()]
    return send_response(200, True, "OK", rows)

@bp.get("/engineering/unassigned-processes")
def list_unassigned_processes():
    keyword  = request.args.get("keyword")
    page     = request.args.get("page")
    pageSize = request.args.get("pageSize")

    specification_dict = {}
    try:
        with odb() as cur:
            sql = """
                SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME FROM IDBUSER.RMS_SYS_PROCESS p
                JOIN IDBUSER.RMS_SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
                JOIN IDBUSER.RMS_SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
                JOIN IDBUSER.RMS_SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
                WHERE REGEXP_LIKE(p.PROCESS_NAME, '^\([LR][0-8][[:digit:]]{2}-[[:digit:]]{2}\)') AND p.PROCESS_NAME NOT LIKE '%人工%' AND sm.ENABLED = 'Y' AND sm.EQM_ID <> 'NA'
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
            cur.execute("SELECT DISTINCT spec_code FROM sfdb.rms_spec_flat")
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
    sql = """
      INSERT IGNORE INTO sfdb.rms_spec_flat
        (dept_code, work_center_name, spec_code, spec_name, project)
      VALUES (%s, %s, %s, %s, %s)
    """
    vals = []
    for process_id in process_ids:
        vals.append(("NA", "NA", process_id["code"], clean_desc_to_name(process_id["desc"]), project))

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
        cur.execute("DELETE FROM sfdb.rms_spec_flat WHERE project=%s AND spec_code=%s", (project, spec_code))
        conn.commit()
    return send_response(200, True, "移除成功", {"deletedSpec": spec_code})

# -------------------------------------- PMS --------------------------------------

HEADER_ROW = ['槽體', '管理項目', '規格下限(OOS-)', '操作下限(OOC-)', '設定值', '操作上限(OOC+)', '規格上限(OOS+)', '參數下放', '說明']
def _nz(v):  # small helper
    return (v or '').strip()

@bp.get("/pms/machine-parameters-set-attribute")
def get_machine_pms_parameters_set_attribute():
    """
    Query Oracle MES PMS rows for a machine and return:
      - items: list of dicts { slot_name, parameter_desc, unit, set_attribute }
      - table_rows: [[HEADER...], [槽體,管理項目,'','','','','',單位,'Y',''], ...]
    Filters:
      - MACHINE_CODE = :machine_id
      - PARAM_COMPARE = 'Y'
      - SET_ATTRIBUTE = 'Y'
    """
    machine_id = _nz(request.args.get("machine_id"))
    if not machine_id:
        return send_response(400, True, "缺少機台代碼", {"message": "請提供 machine_id"})

    try:
        with ora_cursor() as cur:
            cur.execute(
                """
                SELECT
                    TRIM(SLOT_NAME)         AS SLOT_NAME,
                    TRIM(PARAMETER_DESC)    AS PARAMETER_DESC,
                    TRIM(UNIT)              AS UNIT,
                    TRIM(SET_ATTRIBUTE)     AS SET_ATTRIBUTE
                FROM IDBUSER.RMS_FLEX_PMS
                WHERE MACHINE_CODE = :c AND NVL(PARAM_COMPARE, 'N') = 'Y' AND NVL(SET_ATTRIBUTE, 'N') = 'Y'
                ORDER BY SLOT_NAME, PARAMETER_DESC
                """,
                c=machine_id
            )
            rows = cur.fetchall()

        # Normalize to objects
        items: List[Dict[str, str]] = []
        for slot_name, parameter_desc, unit, set_attr in rows or []:
            items.append({
                "slot_name":      _nz(slot_name),
                "parameter_desc": _nz(parameter_desc),
                "unit":           _nz(unit),
                "set_attribute":  _nz(set_attr) or 'Y',  # 照條件應該都是 Y
            })

        # Build table_rows for frontend to drop into the TipTap table model easily
        # 表頭 + 每列填「槽體 / 管理項目 / 單位 / 參數下放」，中間 5 欄位與「說明」留空字串
        table_rows: List[List[str]] = []
        if items:
            table_rows.append(HEADER_ROW[:])  # header
            for it in items:
                table_rows.append([
                    it["slot_name"],          # 槽體
                    f'{it["parameter_desc"]}({it["unit"]})',     # 管理項目
                    '', '', '', '', '',       # 中間 5 欄（前端繼續編輯）
                    'Y',                      # 參數下放（依條件固定 Y）
                    ''                        # 說明
                ])

        payload = {
            "items": items,           # 物件陣列
            "table_rows": table_rows  # 直接可用在前端 DEFAULT_ROWS 的表格資料
        }
        # 若為空，前端可據此顯示「無參數下放資料」
        return send_response(200, True, "請求成功", payload)

    except Exception as e:
        return send_response(500, True, "查詢失敗", {"message": f"Oracle 錯誤: {e}"})

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
        with ora_cursor() as cur:
            cur.execute(
                """
                SELECT
                    slot_name,
                    parameter_desc,
                    unit,
                    set_attribute
                FROM (
                    SELECT
                        TRIM(SLOT_NAME)      AS slot_name,
                        TRIM(PARAMETER_DESC) AS parameter_desc,
                        TRIM(UNIT)           AS unit,
                        TRIM(SET_ATTRIBUTE)  AS set_attribute,
                        ROW_NUMBER() OVER (
                            PARTITION BY TRIM(SLOT_NAME), TRIM(PARAMETER_DESC), TRIM(UNIT)
                            ORDER BY TRIM(SLOT_NAME), TRIM(PARAMETER_DESC)
                        ) AS rn
                    FROM IDBUSER.RMS_FLEX_PMS
                    WHERE MACHINE_CODE = :c AND (SET_POINT IS NOT NULL OR REAL_POINT IS NOT NULL)
                )
                WHERE rn = 1
                ORDER BY slot_name, parameter_desc
                """,
                c=machine_id
            )
            rows = cur.fetchall()

        items: List[Dict[str, str]] = []
        for slot_name, parameter_desc, unit, set_attr in rows or []:
            items.append({
                "slot_name":      _nz(slot_name),
                "parameter_desc": _nz(parameter_desc),
                "unit":           _nz(unit),
                "set_attribute":  _nz(set_attr) or 'Y',
            })

        # ---- 新版 header ----
        # HEADER_ROW = [ "項次", "槽體", "管理項目", "規格下限(OOS-)", "操作下限(OOC-)", "設定值", "操作上限(OOC+)", "規格上限(OOS+)", "單位", "檢查頻率", "檢查方式", "檢驗人員", "記錄", "備註/參考指示書"]
        HEADER_ROW = [ "項次", "槽體", "管理項目", "規格下限(OOS-)", "操作下限(OOC-)", "設定值", "操作上限(OOC+)", "規格上限(OOS+)", "檢查頻率", "檢查方式", "檢驗人員", "記錄", "備註/參考指示書"]
        table_rows: List[List[str]] = []
        if items:
            table_rows.append(HEADER_ROW[:])
            for idx, it in enumerate(items, start=1):
                table_rows.append([
                    str(idx),                  # 項次（之後前端 updateTable 會重算也沒關係）
                    it["slot_name"],           # 槽體
                    f'{it["parameter_desc"]}({it["unit"]})',      # 管理項目(單位)
                    "", "", "", "", "",        # 規格/OOC/設定/OOC+/OOS+
                    "", "", "", "", "",        # 檢查頻率 / 檢查方式 / 檢驗人員 / 記錄 / 備註
                ])

        payload = {
            "items": items,
            "table_rows": table_rows,
        }
        return send_response(200, True, "請求成功", payload)

    except Exception as e:
        return send_response(500, True, "查詢失敗", {"message": f"Oracle 錯誤: {e}"})

@bp.get("/pms/machine-process-flow")
def get_machine_process_flow():
    """
    取得某機台的「預設製程流程」欄位：
      - 從 IDBUSER.RMS_FLEX_PMS 取出 DISTINCT SLOT_NAME
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
        with ora_cursor() as cur:
            cur.execute(
                """
                SELECT slot_name
                FROM (
                    SELECT TRIM(SLOT_NAME) AS slot_name,
                           MIN(PMS_ID)     AS min_pms_id
                    FROM IDBUSER.RMS_FLEX_PMS
                    WHERE MACHINE_CODE = :c
                      AND (REAL_POINT IS NOT NULL OR SET_POINT IS NOT NULL)
                    GROUP BY TRIM(SLOT_NAME)
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

# modules/mes_step1.py
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

    try:
        with ora_cursor() as cur:
            # 基礎：從 RMS_SYS_PROCESS 出發
            sql = """
                SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME FROM IDBUSER.RMS_SYS_PROCESS p
                JOIN IDBUSER.RMS_SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
                JOIN IDBUSER.RMS_SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
                WHERE REGEXP_LIKE(p.PROCESS_NAME, '^\([LR][0-8][[:digit:]]{2}-[[:digit:]]{2}\)') AND p.PROCESS_NAME NOT LIKE '%人工%' AND sm.ENABLED = 'Y' AND sm.EQM_ID <> 'NA'
            """
            binds = {}

            if keyword:
                sql += f" AND LOWER(p.PROCESS_NAME) LIKE '%{keyword}%'"

            # 如果有帶機台，就 join TERMINAL / MACHINE 只保留有掛在該機台線別上的製程
            if machine:
                sql += f" AND sm.MACHINE_CODE = '{machine}'"

            # 如果有帶品目 matnr，就從 EZFLEX_ROUTING / EZFLEX_TOOL 反查有哪些製程
            if matnr:
                sql += f"""
                  AND EXISTS (
                    SELECT 1 FROM IDBUSER.EZFLEX_ROUTING r
                    JOIN IDBUSER.EZFLEX_TOOL t ON r.MATNR = t.MATNR AND r.REVLV = t.REVLV AND r.VORNR = t.VORNR
                    WHERE t.MATNR LIKE '{matnr}%' AND t.SFHNR LIKE '%-ST%' AND r.KTSCH = p.PROCESS_DESC
                  )
                """

            sql += " ORDER BY p.PROCESS_DESC"

            cur.execute(sql, binds)
            rows = cur.fetchall()

        specifics = [{"code": r[0], "name": r[1]} for r in rows]
        
        return send_response(200, True, "請求成功", {"specifics": specifics})
    except Exception as e:
        print("Error /mes/step1/specs:", e)
        return send_response(500, True, "查詢失敗", {"message": str(e)})
