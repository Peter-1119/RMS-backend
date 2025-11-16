# modules/conditions.py
from flask import Blueprint, request
from db import db
from loginFunctions.utils import send_response

bp = Blueprint("conditions", __name__)

@bp.get("/get-conditions")
def get_conditions():
    keyword = (request.args.get('keyword') or "").strip()  # optional
    try:
        with db() as (conn, cur):
            cur.execute("""
                SELECT t1.condition_id, t1.condition_name, t2.parameter_name
                FROM rms_conditions AS t1
                INNER JOIN rms_condition_parameters AS t2
                  ON t1.condition_id = t2.condition_id
                ORDER BY t1.condition_id
            """)
            results = cur.fetchall()
        # build { condition_name: {id, parameters: [] } }
        temp = {}
        for condition_id, condition_name, parameter_name in results:
            bucket = temp.setdefault(condition_name, {"id": condition_id, "parameters": []})
            bucket["parameters"].append(parameter_name)

        # optional keyword filter on name or id
        if keyword:
            filtered = {}
            for name, info in temp.items():
                if (keyword.lower() in name.lower()) or (keyword == str(info["id"])):
                    filtered[name] = info
            temp = filtered

        conditions = [{"name": cn, **ci} for (cn, ci) in temp.items()]
        return send_response(200, True, "請求成功", {"conditions": conditions})
    except Exception as e:
        return send_response(500, True, "請求失敗", {"message": f"DB錯誤: {e}"})

@bp.get("/get-condition-machines")
def get_condition_machines():
    condition_id = (request.args.get("condition_id") or "").strip()
    if not condition_id:
        return send_response(401, True, "請求失敗", {"message": "沒有條件ID"})
    try:
        with db() as (conn, cur):
            cur.execute("""
                SELECT cg.group_id, cg.group_name, gm.machine_id, gm.machine_name FROM rms_condition_groups AS cg
                INNER JOIN rms_group_machines AS gm ON  gm.condition_id = cg.condition_id AND gm.group_id     = cg.group_id
                WHERE cg.condition_id = %s
                ORDER BY cg.group_id, gm.machine_id
            """, (int(condition_id),))
            rows = cur.fetchall()

        groups = {}
        for group_id, group_name, machine_id, machine_name in rows:
            g = groups.setdefault(group_name, {"code": group_id, "machines": {}})
            g["machines"][machine_name] = {"code": machine_id}
        return send_response(200, True, "請求成功", {"groups": groups})
    except Exception as e:
        return send_response(500, True, "請求失敗", {"message": f"DB錯誤: {e}"})

@bp.get("/search-conditions-by-machines")
def search_conditions_by_machines():
    keyword = (request.args.get("keyword") or "").strip().lower()
    if not keyword:
        # reuse get-conditions when empty, for convenience
        return get_conditions()
    try:
        with db() as (conn, cur):
            like = f"%{keyword}%"
            cur.execute("""
                SELECT DISTINCT
                    t1.condition_id, t1.condition_name, t4.parameter_name
                FROM rms_conditions t1
                INNER JOIN rms_condition_groups t2
                    ON t1.condition_id = t2.condition_id
                INNER JOIN rms_group_machines t3
                    ON t2.group_id = t3.group_id AND t2.condition_id = t3.condition_id
                INNER JOIN rms_condition_parameters t4
                    ON t1.condition_id = t4.condition_id
                WHERE LOWER(t3.machine_id) LIKE %s OR LOWER(t3.machine_name) LIKE %s
            """, (like, like))
            rows = cur.fetchall()

        temp = {}
        for condition_id, condition_name, parameter_name in rows:
            bucket = temp.setdefault(condition_name, {"id": condition_id, "parameters": []})
            bucket["parameters"].append(parameter_name)

        conditions = [{"name": cn, **ci} for (cn, ci) in temp.items()]
        return send_response(200, True, "請求成功", {"conditions": conditions})
    except Exception as e:
        return send_response(500, True, "請求失敗", {"message": f"DB錯誤: {e}"})

@bp.get("/delete-condition-by-id")
def delete_condition_by_id():
    condition_id = (request.args.get("condition_id") or "").strip()
    if not condition_id:
        return send_response(400, True, "請求失敗", {"message": "請輸入要刪除的條件ID"})
    try:
        cid = int(condition_id)
        with db() as (conn, cur):
            cur.execute("DELETE FROM rms_conditions WHERE condition_id = %s", (cid,))
            affected = cur.rowcount
        if affected > 0:
            return send_response(200, False, "刪除成功", {"message": "已成功將該條件刪除"})
        else:
            return send_response(200, False, "刪除成功", {"message": "該條件ID不存在，未進行刪除操作"})
    except ValueError:
        return send_response(400, True, "請求失敗", {"message": "條件ID必須是有效的數字"})
    except Exception as e:
        return send_response(500, True, "刪除失敗", {"message": f"資料庫錯誤: {e}"})

# modules/conditions.py (only the parsing parts changed)
def _normalize_machines_payload(obj: dict) -> dict:
    """
    Accept both NEW and OLD shapes and return NEW:
      { gCode: { name: <group_name>, machines: { mCode: { name: <machine_name> } } } }
    """
    if not obj:
        return {}
    # Detect NEW: keys look like groupCodes and inner machines keyed by machineCodes
    # Heuristics: if any value has "name" and "machines" dict keyed not by names
    # We'll just support both explicitly:
    out = {}
    for gk, gv in (obj or {}).items():
        if "name" in gv and "machines" in gv:
            # NEW: gk is groupCode
            gcode = (gk or "").strip()
            gname = (gv.get("name") or gk or "").strip()
            machines = {}
            for mk, mv in (gv.get("machines") or {}).items():
                mcode = (mk or "").strip()
                mname = (mv.get("name") or mk or "").strip()
                if mcode:
                    machines[mcode] = {"name": mname}
            if gcode:
                out[gcode] = {"name": gname, "machines": machines}
        else:
            # OLD: gk is groupName, gv has { code, machines: { mName: { code } } }
            gname = (gk or "").strip()
            gcode = (gv.get("code") or "").strip()
            machines = {}
            for mname, minfo in (gv.get("machines") or {}).items():
                mcode = (minfo.get("code") or "").strip()
                if mcode:
                    machines[mcode] = {"name": (mname or "").strip()}
            if gcode:
                out[gcode] = {"name": gname, "machines": machines}
    return out

@bp.post("/update-condition-data")
def update_condition_data():
    try:
        with db() as (conn, cur):
            condition_id = request.form.get("condition-id")
            condition_name = request.form.get("condition-name")
            condition_parameters = request.form.get("condition-parameters")
            condition_machines = request.form.get("condition-machines")

            if condition_id is None:
                return send_response(400, True, "更新失敗", {"message": "缺少條件ID"})

            cid = int(condition_id)
            updated = 0

            # create
            if cid == -1:
                cur.execute(
                    "INSERT INTO rms_conditions (condition_name, enable) VALUES (%s, %s)",
                    (condition_name.strip(), 1)
                )
                cid = cur.lastrowid
                updated += 1
            # rename
            elif condition_name is not None:
                cur.execute(
                    "UPDATE rms_conditions SET condition_name = %s WHERE condition_id = %s",
                    (condition_name, cid)
                )
                updated += cur.rowcount

            # parameters add/delete
            import json
            if condition_parameters is not None:
                cp = json.loads(condition_parameters)
                adds = cp.get("parametersToAdd") or []
                dels = cp.get("parametersToDelete") or []
                if adds:
                    cur.executemany(
                        "INSERT INTO rms_condition_parameters (condition_id, parameter_name) VALUES (%s, %s)",
                        [(cid, name) for name in adds]
                    )
                    updated += cur.rowcount
                if dels:
                    placeholders = ",".join(["%s"] * len(dels))
                    cur.execute(
                        f"DELETE FROM rms_condition_parameters WHERE condition_id=%s AND parameter_name IN ({placeholders})",
                        (cid, *dels)
                    )
                    updated += cur.rowcount

            # machines add/delete + group cleanup
            if condition_machines is not None:
                cm = json.loads(condition_machines)

                # --- ADD ---
                add_spec_raw = cm.get("machinesToAdd") or {}
                add_spec = _normalize_machines_payload(add_spec_raw)   # NEW normalized
                if add_spec:
                    # ensure groups exist in rms_condition_groups
                    groups_to_add = []
                    for gcode, ginfo in add_spec.items():
                        gname = ginfo.get("name") or gcode
                        cur.execute(
                            "SELECT COUNT(*) FROM rms_condition_groups WHERE condition_id=%s AND group_id=%s",
                            (cid, gcode)
                        )
                        if cur.fetchone()[0] == 0:
                            groups_to_add.append((cid, gcode, gname))
                    if groups_to_add:
                        cur.executemany(
                            "INSERT INTO rms_condition_groups (condition_id, group_id, group_name) VALUES (%s,%s,%s)",
                            groups_to_add
                        )
                        updated += cur.rowcount

                    machines_to_add = []
                    for gcode, ginfo in add_spec.items():
                        gname = ginfo.get("name") or gcode
                        for mcode, minfo in (ginfo.get("machines") or {}).items():
                            mname = minfo.get("name") or mcode
                            machines_to_add.append((cid, gcode, mcode, mname))
                    if machines_to_add:
                        cur.executemany("""
                            INSERT INTO rms_group_machines (condition_id, group_id, machine_id, machine_name)
                            VALUES (%s,%s,%s,%s)
                        """, machines_to_add)
                        updated += cur.rowcount

                # --- DELETE ---
                del_spec_raw = cm.get("machinesToDelete") or {}
                del_spec = _normalize_machines_payload(del_spec_raw)   # 期望回傳 { gcode: { name, machines: { mcode: { name } } } }
                print("[DEL] raw:", json.dumps(del_spec_raw, ensure_ascii=False))
                print("[DEL] norm:", json.dumps(del_spec, ensure_ascii=False))
                if del_spec:
                    to_delete_rows = []   # list[tuple] -> (cid, gcode, mcode)
                    affected_groups = set()

                    for gcode, ginfo in del_spec.items():
                        gcode = (gcode or "").strip()
                        for mcode in (ginfo.get("machines") or {}).keys():
                            mcode = (mcode or "").strip()
                            if not mcode:
                                continue
                            to_delete_rows.append((cid, gcode, mcode))
                            affected_groups.add(gcode)

                    print(f"to_delete_rows: {to_delete_rows}")
                    if to_delete_rows:
                        # ★ 精準刪除：帶 group_id 一起比對，避免“刪不到”或“多刪”的問題
                        cur.executemany(
                            "DELETE FROM rms_group_machines WHERE condition_id=%s AND group_id=%s AND machine_id=%s",
                            to_delete_rows
                        )
                        updated += cur.rowcount

                        # ★ 清空群組：只刪剛才受影響的那幾個 group，效率更好
                        if affected_groups:
                            g_list = list(affected_groups)
                            gph = ",".join(["%s"] * len(g_list))
                            # 若該 group 在此 condition 底下已無任何 machine，就把群組 row 清掉
                            cur.execute(f"""
                                DELETE FROM rms_condition_groups
                                WHERE condition_id=%s AND group_id IN ({gph})
                                AND group_id NOT IN (
                                    SELECT group_id FROM rms_group_machines WHERE condition_id=%s
                                )
                            """, (cid, *g_list, cid))
                            updated += cur.rowcount

        if updated > 0:
            return send_response(200, False, "更新成功", {"message": f"條件更新成功，共異動 {updated} 筆資料。"})
        else:
            return send_response(200, False, "未更新", {"message": "數據已同步或未發生變動。"})
    except Exception as e:
        return send_response(500, True, "更新失敗", {"message": f"資料庫錯誤: {e}"})
