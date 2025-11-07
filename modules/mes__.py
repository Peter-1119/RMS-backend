from flask import Blueprint, request, jsonify
from loginFunctions.utils import send_response
from data_store import get_projects, get_specifics, get_machines
from db import db

bp = Blueprint("mes", __name__)

@bp.get("/projects")
def projects():
    keyword = (request.args.get('keyword') or "").strip()
    if not keyword:
        return send_response(401, True, "沒有搜尋條件", {"message": "請輸入關鍵字"})
    out = {}
    for name, info in get_projects().items():
        if keyword in name or keyword in info.get("code",""):
            out[name] = {"code": info.get("code","")}
    return send_response(200, True, "請求成功", {"projects": out})

@bp.get("/specifics")
def specifics():
    keyword = (request.args.get('keyword') or "").strip()
    machine = (request.args.get('machine') or "").strip()
    if not keyword and not machine:
        return send_response(401, True, "沒有搜尋條件", {"message": "沒有搜尋條件"})
    specifics_data, machines_info = get_specifics(), get_machines()
    results = {}
    if machine and keyword:
        for sp in machines_info.get(machine, {}).get("specifics", []):
            if keyword in sp or keyword in specifics_data.get(sp, {}).get("code",""):
                results[sp] = {"code": specifics_data.get(sp, {}).get("code","")}
    elif machine:
        for sp in machines_info.get(machine, {}).get("specifics", []):
            results[sp] = {"code": specifics_data.get(sp, {}).get("code","")}
    else:
        for sp, info in specifics_data.items():
            if keyword in sp or keyword in info.get("code",""):
                results[sp] = {"code": info.get("code","")}
    return send_response(200, True, "請求成功", {"specifics": results})

@bp.get("/groups-machines")
def groups_machines():
    keyword = (request.args.get('keyword') or "").strip()
    specific = request.args.get('specific')
    project  = request.args.get('project')
    if not keyword and (specific is None and project is None):
        return send_response(401, True, "沒有搜尋條件", {"message": "沒有搜尋條件"})
    specifics_data, projects_data = get_specifics(), get_projects()
    results = {}
    if project is not None and keyword:
        for sp in projects_data.get(project, {}).get("specifics", []):
            g = specifics_data.get(sp, {}).get("groups", {})
            for gn, gi in g.items():
                for mn, mi in gi.get("machines", {}).items():
                    if keyword in mn or keyword in mi.get("code",""):
                        results.setdefault(gn, {"code": gi.get("code",""), "machines": {}})
                        results[gn]["machines"][mn] = {"code": mi.get("code","")}
    elif project is not None:
        for sp in projects_data.get(project, {}).get("specifics", []):
            results |= specifics_data.get(sp, {}).get("groups", {})
    elif specific is not None and keyword:
        for gn, gi in specifics_data.get(specific, {}).get("groups", {}).items():
            for mn, mi in gi.get("machines", {}).items():
                if keyword in mn or keyword in mi.get("code",""):
                    results.setdefault(gn, {"code": gi.get("code",""), "machines": {}})
                    results[gn]["machines"][mn] = {"code": mi.get("code","")}
    elif specific is not None:
        results = {**results, **specifics_data.get(specific, {}).get("groups", {})}
    else:
        # search by machine only
        from data_store import get_machines
        specifics_list = set()
        for mn, mi in get_machines().items():
            if keyword in mn or keyword in mi.get("code",""):
                # results[mn] = mi["code"]
                for sp in mi["specifics"]:
                    specifics_list.add(sp)
        
        for sp in list(specifics_list):
            g = specifics_data.get(sp, {}).get("groups", {})
            for gn, gi in g.items():
                for mn, mi in gi.get("machines", {}).items():
                    if keyword in mn or keyword in mi.get("code",""):
                        results.setdefault(gn, {"code": gi.get("code",""), "machines": {}})
                        results[gn]["machines"][mn] = {"code": mi.get("code","")}
        # specifics_dict = get_specifics()
        # specifics_dict = {sn: specifics_dict[sn] for sn in specifics_list if sn in specifics_dict.get(sn) != None}
        # for sn, si in specifics_dict.items():


    return send_response(200, True, "請求成功", {"groups": results})

@bp.get("/machines")
def machines():
    keyword = (request.args.get('keyword') or "").strip()
    specific = request.args.get('specific')
    if not keyword and specific is None:
        return send_response(401, True, "沒有搜尋條件", {"message": "沒有搜尋條件"})
    specifics_data, machines_info = get_specifics(), get_machines()
    results = {}
    if specific is not None and keyword:
        for gn, gi in specifics_data.get(specific, {}).get("groups", {}).items():
            for mn, mi in gi.get("machines", {}).items():
                if keyword in mn or keyword in mi.get("code",""):
                    results[mn] = {"code": mi.get("code","")}
    elif specific is not None:
        for gn, gi in specifics_data.get(specific, {}).get("groups", {}).items():
            results |= gi.get("machines", {})
    else:
        for mn, mi in machines_info.items():
            if keyword in mn or keyword in mi.get("code",""):
                results[mn] = mi
    return send_response(200, True, "請求成功", {"machines": results})

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

