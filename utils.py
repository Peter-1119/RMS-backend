from __future__ import annotations
import json, uuid
from decimal import Decimal, ROUND_HALF_UP
from flask import jsonify

import re
import unicodedata
from typing import Dict, List, Any
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
_code_prefix_re = re.compile(r"^\s*\(([^)]+)\)\s*(.*)$")

def clean_desc_to_name(desc: str) -> str:
    """
    PROCESS_NAME like "(L100-01)單面前處理" -> "單面前處理"
    If there is no "(...)" prefix, returns the original, trimmed.
    """
    if len(desc) == 0:
        return ""
    m = _code_prefix_re.match(desc)
    return m.group(2).strip() if m else desc

# ----------------- MySQL: project → specs -----------------
def get_spec_codes_by_project(project: str):
    if project == None or len(project) == 0:
        return [], "No input project"

    try:
        with db(dict_cursor=True) as (_, cur):
            sql = f"SELECT DISTINCT spec_code FROM sfdb.rms_spec_flat WHERE project = '{project}'"
            cur.execute(sql)
            return [s["spec_code"] for s in cur.fetchall()], "Success"
    
    except Exception as e:
        print("Project query failed: ", e)
        return [], "Project query failed"

WHERE_PREFIX = "REGEXP_LIKE(p.PROCESS_NAME, '^\([LR][0-8][[:digit:]]{2}-[[:digit:]]{2}\)') AND p.PROCESS_NAME NOT LIKE '%人工%' AND sm.ENABLED = 'Y' AND sm.EQM_ID <> 'NA'"
def get_spec_codes_by_itemType(itemType: str):
    if itemType == None or len(itemType) == 0:
        return [], "No input item type."
    
    try:
        with ora_cursor() as cur:
            sql = f"""
                SELECT DISTINCT p.PROCESS_DESC, p.PROCESS_NAME FROM IDBUSER.RMS_SYS_PROCESS p
                JOIN IDBUSER.RMS_SYS_TERMINAL t ON p.PROCESS_ID = t.PROCESS_ID
                JOIN IDBUSER.RMS_SYS_MACHINE sm ON t.PDLINE_ID = sm.PDLINE_ID
                WHERE {WHERE_PREFIX} AND EXISTS (
                    SELECT 1 FROM IDBUSER.EZFLEX_ROUTING r
                    JOIN IDBUSER.EZFLEX_TOOL t ON r.MATNR = t.MATNR AND r.REVLV = t.REVLV AND r.VORNR = t.VORNR
                    WHERE t.MATNR LIKE '{itemType}%' AND t.SFHNR LIKE '%-ST%' AND r.KTSCH = p.PROCESS_DESC
                )
            """
            # print(f"sql: {sql}")

            cur.execute(sql)
            rows = cur.fetchall()

        # specifics = [{"code": r[0], "name": r[1]} for r in rows]
        # return [{"code": r[0], "name": r[1]} for r in rows], "Success"
        return [r[0] for r in rows], "Success"

    except Exception as e:
        print("Item type query failed: ", e)
        return [], "Item type query failed"
