# modules/department.py
#
# 課別 (RMS_DEPT) 與製程 (SAJET.SYS_PROCESS) 的綁定管理 + 草稿可視範圍 helper。
#
# 設計重點:
#   - 課別權威為 Oracle IDBUSER.RMS_DEPT，本模組不維護課別主表
#   - MySQL 只存 rms_department_process (M:N 綁定)
#   - 可視範圍規則: { 自己 DEPT } ∪ { descendants } ∪ { parent (限 KJ 樹內) }

from flask import Blueprint, request, jsonify
from db import db
from oracle_db import ora_cursor

bp = Blueprint("department", __name__)

# 從 mes.py 拷一份相同的製程篩選 prefix，避免循環 import
_PROCESS_WHERE_PREFIX = (
    "REGEXP_LIKE(p.PROCESS_NAME, '^\\([LR][0-8][[:digit:]]{2}-[A-Z]?[[:digit:]]{2}\\)') "
    "AND p.PROCESS_NAME NOT LIKE '%人工%' "
    "AND sm.ENABLED = 'Y' "
    "AND sm.EQM_ID <> 'NA'"
)

# 我們目前只管 KJ 工程處的課別樹
_DEPT_TREE_FILTER = "KJ%"


# ============================================================
# Visibility Helpers (給 docs.py /drafts /passed 共用)
# ============================================================
def get_visible_dept_codes(user_emp_id):
    """
    依使用者 EMP_NO 回傳可視 DEPT_NO 清單 (含自己 dept、subtree、parent)。
    parent 限 KJ 樹內，避免跑出去看到 KC0000。
    若使用者不存在 / 沒有 dept → 回空 list。
    """
    if not user_emp_id:
        return []

    try:
        with ora_cursor() as cur:
            # 1. 撈使用者 dept + parent
            cur.execute(
                """
                SELECT u.DEPT_NO, d.GL_DEPARTMENT_CODE
                FROM IDBUSER.RMS_USERS u
                JOIN IDBUSER.RMS_DEPT d ON u.DEPT_NO = d.DEPT_NO
                WHERE u.EMP_NO = :emp_id
                """,
                emp_id=str(user_emp_id),
            )
            row = cur.fetchone()
            if not row:
                return []
            user_dept, parent_dept = row[0], row[1]

            # 2. 撈 subtree (含 user_dept 自己)
            cur.execute(
                """
                SELECT DEPT_NO FROM IDBUSER.RMS_DEPT
                START WITH DEPT_NO = :user_dept
                CONNECT BY PRIOR DEPT_NO = GL_DEPARTMENT_CODE
                """,
                user_dept=user_dept,
            )
            depts = {r[0] for r in cur.fetchall()}

            # 3. 加上 parent (僅 KJ 樹內)
            if parent_dept and parent_dept.startswith("KJ"):
                depts.add(parent_dept)

            return sorted(depts)
    except Exception as e:
        print(f"[get_visible_dept_codes] failed for {user_emp_id}: {e}")
        return []


def get_visible_emp_ids(user_emp_id):
    """
    依使用者 EMP_NO 回傳「該使用者可以看到的所有員工 EMP_NO」list (含自己)。
    給 /drafts、/passed 等需要做可視範圍卡控的 API 用。
    """
    visible_depts = get_visible_dept_codes(user_emp_id)
    if not visible_depts:
        return [user_emp_id] if user_emp_id else []

    try:
        with ora_cursor() as cur:
            binds = {f"d{i}": v for i, v in enumerate(visible_depts)}
            placeholders = ", ".join([f":{k}" for k in binds.keys()])
            cur.execute(
                f"SELECT EMP_NO FROM IDBUSER.RMS_USERS WHERE DEPT_NO IN ({placeholders})",
                binds,
            )
            return [r[0] for r in cur.fetchall()]
    except Exception as e:
        print(f"[get_visible_emp_ids] failed for {user_emp_id}: {e}")
        return [user_emp_id] if user_emp_id else []


# ============================================================
# Internal helpers
# ============================================================
def _fetch_kj_depts():
    """從 Oracle 撈所有 KJ 課別 (含階層欄位)"""
    with ora_cursor() as cur:
        cur.execute(
            f"""
            SELECT DEPT_NO, DEPT_NAME, GL_DEPARTMENT_CODE, LEV, LEADER_EMP_ID
            FROM IDBUSER.RMS_DEPT
            WHERE DEPT_NO LIKE '{_DEPT_TREE_FILTER}'
            ORDER BY DEPT_NO
            """
        )
        return [
            {
                "deptNo": r[0],
                "deptName": r[1],
                "parent": r[2],
                "lev": r[3],
                "leaderEmpId": r[4],
            }
            for r in cur.fetchall()
        ]


def _fetch_process_count_map():
    """從 MySQL 撈每個 department 已綁定的製程數"""
    with db() as (_, cur):
        cur.execute(
            "SELECT department_code, COUNT(*) FROM rms_department_process GROUP BY department_code"
        )
        return {code: cnt for code, cnt in cur.fetchall()}


# ============================================================
# Endpoints
# ============================================================
@bp.get("/tree")
def get_department_tree():
    """KJ 課別樹狀結構，每節點附帶 processCount"""
    try:
        depts = _fetch_kj_depts()
        process_count_map = _fetch_process_count_map()

        # 建立 dict 方便組樹
        node_map = {}
        for d in depts:
            d["processCount"] = process_count_map.get(d["deptNo"], 0)
            d["children"] = []
            node_map[d["deptNo"]] = d

        roots = []
        for d in depts:
            parent_code = d.get("parent")
            if parent_code and parent_code in node_map:
                node_map[parent_code]["children"].append(d)
            else:
                roots.append(d)

        return jsonify({"success": True, "data": {"tree": roots}})
    except Exception as e:
        print(f"Error in /department/tree: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.get("")
def list_departments():
    """KJ 課別平鋪清單 (給 dropdown 用)，每筆附 processCount"""
    try:
        depts = _fetch_kj_depts()
        process_count_map = _fetch_process_count_map()
        for d in depts:
            d["processCount"] = process_count_map.get(d["deptNo"], 0)
        return jsonify({"success": True, "data": {"items": depts}})
    except Exception as e:
        print(f"Error in /department: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.get("/<code>/processes")
def list_department_processes(code):
    """列出某課別已綁定的製程"""
    try:
        with db(dict_cursor=True) as (_, cur):
            cur.execute(
                "SELECT process_code, process_name FROM rms_department_process "
                "WHERE department_code = %s ORDER BY process_code",
                (code,),
            )
            items = [
                {"processCode": r["process_code"], "processName": r["process_name"]}
                for r in cur.fetchall()
            ]
        return jsonify({"success": True, "data": {"items": items}})
    except Exception as e:
        print(f"Error in /department/<code>/processes: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.get("/<code>/unassigned-processes")
def list_department_unassigned_processes(code):
    """
    列出該課別「還沒綁」的製程。
    來源 = Oracle SAJET.SYS_PROCESS 全表 - 該課別已綁的。
    """
    keyword = request.args.get("keyword", "").strip()

    try:
        # 1. Oracle 撈製程全表 (依 mes.py 的 WHERE_PREFIX 條件)
        process_map = {}
        sql = f"""
            SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME FROM SAJET.SYS_PROCESS p
            JOIN SAJET.SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
            JOIN SAJET.SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
            JOIN SAJET.SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
            WHERE {_PROCESS_WHERE_PREFIX}
        """
        with ora_cursor(db_alias="machine_db") as cur:
            if keyword:
                sql += " AND p.PROCESS_NAME LIKE :kw"
                cur.execute(sql, kw=f"%{keyword}%")
            else:
                cur.execute(sql)
            process_map = {desc: name for desc, name in cur.fetchall()}

        # 2. MySQL 該課別已綁的 codes
        with db() as (_, cur):
            cur.execute(
                "SELECT process_code FROM rms_department_process WHERE department_code = %s",
                (code,),
            )
            assigned = {r[0] for r in cur.fetchall()}

        unassigned = [
            {"processCode": desc, "processName": name}
            for desc, name in process_map.items()
            if desc not in assigned
        ]
        unassigned.sort(key=lambda x: x["processCode"])

        return jsonify({"success": True, "data": {"items": unassigned}})
    except Exception as e:
        print(f"Error in /department/<code>/unassigned-processes: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.post("/<code>/processes")
def add_department_processes(code):
    """
    為課別新增製程綁定 (批量)。
    body: { "processes": [{ "code": "...", "name": "..." }, ...] }
    回傳: { added: <number> }
    """
    body = request.get_json(silent=True) or {}
    processes = body.get("processes") or []

    if not processes:
        return jsonify({"success": False, "error": "processes 不可為空"}), 400

    vals = []
    for p in processes:
        pcode = (p.get("code") or "").strip()
        pname = (p.get("name") or "").strip()
        if pcode:
            vals.append((code, pcode, pname))

    if not vals:
        return jsonify({"success": False, "error": "processes 內容無效"}), 400

    try:
        with db() as (conn, cur):
            # INSERT IGNORE 撞 UNIQUE 不報錯，重複綁定自動忽略
            cur.executemany(
                "INSERT IGNORE INTO rms_department_process "
                "(department_code, process_code, process_name) VALUES (%s, %s, %s)",
                vals,
            )
            conn.commit()
            added = cur.rowcount or 0
        return jsonify({"success": True, "data": {"added": added}})
    except Exception as e:
        print(f"Error in POST /department/<code>/processes: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


@bp.delete("/<code>/processes/<process_code>")
def delete_department_process(code, process_code):
    """移除課別綁定的單一製程"""
    try:
        with db() as (conn, cur):
            cur.execute(
                "DELETE FROM rms_department_process "
                "WHERE department_code = %s AND process_code = %s",
                (code, process_code),
            )
            conn.commit()
            deleted = cur.rowcount or 0
        return jsonify({"success": True, "data": {"deleted": deleted}})
    except Exception as e:
        print(f"Error in DELETE /department/<code>/processes/<process_code>: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
