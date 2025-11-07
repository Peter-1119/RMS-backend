from flask import Blueprint, request, jsonify
from loginFunctions.utils import send_response
from utils import *

bp = Blueprint("mes", __name__)

# --------- small helpers ---------
def _norm(s: str) -> str:
    return (s or "").strip()

def _icontains(hay: str, needle: str) -> bool:
    return needle.lower() in hay.lower()

def _distinct(iterable):
    seen = set()
    for x in iterable:
        if x not in seen:
            seen.add(x)
            yield x

def _all_spec_codes_from_mysql() -> list[str]:
    # Fast way to enable global search by machine keyword (no project/spec filter)
    sql = "SELECT DISTINCT spec_code FROM sfdb.rms_spec_flat"
    with db(dict_cursor=True) as (_, cur):
        cur.execute(sql)
        return [r["spec_code"] for r in cur.fetchall() if r.get("spec_code")]

def _as_bool(v: str) -> bool:
    return str(v).lower() in ("1", "true", "yes", "y", "t")

# --------- /projects ---------
@bp.get("/projects")
def projects():
    keyword = _norm(request.args.get("keyword"))
    if not keyword:
        return send_response(401, True, "沒有搜尋條件", {"message": "請輸入關鍵字"})

    # projects are stored in MySQL rms_spec_flat
    with db(dict_cursor=True) as (_, cur):
        cur.execute("SELECT DISTINCT project FROM sfdb.rms_spec_flat")
        all_projects = [r["project"] for r in cur.fetchall() if r.get("project")]

    out = {}
    for name in all_projects:
        if _icontains(name, keyword):
            # you previously returned {"code": info.get("code","")}
            # we don't have separate project codes; keep field for compatibility
            out[name] = {"code": ""}

    return send_response(200, True, "請求成功", {"projects": out})

# --------- /specifics ---------
@bp.get("/specifics")
def specifics():
    keyword = _norm(request.args.get("keyword"))   # search in spec name/code
    machine = _norm(request.args.get("machine"))   # search in machine name/code

    # 1) Start from the full set of spec codes (project-agnostic)
    sql = "SELECT DISTINCT spec_code FROM sfdb.rms_spec_flat"
    with db(dict_cursor=True) as (_, cur):
        cur.execute(sql)
        all_spec_codes = [r["spec_code"] for r in cur.fetchall() if r.get("spec_code")]

    # 2) Pull oracle rows once for name resolution (and possible machine filtering)
    rows = fetch_mes_rows_for_specs(all_spec_codes, exclude_ti_to=True)

    # Build a map spec_code -> clean spec_name
    code_to_name = {}
    for r in rows:
        if r["spec_code"] not in code_to_name and r.get("spec_name"):
            code_to_name[r["spec_code"]] = r["spec_name"]

    # 3) If machine keyword exists, narrow specs to those having matching machine
    candidate_spec_codes = set(all_spec_codes)
    if machine:
        rows_by_machine = [
            r for r in rows
            if _icontains(r["machine_name"], machine) or _icontains(r["machine_code"], machine)
        ]
        candidate_spec_codes = {r["spec_code"] for r in rows_by_machine}

    # 4) Apply optional spec keyword (on spec name OR code). If no keyword, list all candidates.
    results = {}
    for sc in sorted(candidate_spec_codes):
        sn = code_to_name.get(sc, "")
        if keyword and not (_icontains(sn, keyword) or _icontains(sc, keyword)):
            continue
        # payload shape: { "<spec name>": {"code": "<spec code>"} }
        results[sn or sc] = {"code": sc}

    return send_response(200, True, "請求成功", {"specifics": results})


# --------- /groups-machines ---------
@bp.get("/groups-machines")
def groups_machines():
    keyword = _norm(request.args.get("keyword"))
    specific = request.args.get("specific")  # spec name or code accepted
    project  = request.args.get("project")

    if not keyword and (specific is None and project is None):
        return send_response(401, True, "沒有搜尋條件", {"message": "沒有搜尋條件"})

    results = {}

    def _add_machine(group_name: str, group_code: str, machine_name: str, machine_code: str):
        results.setdefault(group_name, {"code": group_code, "machines": {}})
        results[group_name]["machines"][machine_name] = {"code": machine_code}

    if project is not None:
        # Project → specs (MySQL) → machines (Oracle)
        spec_codes = get_spec_codes_by_project(project)
        if not spec_codes:
            return send_response(200, True, "請求成功", {"groups": {}})

        rows = fetch_mes_rows_for_specs(spec_codes, exclude_ti_to=True)
        for r in rows:
            if keyword and not (_icontains(r["machine_name"], keyword) or _icontains(r["machine_code"], keyword)):
                continue
            gname = r["machine_group"] or "(未分類)"
            gcode = r["machine_group"] or ""  # use group name as code surrogate
            _add_machine(gname, gcode, r["machine_name"], r["machine_code"])

    elif specific is not None:
        # specific can be name or code; accept both by resolving to code(s)
        # First try as code:
        spec_code = _norm(specific)
        rows = fetch_mes_rows_for_specs([spec_code], exclude_ti_to=True)
        if not rows:
            # If no hit, try resolve by name → code(s)
            all_codes = _all_spec_codes_from_mysql()
            all_rows = fetch_mes_rows_for_specs(all_codes, exclude_ti_to=True)
            cand_codes = sorted({r["spec_code"] for r in all_rows if _icontains(r["spec_name"], specific)})
            rows = [r for r in all_rows if r["spec_code"] in cand_codes]

        for r in rows:
            if keyword and not (_icontains(r["machine_name"], keyword) or _icontains(r["machine_code"], keyword)):
                continue
            gname = r["machine_group"] or "(未分類)"
            gcode = r["machine_group"] or ""
            _add_machine(gname, gcode, r["machine_name"], r["machine_code"])

    else:
        # No project or spec: search by machine keyword across all specs
        if not keyword:
            return send_response(401, True, "沒有搜尋條件", {"message": "沒有搜尋條件"})
        spec_codes = _all_spec_codes_from_mysql()
        rows = fetch_mes_rows_for_specs(spec_codes, exclude_ti_to=True)
        # Filter machines by keyword, then return their groups
        filtered = [r for r in rows if _icontains(r["machine_name"], keyword) or _icontains(r["machine_code"], keyword)]
        for r in filtered:
            gname = r["machine_group"] or "(未分類)"
            gcode = r["machine_group"] or ""
            _add_machine(gname, gcode, r["machine_name"], r["machine_code"])

    return send_response(200, True, "請求成功", {"groups": results})

# --------- /machines ---------
@bp.get("/machines")
def machines():
    keyword = _norm(request.args.get("keyword"))
    specific = request.args.get("specific")  # spec name or code

    if not keyword and specific is None:
        return send_response(401, True, "沒有搜尋條件", {"message": "沒有搜尋條件"})

    results = {}

    if specific is not None:
        # Resolve specific to code(s) and pull its machines
        spec_code = _norm(specific)
        rows = fetch_mes_rows_for_specs([spec_code], exclude_ti_to=True)
        if not rows:
            # Resolve by name → code(s)
            all_codes = _all_spec_codes_from_mysql()
            all_rows = fetch_mes_rows_for_specs(all_codes, exclude_ti_to=True)
            cand_codes = sorted({r["spec_code"] for r in all_rows if _icontains(r["spec_name"], specific)})
            rows = [r for r in all_rows if r["spec_code"] in cand_codes]

        for r in rows:
            if keyword and not (_icontains(r["machine_name"], keyword) or _icontains(r["machine_code"], keyword)):
                continue
            results[r["machine_name"]] = {"code": r["machine_code"]}
    else:
        # machine-only search globally
        if not keyword:
            return send_response(401, True, "沒有搜尋條件", {"message": "沒有搜尋條件"})
        spec_codes = _all_spec_codes_from_mysql()
        rows = fetch_mes_rows_for_specs(spec_codes, exclude_ti_to=True)
        for r in rows:
            if _icontains(r["machine_name"], keyword) or _icontains(r["machine_code"], keyword):
                results[r["machine_name"]] = {"code": r["machine_code"]}

    return send_response(200, True, "請求成功", {"machines": results})

@bp.get("/machine-groups")
def machine_groups():
    print("search groups")
    project   = _norm(request.args.get("project"))
    specific  = _norm(request.args.get("specific"))
    keyword   = _norm(request.args.get("keyword"))

    print(f"project: {project}")
    print(f"specific: {specific}")
    print(f"keyword: {keyword}")

    # *** NEW: if truly no filters, return all groups fast ***
    if not project and not specific and not keyword:
        groups = list_all_machine_groups()
        return send_response(200, True, "請求成功", {"groups": groups})

    spec_codes = []

    if specific:
        rows = fetch_mes_rows_for_specs([specific], exclude_ti_to=True)
        if rows:
            spec_codes = [specific]
        else:
            with db(dict_cursor=True) as (_, cur):
                cur.execute("SELECT DISTINCT spec_code FROM sfdb.rms_spec_flat")
                all_codes = [r["spec_code"] for r in cur.fetchall() if r.get("spec_code")]
            all_rows = fetch_mes_rows_for_specs(all_codes, exclude_ti_to=True)
            spec_codes = sorted({r["spec_code"] for r in all_rows if specific in r["spec_name"]})

    if project and not spec_codes:
        spec_codes = get_spec_codes_by_project(project)

    groups = search_machine_groups(
        project=project or None,
        spec_codes=spec_codes or None,
        text=keyword or None,
        exclude_ti_to=True
    )
    return send_response(200, True, "請求成功", {"groups": groups})

# modules/mes.py
@bp.get("/machines-by-group")
def machines_by_group():
    group_code = _norm(request.args.get("group_code"))
    if not group_code:
        return send_response(400, False, "缺少必要參數 group_code", {"message": "請提供 group_code"})

    project   = _norm(request.args.get("project"))
    specific  = _norm(request.args.get("specific"))
    keyword   = _norm(request.args.get("keyword"))

    # FAST PATH: groups-only flow (radio + no project/spec) → fetch all machines in group
    if not project and not specific:
        machines = fetch_machines_by_group_all(group_code, text=keyword or None)
        return send_response(200, True, "請求成功", {"machines": machines})

    # ORIGINAL spec-aware path (keeps checkbox behaviour unchanged)
    spec_codes: list[str] = []
    if specific:
        rows = fetch_mes_rows_for_specs([specific], exclude_ti_to=True)
        if rows:
            spec_codes = [specific]
        else:
            with db(dict_cursor=True) as (_, cur):
                cur.execute("SELECT DISTINCT spec_code FROM sfdb.rms_spec_flat")
                all_codes = [r["spec_code"] for r in cur.fetchall() if r.get("spec_code")]
            all_rows = fetch_mes_rows_for_specs(all_codes, exclude_ti_to=True)
            spec_codes = sorted({r["spec_code"] for r in all_rows if specific in r["spec_name"]})

    if project and not spec_codes:
        spec_codes = get_spec_codes_by_project(project)

    machines = search_machines_by_group(
        group_code=group_code,
        project=project or None,
        spec_codes=spec_codes or None,
        text=keyword or None,
        exclude_ti_to=True
    )
    return send_response(200, True, "請求成功", {"machines": machines})

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
    keyword = _norm(request.args.get("keyword"))
    page     = request.args.get("page")
    pageSize = request.args.get("pageSize")

    projects = get_projects()  # ['WMC露光工程', ...]
    if keyword:
        projects = [p for p in projects if keyword in p]

    # Build rows expected by your UI: id, projectCode, projectName
    # Use the same string as both code/name unless you have a separate code.
    rows = [{"id": p, "projectCode": p, "projectName": p} for p in projects]
    return send_response(200, True, "OK", _paginate(rows, page, pageSize))

@bp.get("/engineering/<project_id>/processes")
def list_engineering_processes(project_id):
    """List specs already assigned to this project (from MySQL)."""
    project = _norm(project_id)
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

@bp.get("/engineering/<project_id>/unassigned-processes")
def list_unassigned_processes(project_id):
    """
    Specs that exist in Oracle but NOT in MySQL mapping for this project.
    Filters by spec name/code keyword.
    Pagination supported.
    """
    project  = _norm(project_id)
    keyword  = _norm(request.args.get("keyword"))
    page     = request.args.get("page")
    pageSize = request.args.get("pageSize")

    # Already assigned
    assigned = set(get_spec_codes_by_project(project))

    # Pull *all* spec codes we know (fast from MySQL)
    with db(dict_cursor=True) as (_, cur):
        cur.execute("SELECT DISTINCT spec_code FROM sfdb.rms_spec_flat")
        all_codes = [r["spec_code"] for r in cur.fetchall() if r.get("spec_code")]

    # Resolve Oracle names for those spec codes
    rows = fetch_mes_rows_for_specs(all_codes, exclude_ti_to=True)
    # We only need one row per spec for name; build map
    code_to_name = {}
    for r in rows:
        if r["spec_code"] not in code_to_name:
            code_to_name[r["spec_code"]] = r["spec_name"]

    # Unassigned list
    unassigned = []
    for sc, sn in code_to_name.items():
        if sc in assigned:
            continue
        if keyword and not (keyword in (sn or "") or keyword in sc):
            continue
        unassigned.append({"id": sc, "specCode": sc, "specName": sn or sc})

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
    import json
    project = _norm(project_id)
    payload = request.get_json(force=True) or {}
    process_ids = payload.get("processIds") or []  # list of spec_code

    if not process_ids:
        return send_response(400, False, "缺少 processIds", {"message": "請提供要加入的製程代碼陣列"})

    # Resolve names from Oracle (spec_code -> spec_name)
    rows = fetch_mes_rows_for_specs(list(process_ids), exclude_ti_to=True)
    name_map = {}
    for r in rows:
        if r["spec_code"] not in name_map and r.get("spec_name"):
            name_map[r["spec_code"]] = r["spec_name"]

    # Bulk insert IGNORE
    sql = """
      INSERT IGNORE INTO sfdb.rms_spec_flat
        (dept_code, work_center_name, spec_code, spec_name, project)
      VALUES (%s, %s, %s, %s, %s)
    """
    vals = []
    for sc in process_ids:
        sn = name_map.get(sc, sc)
        vals.append(("NA", "NA", sc, sn, project))

    with db(dict_cursor=True) as (conn, cur):
        cur.executemany(sql, vals)
        conn.commit()

    return send_response(200, True, "新增成功", {"added": len(vals)})

@bp.delete("/engineering/<project_id>/processes/<spec_code>")
def delete_process_from_engineering(project_id, spec_code):
    """Remove a mapping row(s) for this project/spec_code."""
    project = _norm(project_id)
    spec_code = _norm(spec_code)
    with db(dict_cursor=True) as (conn, cur):
        cur.execute(
            "DELETE FROM sfdb.rms_spec_flat WHERE project=%s AND spec_code=%s",
            (project, spec_code)
        )
        conn.commit()
    return send_response(200, True, "移除成功", {"deletedSpec": spec_code})