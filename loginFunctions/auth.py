import os
import json
import datetime

from functools import wraps
from flask import request, g

from loginFunctions.simple_crypto import verify_signature

SECRET_KEY = os.getenv("SECRET_KEY", "default_unsafe_key_change_this_in_production")

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        # 從 JSON 請求主體中取得 token 資訊
        req_data = request.get_json(silent=True)
        if not req_data:
            return {"success": False, "message": "請傳入 JSON 格式資料"}, 400
        
        token_info = req_data.get("token")

        if not token_info or not isinstance(token_info, dict) or not token_info.get("payload") or not token_info.get("signature"):
            return {"success": False, "message": "Token 缺失或格式不正確"}, 401

        payload = token_info.get("payload")
        signature = token_info.get("signature")

        # 1. 驗證簽名
        #    使用 sorted_keys=True 確保與產生簽名時的字串一致
        payload_str = json.dumps(payload, sort_keys=True)
        if not verify_signature(payload_str, signature):
            return {"success": False, "message": "無效的 Token"}, 401
        
        # 2. 驗證過期時間
        exp_str = payload.get("exp")
        if not exp_str:
            return {"success": False, "message": "Token 缺少過期時間"}, 401
        
        # 從 ISO 格式字串轉換回 datetime 物件
        token_exp = datetime.datetime.fromisoformat(exp_str)
        if datetime.datetime.utcnow() > token_exp:
            return {"success": False, "message": "Token 已過期"}, 401
        
        # 驗證通過，將使用者資訊存入 g 物件
        g.emp_no = payload.get("empNo")

        return f(*args, **kwargs)
    return decorated