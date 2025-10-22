import json, requests

from flask import jsonify

##########################################
#日期：2025.07.10
#編輯：田家融
#功能：統一輸出模式
##########################################
def send_response(status_code: int, success: bool, message: str, data=None):
    response_body = {
        "success": success,
        "message": message,
        "data": data 
    }
    return jsonify(response_body), status_code

##########################################
#日期：2025.07.10
#編輯：田家融
#功能：呼叫 API 功能
##########################################
def call_external_api(cmd: int, data: dict):
    # 把資料轉成 JSON 字串格式
    request_data = {
        "CmdCode": cmd,
        "InMessage_Json": json.dumps(data)
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    target_url = "http://10.1.5.122/gxfirstOIS/gxfirstOIS.asmx/GetOISData"

    try:
        # 使用 data= 表示用 form-urlencoded 格式傳送
        response = requests.post(target_url, data=request_data, headers=headers, timeout=5)
        response.raise_for_status()

        # ASP.NET asmx 常用這種格式：{"d": "JSON 字串"}
        response_data = response.json()
        if isinstance(response_data, dict) and 'd' in response_data:
            return json.loads(response_data['d'])  # 解析內層 JSON
        else:
            return response_data
    except requests.exceptions.RequestException as e:
        return {"error": str(e)}