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

    if not keyword and not machine:
        return send_response(401, True, "沒有搜尋條件", {"message": "沒有搜尋條件"})

    # 1) Start from the full set of spec codes (project-agnostic)
    sql = "SELECT DISTINCT spec_code FROM sfdb.rms_spec_flat"
    with db(dict_cursor=True) as (_, cur):
        cur.execute(sql)
        all_spec_codes = [r["spec_code"] for r in cur.fetchall() if r.get("spec_code")]

    # 2) If a machine keyword is given, narrow specs down to those that have
    #    at least one matching machine (by code or name).
    candidate_spec_codes = set(all_spec_codes)
    if machine:
        rows = fetch_mes_rows_for_specs(all_spec_codes, exclude_ti_to=True)
        rows = [
            r for r in rows
            if _icontains(r["machine_name"], machine) or _icontains(r["machine_code"], machine)
        ]
        candidate_spec_codes = {r["spec_code"] for r in rows}

        # Build a mapping spec_code -> clean spec_name from the same rows
        code_to_name = {}
        for r in rows:
            if r["spec_code"] not in code_to_name and r.get("spec_name"):
                code_to_name[r["spec_code"]] = r["spec_name"]
    else:
        # No machine filter: we still need spec names.
        # Reuse the same helper (joins are OK), then dedup to a simple map.
        rows = fetch_mes_rows_for_specs(all_spec_codes, exclude_ti_to=True)
        code_to_name = {}
        for r in rows:
            if r["spec_code"] not in code_to_name and r.get("spec_name"):
                code_to_name[r["spec_code"]] = r["spec_name"]

    # 3) Apply the spec keyword (on spec name OR spec code)
    results = {}
    for sc in sorted(candidate_spec_codes):
        sn = code_to_name.get(sc, "")  # clean name if we saw one
        if keyword and not (_icontains(sn, keyword) or _icontains(sc, keyword)):
            continue
        # Keep your original payload shape: { "<spec name>": {"code": "<spec code>"} }
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

def _extract_project_code(name: str) -> str:
    # 依你的資料慣例「WMC露光工程」→ 取前綴英文當代碼，取不到就用全名
    import re
    if not name:
        return ""
    m = re.match(r"^([A-Za-z]+)", name)
    return m.group(1) if m else name

def _build_like_kw(keyword: str):
    kw = (keyword or "").strip()
    return f"%{kw}%" if kw else None

# -- API: 工程清單（左表） -------------------------------------------
@bp.get("/engineering")
def list_engineering():
    """
    Query params:
      keyword: 搜尋工程名 或 底下製程名
      page, pageSize: 分頁
    Return:
      { items: [{id, projectCode, projectName}], total }
      其中 id 以 projectName 當唯一鍵（前端當成 key 使用）
    """
    keyword = request.args.get("keyword", "").strip()
    page = max(int(request.args.get("page", 1)), 1)
    page_size = max(min(int(request.args.get("pageSize", 20)), 100), 1)
    offset = (page - 1) * page_size

    # 關鍵字同時匹配 工程名 or 底下的製程名（EXISTS 子查詢）
    with db(dict_cursor=True) as (conn, cur):
        where = ["project <> 'NA'"]  # 排除未分配
        params = []

        if keyword:
            where.append("(project LIKE %s OR EXISTS (SELECT 1 FROM rms_spec_flat s2 WHERE s2.project = s.project AND (s2.spec_code LIKE %s OR s2.spec_name LIKE %s)))")
            kw = f"%{keyword}%"
            params += [kw, kw, kw]

        where_sql = " AND ".join(where) if where else "1"
        # 先拿 total
        cur.execute(f"""
            SELECT COUNT(*) AS cnt
            FROM (
              SELECT project
              FROM rms_spec_flat s
              WHERE {where_sql}
              GROUP BY project
            ) t
        """, params)
        total = cur.fetchone()["cnt"] if cur.rowcount else 0

        # 取 items
        cur.execute(f"""
            SELECT project
            FROM rms_spec_flat s
            WHERE {where_sql}
            GROUP BY project
            ORDER BY project ASC
            LIMIT %s OFFSET %s
        """, params + [page_size, offset])
        rows = cur.fetchall() or []

    items = []
    for r in rows:
        pname = r["project"]
        items.append({
            "id": pname,  # 以 projectName 當 id（簡單穩定）
            "projectCode": _extract_project_code(pname),
            "projectName": pname
        })

    return jsonify({"items": items, "total": total})


# -- API: 工程底下的製程清單（右表） ---------------------------------
@bp.get("/engineering/<project_name>/processes")
def engineering_processes(project_name):
    with db(dict_cursor=True) as (conn, cur):
        cur.execute("""
          SELECT id, spec_code AS specCode, spec_name AS specName
          FROM rms_spec_flat
          WHERE project=%s
          ORDER BY spec_code ASC, spec_name ASC
        """, (project_name,))
        rows = cur.fetchall() or []
    return jsonify(rows)


# -- API: 未被新增的製程（給對話框右側清單） ----------------------------
@bp.get("/unassigned")
def list_unassigned():
    """
    Query params: keyword, page, pageSize
    Return: { items: [{id,specCode,specName}], total }
    僅 project='NA' 視為未分配；忽略 spec_name 空白行。
    """
    keyword = request.args.get("keyword", "").strip()
    page = max(int(request.args.get("page", 1)), 1)
    page_size = max(min(int(request.args.get("pageSize", 20)), 100), 1)
    offset = (page - 1) * page_size

    like_kw = _build_like_kw(keyword)

    with db(dict_cursor=True) as (conn, cur):
        if like_kw:
            params = [like_kw, like_kw]
            cur.execute("""
              SELECT COUNT(*) AS cnt
              FROM rms_spec_flat
              WHERE project='NA' AND spec_name<>'' AND (spec_code LIKE %s OR spec_name LIKE %s)
            """, params)
            total = cur.fetchone()["cnt"] if cur.rowcount else 0

            cur.execute("""
              SELECT id, spec_code AS specCode, spec_name AS specName
              FROM rms_spec_flat
              WHERE project='NA' AND spec_name<>'' AND (spec_code LIKE %s OR spec_name LIKE %s)
              ORDER BY spec_code ASC
              LIMIT %s OFFSET %s
            """, params + [page_size, offset])
        else:
            cur.execute("""
              SELECT COUNT(*) AS cnt
              FROM rms_spec_flat
              WHERE project='NA' AND spec_name<>''
            """)
            total = cur.fetchone()["cnt"] if cur.rowcount else 0

            cur.execute("""
              SELECT id, spec_code AS specCode, spec_name AS specName
              FROM rms_spec_flat
              WHERE project='NA' AND spec_name<>''
              ORDER BY spec_code ASC
              LIMIT %s OFFSET %s
            """, [page_size, offset])

        rows = cur.fetchall() or []
    return jsonify({"items": rows, "total": total})


# -- API: 新增工程 & 指派製程 ----------------------------------------
@bp.post("/engineering")
def create_engineering_with_processes():
    """
    Body: { projectCode, projectName, processIds: [id,...] }
    實作：把給定 id 列的 project 改為 projectName
    """
    data = request.get_json(force=True) or {}
    project_code = (data.get("projectCode") or "").strip()
    project_name = (data.get("projectName") or "").strip()
    ids = data.get("processIds") or []

    if not project_name or not ids:
        return jsonify({"success": False, "message": "projectName 與 processIds 必填"}), 400

    # 保守起見，過濾成整數
    try:
        id_list = [int(x) for x in ids if int(x) > 0]
    except Exception:
        return jsonify({"success": False, "message": "processIds 非法"}), 400
    if not id_list:
        return jsonify({"success": False, "message": "無有效 processIds"}), 400

    with db() as (conn, cur):
        # 僅更新目前仍未分配的列，避免意外覆蓋其他工程
        fmt_ids = ",".join(["%s"] * len(id_list))
        sql = f"""
          UPDATE rms_spec_flat
          SET project=%s
          WHERE id IN ({fmt_ids}) AND project='NA'
        """
        cur.execute(sql, [project_name] + id_list)
        updated = cur.rowcount

    return jsonify({"success": True, "updated": updated})


# -- API: 從工程移除某製程 -------------------------------------------
@bp.delete("/engineering/<project_name>/processes/<int:proc_id>")
def remove_process(project_name, proc_id):
    with db() as (conn, cur):
        cur.execute("""
          UPDATE rms_spec_flat
          SET project='NA'
          WHERE id=%s AND project=%s
          LIMIT 1
        """, (proc_id, project_name))
        changed = cur.rowcount
    return jsonify({"success": True, "changed": changed})


# -- API: 刪除工程（把該工程所有製程改回 NA） -------------------------
@bp.delete("/engineering/<project_name>")
def delete_engineering(project_name):
    with db() as (conn, cur):
        cur.execute("""
          UPDATE rms_spec_flat
          SET project='NA'
          WHERE project=%s
        """, (project_name,))
        changed = cur.rowcount
    return jsonify({"success": True, "changed": changed})

