import json, uuid
from decimal import Decimal, ROUND_HALF_UP
from flask import jsonify

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