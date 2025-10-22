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
                SELECT
                    t1.group_id, t1.group_name,
                    t2.machine_id, t2.machine_name
                FROM rms_condition_groups t1
                INNER JOIN rms_group_machines t2
                    ON t1.group_id = t2.group_id
                WHERE t1.condition_id = %s
                ORDER BY t1.condition_id, t1.group_id, t2.machine_id
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

                # add
                add_spec = cm.get("machinesToAdd") or {}
                if add_spec:
                    groups_to_add = []
                    for gname, ginfo in add_spec.items():
                        gid = ginfo.get("code")
                        cur.execute(
                            "SELECT COUNT(*) FROM rms_condition_groups WHERE condition_id=%s AND group_id=%s",
                            (cid, gid)
                        )
                        if cur.fetchone()[0] == 0:
                            groups_to_add.append((cid, gid, gname))
                    if groups_to_add:
                        cur.executemany(
                            "INSERT INTO rms_condition_groups (condition_id, group_id, group_name) VALUES (%s,%s,%s)",
                            groups_to_add
                        )
                        updated += cur.rowcount

                    machines_to_add = []
                    for gname, ginfo in add_spec.items():
                        gid = ginfo.get("code")
                        for mname, minfo in ginfo.get("machines", {}).items():
                            mid = minfo.get("code")
                            machines_to_add.append((cid, gid, mid, mname))
                    if machines_to_add:
                        cur.executemany("""
                            INSERT INTO rms_group_machines (condition_id, group_id, machine_id, machine_name)
                            VALUES (%s,%s,%s,%s)
                        """, machines_to_add)
                        updated += cur.rowcount

                # delete + cleanup
                del_spec = cm.get("machinesToDelete") or {}
                if del_spec:
                    machine_ids = []
                    for ginfo in del_spec.values():
                        for minfo in ginfo.get("machines", {}).values():
                            machine_ids.append(minfo.get("code"))
                    if machine_ids:
                        placeholders = ",".join(["%s"] * len(machine_ids))
                        # which groups are affected
                        cur.execute(
                            f"SELECT DISTINCT group_id FROM rms_group_machines WHERE condition_id=%s AND machine_id IN ({placeholders})",
                            (cid, *machine_ids)
                        )
                        affected_groups = [row[0] for row in cur.fetchall()]

                        # delete machines
                        cur.execute(
                            f"DELETE FROM rms_group_machines WHERE condition_id=%s AND machine_id IN ({placeholders})",
                            (cid, *machine_ids)
                        )
                        updated += cur.rowcount

                        # delete empty groups
                        if affected_groups:
                            gph = ",".join(["%s"] * len(affected_groups))
                            cur.execute(f"""
                                DELETE FROM rms_condition_groups
                                WHERE condition_id=%s AND group_id IN ({gph})
                                  AND group_id NOT IN (
                                      SELECT group_id FROM rms_group_machines WHERE condition_id=%s
                                  )
                            """, (cid, *affected_groups, cid))
                            updated += cur.rowcount

        if updated > 0:
            return send_response(200, False, "更新成功", {"message": f"條件更新成功，共異動 {updated} 筆資料。"})
        else:
            return send_response(200, False, "未更新", {"message": "數據已同步或未發生變動。"})
    except Exception as e:
        return send_response(500, True, "更新失敗", {"message": f"資料庫錯誤: {e}"})
