from flask import Blueprint, request
from loginFunctions.utils import send_response
from data_store import get_projects, get_specifics, get_machines

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
        for mn, mi in get_machines().items():
            if keyword in mn or keyword in mi.get("code",""):
                results[mn] = mi["code"]
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
