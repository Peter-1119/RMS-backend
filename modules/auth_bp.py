from flask import Blueprint, request, g
from datetime import datetime, timedelta
import json
from loginFunctions.utils import send_response, call_external_api
from loginFunctions.simple_crypto import generate_signature
from loginFunctions.auth import token_required

bp = Blueprint("auth_bp", __name__)

@bp.post("/loginTest")
def login_test():
    req = request.get_json() or {}
    emp_no = (req.get("empNo") or "").strip()
    emp_pw = (req.get("empPw") or "").strip()
    if not emp_no or not emp_pw:
        return send_response(401, False, "帳號或密碼為必填")
    payload = {"empNo": emp_no, "exp": (datetime.utcnow() + timedelta(hours=8)).isoformat()}
    token_info = {"payload": payload, "signature": generate_signature(json.dumps(payload, sort_keys=True))}
    return send_response(200, True, "登入成功", {"empNo": 12868, "deptDesc": "KD11", "empName": "王巨成", "deptName": "項目管理課", "token": token_info})

@bp.post("/login")
def login():
    req = request.get_json() or {}
    emp_no = (req.get("empNo") or "").strip()
    emp_pw = (req.get("empPw") or "").strip()
    if not emp_no or not emp_pw:
        return send_response(401, False, "帳號或密碼為必填")

    ret = call_external_api(5, {"Emp_NO": emp_no})
    if ret.get("code") != 200:
        return send_response(ret.get("code"), False, "用戶名錯誤")

    user = ret.get("data") or {}
    if (user.get("empPW") or "").strip() != emp_pw:
        return send_response(401, False, "密碼錯誤")

    payload = {"empNo": emp_no, "exp": (datetime.utcnow() + timedelta(hours=8)).isoformat()}
    token_info = {"payload": payload, "signature": generate_signature(json.dumps(payload, sort_keys=True))}
    userInfo = {
        "empNo": emp_no, "deptDesc": user.get("deptDesc",""),
        "empName": user.get("empName",""), "deptName": user.get("deptName",""),
        "token": token_info
    }
    return send_response(200, True, "登入成功", userInfo)

@bp.post("/user-info-token")
@token_required
def user_info_token():
    return send_response(200, True, "取得使用者資訊", {"empNo": g.emp_no})

@bp.post("/user-info")
def user_info():
    req = request.get_json(silent=True) or {}
    emp_no = (req.get("empNo") or "").strip()
    if not emp_no:
        return send_response(400, False, "缺少 empNo 參數")
    return send_response(200, True, "取得使用者資訊", {"empNo": emp_no})
