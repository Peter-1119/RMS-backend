from __future__ import annotations
import json, uuid
from decimal import Decimal, ROUND_HALF_UP
from flask import jsonify

import re
import unicodedata
from collections import defaultdict
from typing import Dict, List, Tuple, Any, Iterable, Optional
from db import db
from oracle_db import ora_cursor

def send_response(status_code, success, message, data=None):
    return jsonify({"success": success, "message": message, **({"data": data} if data is not None else {})}), status_code

def jload(v, default=None):
    if v is None: return default
    if isinstance(v, (dict, list)): return v
    try: return json.loads(v)
    except Exception: return default

def jdump(v): return json.dumps(v, ensure_ascii=False) if v is not None else None

def dver(v):
    try: return Decimal(str(v)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception: return Decimal("0.00")

def none_if_blank(v):
    return v if (v is not None and str(v).strip() != "") else None

def new_token(): return str(uuid.uuid4())



# ----------------- utilities -----------------
def _norm(s: Any) -> str:
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).strip()
    return " ".join(s.split())

_code_prefix_re = re.compile(r"^\s*\(([^)]+)\)\s*(.*)$")

def split_code_from_desc(desc: str) -> Tuple[str, str]:
    """
    MACHINE_DESC like "(L20AA2)SBS NC 沖孔機-12" -> ("L20AA2", "SBS NC 沖孔機-12")
    If no code prefix exists, returns ("", trimmed_desc).
    """
    if not desc:
        return "", ""
    s = _norm(desc)
    m = _code_prefix_re.match(s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", s

def clean_process_name(raw: str) -> str:
    """
    PROCESS_NAME like "(L100-01)單面前處理" -> "單面前處理"
    If there is no "(...)" prefix, returns the original, trimmed.
    """
    if not raw:
        return ""
    s = _norm(raw)
    m = _code_prefix_re.match(s)
    return m.group(2).strip() if m else s

# ----------------- MySQL: project → specs -----------------
def get_spec_codes_by_project(project: str) -> List[str]:
    """
    From sfdb.rms_spec_flat, return unique spec_code for a given project.
    """
    sql = """
        SELECT DISTINCT spec_code
        FROM sfdb.rms_spec_flat
        WHERE project = %s
    """
    with db(dict_cursor=True) as (_, cur):
        cur.execute(sql, (project,))
        return [_norm(r["spec_code"]) for r in cur.fetchall() if r.get("spec_code")]

def get_projects() -> List[str]:
    sql = "SELECT DISTINCT project FROM sfdb.rms_spec_flat ORDER BY project"
    with db(dict_cursor=True) as (_, cur):
        cur.execute(sql)
        return [_norm(r["project"]) for r in cur.fetchall() if r.get("project")]

# ----------------- Oracle: specs → machines -----------------
def fetch_mes_rows_for_specs(
    spec_codes: Iterable[str],
    exclude_ti_to: bool = True,
) -> List[Dict[str, Any]]:
    """
    Given spec_codes (PROCESS_DESC), pull rows:
      - spec_code (PROCESS_DESC)
      - spec_name (cleaned PROCESS_NAME)
      - machine_group_code   (MACHINE_GROUP / MACHINE_TYPE_NAME)
      - machine_group_name   (cleaned MACHINE_TYPE_DESC, without '(CODE)')
      - machine_type_name    (alias of group code; for backward compat)
      - machine_type_desc    (raw MACHINE_TYPE_DESC)
      - machine_code         (prefer column; else from MACHINE_DESC)
      - machine_name         (MACHINE_DESC without '(CODE)')
    Joins:
      PROCESS p
        JOIN TERMINAL t ON t.process_id = p.process_id
        JOIN MACHINE  m ON m.pdline_id  = t.pdline_id
        LEFT JOIN MACHINE_TYPE mt
               ON mt.machine_type_name = m.machine_group
    """
    spec_codes = [s for s in {_norm(s) for s in spec_codes} if s]
    if not spec_codes:
        return []

    bind_names = ", ".join([f":b{i}" for i in range(len(spec_codes))])

    where_suffix = ""
    if exclude_ti_to:
        where_suffix = "AND UPPER(SUBSTR(RTRIM(p.PROCESS_NAME), -2)) NOT IN ('TI','TO')"

    sql = f"""
        SELECT
            p.PROCESS_DESC,
            p.PROCESS_NAME,
            m.MACHINE_GROUP,              -- code on MACHINE
            mt.MACHINE_TYPE_NAME,         -- code on TYPE (should match MACHINE_GROUP)
            mt.MACHINE_TYPE_DESC,         -- human display (contains '(CODE)' prefix)
            m.MACHINE_CODE,
            m.MACHINE_DESC
        FROM IDBUSER.RMS_SYS_PROCESS   p
        JOIN IDBUSER.RMS_SYS_TERMINAL  t ON t.PROCESS_ID = p.PROCESS_ID
        JOIN IDBUSER.RMS_SYS_MACHINE   m ON m.PDLINE_ID  = t.PDLINE_ID
        LEFT JOIN IDBUSER.RMS_SYS_MACHINE_TYPE mt
               ON mt.MACHINE_TYPE_NAME = m.MACHINE_GROUP
        WHERE p.PROCESS_DESC IN ({bind_names})
          {where_suffix}
    """

    rows: List[Dict[str, Any]] = []
    with ora_cursor() as cur:
        cur.arraysize = 5000
        cur.prefetchrows = 5000
        binds = {f"b{i}": spec_codes[i] for i in range(len(spec_codes))}
        cur.execute(sql, binds)
        for (proc_desc, proc_name,
             mgroup_code_from_machine,
             mtype_name_code,
             mtype_desc_raw,
             mcode_col, mdesc) in cur:

            spec_code = _norm(proc_desc)
            spec_name = clean_process_name(proc_name)

            # Group code (prefer MACHINE_GROUP; fall back to TYPE_NAME)
            machine_group_code = _norm(mtype_name_code) or _norm(mgroup_code_from_machine)

            code_from_desc, group_name_clean = split_code_from_desc(mtype_desc_raw or "")
            machine_group_name = group_name_clean or _norm(mtype_desc_raw)

            code_from_mdesc, machine_name_clean = split_code_from_desc(mdesc or "")
            machine_code = _norm(mcode_col) or code_from_mdesc
            machine_name = machine_name_clean or _norm(mdesc)

            rows.append({
                "spec_code": spec_code,
                "spec_name": spec_name,

                "machine_group_code": machine_group_code,          # <- real code
                "machine_group_name": machine_group_name,          # <- clean display
                "machine_type_desc": _norm(mtype_desc_raw or ""),  # raw desc

                # Back-compat keys:
                "machine_group": machine_group_name,               # display
                "machine_type_name": machine_group_code,           # code

                "machine_code": machine_code,
                "machine_name": machine_name,
            })
    return rows

# ----------------- public APIs -----------------
def get_hierarchy_by_project(project: str, *, exclude_ti_to: bool = True) -> Dict[str, Any]:
    """
    Build a nested structure for one project:
    {
      "project": <project>,
      "specs": [
        {
          "spec_code": "...",
          "spec_name": "...",
          "machine_groups": [
            {
              "machine_group": "...",
              "machine_type_name": "...",
              "machines": [
                {"machine_code": "...", "machine_name": "..."},
                ...
              ]
            },
            ...
          ]
        }, ...
      ]
    }
    """
    spec_codes = get_spec_codes_by_project(project)
    rows = fetch_mes_rows_for_specs(spec_codes, exclude_ti_to=exclude_ti_to)

    # group by spec_code → machine_group
    spec_map: Dict[str, Dict[str, Any]] = {}
    groups_map: Dict[Tuple[str, str], Dict[str, Any]] = {}

    for r in rows:
        sc = r["spec_code"]
        sn = r["spec_name"]

        mg_code = r.get("machine_group_code", "") or ""
        mg_name = r.get("machine_group_name", "") or r.get("machine_group", "")
        mtn_desc = r.get("machine_type_desc", "")

        mc = r["machine_code"]
        mn = r["machine_name"]

        if sc not in spec_map:
            spec_map[sc] = {"spec_code": sc, "spec_name": sn, "machine_groups": []}

        gkey = (sc, mg_code or mg_name or "(UNSPECIFIED)")
        if gkey not in groups_map:
            groups_map[gkey] = {
                "machine_group": mg_name,            # display
                "machine_group_code": mg_code,       # code
                "machine_type_name": mg_code,        # backward compat: keep code here
                "machine_type_desc": mtn_desc,       # raw TYPE DESC
                "machines": []
            }
            spec_map[sc]["machine_groups"].append(groups_map[gkey])

        # Dedup machines within group/spec
        if not any(x["machine_code"] == mc and x["machine_name"] == mn for x in groups_map[gkey]["machines"]):
            groups_map[gkey]["machines"].append({"machine_code": mc, "machine_name": mn})


    return {
        "project": project,
        "specs": sorted(spec_map.values(), key=lambda x: (x["spec_code"], x["spec_name"]))
    }

def get_machines_by_spec(spec_code: str, *, exclude_ti_to: bool = True) -> Dict[str, Any]:
    """
    Return grouped machines for a single specification code.
    """
    rows = fetch_mes_rows_for_specs([spec_code], exclude_ti_to=exclude_ti_to)
    result = {
        "spec_code": _norm(spec_code),
        "spec_name": next((r["spec_name"] for r in rows if r["spec_name"]), ""),
        "machine_groups": []
    }
    gmap: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        mg_code = r.get("machine_group_code", "") or ""
        mg_name = r.get("machine_group_name", "") or r.get("machine_group", "")
        mtn_desc = r.get("machine_type_desc", "")

        gkey = mg_code or mg_name
        if gkey not in gmap:
            gmap[gkey] = {
                "machine_group": mg_name,           # display
                "machine_group_code": mg_code,      # code
                "machine_type_name": mg_code,       # keep code for compat
                "machine_type_desc": mtn_desc,      # raw desc
                "machines": []
            }
            result["machine_groups"].append(gmap[gkey])

        if not any(x["machine_code"] == r["machine_code"] for x in gmap[gkey]["machines"]):
            gmap[gkey]["machines"].append({
                "machine_code": r["machine_code"],
                "machine_name": r["machine_name"]
            })

    return result

def search_machines(
    *, project: str | None = None,
    spec_codes: Iterable[str] | None = None,
    text: str | None = None,
    exclude_ti_to: bool = True
) -> List[Dict[str, Any]]:
    """
    Flexible search:
      - by project (maps to spec_codes via MySQL),
      - or directly by spec_codes,
      - optional free-text filter (applies to machine_code/name and spec fields).
    Returns flat rows (good for tables).
    """
    if project:
        spec_codes = get_spec_codes_by_project(project)
    spec_codes = list(spec_codes or [])
    rows = fetch_mes_rows_for_specs(spec_codes, exclude_ti_to=exclude_ti_to)

    if text:
        needle = _norm(text).upper()
        out = []
        for r in rows:
            hay = " ".join([
                r["spec_code"], r["spec_name"],
                r.get("machine_group_name",""), r.get("machine_group_code",""),
                r["machine_code"], r["machine_name"]
            ]).upper()
            if needle in hay:
                out.append(r)
        return out

    return rows

# --- add this helper in utils.py (keep your existing code untouched) ---
def search_machine_groups(
    *,
    project: Optional[str] = None,
    spec_codes: Optional[Iterable[str]] = None,
    text: Optional[str] = None,
    exclude_ti_to: bool = True,
) -> List[Dict[str, Any]]:
    """
    Return machine groups only (no machines) filtered by project/spec/keyword.
    Output:
      [
        {
          "group_code": "...",          # MACHINE_TYPE_NAME / MACHINE_GROUP
          "group_name": "...",          # cleaned MACHINE_TYPE_DESC
          "group_desc": "...",          # raw MACHINE_TYPE_DESC
          "count": <#machines in this result set>,
        }, ...
      ]
    """
    # Resolve spec set
    if project and not spec_codes:
        spec_codes = get_spec_codes_by_project(project)
    spec_codes = list(spec_codes or [])

    # Pull once; allow groups-only search by keyword (no specs) if text given
    if not spec_codes and not text:
        return []

    rows = fetch_mes_rows_for_specs(spec_codes, exclude_ti_to=exclude_ti_to)

    # Optional keyword filter across group fields (and spec/machine text for convenience)
    if text:
        needle = _norm(text).upper()
        def hit(r):
            hay = " ".join([
                r.get("machine_group_name",""), r.get("machine_group_code",""),
                r.get("machine_type_desc",""), r["spec_code"], r["spec_name"]
            ]).upper()
            return needle in hay
        rows = [r for r in rows if hit(r)]

    # Aggregate by group code
    gmap: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        gcode = r.get("machine_group_code") or ""
        gname = r.get("machine_group_name") or r.get("machine_group") or ""
        gdesc = r.get("machine_type_desc", "")
        if not gcode and not gname:
            gcode = "(UNSPECIFIED)"; gname = "(未分類)"

        g = gmap.get(gcode)
        if not g:
            g = gmap[gcode] = {
                "group_code": gcode,
                "group_name": gname,
                "group_desc": gdesc,
                "count": 0,
            }
        # count unique machines per group (in case of duplicates across terminals)
        g["count"] += 1

    groups = list(gmap.values())
    groups.sort(key=lambda x: (x["group_name"], x["group_code"]))
    return groups

def search_machines_by_group(
    *,
    group_code: str,
    project: Optional[str] = None,
    spec_codes: Optional[Iterable[str]] = None,
    text: Optional[str] = None,
    exclude_ti_to: bool = True,
) -> List[Dict[str, str]]:
    """
    Return machines for a specific group_code, optionally constrained by project/spec/keyword.
    Output:
      [ {"machine_code":"...", "machine_name":"..."}, ... ]  (deduped & sorted)
    """
    if project and not spec_codes:
        spec_codes = get_spec_codes_by_project(project)
    spec_codes = list(spec_codes or [])

    if not group_code:
        return []

    rows = fetch_mes_rows_for_specs(spec_codes, exclude_ti_to=exclude_ti_to)
    rows = [r for r in rows if _norm(r.get("machine_group_code","")) == _norm(group_code)]

    if text:
        needle = _norm(text).upper()
        rows = [r for r in rows if needle in (r["machine_code"] + " " + r["machine_name"]).upper()]

    # Dedup by machine_code (prefer stable code id)
    seen = set()
    out = []
    for r in rows:
        mc = r["machine_code"]
        if mc and mc not in seen:
            seen.add(mc)
            out.append({"machine_code": mc, "machine_name": r["machine_name"]})
    out.sort(key=lambda x: (x["machine_code"], x["machine_name"]))
    return out


def list_all_machine_groups() -> List[Dict[str, Any]]:
    """
    Return ALL machine groups from Oracle, fast, with machine counts.
    Uses RMS_SYS_MACHINE_TYPE (group master) and RMS_SYS_MACHINE for counts.
    Output: [{group_code, group_name, group_desc, count}, ...]
    """
    sql = """
        SELECT
            mt.MACHINE_TYPE_NAME   AS group_code,
            mt.MACHINE_TYPE_DESC   AS group_desc,
            COUNT(DISTINCT m.MACHINE_CODE) AS cnt
        FROM IDBUSER.RMS_SYS_MACHINE_TYPE mt
        LEFT JOIN IDBUSER.RMS_SYS_MACHINE m
               ON m.MACHINE_GROUP = mt.MACHINE_TYPE_NAME
        GROUP BY mt.MACHINE_TYPE_NAME, mt.MACHINE_TYPE_DESC
        ORDER BY mt.MACHINE_TYPE_NAME
    """
    out: List[Dict[str, Any]] = []
    with ora_cursor() as cur:
        cur.arraysize = 2000
        cur.prefetchrows = 2000
        cur.execute(sql)
        for group_code, group_desc, cnt in cur:
            code_from_desc, clean_name = split_code_from_desc(group_desc or "")
            group_name = clean_name or _norm(group_desc)
            out.append({
                "group_code": _norm(group_code),
                "group_name": group_name,
                "group_desc": _norm(group_desc or ""),
                "count": int(cnt or 0),
            })
    # Sort by display name then code for stability
    out.sort(key=lambda x: (x["group_name"], x["group_code"]))
    return out

def fetch_machines_by_group_all(
    group_code: str,
    text: str | None = None,
) -> List[Dict[str, Any]]:
    """
    Return ALL machines under a MACHINE_GROUP (group_code), ignoring specs/projects.
    Optionally filter by free-text (code or name).
    Output: [{machine_code, machine_name}]
    """
    group_code = _norm(group_code)
    if not group_code:
        return []

    sql = """
        SELECT m.MACHINE_CODE, m.MACHINE_DESC
        FROM IDBUSER.RMS_SYS_MACHINE m
        WHERE m.MACHINE_GROUP = :g
    """
    rows: List[Dict[str, Any]] = []
    with ora_cursor() as cur:
        cur.arraysize = 5000
        cur.prefetchrows = 5000
        cur.execute(sql, {"g": group_code})
        for mcode, mdesc in cur:
            code_from_desc, name_clean = split_code_from_desc(mdesc or "")
            machine_code = _norm(mcode) or code_from_desc
            machine_name = name_clean or _norm(mdesc)
            rows.append({"machine_code": machine_code, "machine_name": machine_name})

    if text:
        needle = _norm(text).upper()
        rows = [r for r in rows if (needle in (r["machine_code"] or "").upper()
                                    or needle in (r["machine_name"] or "").upper())]
    # (optional) stable sort
    rows.sort(key=lambda r: (r["machine_name"], r["machine_code"]))
    return rows
