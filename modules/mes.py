from typing import Dict, List, Set
from flask import Blueprint, request, jsonify
from loginFunctions.utils import send_response

from db import db
from oracle_db import ora_cursor as odb
from utils import *


bp = Blueprint("mes", __name__)

# --------- small helpers ---------
def _norm(s: str) -> str:
    return (s or "").strip()

def _icontains(hay: str, needle: str) -> bool:
    return needle.lower() in (hay or "").lower()

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

    # 1) Start from all spec codes
    with db(dict_cursor=True) as (_, cur):
        cur.execute("SELECT DISTINCT spec_code FROM sfdb.rms_spec_flat")
        all_spec_codes = [r["spec_code"] for r in cur.fetchall() if r.get("spec_code")]

    # 2) Pull oracle rows once for name resolution (+ machine filter)
    rows = fetch_mes_rows_for_specs(all_spec_codes, exclude_ti_to=True)

    # spec_code -> spec_name
    code_to_name = {}
    for r in rows:
        sc = r.get("spec_code")
        if sc and sc not in code_to_name and r.get("spec_name"):
            code_to_name[sc] = r["spec_name"]

    # 3) Optional filter by machine keyword
    candidate_spec_codes = set(all_spec_codes)
    if machine:
        candidate_spec_codes = {
            r["spec_code"] for r in rows
            if _icontains(r["machine_name"], machine) or _icontains(r["machine_code"], machine)
        }

    # 4) Optional filter by spec keyword; payload keyed by spec_code
    results = {}
    for sc in sorted(candidate_spec_codes):
        sn = code_to_name.get(sc, "")
        if keyword and not (_icontains(sn, keyword) or _icontains(sc, keyword)):
            continue
        results[sc] = {"name": sn or sc}

    return send_response(200, True, "請求成功", {"specifics": results})

# ---- helper: allowed codes by (project?, keyword?) mirroring groups-machines scope ----

# helpers – put near your utils in modules/mes.py
# --- clean helpers you already have ---
def _clean_code(v: str) -> str:
    return (v or "").strip()

def _clean_name(v: str) -> str:
    return (v or "").strip()

# --- NEW: resolve spec codes by code or (partial) name ---
def _resolve_spec_codes(specific: str) -> list[str]:
    """
    Accepts either an exact spec code or a partial/complete spec name.
    Returns a list of spec codes (possibly empty).
    Source of truth: MySQL sfdb.rms_spec_flat (has spec_code, spec_name).
    """
    specific = (specific or "").strip()
    if not specific:
        return []

    with db(dict_cursor=True) as (_, cur):
        # try exact code first
        cur.execute("""
            SELECT DISTINCT spec_code
            FROM sfdb.rms_spec_flat
            WHERE spec_code = %s
        """, (specific,))
        codes = [r["spec_code"] for r in cur.fetchall() if r.get("spec_code")]
        if codes:
            return codes

        # fallback: fuzzy by name
        like = f"%{specific}%"
        cur.execute("""
            SELECT DISTINCT spec_code
            FROM sfdb.rms_spec_flat
            WHERE spec_name LIKE %s
        """, (like,))
        return [r["spec_code"] for r in cur.fetchall() if r.get("spec_code")]

# --- as before: project -> spec codes -> oracle machines ---
def _allowed_codes_by_project(project: str) -> set[str]:
    project = (project or "").strip()
    if not project:
        return set()

    spec_codes = get_spec_codes_by_project(project) or []
    if not spec_codes:
        return set()

    rows = fetch_mes_rows_for_specs(spec_codes, exclude_ti_to=True) or []
    return { (r.get("machine_code") or "").strip() for r in rows if r.get("machine_code") }

# --- NEW: specific -> oracle machines ---
def _allowed_codes_by_specific(specific: str) -> set[str]:
    scodes = _resolve_spec_codes(specific)
    if not scodes:
        return set()
    rows = fetch_mes_rows_for_specs(scodes, exclude_ti_to=True) or []
    return { (r.get("machine_code") or "").strip() for r in rows if r.get("machine_code") }

# --- NEW: one place to compute the intersection window (project ∩ specific ∩ keyword) ---
def _allowed_codes_window(*, project, specific, keyword):
    """
    Returns:
      - None  : no scoping (when project, specific, keyword are ALL empty)
      - set() : explicit empty window (something requested, but no results)
      - set   : allowed codes window after applying all filters

    Order:
      1) Start with project set (if provided)
      2) Intersect with specific set (if provided)
      3) Apply keyword in Oracle then intersect again
    """
    proj = (project or "").strip()
    spec = (specific or "").strip()
    kw   = (keyword or "").strip()

    window: set | None = None

    if proj:
        proj_set = _allowed_codes_by_project(proj)
        window = proj_set if window is None else (window & proj_set)

    if spec:
        spec_set = _allowed_codes_by_specific(spec)
        window = spec_set if window is None else (window & spec_set)

    # if nothing yet and no keyword => truly no scope requested
    if window is None and not kw:
        return None

    # keyword narrowing (only if a keyword given OR we already have a window)
    with odb() as cur:
        sql = "SELECT DISTINCT sm.MACHINE_CODE FROM IDBUSER.RMS_SYS_MACHINE sm WHERE 1=1 AND sm.EQM_ID <> 'NA' AND sm.ENABLED = 'Y'"
        binds = {}
        if kw:
            sql += " AND (LOWER(sm.MACHINE_CODE) LIKE :k OR LOWER(NVL(sm.MACHINE_DESC, sm.MACHINE_CODE)) LIKE :k)"
            binds["k"] = f"%{kw.lower()}%"
        if window is not None:
            if not window:
                return set()
            placeholders = ",".join([f":c{i}" for i, _ in enumerate(sorted(window))])
            sql += f" AND sm.MACHINE_CODE IN ({placeholders})"
            binds.update({f"c{i}": code for i, code in enumerate(sorted(window))})

        cur.execute(sql, binds)
        narrowed = {r[0] for r in cur.fetchall() if r and r[0]}

    return narrowed

# 依 project / specific 找到所有相關的 spec，建立 machine_code -> (spec_code, spec_name) 對照
def _spec_map_for_project_specific(project: str, specific: str) -> dict[str, dict]:
    """
    回傳:
      {
        "M001": {"code": "R221-01", "name": "蝕刻前處理"},
        "M002": {"code": "R221-01", "name": "蝕刻前處理"},
        ...
      }
    """
    project = (project or "").strip()
    specific = (specific or "").strip()

    spec_codes: set[str] = set()

    # 1) from project → spec_codes
    if project:
        proj_specs = get_spec_codes_by_project(project) or []
        spec_codes.update([s for s in proj_specs if s])

    # 2) from specific (單一或多個) → spec_codes
    if specific:
        scodes = _resolve_spec_codes(specific) or []
        spec_codes.update([s for s in scodes if s])

    if not spec_codes:
        return {}

    # ★ 注意：這裡 rows 的 key 是小寫 spec_code / spec_name / machine_code
    rows = fetch_mes_rows_for_specs(sorted(spec_codes), exclude_ti_to=True) or []

    m: dict[str, dict] = {}
    for r in rows:
        mcode = (r.get("machine_code") or "").strip()   # ← 小寫
        scode = (r.get("spec_code") or "").strip()      # ← 小寫
        sname = (r.get("spec_name") or "").strip()      # ← 小寫
        if not mcode or not scode:
            continue
        # 如果一台機台對應多個製程，你可以改成 list；這裡先取第一個
        if mcode not in m:
            m[mcode] = {"code": scode, "name": sname or scode}

    return m


@bp.get("/groups-machines")
def groups_machines():
    project  = _norm(request.args.get("project"))
    specific = _norm(request.args.get("specific"))   # <-- NEW
    keyword  = _norm(request.args.get("keyword"))

    # print(f"project: {project}, specific: {specific}, keyword: {keyword}")
    window = _allowed_codes_window(project=project, specific=specific, keyword=keyword)

    out: Dict[str, Dict] = {}
    # 先算好 machine_code -> spec
    spec_map = _spec_map_for_project_specific(project, specific)

    def _add(gcode: str, gname: str, mcode: str, mname: str, mbuilding: str):
        machine_info = {
            "name": mname,
            "building": mbuilding,
        }
        # ★ 在這裡塞 spec_code / spec_name
        spec_info = spec_map.get(mcode)
        if spec_info:
            machine_info["spec_code"] = spec_info["code"]
            machine_info["spec_name"] = spec_info["name"]

        out.setdefault(gcode, {"name": gname, "machines": {}})
        out[gcode]["machines"][mcode] = machine_info

    # If window explicitly empty -> empty payload
    if window == set():
        return send_response(200, True, "請求成功", {"groups": out})

    with odb() as cur:
        sql = """
          SELECT
            sm.MACHINE_CODE,
            NVL(sm.MACHINE_DESC, sm.MACHINE_CODE)                AS MACHINE_NAME,
            NVL(sm.BUILDING, ''),
            NVL(mt.MACHINE_TYPE_NAME, '(未分類)')                     AS MACHINE_GROUP_CODE,
            NVL(mt.MACHINE_TYPE_DESC, sm.MACHINE_GROUP)          AS MACHINE_GROUP_NAME
          FROM IDBUSER.RMS_SYS_MACHINE sm
          LEFT JOIN IDBUSER.RMS_SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
          WHERE 1=1 AND sm.EQM_ID <> 'NA' AND sm.ENABLED = 'Y'
        """
        binds = {}
        if isinstance(window, set):  # concrete allowed list
            if not window:
                return send_response(200, True, "請求成功", {"groups": out})
            sql += f" AND sm.MACHINE_CODE IN ({','.join([f':c{i}' for i in range(len(window))])})"
            binds.update({f"c{i}": code for i, code in enumerate(sorted(window))})

        cur.execute(sql, binds)
        for mcode, mname, mbuilding, gcode, gname in cur.fetchall():
            _add(gcode or "(未分類)", gname or "(未分類)", (mcode or "").strip(), (mname or mcode).strip(), (mbuilding or ""))

    return send_response(200, True, "請求成功", {"groups": out})

@bp.get("/spec-groups-machines")
def spec_groups_machines():
    """
    根據多個適用工程 code (specific list) 回傳：
    {
      "specGroups": {
        "R221-01": {
          "GROUP_CODE": {
            "name": "群組名稱",
            "machines": [
              {"code": "MACHINE_CODE", "name": "MACHINE_NAME", "building": "BD"},
              ...
            ]
          },
          ...
        },
        "R331-03": { ... },
        ...
      }
    }
    """
    project = _norm(request.args.get("project"))
    keyword = _norm(request.args.get("keyword"))

    # 允許 ?specific=A&specific=B&specific=C 這種
    raw_specs = request.args.getlist("specific") or request.args.getlist("specific[]")
    specs = [_norm(s) for s in raw_specs if _norm(s)]

    # 相容：只給一個 specific=xxx
    if not specs:
        one = _norm(request.args.get("specific"))
        if one:
            specs = [one]

    # 沒有給 specific → 回空
    if not specs:
        return send_response(200, True, "請求成功", {"specGroups": {}})

    result: Dict[str, Dict] = {}

    with odb() as cur:
        for spec in specs:
            window = _allowed_codes_window(
                project=project,
                specific=spec,
                keyword=keyword,
            )

            spec_groups: Dict[str, Dict] = {}
            # 明確禁止 → 給空 map
            if window == set():
                result[spec] = spec_groups
                continue

            sql = """
              SELECT
                sm.MACHINE_CODE,
                NVL(sm.MACHINE_DESC, sm.MACHINE_CODE)                AS MACHINE_NAME,
                NVL(sm.BUILDING, ''),
                NVL(mt.MACHINE_TYPE_NAME, '(未分類)')                 AS MACHINE_GROUP_CODE,
                NVL(mt.MACHINE_TYPE_DESC, sm.MACHINE_GROUP)          AS MACHINE_GROUP_NAME
              FROM IDBUSER.RMS_SYS_MACHINE sm
              LEFT JOIN IDBUSER.RMS_SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
              WHERE 1=1 AND sm.EQM_ID <> 'NA' AND sm.ENABLED = 'Y'
            """
            binds = {}

            if isinstance(window, set):
                if not window:
                    result[spec] = spec_groups
                    continue
                sql += f" AND sm.MACHINE_CODE IN ({','.join([f':c{i}' for i in range(len(window))])})"
                binds.update({f"c{i}": code for i, code in enumerate(sorted(window))})

            cur.execute(sql, binds)
            for mcode, mname, mbuilding, gcode, gname in cur.fetchall():
                gkey = (gcode or "(未分類)").strip()
                gtitle = (gname or gkey).strip()
                mcode_s = (mcode or "").strip()
                mname_s = (mname or mcode_s).strip()
                bld_s   = (mbuilding or "").strip()

                if gkey not in spec_groups:
                    spec_groups[gkey] = {"name": gtitle, "machines": []}

                # 避免同一台機器重複塞
                if not any(m["code"] == mcode_s for m in spec_groups[gkey]["machines"]):
                    spec_groups[gkey]["machines"].append({
                        "code": mcode_s,
                        "name": mname_s,
                        "building": bld_s,
                    })

            result[spec] = spec_groups

    return send_response(200, True, "請求成功", {"specGroups": result})

@bp.post("/filter-by-pms")
def filter_by_pms():
    body       = request.get_json(silent=True) or {}
    base_code  = _clean_code(body.get("machine_code"))
    base_name  = _clean_name(body.get("machine_name"))
    project    = _norm(body.get("project"))
    specific   = _norm(body.get("specific"))     # <-- NEW
    keyword    = _norm(body.get("keyword"))

    # No baseline -> return original (respect project/specific/keyword)
    if not base_code:
        qs = {}
        if project:  qs["project"]  = project
        if specific: qs["specific"] = specific
        if keyword:  qs["keyword"]  = keyword
        with bp.test_request_context(query_string=qs):
            return groups_machines()

    # Window (project ∩ specific ∩ keyword). None => no scoping; set() => explicit empty
    window = _allowed_codes_window(project=project, specific=specific, keyword=keyword)
    if window == set():
        return send_response(200, True, "請求成功", {"groups": {}})

    # ----- PMS-equality (same as your working version) -----
    with odb() as cur:
        cur.execute("""
          SELECT DISTINCT TRIM(SLOT_NAME)
          FROM IDBUSER.RMS_FLEX_PMS
          WHERE MACHINE_CODE = :c
            AND SLOT_NAME IS NOT NULL
            AND LENGTH(TRIM(SLOT_NAME)) > 0
        """, c=base_code)
        base_slots = {r[0] for r in cur.fetchall()}
        baseline_has_pms = len(base_slots) > 0

        cur.execute("""
          SELECT MACHINE_CODE
          FROM IDBUSER.RMS_FLEX_PMS
          WHERE SLOT_NAME IS NOT NULL AND LENGTH(TRIM(SLOT_NAME)) > 0
          GROUP BY MACHINE_CODE
        """)
        has_pms_codes = { (r[0] or "").strip() for r in cur.fetchall() if r and r[0] }

        if not base_name:
            cur.execute("""
              SELECT NVL(MACHINE_DESC, MACHINE_CODE)
              FROM IDBUSER.RMS_SYS_MACHINE
              WHERE MACHINE_CODE = :c AND EQM_ID <> 'NA' AND ENABLED = 'Y'
            """, c=base_code)
            row = cur.fetchone()
            base_name = _clean_name(row[0] if row and row[0] else base_code)

        # PMS-equal candidates + group meta
        cur.execute("""
          WITH base AS (
            SELECT DISTINCT TRIM(SLOT_NAME) AS SLOT_NAME
            FROM IDBUSER.RMS_FLEX_PMS
            WHERE MACHINE_CODE = :c
              AND SLOT_NAME IS NOT NULL
              AND LENGTH(TRIM(SLOT_NAME)) > 0
          ),
          base_cnt AS ( SELECT COUNT(*) AS cnt FROM base ),
          cand AS (
            SELECT DISTINCT MACHINE_CODE, TRIM(SLOT_NAME) AS SLOT_NAME
            FROM IDBUSER.RMS_FLEX_PMS
            WHERE SLOT_NAME IS NOT NULL
              AND LENGTH(TRIM(SLOT_NAME)) > 0
          )
          SELECT
            sm.MACHINE_CODE,
            NVL(sm.MACHINE_DESC, sm.MACHINE_CODE)       AS MACHINE_NAME,
            NVL(sm.MACHINE_GROUP, '(未分類)')            AS MACHINE_GROUP_CODE,
            NVL(mt.MACHINE_TYPE_DESC, sm.MACHINE_GROUP) AS MACHINE_GROUP_NAME
          FROM IDBUSER.RMS_SYS_MACHINE sm
          LEFT JOIN IDBUSER.RMS_SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_NAME = sm.MACHINE_GROUP, base_cnt bc
          WHERE
            ((bc.cnt = 0 AND NOT EXISTS (SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE))
            OR
            (bc.cnt > 0
             AND EXISTS (SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE)
             AND NOT EXISTS (
               SELECT 1 FROM cand cx
               WHERE cx.MACHINE_CODE = sm.MACHINE_CODE
                 AND NOT EXISTS (SELECT 1 FROM base b WHERE b.SLOT_NAME = cx.SLOT_NAME)
             )
             AND NOT EXISTS (
               SELECT 1 FROM base b
               WHERE NOT EXISTS (
                 SELECT 1 FROM cand cx
                 WHERE cx.MACHINE_CODE = sm.MACHINE_CODE
                   AND cx.SLOT_NAME = b.SLOT_NAME
               )
             )
            )) AND sm.EQM_ID <> 'NA' AND sm.ENABLED = 'Y'
        """, c=base_code)

        rows = cur.fetchall()
        pms_equal_codes: set[str] = set()
        meta: Dict[str, Dict] = {}
        for mcode, mname, gcode, gname in rows:
            code = _clean_code(mcode)
            # restrict to window (if there is a concrete set)
            if isinstance(window, set) and code not in window:
                continue
            pms_equal_codes.add(code)
            meta[code] = {
                "name":  _clean_name(mname) or code,
                "gcode": (gcode or "(未分類)").strip(),
                "gname": (gname or "(未分類)").strip(),
            }

    # ----- MySQL: equal condition set by (machine_name, condition_id) -----
    with db(dict_cursor=True) as (_, cur):
        cur.execute("""
          SELECT DISTINCT condition_id
          FROM sfdb.rms_group_machines
          WHERE machine_name = %s
        """, (base_name,))
        base_cond = {r["condition_id"] for r in cur.fetchall()}

        cur.execute("SELECT machine_name, condition_id FROM sfdb.rms_group_machines")
        name_to_cond: Dict[str, set[int]] = {}
        for r in cur.fetchall():
            nm = _clean_name(r["machine_name"])
            if not nm: continue
            name_to_cond.setdefault(nm, set()).add(r["condition_id"])

    # ----- intersect PMS parity + exact condition equality -----
    final_codes: set[str] = set()
    for code in pms_equal_codes:
        if (code in has_pms_codes) != (baseline_has_pms):
            continue
        cand_name = meta.get(code, {}).get("name", code)
        if name_to_cond.get(cand_name, set()) != base_cond:
            continue
        final_codes.add(code)

    # ----- build groups payload -----
    groups_payload: Dict[str, Dict] = {}
    for code in sorted(final_codes):
        m = meta.get(code, {"name": code, "gcode": "(未分類)", "gname": "(未分類)"})
        gcode, gname = m["gcode"], m["gname"]
        groups_payload.setdefault(gcode, {"name": gname, "machines": {}})
        groups_payload[gcode]["machines"][code] = {"name": m["name"]}

    return send_response(200, True, "請求成功", {"groups": groups_payload})

@bp.post("/filter-by-baseline")
def filter_by_baseline():
    body       = request.get_json(silent=True) or {}
    base_code  = _clean_code(body.get("machine_code"))  # Oracle MACHINE_CODE
    base_name  = _clean_name(body.get("machine_name"))
    project    = _norm(body.get("project"))
    specific   = _norm(body.get("specific"))
    keyword    = _norm(body.get("keyword"))

    spec_map = _spec_map_for_project_specific(project, specific)

    # 1) 無基準 → 回原清單
    if not base_code:
        qs = {}
        if project:  qs["project"]  = project
        if specific: qs["specific"] = specific
        if keyword:  qs["keyword"]  = keyword
        with bp.test_request_context(query_string=qs):
            return groups_machines()

    # 2) 計算 window（project ∩ specific ∩ keyword）
    window = _allowed_codes_window(project=project, specific=specific, keyword=keyword)
    if window == set():
        return send_response(200, True, "請求成功", {"groups": {}})

    # 3) Oracle：PMS 相等候選 + Meta
    with odb() as cur:
        cur.execute("""
            SELECT DISTINCT TRIM(SLOT_NAME) FROM IDBUSER.RMS_FLEX_PMS
            WHERE MACHINE_CODE = :c AND SLOT_NAME IS NOT NULL AND LENGTH(TRIM(SLOT_NAME)) > 0
        """, c=base_code)
        base_slots = {r[0] for r in cur.fetchall()}
        baseline_has_pms = len(base_slots) > 0

        cur.execute("""
            SELECT MACHINE_CODE FROM IDBUSER.RMS_FLEX_PMS
            WHERE SLOT_NAME IS NOT NULL AND LENGTH(TRIM(SLOT_NAME)) > 0
            GROUP BY MACHINE_CODE
        """)
        has_pms_codes = {(r[0] or "").strip() for r in cur.fetchall() if r and r[0]}

        if not base_name:
            cur.execute("""
              SELECT NVL(MACHINE_DESC, MACHINE_CODE)
              FROM IDBUSER.RMS_SYS_MACHINE
              WHERE MACHINE_CODE = :c AND EQM_ID <> 'NA' AND ENABLED = 'Y'
            """, c=base_code)
            row = cur.fetchone()
            base_name = _clean_name(row[0] if row and row[0] else base_code)

        # 這段是你既有的「PMS 完全相等」SQL（沿用你新版機種型別關聯）
        cur.execute("""
          WITH base AS (
            SELECT DISTINCT TRIM(SLOT_NAME) AS SLOT_NAME
            FROM IDBUSER.RMS_FLEX_PMS
            WHERE MACHINE_CODE = :c
              AND SLOT_NAME IS NOT NULL
              AND LENGTH(TRIM(SLOT_NAME)) > 0
          ),
          base_cnt AS ( SELECT COUNT(*) AS cnt FROM base ),
          cand AS (
            SELECT DISTINCT MACHINE_CODE, TRIM(SLOT_NAME) AS SLOT_NAME
            FROM IDBUSER.RMS_FLEX_PMS
            WHERE SLOT_NAME IS NOT NULL
              AND LENGTH(TRIM(SLOT_NAME)) > 0
          )
          SELECT
            sm.MACHINE_CODE,
            NVL(sm.MACHINE_DESC, sm.MACHINE_CODE)       AS MACHINE_NAME,
            NVL(sm.building, ''),
            NVL(mt.MACHINE_TYPE_NAME, '(未分類)')        AS MACHINE_GROUP_CODE,
            NVL(mt.MACHINE_TYPE_DESC, sm.MACHINE_GROUP) AS MACHINE_GROUP_NAME
          FROM IDBUSER.RMS_SYS_MACHINE sm
          LEFT JOIN IDBUSER.RMS_SYS_MACHINE_TYPE mt
              ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID, base_cnt bc
          WHERE
            ((bc.cnt = 0 AND NOT EXISTS (SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE))
            OR
            (bc.cnt > 0
             AND EXISTS (SELECT 1 FROM cand cx WHERE cx.MACHINE_CODE = sm.MACHINE_CODE)
             AND NOT EXISTS (
               SELECT 1 FROM cand cx
               WHERE cx.MACHINE_CODE = sm.MACHINE_CODE
                 AND NOT EXISTS (SELECT 1 FROM base b WHERE b.SLOT_NAME = cx.SLOT_NAME)
             )
             AND NOT EXISTS (
               SELECT 1 FROM base b
               WHERE NOT EXISTS (
                 SELECT 1 FROM cand cx
                 WHERE cx.MACHINE_CODE = sm.MACHINE_CODE
                   AND cx.SLOT_NAME = b.SLOT_NAME
               )
             )
            )) AND sm.EQM_ID <> 'NA' AND sm.ENABLED = 'Y'
        """, c=base_code)

        rows = cur.fetchall()
        pms_equal_codes: set[str] = set()
        meta: Dict[str, Dict] = {}
        for mcode, mname, mbuilding, gcode, gname in rows:
            code = _clean_code(mcode)
            if isinstance(window, set) and code not in window:
                continue
            pms_equal_codes.add(code)
            meta[code] = {
                "name":  _clean_name(mname) or code,
                "building": (mbuilding or ""),
                "gcode": (gcode or "(未分類)").strip(),
                "gname": (gname or "(未分類)").strip(),
            }

    # 候選為空直接回傳
    if not pms_equal_codes:
        return send_response(200, True, "請求成功", {"groups": {}})

    # 4) MySQL：只針對「PMS 候選」比對「條件集合簽章」
    #    - 先算 baseline 的簽章 (優先 machine_id; 若無資料，再用 machine_name)
    #    - 然後把候選以 UNION ALL 做成 derived table，LEFT JOIN group_machines，GROUP BY 後算出每台簽章，比對等於 baseline_sig
    with db(dict_cursor=False) as (_, cur_mysql):
        # baseline sig by id
        cur_mysql.execute("""
            SELECT COALESCE(GROUP_CONCAT(DISTINCT condition_id ORDER BY condition_id SEPARATOR ','), '')
            FROM sfdb.rms_group_machines
            WHERE machine_id = %s
        """, (base_code,))
        baseline_sig = cur_mysql.fetchone()[0]  # '' 代表空集合

        # 若基準機台以 id 找不到任何 row（None 或 None-like），再用 name 算一次
        if baseline_sig is None:
            cur_mysql.execute("""
                SELECT COALESCE(GROUP_CONCAT(DISTINCT condition_id ORDER BY condition_id SEPARATOR ','), '')
                FROM sfdb.rms_group_machines
                WHERE machine_name = %s
            """, (base_name,))
            baseline_sig = cur_mysql.fetchone()[0]
            if baseline_sig is None:
                baseline_sig = ''  # 真正完全無條件

        cand_codes = sorted(pms_equal_codes)
        # derived table: (SELECT %s) UNION ALL (SELECT %s) ...
        cand_union_sql = " UNION ALL ".join(["SELECT %s"] * len(cand_codes))

        # gm 子查詢只掃候選集合
        in_placeholders = ",".join(["%s"] * len(cand_codes))

        sql = f"""
            SELECT cand.machine_id
            FROM ({cand_union_sql}) AS cand(machine_id)
            LEFT JOIN (
                SELECT machine_id, condition_id
                FROM sfdb.rms_group_machines
                WHERE machine_id IN ({in_placeholders})
            ) AS gm
            ON gm.machine_id = cand.machine_id
            GROUP BY cand.machine_id
            HAVING COALESCE(GROUP_CONCAT(DISTINCT gm.condition_id ORDER BY gm.condition_id SEPARATOR ','), '') = %s
        """
        params = cand_codes + cand_codes + [baseline_sig]
        cur_mysql.execute(sql, params)
        matched_codes = { (r[0] or "").strip() for r in cur_mysql.fetchall() if r and r[0] }

    # 5) 再做一次 PMS parity（有/無 PMS 必須跟基準一致），最終名單
    final_codes: set[str] = set()
    for code in matched_codes:
        if (code in has_pms_codes) != (baseline_has_pms):
            continue
        final_codes.add(code)

    # 6) 組回 groups payload
    groups_payload: Dict[str, Dict] = {}
    for code in sorted(final_codes):
        m = meta.get(code, {
            "name": code,
            "gcode": "(未分類)",
            "gname": "(未分類)",
            "building": "",
        })
        gcode, gname = m["gcode"], m["gname"]
        groups_payload.setdefault(gcode, {"name": gname, "machines": {}})

        machine_info = {
            "name": m["name"],
            "building": m["building"],
        }

        # ★ 補上 spec_code / spec_name
        spec_info = spec_map.get(code)
        print(f"code: {code}, sepc_info: {spec_info}")
        if spec_info:
            machine_info["spec_code"] = spec_info["code"]
            machine_info["spec_name"] = spec_info["name"]

        groups_payload[gcode]["machines"][code] = machine_info

    return send_response(200, True, "請求成功", {"groups": groups_payload})

# --------- /machines ---------
@bp.get("/machines")
def machines():
    keyword  = _norm(request.args.get("keyword"))
    specific = request.args.get("specific")  # spec name or code

    if not keyword and specific is None:
        return send_response(401, True, "沒有搜尋條件", {"message": "沒有搜尋條件"})

    results = {}

    def _add(r):
        gname = r.get("machine_group") or "(未分類)"
        gcode = r.get("machine_group") or "(未分類)"  # TODO: swap to MACHINE_GROUP_CODE when available
        results[r["machine_code"]] = {
            "name": r["machine_name"],
            "group_code": gcode,
            "group_name": gname
        }

    if specific is not None:
        spec_code = _norm(specific)
        rows = fetch_mes_rows_for_specs([spec_code], exclude_ti_to=True)
        if not rows:
            all_codes = _all_spec_codes_from_mysql()
            all_rows = fetch_mes_rows_for_specs(all_codes, exclude_ti_to=True)
            cand_codes = sorted({r["spec_code"] for r in all_rows if _icontains(r["spec_name"], specific)})
            rows = [r for r in all_rows if r["spec_code"] in cand_codes]

        for r in rows:
            if keyword and not (_icontains(r["machine_name"], keyword) or _icontains(r["machine_code"], keyword)):
                continue
            _add(r)
    else:
        if not keyword:
            return send_response(401, True, "沒有搜尋條件", {"message": "沒有搜尋條件"})
        rows = fetch_mes_rows_for_specs(_all_spec_codes_from_mysql(), exclude_ti_to=True)
        for r in rows:
            if _icontains(r["machine_name"], keyword) or _icontains(r["machine_code"], keyword):
                _add(r)

    return send_response(200, True, "請求成功", {"machines": results})

@bp.get("/machine-groups")
def machine_groups():
    project   = _norm(request.args.get("project"))
    specific  = _norm(request.args.get("specific"))
    keyword   = _norm(request.args.get("keyword"))

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


# -------------------------------------- PMS --------------------------------------

HEADER_ROW = ['槽體', '管理項目', '規格下限(OOS-)', '操作下限(OOC-)', '設定值', '操作上限(OOC+)', '規格上限(OOS+)', '單位', '參數下放', '說明']
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
              SELECT DISTINCT
                p.PROCESS_DESC AS process_code,
                p.PROCESS_NAME AS process_name
              FROM IDBUSER.RMS_SYS_PROCESS p
              WHERE 1=1
            """
            binds = {}

            # 依 keyword 過濾 PROCESS_NAME
            if keyword:
                sql += " AND LOWER(p.PROCESS_NAME) LIKE :kw"
                binds["kw"] = f"%{keyword}%"

            # 如果有帶機台，就 join TERMINAL / MACHINE 只保留有掛在該機台線別上的製程
            if machine:
                sql += """
                  AND EXISTS (
                    SELECT 1 FROM IDBUSER.RMS_SYS_TERMINAL t
                    JOIN IDBUSER.RMS_SYS_MACHINE m ON t.PDLINE_ID = m.PDLINE_ID
                    WHERE t.PROCESS_ID = p.PROCESS_ID AND m.MACHINE_CODE = :mcode AND m.EQM_ID <> 'NA' AND m.ENABLED = 'Y'
                  )
                """
                binds["mcode"] = machine

            # 如果有帶品目 matnr，就從 EZFLEX_ROUTING / EZFLEX_TOOL 反查有哪些製程
            if matnr:
                sql += """
                  AND EXISTS (
                    SELECT 1
                    FROM IDBUSER.EZFLEX_ROUTING r
                    JOIN IDBUSER.EZFLEX_TOOL t
                      ON r.MATNR = t.MATNR AND r.REVLV = t.REVLV AND r.VORNR = t.VORNR
                    WHERE t.MATNR = :matnr
                      AND t.SFHNR LIKE '%-ST%'
                      AND r.KTSCH = p.PROCESS_DESC
                  )
                """
                binds["matnr"] = matnr

            sql += " ORDER BY p.PROCESS_DESC"

            cur.execute(sql, binds)
            rows = cur.fetchall()

        specifics = [
            {"code": r[0], "name": r[1]}  # process_code, process_name
            for r in rows
        ]
        
        return send_response(200, True, "請求成功", {"specifics": specifics})
    except Exception as e:
        print("Error /mes/step1/specs:", e)
        return send_response(500, True, "查詢失敗", {"message": str(e)})

@bp.get("/step1/machines")
def step1_machines():
    """
    機台群與機台選單：
      - keyword: 模糊 MACHINE_DESC
      - specific: 製程代碼 (PROCESS_DESC)
      - matnr: 品目 (MATNR)
    """
    keyword  = (request.args.get("keyword") or "").strip().lower()
    specific = (request.args.get("specific") or "").strip()
    matnr    = (request.args.get("matnr") or "").strip()

    try:
        with ora_cursor() as cur:
            sql = """
              SELECT
                sm.MACHINE_CODE,
                NVL(sm.MACHINE_DESC, sm.MACHINE_CODE)                AS MACHINE_NAME,
                NVL(mt.MACHINE_TYPE_NAME, '(未分類)')                 AS MACHINE_GROUP_CODE,
                NVL(mt.MACHINE_TYPE_DESC, sm.MACHINE_GROUP)          AS MACHINE_GROUP_NAME
              FROM IDBUSER.RMS_SYS_MACHINE sm
              LEFT JOIN IDBUSER.RMS_SYS_MACHINE_TYPE mt ON mt.MACHINE_TYPE_ID = sm.MACHINE_TYPE_ID
              WHERE 1=1 AND sm.EQM_ID <> 'NA' AND sm.ENABLED = 'Y'
            """
            binds = {}

            if keyword:
                sql += " AND LOWER(sm.MACHINE_DESC) LIKE :kw"
                binds["kw"] = f"%{keyword}%"

            # 若有指定製程，只保留「線別上有掛此製程」的機台
            if specific:
                sql += """
                  AND EXISTS (
                    SELECT 1
                    FROM IDBUSER.RMS_SYS_TERMINAL t
                    JOIN IDBUSER.RMS_SYS_PROCESS p
                      ON t.PROCESS_ID = p.PROCESS_ID
                    WHERE t.PDLINE_ID = sm.PDLINE_ID
                      AND p.PROCESS_DESC = :spc
                  )
                """
                binds["spc"] = specific

            # 若有指定品目，只保留「該品目 routing 中有用到的機台所屬線別」的機台
            if matnr:
                sql += """
                  AND EXISTS (
                    SELECT 1
                    FROM IDBUSER.EZFLEX_ROUTING r
                    JOIN IDBUSER.EZFLEX_TOOL t
                      ON r.MATNR = t.MATNR AND r.REVLV = t.REVLV AND r.VORNR = t.VORNR
                    JOIN IDBUSER.RMS_SYS_PROCESS p
                      ON p.PROCESS_DESC = r.KTSCH
                    JOIN IDBUSER.RMS_SYS_TERMINAL term
                      ON term.PROCESS_ID = p.PROCESS_ID
                    WHERE t.MATNR = :matnr
                      AND t.SFHNR LIKE '%-ST%'
                      AND term.PDLINE_ID = sm.PDLINE_ID
                  )
                """
                binds["matnr"] = matnr

            cur.execute(sql, binds)
            rows = cur.fetchall()

        groups = {}
        for mcode, mname, gcode, gname in rows:
            gcode = (gcode or "(未分類)").strip()
            gname = (gname or gcode).strip()
            mcode = (mcode or "").strip()
            mname = (mname or mcode).strip()
            if not mcode:
                continue

            g = groups.setdefault(gcode, {"name": gname, "machines": {}})
            g["machines"][mcode] = {"name": mname}

        return send_response(200, True, "請求成功", {"groups": groups})
    except Exception as e:
        print("Error /mes/step1/machines:", e)
        return send_response(500, True, "查詢失敗", {"message": str(e)})

