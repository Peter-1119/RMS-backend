# modules/docs.py
from __future__ import annotations
import datetime, os, uuid, re, json, math
from collections import defaultdict
from datetime import timezone, timedelta
from decimal import Decimal

# Flask's send_file must be explicitly imported
from flask import Blueprint, request, jsonify, send_file, after_this_request
from db import db
from oracle_db import ora_cursor as odb
from utils import send_response, jload, jdump, dver, none_if_blank, new_token
from DocxDefinition import get_docx
from DocxDefinitionNoFramework import get_docx_without_framework
from DocxDefinition_ import get_docx_
from DocxDefinitionNoFramework_ import get_docx_without_framework_
from modules.department import get_visible_emp_ids  # 可視範圍卡控
from modules.block_tree import flatten_tree, build_tree, normalize_legacy_blocks, migrate_legacy_blocks, NEW_BLOCK_COLUMNS  # 階層樹核心

BASE_DIR = "docxTemp"
os.makedirs(BASE_DIR, exist_ok=True)

bp = Blueprint("docs", __name__)

LOCK_STATUS_SET = {"審核中", "已簽核", "作廢", "否決", "退回申請者"}
STATUS_MAP = {"審核中": 1, "正常結案": 2, "作廢": 3, "否決": 4, "退回申請者": 5}

TZ_TW = timezone(timedelta(hours=8))

def db_data_fetch(sql, fetch_one = False):
    try:
        with db() as (_, cur):
            cur.execute(sql)
            return cur.fetchall() if not fetch_one else cur.fetchone(), "Success"
    
    except Exception as e:
        return [], e

def db_update(sql):
    try:
        with db() as (conn, cur):
            cur.execute(sql)
            conn.commit()
        return "Success"

    except Exception as e:
        print(f"Error result: {e}")
        return "Failed"

def odb_data_fetch(sql):
    try:
        with odb() as cur:
            cur.execute(sql)
            return cur.fetchall(), "Success"
    
    except Exception as e:
        return [], e

# ---- Attributes ------------------------------------------------
@bp.post("/latest-instruction-version")
def get_latest_instruction_document_version():
    body = request.get_json(silent=True) or {}

    applyProject = body.get("applyProject", "")
    machines = body.get("machines", [])

    if applyProject == "":
        return send_response(500, False, "適用工程不可為空", {"message": "請填寫適用工程"})

    document_id, document_version = _process_instruction_id_ver(applyProject, machines)

    return send_response(200, False, "獲取歷史版本成功", {"success": True, "document_id": document_id, "document_version": document_version})

@bp.get("/latest-specification-version")
def get_latest_specification_document_version():
    """
    根據 style_no (去尾) 查詢 rms_document_list 中是否已有舊文件。
    若有，回傳舊的 document_id 以及 (舊版本號 + 1) 作為新版本。
    若無，回傳預設版本 1.0。
    """
    style_no = request.args.get("style_no")
    if not style_no:
        return send_response(400, False, "缺少 style_no 參數")

    # 1. 忽略最後的英文字母
    clean_style_no = re.sub(r'[A-Za-z]+$', '', style_no)

    try:
        with db(dict_cursor=True) as (conn, cur): # 請換成你實際連線 rms_document_list 的 db alias
            # 2. 查詢資料庫，並用 ORDER BY 確保抓到最大的版本號
            # query = "SELECT document_id, document_version FROM rms_document_list WHERE REGEXP_REPLACE(style_no, '[A-Za-z]+$', '') = %s ORDER BY CAST(document_version AS DECIMAL(10,1)) DESC LIMIT 1"
            # cur.execute(query, (clean_style_no,))
            query = "SELECT document_id, document_version FROM rms_document_list WHERE style_no = %s ORDER BY CAST(document_version AS DECIMAL(10,1)) DESC LIMIT 1"
            cur.execute(query, (style_no,))
            exist_row = cur.fetchone()

            if exist_row and exist_row["document_id"]:
                # 若找到歷史紀錄，沿用 document_id，並將版本 + 1
                doc_id = exist_row["document_id"]
                old_doc_ver = float(exist_row["document_version"] or 1.0)
                next_ver = old_doc_ver + 1.0
                
                return send_response(200, True, "獲取歷史版本成功", {
                    "document_id": doc_id,
                    "document_version": next_ver
                })
            else:
                # 查無紀錄，為全新文件
                return send_response(200, True, "查無舊版紀錄", {
                    "document_id": "",
                    "document_version": 1.0
                })
    except Exception as e:
        print(f"Error fetching latest version: {e}")
        return send_response(500, False, "查詢舊版紀錄失敗", {"message": str(e)})
    
@bp.post("/init")
def init_doc():
    body = request.get_json(silent=True) or {}
    doc_type = int(body.get("document_type", 0))
    token = new_token()
    with db() as (conn, cur):
        cur.execute("INSERT INTO rms_document_attributes (document_type, EIP_id, status, document_token, document_version, issue_date) VALUES (%s,%s,%s,%s,1.00,NOW())", (doc_type, None, 0, token))
    return jsonify({"success": True, "token": token})

@bp.get("/get-personnel")
def get_personnel():
    emp_id = request.args.get("emp_id")
    if emp_id == None:
        return send_response(400, True, "工號未提供", {"message": "請提供工號"})
    
    try:
        with odb() as cur:
            sql = f"""
                SELECT A.EMP_NO, A.EMPNAME, A.IN_DATE, C.EMP_NO, C.EMPNAME, B.LEV, E.EMP_NO, E.EMPNAME, D.LEV FROM IDBUSER.RMS_USERS A
                INNER JOIN IDBUSER.RMS_DEPT B ON A.DEPT_NO = B.DEPT_NO
                LEFT JOIN IDBUSER.RMS_USERS C ON B.LEADER_EMP_ID = C.EMP_NO
                LEFT JOIN IDBUSER.RMS_DEPT D ON B.GL_DEPARTMENT_CODE = D.DEPT_NO
                LEFT JOIN IDBUSER.RMS_USERS E ON D.LEADER_EMP_ID = E.EMP_NO
                WHERE A.OUT_DATE IS NULL AND A.EMP_NO = '{emp_id}'
            """
            cur.execute(sql)
            personnelInfo = cur.fetchall()[0]
    
    except Exception as e:
        print(f"error result: {e}")
        return send_response(400, True, "請求資料", {"message": "無法取得人員資料，請重新嘗試"})

    personnel = {"confirmer": personnelInfo[4], "approver": personnelInfo[7]}
    return send_response(200, True, "請求成功", {"personnel": personnel})

@bp.post("/draft/save-all")
def save_draft_all():
    """
    一次把：
      - attributes
      - 多個 step_type 的 blocks
      - 多個 step_type 的 params
      - references
    全部存起來（單一 transaction）
    body 形狀大致為：
    {
      "token": "...",
      "form": {...},                # 原本 save_attributes form
      "blockRequests": [            # 對應原本 /blocks/save
        { "step_type": 0, "blocks": [...] },
        { "step_type": 1, "blocks": [...] },
        ...
      ],
      "paramRequests": [            # 對應原本 /params/save
        { "step_type": 2, "blocks": [...] },
        { "step_type": 5, "blocks": [...] },
      ],
      "references": {               # 對應原本 /references/save
        "documents": [...],
        "forms": [...]
      }
    }
    """
    body = request.get_json(silent=True) or {}

    token = (body.get("token") or "").strip() or new_token()
    form  = body.get("form") or {}
    block_requests = body.get("blockRequests") or []
    param_requests = body.get("paramRequests") or []
    refs           = body.get("references") or {}

    # 🔒 先檢查是否已經在 EIP 產生正式狀態
    # 注意：如果是新建、第一次儲存，token 可能還查不到 document_id，is_document_locked 會回 False
    if is_document_locked(token):
        return send_response(
            409, False,
            "此文件已送出或已結案，禁止再修改草稿內容。請重新開啟新版本。",
            {"message": "EIP 狀態已更新，無法再儲存。"}
        )

    # ---------- 1) attributes：沿用你原本 save_attributes 的 mapping ----------
    f = {
        "document_type": int(form.get("documentType", 0) or 0),
        "prev_token": none_if_blank(form.get("previousDocumentToken")),
        "doc_id": none_if_blank(form.get("documentID")),
        "doc_name": none_if_blank(form.get("documentName")),
        "doc_ver": dver(form.get("documentVersion", 1.0)),
        "dept": none_if_blank(form.get("department")),
        "author_id": none_if_blank(form.get("author_id")),
        "author": none_if_blank(form.get("author")),
        "approver": none_if_blank(form.get("approver")),
        "confirmer": none_if_blank(form.get("confirmer")),
        "chg_reason": none_if_blank(form.get("reviseReason")),
        "chg_summary": none_if_blank(form.get("revisePoint")),
        "purpose": none_if_blank(form.get("documentPurpose")),
        "attr_json": jdump(form.get("attribute") or {}),
    }

    issue_time_str = None
    resp_form = None

    with db() as (conn, cur):

        # --- 1.1 upsert attributes（跟 save_attributes 幾乎一樣） ---
        cur.execute("""
          UPDATE rms_document_attributes SET document_type=%s, previous_document_token=%s, status=1, document_id=%s, document_name=%s, document_version=%s, attribute=%s, department=%s, author_id=%s, author=%s, approver=%s, confirmer=%s, change_reason=%s, change_summary=%s, purpose=%s, issue_date=NOW()
          WHERE document_token=%s
        """, (f["document_type"], f["prev_token"],
              f["doc_id"], f["doc_name"], f["doc_ver"],
              f["attr_json"], f["dept"], f["author_id"], f["author"],
              f["approver"], f["confirmer"], f["chg_reason"], f["chg_summary"], f["purpose"],
              token))

        if cur.rowcount == 0:
            cur.execute("""
              INSERT INTO rms_document_attributes (document_type, EIP_id, status, document_token, previous_document_token, document_id, document_name, document_version, attribute, department, author_id, author, approver, confirmer, issue_date, change_reason, change_summary, purpose)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s)
            """, (f["document_type"], None, 1, token, f["prev_token"],
                  f["doc_id"], f["doc_name"], f["doc_ver"], f["attr_json"], f["dept"],
                  f["author_id"], f["author"], f["approver"], f["confirmer"],
                  f["chg_reason"], f["chg_summary"], f["purpose"]))

        # 重新撈一次 row，用來回傳 issueTime & form
        cur.execute("SELECT * FROM rms_document_attributes WHERE document_token=%s", (token,))
        row = cur.fetchone()
        if row:
            # 注意：這裡沿用你原本 save_attributes 的 index 寫法
            attr = jload(row[8], {}) or {}
            issue_time_str = row[15].strftime("%Y-%m-%d %H:%M:%S") if row[15] else None
            resp_form = {
                "documentType": row[0] or 0,
                "documentID": row[5] or "",
                "documentName": row[6] or "",
                "documentVersion": float(row[7] or 1.0),
                "attribute": attr,
                "department": row[9] or "",
                "author_id": row[10] or "",
                "author": row[11] or "",
                "approver": row[12] or "",
                "confirmer": row[13] or "",
                "documentPurpose": row[19] or "",
                "reviseReason": row[16] or "",
                "revisePoint": row[17] or "",
                "previousDocumentToken": row[4] or "",
            }

        # ---------- 2) blocks：把多個 step_type 一次處理 ----------
        ins_block_sql = """
          INSERT INTO rms_block_content
          (content_id, document_token, step_type, tier_no, sub_no, content_type,
           header_text, header_json, content_text, content_json, files, metadata,
           created_at, updated_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        """

        for br in block_requests:
            step_type = br.get("step_type", None)
            if step_type is None:
                continue
            step_type = int(step_type)
            blocks = br.get("blocks") or []

            # 先清掉該 step_type 的舊資料
            cur.execute(
                "DELETE FROM rms_block_content WHERE document_token=%s AND step_type=%s",
                (token, step_type)
            )

            # 再依照你原本 /blocks/save 的邏輯 insert
            for blk in blocks:
                tier = int(blk.get("tier", 1))
                for idx, it in enumerate(blk.get("data") or [], start=1):
                    cur.execute(ins_block_sql, (
                        new_token(), token, step_type, tier, idx,
                        int(it.get("option", 0)),
                        None,
                        jdump(it.get("jsonHeader")),
                        None,
                        jdump(it.get("jsonContent")),
                        jdump(it.get("files") or []),
                        jdump({"source": "dynamic"}),
                    ))

        # ---------- 3) params：多個 step_type 一次處理 ----------
        ins_param_sql = """
          INSERT INTO rms_block_content
          (content_id, document_token, step_type, tier_no, sub_no, content_type,
           header_text, header_json, content_text, content_json, files, metadata,
           created_at, updated_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        """

        for pr in param_requests:
            step_type = int(pr.get("step_type", 2))
            blocks = pr.get("blocks") or []

            # 先清掉該 step 的舊資料
            cur.execute(
                "DELETE FROM rms_block_content WHERE document_token=%s AND step_type=%s",
                (token, step_type)
            )

            for b in blocks:
                tier = int(b.get("tier_no", 1))

                # sub 0 : parameter
                param_json = b.get("jsonParameterContent")
                param_arr  = b.get("arrayParameterData") or []
                meta = b.get("metadata") or {}

                cur.execute(ins_param_sql, (
                    new_token(), token, step_type, tier, 0, 2,
                    None, None,
                    jdump(param_arr),
                    jdump(param_json),
                    jdump([]),
                    jdump({"kind": "mcr-parameter", **meta}),
                ))

                # sub 1 : condition（只有 step_type == 2 的 MCR 才有）
                if step_type == 2:
                    cond_json = b.get("jsonConditionContent")
                    cond_arr  = b.get("arrayConditionData") or []
                    cur.execute(ins_param_sql, (
                        new_token(), token, step_type, tier, 1, 2,
                        None, None,
                        jdump(cond_arr),
                        jdump(cond_json),
                        jdump([]),
                        jdump({"kind": "mcr-condition", **meta}),
                    ))

        # ---------- 4) references ----------
        documents = refs.get("documents") or []
        forms     = refs.get("forms")     or []

        # 先刪除再新增
        cur.execute("DELETE FROM rms_references WHERE document_token=%s", (token,))
        if documents or forms:
            ins_ref_sql = "INSERT INTO rms_references(document_token, refer_type, refer_document, refer_document_name, color, created_at) VALUES (%s,%s,%s,%s,%s,NOW())"
            for d in documents:
                print(f"document: {d}")
                cur.execute(ins_ref_sql, (token, 0, (d.get("docId") or "").strip(), (d.get("docName") or "").strip(), d.get("color", "black")))
            for f_ in forms:
                print(f"form: {f_}")
                cur.execute(ins_ref_sql, (token, 1, (f_.get("formId") or "").strip(), (f_.get("formName") or "").strip(), f_.get("color", "black")))

    # transaction 結束
    return jsonify({
        "success": True,
        "token": token,
        "issueTime": issue_time_str,
        "form": resp_form,
    })

def is_document_locked(token: str) -> bool:
    """
    若此 token 對應的文件已在 EIP 有任何狀態，就鎖住。
   （避免使用者在瀏覽器沒關的情況下繼續存草稿，破壞快照一致性）
    """
    with db(dict_cursor=True) as (conn, cur):
        cur.execute("SELECT document_id, document_version FROM rms_document_attributes WHERE document_token=%s", (token,))
        row = cur.fetchone()

    if not row:
        return False  # 找不到就當沒鎖（也可以選擇 raise）

    doc_id = (row["document_id"] or "").strip()
    doc_ver = float(row["document_version"] or 1.0)

    if not doc_id:
        # 還沒產 Word → 一定沒有 EIP 紀錄
        return False
    
    # print(f"doc_id: {doc_id}, doc_ver: {doc_ver}")

    # 查 Oracle
    with odb() as cur_o:
        cur_o.execute(f"""
            SELECT EIP_STATUS, EIP_CREATEDT, EIPNO FROM IDBUSER.RMS_DCC2EIP
            WHERE RMS_DCCNO = '{doc_id}' AND EIP_STATUS = '已簽核' AND RMS_VER = '{int(doc_ver)}'
            ORDER BY EIP_CREATEDT DESC
        """)
        r = cur_o.fetchone()

    if not r:
        return False

    eip_status = (r[0] or "").strip()
    eip_created = r[1]
    eipno = (r[2] or "").strip()

    # 只要有任一指標，就當作已進 EIP 流程 → 鎖住
    if eip_status in LOCK_STATUS_SET or eip_created or eipno:
        return True

    return False

ATTR_COLUMNS = [
    "document_type", "previous_document_token", "document_id", "document_name",
    "document_version", "attribute", "department", "author_id", "author",
    "approver", "confirmer", "change_reason", "change_summary", "purpose",
]

BLOCK_COLUMNS = [
    "content_id", "document_token", "step_type", "tier_no", "sub_no",
    "content_type", "header_text", "header_json", "content_text", "content_json",
    "table_text", "table_json", "files", "metadata"
]

REF_COLUMNS = [
    "document_token", "refer_type", "refer_document", "refer_document_name", "color"
]

clean_value = lambda v: None if isinstance(v, str) and len(v) == 0 else v
def clean_payload(data: dict, json_fields=None, decimal_fields=None):
    json_fields = set(json_fields or [])
    decimal_fields = set(decimal_fields or [])

    out = {}
    for k, v in data.items():
        if k in json_fields:
            out[k] = jdump(v) if v is not None else None
        elif k in decimal_fields:
            out[k] = dver(v) if v is not None else None
        else:
            out[k] = clean_value(v)
    return out

def build_update_sql(table, columns, where_clause):
    set_clause = ", ".join(f"{col}=%({col})s" for col in columns)
    return f"UPDATE {table} SET {set_clause} {where_clause}"

# ==========================================
# 資料準備區 (因為欄位全對齊，程式碼大幅縮短)
# ==========================================

def prepare_attr_form(form: dict, token: str):
    # 注意：status 不在這裡寫死。
    # status 由 save / download API 各自用 SQL 表達式 (GREATEST / CASE) 處理，
    # 以避免把 2 (已公告) 或 3 (已下載) 的狀態硬退回 1 (草稿)。
    payload = {k: form.get(k) for k in ATTR_COLUMNS}
    payload = clean_payload(payload, json_fields={"attribute"}, decimal_fields={"document_version"})
    payload["document_token"] = token
    return payload

def prepare_block_item(item: dict, token: str):
    # 🟢 前端已經攤平並帶有 sub_no，直接讀取即可
    payload = {k: item.get(k) for k in BLOCK_COLUMNS}
    payload = clean_payload(payload, json_fields={"header_json", "content_json", "table_text", "table_json", "files", "metadata"})
    payload["content_id"] = new_token()
    payload["document_token"] = token
    return payload

def prepare_ref_item(ref: dict, token: str):
    payload = {k: ref.get(k) for k in REF_COLUMNS if k != "document_token"}
    payload = clean_payload(payload)
    payload["document_token"] = token
    payload["refer_type"] = int(payload.get("refer_type") or 0)
    payload["color"] = payload.get("color") or "black"   # color NOT NULL，缺省帶 black
    return payload

# === 階層樹 block 落地 / 還原（與 modules.block_tree 共用）=================
_BLOCK_JSON_FIELDS = ("header_json", "content_json", "table_text", "table_json", "files", "metadata")

def serialize_tree_row(row: dict) -> dict:
    """flatten_tree 產出的節點 → DB 參數（json 欄位 jdump、空字串轉 None）。"""
    out = {}
    for k in NEW_BLOCK_COLUMNS:
        v = row.get(k)
        out[k] = (jdump(v) if v is not None else None) if k in _BLOCK_JSON_FIELDS else clean_value(v)
    return out

def deserialize_block_row(r: dict) -> dict:
    """DB row（dict）→ build_tree 可吃的節點（json 欄位 jload）。"""
    return {
        "content_id": r["content_id"],
        "step_type": r["step_type"],
        "parent_id": r.get("parent_id"),
        "sort_order": r.get("sort_order"),
        "depth": r.get("depth"),
        "content_type": r.get("content_type"),
        "header_text": r.get("header_text"),
        "header_json": jload(r.get("header_json")),
        "content_text": r.get("content_text"),
        "content_json": jload(r.get("content_json")),
        "table_text": jload(r.get("table_text")),
        "table_json": jload(r.get("table_json")),
        "files": jload(r.get("files"), []),
        "metadata": jload(r.get("metadata"), {}),
    }

# ==========================================
# save API for New version
# ==========================================
def _is_document_announced(token: str) -> bool:
    """
    判斷指定 token 的文件當前 status 是否為 2 (已公告)。
    - 若 token 在 DB 中查無資料 (新文件)：回傳 False (允許儲存)。
    - 若 status = 2：回傳 True (禁止儲存)。
    """
    if not token:
        return False
    try:
        with db() as (_, cur):
            cur.execute(
                "SELECT status FROM rms_document_attributes WHERE document_token=%s",
                (token,),
            )
            row = cur.fetchone()
            return bool(row and row[0] == 2)
    except Exception as e:
        print(f"[_is_document_announced] check failed: {e}")
        # 查詢失敗時保守處理：不阻擋儲存 (避免誤鎖)
        return False


# =====================================================================
# Form attribute (tiptap 樣式 JSON) — 存到獨立表 rms_document_form_attributes
#   key 名稱 = 主表欄位名：document_name / applyProject / purpose
#   - upsert: value 非 null → INSERT ON DUPLICATE KEY UPDATE
#   - delete: value 為 null → DELETE 該 (token, field) 列
# =====================================================================
FORM_ATTRIBUTE_FIELDS = ("document_name", "applyProject", "purpose")
def _save_form_attributes(cur, token, form_attribute):
    """
    把 form_attribute dict 寫進 rms_document_form_attributes。
    僅處理已知的 FORM_ATTRIBUTE_FIELDS，避免任意 key 灌進去。
    沒帶該 key 的欄位不動 (保留原樣)；帶 null 則刪掉該列。
    """
    if not isinstance(form_attribute, dict):
        return
    for field_name in FORM_ATTRIBUTE_FIELDS:
        if field_name not in form_attribute:
            continue  # 沒帶就略過，不會誤刪
        value = form_attribute[field_name]
        if value is None:
            cur.execute(
                "DELETE FROM rms_document_form_attributes "
                "WHERE document_token=%s AND field_name=%s",
                (token, field_name),
            )
        else:
            payload = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            cur.execute(
                "INSERT INTO rms_document_form_attributes (document_token, field_name, style_json) "
                "VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE style_json = VALUES(style_json)",
                (token, field_name, payload),
            )

def _load_form_attributes(cur, token):
    """
    讀出該文件所有 form_attribute，回傳 { field_name: style_json|None }。
    已知的 3 個 key 預設給 None，前端拿到後直接用即可 (查不到 → fallback)。
    """
    out = {k: None for k in FORM_ATTRIBUTE_FIELDS}
    if not token:
        return out
    cur.execute(
        "SELECT field_name, style_json FROM rms_document_form_attributes WHERE document_token=%s",
        (token,),
    )
    for r in cur.fetchall():
        # 兼容 tuple cursor / dict cursor
        if isinstance(r, dict):
            field_name = r.get("field_name")
            style = r.get("style_json")
        else:
            field_name, style = r[0], r[1]
        # 某些 driver 對 JSON 欄位會回字串，這裡統一 parse 回 dict
        if isinstance(style, (bytes, bytearray)):
            try:
                style = style.decode("utf-8")
            except UnicodeDecodeError:
                style = None
        if isinstance(style, str):
            try:
                style = json.loads(style)
            except (ValueError, TypeError):
                pass
        if field_name in out:
            out[field_name] = style
    return out

def _resolve_form_attribute(data, token):
    """Word 產生用：優先取前端送的 form_attribute（含尚未存的當前樣式）；沒有 → 依 token 從 DB 讀。"""
    fa = data.get("form_attribute") if isinstance(data, dict) else None
    if isinstance(fa, dict):
        return fa
    if token:
        try:
            with db() as (conn, cur):
                return _load_form_attributes(cur, token)
        except Exception as e:
            print(f"[_resolve_form_attribute] load failed: {e}")
    return {}

@bp.post("/draft/save-instruction")
def save_instruction():
    body = request.get_json(silent=True) or {}

    token = clean_value(body.get("token")) or new_token()
    attribute_list = body.get("attribute") or []
    content_list = body.get("content") or []          # 舊平鋪格式（保留相容）
    content_tree = body.get("tree") or []             # 新巢狀樹格式（§7.1）
    reference_list = body.get("reference") or []
    form = attribute_list[0] if attribute_list else {}
    form_attribute = body.get("form_attribute")  # ← 新增；可能為 None / dict

    # ★ 前置檢查：已公告 (status=2) 的文件禁止儲存，使用者需自行從前端走變版流程
    if _is_document_announced(token):
        return send_response(409, False, "文件已公告，禁止儲存。請使用變版流程建立新版本。", {})

    # if is_document_locked(token):
    #     return send_response(409, False, "此文件已送出或已結案，禁止再修改草稿內容。請重新開啟新版本。", {"message": "EIP 狀態已更新，無法再儲存。"})

    issue_time_str = None
    resp_form = None

    with db() as (conn, cur):
        # 1. ★ 移除原本的 INSERT，純粹使用 UPDATE 更新主表 (不含 status)
        attr_payload = prepare_attr_form(form, token)
        update_sql = build_update_sql("rms_document_attributes", ATTR_COLUMNS, "WHERE document_token=%(document_token)s")
        cur.execute(update_sql, attr_payload)

        # 1.1 ★ status 提升：0→1 (草稿)，1/2/3 維持原值
        # 用 GREATEST 確保已公告 (2) 與已下載 (3) 不會被退回成草稿
        cur.execute(
            "UPDATE rms_document_attributes SET status = GREATEST(COALESCE(status, 0), 1), issue_date = NOW() WHERE document_token=%s",
            (token,)
        )

        # 2. 取得更新後的主表資訊 (維持你原本的邏輯)
        cur.execute("SELECT * FROM rms_document_attributes WHERE document_token=%s", (token,))
        row = cur.fetchone()
        if row:
            issue_time_str = row[15].strftime("%Y-%m-%d %H:%M:%S") if row[15] else None
            resp_form = {
                "document_type": row[0] or 0, "document_id": row[5] or "", "document_name": row[6] or "",
                "document_version": float(row[7] or 1.0), "attribute": jload(row[8], {}) or {},
                "department": row[9] or "", "author_id": row[10] or "", "author": row[11] or "",
                "approver": row[12] or "", "confirmer": row[13] or "", "change_reason": row[16] or "",
                "change_summary": row[17] or "", "purpose": row[19] or "", "previous_document_token": row[4] or "",
            }

        # 3. ★ Block 處理：巢狀樹 → DFS 攤平（block_tree.flatten_tree），對齊新 schema
        cur.execute("DELETE FROM rms_block_content WHERE document_token=%s", (token,))
        block_rows, node_id_map = flatten_tree(content_tree, token)
        if block_rows:
            insert_block_sql = f"""INSERT INTO rms_block_content ({", ".join(NEW_BLOCK_COLUMNS)}, created_at, updated_at) VALUES ({", ".join(f"%({c})s" for c in NEW_BLOCK_COLUMNS)}, NOW(), NOW())"""
            cur.executemany(insert_block_sql, [serialize_tree_row(r) for r in block_rows])

        # 4. ★ Ref 處理：同上
        cur.execute("DELETE FROM rms_references WHERE document_token=%s", (token,))
        if reference_list:
            insert_ref_sql = f"""INSERT INTO rms_references ({", ".join(REF_COLUMNS)}, created_at) VALUES ({", ".join(f"%({c})s" for c in REF_COLUMNS)}, NOW())"""
            ref_data = [prepare_ref_item(ref, token) for ref in reference_list]
            cur.executemany(insert_ref_sql, ref_data)

        # 5. ★ Form attribute (tiptap 樣式 JSON) → rms_document_form_attributes
        # 沒帶 form_attribute → 完全不動 (向下相容舊前端)
        if form_attribute is not None:
            _save_form_attributes(cur, token, form_attribute)

    return jsonify({"success": True, "token": token, "issueTime": issue_time_str, "form": resp_form, "nodeIdMap": node_id_map })

@bp.get("/draft/load-instruction")
def load_instruction():
    print("Receive load instruction API")
    token = request.args.get("token", "").strip()
    emp_id = request.args.get("emp_id", "").strip()

    # 準備裝載所有資料的超級大禮包
    payload = {
        "projects": [],
        "personnel": {"confirmer": "", "approver": ""},
        "form": None,
        "tree": [],
        "references": None,
        # form_attribute: 主表欄位的 tiptap 樣式 JSON；查不到 → null，前端 fallback 純文字
        "form_attribute": {k: None for k in FORM_ATTRIBUTE_FIELDS},
    }

    # ==========================================
    # 1. 取得 Personnel 簽核人員資訊 (Oracle)
    # ==========================================
    if emp_id:
        try:
            with odb() as cur:
                sql = f"""
                    SELECT A.EMP_NO, A.EMPNAME, A.IN_DATE, C.EMP_NO, C.EMPNAME, B.LEV, E.EMP_NO, E.EMPNAME, D.LEV FROM IDBUSER.RMS_USERS A
                    INNER JOIN IDBUSER.RMS_DEPT B ON A.DEPT_NO = B.DEPT_NO
                    LEFT JOIN IDBUSER.RMS_USERS C ON B.LEADER_EMP_ID = C.EMP_NO
                    LEFT JOIN IDBUSER.RMS_DEPT D ON B.GL_DEPARTMENT_CODE = D.DEPT_NO
                    LEFT JOIN IDBUSER.RMS_USERS E ON D.LEADER_EMP_ID = E.EMP_NO
                    WHERE A.OUT_DATE IS NULL AND A.EMP_NO = '{emp_id}'
                """
                cur.execute(sql)
                p_rows = cur.fetchall()
                if p_rows:
                    payload["personnel"]["confirmer"] = p_rows[0][4] or ""
                    payload["personnel"]["approver"] = p_rows[0][7] or ""
        except Exception as e:
            print(f"Oracle Personnel Error: {e}")

    # ==========================================
    # 2. 取得 適用工程 & 草稿資料 (MySQL)
    # ==========================================
    try:
        with db() as (conn, cur):
            # (A) 取得 Projects 適用工程
            cur.execute("SELECT DISTINCT project FROM rms_spec_flat ORDER BY project")
            payload["projects"] = [{"id": p[0], "projectCode": p[0], "projectName": p[0]} for p in cur.fetchall()]

            # (B) 取得草稿資料 (如果有 token)
            if token:
                # --- 載入主表屬性 (Form) ---
                cur.execute("SELECT * FROM rms_document_attributes WHERE document_token=%s", (token,))
                row = cur.fetchone()
                if row:
                    cols = [desc[0] for desc in cur.description]
                    r_dict = dict(zip(cols, row))
                    
                    payload["form"] = {
                        "document_type": r_dict.get("document_type") or 0,
                        "document_id": r_dict.get("document_id") or "",
                        "document_name": r_dict.get("document_name") or "",
                        "document_version": float(r_dict.get("document_version") or 1.0),
                        "attribute": jload(r_dict.get("attribute"), {}) or {},
                        "department": r_dict.get("department") or "",
                        "author_id": r_dict.get("author_id") or "",
                        "author": r_dict.get("author") or "",
                        # ★ 核心防呆：如果草稿沒有簽核人，就自動帶入剛剛從 Oracle 查到的人事資料！
                        "approver": r_dict.get("approver") or payload["personnel"]["approver"],
                        "confirmer": r_dict.get("confirmer") or payload["personnel"]["confirmer"],
                        "change_reason": r_dict.get("change_reason") or "",
                        "change_summary": r_dict.get("change_summary") or "",
                        "purpose": r_dict.get("purpose") or "",
                        "previous_document_token": r_dict.get("previous_document_token") or "",
                    }

                    # --- 載入 Block 區塊內容（組回巢狀樹，§7.3） ---
                    cur.execute(
                        "SELECT content_id, step_type, parent_id, sort_order, depth, content_type, "
                        "header_text, header_json, content_text, content_json, table_text, table_json, files, metadata "
                        "FROM rms_block_content WHERE document_token=%s ORDER BY step_type, sort_order",
                        (token,),
                    )
                    b_cols = [desc[0] for desc in cur.description]
                    block_rows = [deserialize_block_row(dict(zip(b_cols, b_row))) for b_row in cur.fetchall()]
                    payload["tree"] = build_tree(block_rows)
                    for blockk in payload["tree"]:
                        print(blockk)

                    # --- 載入 Reference 參考文件/表單 ---
                    cur.execute("SELECT refer_type, refer_document, refer_document_name FROM rms_references WHERE document_token=%s", (token,))
                    refs_out = {"document": [], "form": []}
                    
                    for r_type, r_doc, r_name in cur.fetchall():
                        ref_obj = {"refer_document": r_doc, "refer_document_name": r_name}
                        if r_type == 0:
                            refs_out["document"].append(ref_obj)
                        elif r_type == 1:
                            refs_out["form"].append(ref_obj)

                    payload["references"] = refs_out

                    # --- 載入 Form Attribute (tiptap 樣式 JSON) ---
                    payload["form_attribute"] = _load_form_attributes(cur, token)

    except Exception as e:
        print(f"MySQL Load Error: {e}")
        return send_response(500, False, "資料載入失敗", {"message": str(e)})

    # 一次回傳所有資料！
    return send_response(200, True, "請求成功", payload)

@bp.post("/draft/save-specification")
def save_specification():
    body = request.get_json(silent=True) or {}

    token = clean_value(body.get("token")) or new_token()
    attribute_list = body.get("attribute") or []
    content_list = body.get("content") or []          # 舊平鋪格式（保留相容）
    content_tree = body.get("tree") or []             # 新巢狀樹格式（§7.1）
    reference_list = body.get("reference") or []
    form = attribute_list[0] if attribute_list else {}
    form_attribute = body.get("form_attribute")  # tiptap 樣式 JSON（讓「目的」可變色）；可能 None / dict

    # ★ 前置檢查：已公告 (status=2) 的文件禁止儲存，使用者需自行從前端走變版流程
    if _is_document_announced(token):
        return send_response(409, False, "文件已公告，禁止儲存。請使用變版流程建立新版本。", {})

    # if is_document_locked(token):
    #     return send_response(409, False, "此文件已送出或已結案，禁止再修改草稿內容。請重新開啟新版本。", {"message": "EIP 狀態已更新，無法再儲存。"})

    issue_time_str = None
    resp_form = None

    with db() as (conn, cur):
        # 1. 更新主表屬性 (不含 status)
        attr_payload = prepare_attr_form(form, token)
        update_sql = build_update_sql("rms_document_attributes", ATTR_COLUMNS, "WHERE document_token=%(document_token)s")
        cur.execute(update_sql, attr_payload)

        # 1.1 ★ status 提升：0→1 (草稿)，1/2/3 維持原值
        # 用 GREATEST 確保已公告 (2) 與已下載 (3) 不會被退回成草稿
        cur.execute(
            "UPDATE rms_document_attributes SET status = GREATEST(COALESCE(status, 0), 1), issue_date = NOW() WHERE document_token=%s",
            (token,)
        )

        # 2. 取得更新後的主表資訊 (回傳給前端)
        cur.execute("SELECT * FROM rms_document_attributes WHERE document_token=%s", (token,))
        row = cur.fetchone()
        if row:
            issue_time_str = row[15].strftime("%Y-%m-%d %H:%M:%S") if row[15] else None
            resp_form = {
                "document_type": row[0] or 1, "document_id": row[5] or "", "document_name": row[6] or "",
                "document_version": float(row[7] or 1.0), "attribute": jload(row[8], {}) or {},
                "department": row[9] or "", "author_id": row[10] or "", "author": row[11] or "",
                "approver": row[12] or "", "confirmer": row[13] or "", "change_reason": row[16] or "",
                "change_summary": row[17] or "", "purpose": row[19] or "", "previous_document_token": row[4] or "",
            }

        # 3. Block 處理：巢狀樹 → DFS 攤平（block_tree.flatten_tree），對齊新 schema
        cur.execute("DELETE FROM rms_block_content WHERE document_token=%s", (token,))
        block_rows, node_id_map = flatten_tree(content_tree, token)
        if block_rows:
            insert_block_sql = f"""INSERT INTO rms_block_content ({", ".join(NEW_BLOCK_COLUMNS)}, created_at, updated_at) VALUES ({", ".join(f"%({c})s" for c in NEW_BLOCK_COLUMNS)}, NOW(), NOW())"""
            cur.executemany(insert_block_sql, [serialize_tree_row(r) for r in block_rows])

        # 4. Reference 處理：刪除舊的，批量新增新的
        cur.execute("DELETE FROM rms_references WHERE document_token=%s", (token,))
        if reference_list:
            insert_ref_sql = f"""INSERT INTO rms_references ({", ".join(REF_COLUMNS)}, created_at) VALUES ({", ".join(f"%({c})s" for c in REF_COLUMNS)}, NOW())"""
            ref_data = [prepare_ref_item(ref, token) for ref in reference_list]
            cur.executemany(insert_ref_sql, ref_data)

        # 5. Form attribute (tiptap 樣式 JSON) → rms_document_form_attributes（讓「目的」可變色）
        if form_attribute is not None:
            _save_form_attributes(cur, token, form_attribute)

    return jsonify({"success": True, "token": token, "issueTime": issue_time_str, "form": resp_form, "nodeIdMap": node_id_map })

@bp.get("/draft/load-specification")
def load_specification():
    print("Receive load specification API")
    token = request.args.get("token", "").strip()
    emp_id = request.args.get("emp_id", "").strip()

    # 準備裝載資料的 payload (不需要 projects 列表，所以拿掉以加速查詢)
    payload = {"personnel": {"confirmer": "", "approver": ""}, "form": None, "tree": [], "references": None, "form_attribute": {k: None for k in FORM_ATTRIBUTE_FIELDS}}

    # ==========================================
    # 1. 取得 Personnel 簽核人員資訊 (Oracle)
    # ==========================================
    if emp_id:
        try:
            with odb() as cur:
                sql = f"""
                    SELECT A.EMP_NO, A.EMPNAME, A.IN_DATE, C.EMP_NO, C.EMPNAME, B.LEV, E.EMP_NO, E.EMPNAME, D.LEV FROM IDBUSER.RMS_USERS A
                    INNER JOIN IDBUSER.RMS_DEPT B ON A.DEPT_NO = B.DEPT_NO
                    LEFT JOIN IDBUSER.RMS_USERS C ON B.LEADER_EMP_ID = C.EMP_NO
                    LEFT JOIN IDBUSER.RMS_DEPT D ON B.GL_DEPARTMENT_CODE = D.DEPT_NO
                    LEFT JOIN IDBUSER.RMS_USERS E ON D.LEADER_EMP_ID = E.EMP_NO
                    WHERE A.OUT_DATE IS NULL AND A.EMP_NO = '{emp_id}'
                """
                cur.execute(sql)
                p_rows = cur.fetchall()
                if p_rows:
                    payload["personnel"]["confirmer"] = p_rows[0][4] or ""
                    payload["personnel"]["approver"] = p_rows[0][7] or ""
        except Exception as e:
            print(f"Oracle Personnel Error: {e}")

    # ==========================================
    # 2. 取得 草稿資料 (MySQL)
    # ==========================================
    try:
        with db() as (conn, cur):
            if token:
                # --- 載入主表屬性 (Form) ---
                cur.execute("SELECT * FROM rms_document_attributes WHERE document_token=%s", (token,))
                row = cur.fetchone()
                if row:
                    cols = [desc[0] for desc in cur.description]
                    r_dict = dict(zip(cols, row))
                    
                    payload["form"] = {
                        "document_type": r_dict.get("document_type") or 1,
                        "document_id": r_dict.get("document_id") or "",
                        "document_name": r_dict.get("document_name") or "",
                        "document_version": float(r_dict.get("document_version") or 1.0),
                        "attribute": jload(r_dict.get("attribute"), {}) or {},
                        "department": r_dict.get("department") or "",
                        "author_id": r_dict.get("author_id") or "",
                        "author": r_dict.get("author") or "",
                        # ★ 核心防呆：自動帶入人事資料
                        "approver": r_dict.get("approver") or payload["personnel"]["approver"],
                        "confirmer": r_dict.get("confirmer") or payload["personnel"]["confirmer"],
                        "change_reason": r_dict.get("change_reason") or "",
                        "change_summary": r_dict.get("change_summary") or "",
                        "purpose": r_dict.get("purpose") or "",
                        "previous_document_token": r_dict.get("previous_document_token") or "",
                    }

                    # --- 載入 Block 區塊內容（組回巢狀樹，§7.3） ---
                    cur.execute(
                        "SELECT content_id, step_type, parent_id, sort_order, depth, content_type, "
                        "header_text, header_json, content_text, content_json, table_text, table_json, files, metadata "
                        "FROM rms_block_content WHERE document_token=%s ORDER BY step_type, sort_order",
                        (token,),
                    )
                    b_cols = [desc[0] for desc in cur.description]
                    block_rows = [deserialize_block_row(dict(zip(b_cols, b_row))) for b_row in cur.fetchall()]
                    payload["tree"] = build_tree(block_rows)

                    # --- 載入 Reference 參考表單 ---
                    cur.execute("SELECT refer_type, refer_document, refer_document_name, color FROM rms_references WHERE document_token=%s", (token,))
                    refs_out = {"document": [], "form": []}
                    
                    for r_type, r_doc, r_name, color in cur.fetchall():
                        ref_obj = {"refer_document": r_doc, "refer_document_name": r_name, "color": color}
                        if r_type == 0:
                            refs_out["document"].append(ref_obj)
                        elif r_type == 1:
                            refs_out["form"].append(ref_obj)
                            
                    payload["references"] = refs_out

                    # --- 載入 Form Attribute (tiptap 樣式 JSON，讓「目的」可變色) ---
                    payload["form_attribute"] = _load_form_attributes(cur, token)

    except Exception as e:
        print(f"MySQL Load Error: {e}")
        return send_response(500, False, "資料載入失敗", {"message": str(e)})

    return send_response(200, True, "請求成功", payload)

@bp.get("/<token>/draft-all")
def load_draft_all(token):
    """
    Query string:
      - attrs=0/1 (預設 1)
      - blocks=0,1,3,4,...
      - params=2,5,...
      - refs=0/1 (預設 1)
    回傳：
    {
      "success": true,
      "token": "...",
      "attributes": { success, status, issueTime, form },
      "blocks": {
        "0": { success, blocks:[...] },
        "1": { success, blocks:[...] },
        ...
      },
      "params": {
        "2": { success, blocks:[...] },
        "5": { success, blocks:[...] },
        ...
      },
      "references": { success, documents, forms }
    }
    """
    include_attrs = (request.args.get("attrs", "1") != "0")
    block_str = (request.args.get("blocks") or "").strip()
    param_str = (request.args.get("params") or "").strip()
    include_refs = (request.args.get("refs", "1") != "0")

    block_steps = []
    if block_str:
        for p in block_str.split(","):
            p = p.strip()
            if p:
                try:
                    block_steps.append(int(p))
                except ValueError:
                    pass

    param_steps = []
    if param_str:
        for p in param_str.split(","):
            p = p.strip()
            if p:
                try:
                    param_steps.append(int(p))
                except ValueError:
                    pass

    out = {
        "success": True,
        "token": token,
        "attributes": None,
        "blocks": {},
        "params": {},
        "references": None,
    }

    with db(dict_cursor=True) as (conn, cur):

        # ---------- 1) attributes ----------
        if include_attrs:
            cur.execute("SELECT * FROM rms_document_attributes WHERE document_token=%s", (token,))
            r = cur.fetchone()
            print(f"document_id: {r['document_id'] or ''}")
            if not r:
                out["attributes"] = {"success": False, "message": "Not found"}
            else:
                attr = jload(r.get("attribute"), {}) or {}
                issue = r["issue_date"].strftime("%Y-%m-%d %H:%M:%S") if r["issue_date"] else None
                out["attributes"] = {
                    "success": True,
                    "token": r["document_token"],
                    "status": r["status"],
                    "issueTime": issue,
                    "form": {
                        "documentType": r["document_type"],
                        "documentID": r["document_id"] or "",
                        "documentName": r["document_name"] or "",
                        "documentVersion": float(r["document_version"] or 1.0),
                        "attribute": attr,
                        "department": r["department"] or "",
                        "author_id": r["author_id"] or "",
                        "author": r["author"] or "",
                        "approver": r["approver"] or "",
                        "confirmer": r["confirmer"] or "",
                        "documentPurpose": r["purpose"] or "",
                        "reviseReason": r["change_reason"] or "",
                        "revisePoint": r["change_summary"] or "",
                        "previousDocumentToken": r["previous_document_token"] or "",
                    },
                }

        # ---------- 2) blocks ----------
        for st in block_steps:
            cur.execute("""
              SELECT tier_no, sub_no, content_type, header_json, content_json, files FROM rms_block_content
              WHERE document_token=%s AND step_type=%s
              ORDER BY tier_no ASC, sub_no ASC
            """, (token, st))
            rows = cur.fetchall() or []

            grouped = {}
            for r in rows:
                t = int(r["tier_no"])
                grouped.setdefault(t, []).append({
                    "option": int(r["content_type"]),
                    "jsonHeader": jload(r["header_json"]),
                    "jsonContent": jload(r["content_json"]),
                    "files": jload(r["files"], []) or [],
                })

            data = [{"id": f"{st}-{t}", "step": st, "tier": t, "data": grouped[t]} for t in sorted(grouped)]
            out["blocks"][str(st)] = {"success": True, "blocks": data}

        # ---------- 3) params ----------
        for st in param_steps:
            cur.execute("""
              SELECT tier_no, sub_no, header_text, content_text, content_json, metadata FROM rms_block_content
              WHERE document_token=%s AND step_type=%s
              ORDER BY tier_no ASC, sub_no ASC
            """, (token, st))
            rows = cur.fetchall() or []

            merged = {}
            for r in rows:
                t = int(r["tier_no"])
                sub = int(r["sub_no"])
                merged.setdefault(t, {
                    "code": f"XXXX{t}",
                    "jsonParameterContent": None,
                    "arrayParameterData": [],
                    "jsonConditionContent": None,
                    "arrayConditionData": [],
                    "metadata": None,
                })
                if sub == 0:
                    merged[t]["code"] = r["header_text"] or merged[t]["code"]
                    merged[t]["arrayParameterData"] = jload(r["content_text"], []) or []
                    merged[t]["jsonParameterContent"] = jload(r["content_json"])
                    merged[t]["metadata"] = jload(r["metadata"])
                elif sub == 1:
                    merged[t]["arrayConditionData"] = jload(r["content_text"], []) or []
                    merged[t]["jsonConditionContent"] = jload(r["content_json"])

            blocks = []
            for i, t in enumerate(sorted(merged.keys()), start=1):
                b = merged[t]
                blocks.append({
                    "id": f"p-{t}",
                    "code": b["code"] or f"XXXX{t}",
                    "jsonParameterContent": b["jsonParameterContent"],
                    "arrayParameterData": b["arrayParameterData"],
                    "jsonConditionContent": b["jsonConditionContent"],
                    "arrayConditionData": b["arrayConditionData"],
                    "metadata": b["metadata"],
                })

            out["params"][str(st)] = {"success": True, "blocks": blocks}

        # ---------- 4) references ----------
        if include_refs:
            cur.execute("SELECT refer_type, refer_document, refer_document_name, color FROM rms_references WHERE document_token=%s ORDER BY refer_type ASC, id ASC", (token,))
            rows = cur.fetchall() or []

            docs, forms = [], []
            for r in rows:
                if int(r["refer_type"]) == 0:
                    docs.append({"docId": r["refer_document"],"docName": r["refer_document_name"], "color": r["color"]})
                else:
                    forms.append({"formId": r["refer_document"],"formName": r["refer_document_name"], "color": r["color"]})
            out["references"] = {
                "success": True,
                "documents": docs,
                "forms": forms,
            }

    return jsonify(out)

@bp.get("/<token>/snapshot-draft-all")
def load_snapshot_draft_all(token):
    """
    從 rms_document_snapshots 讀快照資料。
    支援 Query string:
      - attrs=0/1
      - blocks=0,1,3,...
      - params=2,5,...
      - refs=0/1
      - rms_id=xxx   ★ 新增，用來鎖定某一張 RMS 單對應的 snapshot
    """
    include_attrs = (request.args.get("attrs", "1") != "0")
    block_str = (request.args.get("blocks") or "").strip()
    param_str = (request.args.get("params") or "").strip()
    include_refs = (request.args.get("refs", "1") != "0")
    rms_id = (request.args.get("rms_id") or "").strip()

    block_steps = []
    if block_str:
        for p in block_str.split(","):
            p = p.strip()
            if not p:
                continue
            try:
                block_steps.append(int(p))
            except ValueError:
                pass

    param_steps = []
    if param_str:
        for p in param_str.split(","):
            p = p.strip()
            if not p:
                continue
            try:
                param_steps.append(int(p))
            except ValueError:
                pass

    # ---------- 先抓 snapshot row ----------
    with db(dict_cursor=True) as (conn, cur):
        where = ["rds.document_token = %s"]
        params = [token]

        if rms_id:
            # 如果有帶 rms_id，就鎖定在這張 RMS 單的 snapshot
            where.append("rds.rms_id = %s")
            params.append(rms_id)

        where_sql = " AND ".join(where)

        cur.execute(f"""
            SELECT rdsp.document_row, rdsp.blocks_rows, rdsp.references_rows FROM rms_document_snapshot_payloads AS rdsp
            JOIN rms_document_snapshots AS rds ON rds.snapshot_id = rdsp.snapshot_id
            WHERE {where_sql}
            ORDER BY created_at DESC
            LIMIT 1
        """, params)
        snap = cur.fetchone()

    if not snap:
        return jsonify({
            "success": False,
            "message": "snapshot not found for this token / rms_id"
        }), 404

    # 下面照你原本的邏輯就好
    doc_row   = jload(snap["document_row"], {}) or {}
    blocks_rs = jload(snap["blocks_rows"], []) or []
    refs_rs   = jload(snap["references_rows"], []) or []

    out = {
        "success": True,
        "token": token,
        "attributes": None,
        "blocks": {},
        "params": {},
        "references": None,
    }

    # ---------- 1) attributes ----------
    if include_attrs:
        issue = doc_row.get("issue_date")
        if hasattr(issue, "strftime"):
            issue_str = issue.strftime("%Y-%m-%d %H:%M:%S")
        else:
            issue_str = issue

        attr_json = jload(doc_row.get("attribute"), {}) or {}

        out["attributes"] = {
            "success": True,
            "token": doc_row.get("document_token") or token,
            "status": doc_row.get("status"),
            "issueTime": issue_str,
            "form": {
                "documentType": doc_row.get("document_type") or 0,
                "documentID": doc_row.get("document_id") or "",
                "documentName": doc_row.get("document_name") or "",
                "documentVersion": float(doc_row.get("document_version") or 1.0),
                "attribute": attr_json,
                "department": doc_row.get("department") or "",
                "author_id": doc_row.get("author_id") or "",
                "author": doc_row.get("author") or "",
                "approver": doc_row.get("approver") or "",
                "confirmer": doc_row.get("confirmer") or "",
                "documentPurpose": doc_row.get("purpose") or "",
                "reviseReason": doc_row.get("change_reason") or "",
                "revisePoint": doc_row.get("change_summary") or "",
                "previousDocumentToken": doc_row.get("previous_document_token") or "",
            },
        }

    # ---------- 2) blocks ----------
    by_step = {}
    for r in blocks_rs:
        try:
            st = int(r.get("step_type"))
        except (TypeError, ValueError):
            continue
        by_step.setdefault(st, []).append(r)

    for st in block_steps:
        rows = by_step.get(st, [])
        grouped = {}
        for r in rows:
            t = int(r.get("tier_no"))
            grouped.setdefault(t, []).append({
                "option": int(r.get("content_type") or 0),
                "jsonHeader": _normalize_metadata(r.get("header_json")),
                "jsonContent": _normalize_metadata(r.get("content_json")),
                "files": _normalize_metadata(r.get("files")) or [],
            })

        data = [{
            "id": f"{st}-{t}",
            "step": st,
            "tier": t,
            "data": grouped[t]
        } for t in sorted(grouped.keys())]

        out["blocks"][str(st)] = {"success": True, "blocks": data}

    # ---------- 3) params ----------
    for st in param_steps:
        rows = [r for r in blocks_rs if int(r.get("step_type")) == st]
        merged = {}
        for r in rows:
            t = int(r.get("tier_no"))
            sub = int(r.get("sub_no"))
            merged.setdefault(t, {
                "code": f"XXXX{t}",
                "jsonParameterContent": None,
                "arrayParameterData": [],
                "jsonConditionContent": None,
                "arrayConditionData": [],
                "metadata": None,
            })
            if sub == 0:
                merged[t]["code"] = r.get("header_text") or merged[t]["code"]
                merged[t]["arrayParameterData"] = jload(r.get("content_text"), []) or []
                merged[t]["jsonParameterContent"] = _normalize_metadata(r.get("content_json"))
                merged[t]["metadata"] = _normalize_metadata(r.get("metadata"))
            elif sub == 1:
                merged[t]["arrayConditionData"] = jload(r.get("content_text"), []) or []
                merged[t]["jsonConditionContent"] = _normalize_metadata(r.get("content_json"))

        blocks = []
        for t in sorted(merged.keys()):
            b = merged[t]
            blocks.append({
                "id": f"p-{t}",
                "code": b["code"] or f"XXXX{t}",
                "jsonParameterContent": b["jsonParameterContent"],
                "arrayParameterData": b["arrayParameterData"],
                "jsonConditionContent": b["jsonConditionContent"],
                "arrayConditionData": b["arrayConditionData"],
                "metadata": b["metadata"],
            })

        out["params"][str(st)] = {"success": True, "blocks": blocks}

    # ---------- 4) references ----------
    if include_refs:
        docs, forms = [], []
        for r in refs_rs:
            if int(r.get("refer_type") or 0) == 0:
                docs.append({
                    "docId": r.get("refer_document"),
                    "docName": r.get("refer_document_name"),
                })
            else:
                forms.append({
                    "formId": r.get("refer_document"),
                    "formName": r.get("refer_document_name"),
                })
        out["references"] = {
            "success": True,
            "documents": docs,
            "forms": forms,
        }

    return jsonify(out)

@bp.post("/revise")
def create_revision():
    """
    建立新一版：
      - 由前一版 previous_token 複製一份
      - document_version + 1.00
      - status = 0 (新的草稿)
      - previous_document_token 指向舊 token
      - document_id 直接沿用舊版（可能是 NULL，表示初版尚未產生文件）
      - 🔥 同時複製 blocks / references 到新 token
    """
    body = request.get_json(silent=True) or {}
    prev_token = (body.get("previous_token") or "").strip()
    if not prev_token:
        return send_response(400, False, "previous_token is required")

    with db(dict_cursor=True) as (conn, cur):
        cur.execute("SELECT * FROM rms_document_attributes WHERE document_token=%s", (prev_token,))
        r = cur.fetchone()
        if not r:
            return send_response(404, False, "previous document not found")

        new_token_ = new_token()
        old_ver = float(r["document_version"] or 1.0)
        new_ver = dver(old_ver + 1.0)

        doc_id = r["document_id"]  # 🔸 變版沿用同一個 document_ID（可能是 NULL）
        # 1) 新增 attributes
        cur.execute("""
          INSERT INTO rms_document_attributes
          (document_type, EIP_id, status, document_token, previous_document_token,
           document_id, document_name, document_version, attribute, department,
           author_id, author, approver, confirmer, issue_date,
           change_reason, change_summary, reject_reason, purpose)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s)
        """, (
            r["document_type"], None, 0, new_token_, prev_token,
            doc_id, r["document_name"], new_ver,
            r["attribute"], r["department"], r["author_id"], r["author"],
            r["approver"], r["confirmer"],
            "", "", None, r["purpose"],
        ))

        # 2) 複製 blocks：用 block_tree 重建樹再攤平 → 重配 content_id 並 remap parent_id（新階層 schema）
        cur.execute(
            "SELECT content_id, step_type, parent_id, sort_order, depth, content_type, "
            "header_text, header_json, content_text, content_json, table_text, table_json, files, metadata "
            "FROM rms_block_content WHERE document_token = %s ORDER BY step_type, sort_order",
            (prev_token,),
        )
        old_blocks = [deserialize_block_row(b) for b in (cur.fetchall() or [])]
        new_block_rows, _ = flatten_tree(build_tree(old_blocks), new_token_)
        if new_block_rows:
            ins_blk_sql = f"""INSERT INTO rms_block_content ({", ".join(NEW_BLOCK_COLUMNS)}, created_at, updated_at) VALUES ({", ".join(f"%({c})s" for c in NEW_BLOCK_COLUMNS)}, NOW(), NOW())"""
            cur.executemany(ins_blk_sql, [serialize_tree_row(r) for r in new_block_rows])

        # 3) 複製 references
        cur.execute("""
          SELECT refer_type, refer_document, refer_document_name
          FROM rms_references
          WHERE document_token = %s
        """, (prev_token,))
        old_refs = cur.fetchall() or []

        # 變版：reference 一律重置為黑色（上一版內容視為未變更，使用者改了才轉藍）
        ins_ref_sql = """
          INSERT INTO rms_references
          (document_token, refer_type, refer_document, refer_document_name, color, created_at)
          VALUES (%s,%s,%s,%s,'black',NOW())
        """
        for r_ref in old_refs:
            cur.execute(ins_ref_sql, (
                new_token_,
                r_ref["refer_type"],
                r_ref["refer_document"],
                r_ref["refer_document_name"],
            ))

        conn.commit()

    return jsonify({
        "success": True,
        "token": new_token_,
        "form": {
            "documentType": r["document_type"],
            "documentID": doc_id or "",
            "documentName": r["document_name"] or "",
            "documentVersion": float(new_ver),
            "attribute": jload(r["attribute"], {}) or {},
            "department": r["department"] or "",
            "author_id": r["author_id"] or "",
            "author": r["author"] or "",
            "approver": r["approver"] or "",
            "confirmer": r["confirmer"] or "",
            "documentPurpose": r["purpose"] or "",
            "reviseReason": "",
            "revisePoint": "",
            "previousDocumentToken": prev_token,
        }
    })

# ----- EIP Process ----- #
# ----------------------------------------------------
# 輔助函數：處理 Oracle 端 RMS_DCC2EIP 的 RMS_ID 欄位
# ----------------------------------------------------
def _normalize_metadata(raw):
    """
    確保 metadata 是 dict/list，而不是被 double-JSON 的字串。
    e.g. "\"{\\\"kind\\\": ...}\"" -> {"kind": ...}
    """
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw

    v = raw
    # 最多解兩層，避免無限 loop
    for _ in range(2):
        if not isinstance(v, str):
            break
        parsed = jload(v, default=None)
        if parsed is None or parsed == v:
            break
        v = parsed
    return v

placeholder = lambda x: ','.join(['%s'] * len(x))
list2SqlList = lambda l: "','".join(l)
def _data_compilation(status, rows, docs_filter = None):
    data, delete_id_list = {}, []
    for row in rows:
        if row[8] in status and (docs_filter == None or f"{row[1]} {row[2]}" not in docs_filter):
            if data.get(row[1]) == None:
                data[row[1]] = {"rms_id": row[0], "doc_version": row[2], "doc_name": row[3], "eip_no": row[5], "eip_createdt": row[7], "decision_user": row[9], "decision_comment": row[10]}
            else:
                delete_id_list.append(row[0])
    return data, delete_id_list

ATTRIBUTE_ORDER = ["document_type", "EIP_id", "status", "document_token", "previous_document_token", "document_id", "document_name", "document_version", "attribute", "department", "author_id", "author", "approver", "confirmer", "rejecter", "issue_date", "change_reason", "change_summary", "reject_reason", "purpose"]
# 新 schema：移除 tier_no/sub_no，新增 parent_id/sort_order/depth，並補上 table_text/table_json（舊版漏帶，見 spec §20 第1點）
BLOCK_CONTENT_ORDER = ["content_id", "document_token", "step_type", "parent_id", "sort_order", "depth", "content_type", "header_text", "header_json", "content_text", "content_json", "table_text", "table_json", "files", "metadata", "created_at", "updated_at"]
REFERENCE_ORDER = ["document_token", "refer_type", "refer_document", "refer_document_name", "color", "created_at"]
def apply_snapshots_to_main_db(signed_docs):
    rms_id_map = {info["rms_id"]: info for info in signed_docs.values()}
    signed_rms_id_list = list(rms_id_map.keys())
    sql = f"""
        SELECT rds.snapshot_id, rds.rms_id, rds.document_token, rdsp.document_row, rdsp.blocks_rows, rdsp.references_rows, rdsp.program_codes_rows, rdsp.form_attributes
        FROM rms_document_snapshots AS rds
        JOIN rms_document_snapshot_payloads AS rdsp ON rds.snapshot_id = rdsp.snapshot_id
        WHERE rds.rms_id IN ({placeholder(signed_rms_id_list)})
    """

    attr_params_list = []
    block_params_list = []
    ref_params_list = []
    program_codes_params_list = []
    form_attr_restores = []   # [(final_token, form_attr_dict)]，寫回時還原 form_attributes
    
    # 紀錄快照更新 (如果有重配 Token，必須連快照表一起更新)
    snapshot_token_updates = []
    # 紀錄需要清除舊畫布的 Token
    tokens_to_clear_canvas = []

    parse_func = lambda r: json.dumps(r) if isinstance(r, (dict, list)) else r  # dict/list 都序列化（table_text 2D 陣列等）
    # v1 快照 block 內層 JSON 欄位（migrate 前需深解析成 dict/list）
    _snap_block_json_fields = ("header_json", "content_json", "table_json", "table_text", "files", "metadata", "content_text")
    try:
        with db(dict_cursor=True) as (conn, cur):
            cur.execute(sql, signed_rms_id_list)
            rows = cur.fetchall()

            # 1. 預先收集這批快照的 Token，查詢資料庫確認「所有權」
            raw_tokens = []
            parsed_rows = []
            for row in rows:
                doc_snap = json.loads(row["document_row"]) or {}
                token = doc_snap.get("document_token")
                doc_id = doc_snap.get("document_id")
                if token and doc_id:
                    raw_tokens.append(token)
                parsed_rows.append({"row": row, "doc_snap": doc_snap, "token": token, "doc_id": doc_id})

            # 建立 Token 擁有者追蹤器: { token: document_id }
            token_owner_tracker = {}
            if raw_tokens:
                cur.execute(f"SELECT document_token, document_id FROM rms_document_attributes WHERE document_token IN ({placeholder(raw_tokens)})", raw_tokens)
                for res in cur.fetchall():
                    token_owner_tracker[res['document_token']] = res['document_id']

            # 2. 開始處理資料，遇到衝突就重新發配 Token
            for item in parsed_rows:
                row = item["row"]
                doc_snap = item["doc_snap"]
                original_token = item["token"]
                doc_id = item["doc_id"]
                
                final_token = original_token
                
                # 衝突偵測：如果 Token 已存在，且擁有者不是現在這份 document_id，代表發生污染！
                if original_token in token_owner_tracker and token_owner_tracker[original_token] != doc_id:
                    final_token = str(uuid.uuid4()) # 重新配號
                    snapshot_token_updates.append((final_token, row["snapshot_id"]))
                    print(f"⚠️ 警告：偵測到 Token 污染！文件 {doc_id} 已自動重新配發新 Token: {final_token}")
                else:
                    # 登記所有權，防止同批次內的互相污染
                    token_owner_tracker[original_token] = doc_id

                tokens_to_clear_canvas.append(final_token)

                oracle_info = rms_id_map.get(row["rms_id"], {})
                blocks_snap = json.loads(row["blocks_rows"]) or []
                # 階層格式判斷（spec §10.3）：v1 舊快照帶 tier_no/sub_no → migrate 成新階層
                #（structural + 內容分類 + 定值項目 + step5 program-code）；v2 帶 parent_id → pass-through。
                if blocks_snap and ("tier_no" in blocks_snap[0]) and ("parent_id" not in blocks_snap[0]):
                    attr_obj = _normalize_metadata(doc_snap.get("attribute")) or {}
                    item_type = attr_obj.get("itemType") if isinstance(attr_obj, dict) else None
                    legacy_parsed = [
                        {**b, **{k: _normalize_metadata(b.get(k)) for k in _snap_block_json_fields if k in b}}
                        for b in blocks_snap
                    ]
                    blocks_snap = migrate_legacy_blocks(legacy_parsed, item_type=item_type)
                refs_snap = json.loads(row["references_rows"]) or []
                codes_snap = json.loads(row["program_codes_rows"]) or []

                # 把 Final Token 強制寫入資料中
                doc_snap["document_token"] = final_token
                doc_snap = {**doc_snap, "EIP_id": oracle_info.get("eip_no"), "status": 2, "issue_date": oracle_info.get("eip_createdt")}

                attr_params_list.append([parse_func(doc_snap.get(key)) for key in ATTRIBUTE_ORDER])
                
                # Blocks, Refs, Codes 也強制替換為 Final Token
                for b in blocks_snap:
                    b_ = {**b, "document_token": final_token, "created_at": oracle_info.get("eip_createdt"), "updated_at": oracle_info.get("eip_createdt")}
                    block_params_list.append([parse_func(b_.get(key)) for key in BLOCK_CONTENT_ORDER])
                for r in refs_snap:
                    r_ = {**r, "document_token": final_token, "created_at": oracle_info.get("eip_createdt"), "color": r.get("color") or "black"}
                    ref_params_list.append([parse_func(r_.get(key)) for key in REFERENCE_ORDER])
                
                program_codes_params_list.extend([[final_token, code] for code in codes_snap])

                # form_attributes（彩色標題/目的樣式）→ 寫回時還原；舊快照無此欄為 None，略過
                form_attr_snap = _normalize_metadata(row.get("form_attributes"))
                if isinstance(form_attr_snap, dict) and form_attr_snap:
                    form_attr_restores.append((final_token, form_attr_snap))

            # 3. 開始寫入資料庫
            if attr_params_list:
                cols = ",".join(ATTRIBUTE_ORDER)
                updates = ", ".join([f"{col}=VALUES({col})" for col in ATTRIBUTE_ORDER])
                sql_insert_attr = f"INSERT INTO rms_document_attributes ({cols}) VALUES ({placeholder(ATTRIBUTE_ORDER)}) ON DUPLICATE KEY UPDATE {updates}"
                cur.executemany(sql_insert_attr, attr_params_list)

                # 清除舊畫布 (確保不會殘留使用者刪除的區塊)
                if tokens_to_clear_canvas:
                    cur.execute(f"DELETE FROM rms_block_content WHERE document_token IN ({placeholder(tokens_to_clear_canvas)})", tokens_to_clear_canvas)
                    cur.execute(f"DELETE FROM rms_references WHERE document_token IN ({placeholder(tokens_to_clear_canvas)})", tokens_to_clear_canvas)
                    cur.execute(f"DELETE FROM rms_document_form_attributes WHERE document_token IN ({placeholder(tokens_to_clear_canvas)})", tokens_to_clear_canvas)

                if block_params_list:
                    cols = ",".join(BLOCK_CONTENT_ORDER)
                    updates = ", ".join([f"{col}=VALUES({col})" for col in BLOCK_CONTENT_ORDER])
                    sql_insert_block = f"INSERT INTO rms_block_content ({cols}) VALUES ({placeholder(BLOCK_CONTENT_ORDER)}) ON DUPLICATE KEY UPDATE {updates}"
                    cur.executemany(sql_insert_block, block_params_list)

                if ref_params_list:
                    cols = ",".join(REFERENCE_ORDER)
                    updates = ", ".join([f"{col}=VALUES({col})" for col in REFERENCE_ORDER])
                    sql_insert_ref = f"INSERT INTO rms_references ({cols}) VALUES ({placeholder(REFERENCE_ORDER)}) ON DUPLICATE KEY UPDATE {updates}"
                    cur.executemany(sql_insert_ref, ref_params_list)

                if program_codes_params_list:
                    sql_update_code = "UPDATE rms_program_code SET document_token = %s, status = 1 WHERE program_code = %s"
                    cur.executemany(sql_update_code, program_codes_params_list)
                    
                # 4. 還原 form_attributes（彩色標題/目的樣式）回 rms_document_form_attributes
                for tok, fa in form_attr_restores:
                    _save_form_attributes(cur, tok, fa)

                # 5. 更新快照表中的 Token (根除污染源)
                if snapshot_token_updates:
                    cur.executemany("UPDATE rms_document_snapshots SET document_token = %s WHERE snapshot_id = %s", snapshot_token_updates)

            conn.commit()
        return "Success"

    except Exception as e:
        print(f"Error in apply_snapshots_to_main_db: {e}")
        return "Failed"

@bp.post("/sync-eip")
def sync_eip():
    """
    Docstring for sync_eip_
        sync_eip API - Process
            1. 處理已簽核文件
            -- 1.1 (Oracle) 取得"簽核成功"的文件
            -- 1.2 (MySQL)  刪除 other document attribute drafts which is the same version, and previous version contents & references, signed draft (連動刪除 content block, references, program code)
            -- 1.3 (MySQL)  將簽核成功(簽核成功)文件資料利用 snapshots 進行回溯 & update issue_date
            -- 1.4 (MySQL)  將新版(簽核成功)文件 document_token 取代舊版 document_token 的 program code (program code status = 9 release)
            -- 1.5 (MySQL)  刪除簽核成功文件相關的 snapshots (where document_id is the same with signed document) 
            -- 1.6 (Oracle) 更新 RMS_DCC2EIP 的 RMS_ID 為 NULL (where document_id is the same with signed document)

            2. 處理作廢文件
            -- 2.1 (Oracle) 取得"作廢"文件
            -- 2.2 (MySQL)  刪除作廢文件的 snapshots
            -- 2.3 (Oracle) 更新 RMS_DCC2EIP 的 RMS_ID 為 NULL (Only update RMS_DCC2EIP table where EIP_status = '作廢')

            3. 處理(否決, 退回申請者)文件
            -- 3.1 (Oracle) 取得(否決, 退回申請者)文件
            -- 3.2 (Python) 取得每份('否決', '退回申請者')的最新版以及列出舊版('否決', '退回申請者')清單 (利用 RMS_DCCNO, RMS_VER 歸類並用 EIP_CREATEDT 排序)
            -- 3.3 (MySQL)  將最新的拒絕文件更新在該草稿資訊中
            -- 3.4 (MySQL)  刪除舊版拒絕文件的 snapshot
            -- 3.5 (MySQL)  更新每份最後一次被拒絕的文件 sync_status = 2 FROM rms_document_snapshots
            -- 3.6 (Oracle) 更新 RMS_DCC2EIP 的 RMS_ID 為 NULL (Only update RMS_DCC2EIP table where EIP_status IN ('否決', '退回申請者'))

            4. 處理送審中文件
            -- 4.1 (Oracle) 取得"送審中"文件
            -- 4.2 (MySQL)  更新 rms_document_snapshots synced_at = NOW()
    """
    data, info = odb_data_fetch("""SELECT RMS_ID, RMS_DCCNO, RMS_VER, RMS_DCCNAME, RMS_INSDT, EIPNO, EIP_USER, EIP_CREATEDT,
            CASE WHEN HAS_SIGNED > 0 AND (EIP_STATUS != '已簽核' OR EIP_STATUS IS NULL) THEN '作廢' WHEN EIP_STATUS IS NOT NULL THEN EIP_STATUS ELSE EIP_STATUS END AS EIP_STATUS, DECISION_USER, DECISION_COMMENT
        FROM (SELECT t.*, COUNT(CASE WHEN EIP_STATUS = '已簽核' THEN 1 END) OVER (PARTITION BY RMS_DCCNO, RMS_VER) AS HAS_SIGNED FROM IDBUSER.RMS_DCC2EIP t)
        WHERE (EIP_STATUS IS NOT NULL OR HAS_SIGNED > 0) AND RMS_DCCNAME IS NOT NULL
        ORDER BY CASE WHEN EIP_STATUS = '已簽核' THEN 0 ELSE 1 END, EIP_CREATEDT DESC"""
    )

    print(f"data: {data}")

    if info != "Success":
        return jsonify({"Success": False, "error": "Connect database error, please try again!"}), 500

    signed_docs, signed_delete_id_list = _data_compilation(["已簽核"], data)
    rejected_docs, rejected_delete_id_list = _data_compilation(["否決", "退回申請者"], data, docs_filter = [f"{doc_id} {doc_info['doc_version']}" for doc_id, doc_info in signed_docs.items()])
    invalid_docs = [row[0] for row in data if row[8] == '作廢']
    submitted_docs = [row[0] for row in data if row[8] == '審核中' and signed_docs.get(row[1]) == None]

    # Process signed document data
    signed_rms_id_list = [doc_info["rms_id"] for doc_info in signed_docs.values()]
    if len(signed_rms_id_list) > 0:
        sql = f"""
            DELETE rbc FROM rms_block_content AS rbc
            JOIN rms_document_attributes AS rda ON rbc.document_token = rda.previous_document_token
            JOIN rms_document_snapshots AS rds ON rda.document_token = rds.document_token
            WHERE rds.rms_id IN ('{list2SqlList(signed_rms_id_list)}')
        """
        db_status = db_update(sql)
        if db_status == "Failed":
            return jsonify({"Success": False, "error": "Step 1.2 rms_block_content previous content block delete failed"})

        sql = f"""
            DELETE rf FROM rms_references AS rf
            JOIN rms_document_attributes AS rda ON rf.document_token = rda.previous_document_token
            JOIN rms_document_snapshots AS rds ON rda.document_token = rds.document_token
            WHERE rds.rms_id IN ('{list2SqlList(signed_rms_id_list)}')
        """
        db_status = db_update(sql)
        if db_status == "Failed":
            return jsonify({"Success": False, "error": "Step 1.2 rms_references previous references delete failed"})

        sql = f"""
            DELETE rda FROM rms_document_attributes AS rda
            JOIN rms_document_snapshots AS rds ON rds.rms_id IN ('{list2SqlList(signed_rms_id_list)}') AND rda.document_id = rds.document_id AND rda.document_version = rds.document_version
        """
        db_status = db_update(sql)
        if db_status == "Failed":
            return jsonify({"Success": False, "error": "Step 1.2 rms_document_attributes other drafts and signed draft delete failed"})
        
        db_status = apply_snapshots_to_main_db(signed_docs)
        if db_status == "Failed":
            return jsonify({"Success": False, "error": "Step 1.3 failed"})
        
        sql = f"""
            UPDATE rms_program_code rpc
            INNER JOIN (
                SELECT rda.document_token AS new_token, rda.previous_document_token AS old_token FROM rms_document_attributes rda
                INNER JOIN rms_document_snapshots rds ON rds.document_token = rda.document_token
                WHERE rda.previous_document_token IS NOT NULL AND rds.rms_id IN ('{list2SqlList(signed_rms_id_list)}')
                GROUP BY rda.document_token, rda.previous_document_token
            ) AS NewTokenMap ON rpc.document_token = NewTokenMap.old_token
            SET rpc.status = 9, rpc.document_token = NULL
        """
        db_status = db_update(sql)
        if db_status == "Failed":
            return jsonify({"Success": False, "error": "Step 1.4 failed"})
        
        sql = f"""
            DELETE rds FROM RMS_document_snapshots AS rds
            JOIN RMS_document_snapshots AS rds_ ON rds_.rms_id IN ('{list2SqlList(signed_rms_id_list)}') AND rds_.document_id = rds.document_id AND rds_.document_version = rds.document_version
        """
        db_status = db_update(sql)
        if db_status == "Failed":
            return jsonify({"Success": False, "error": "Step 1.5 failed"})

    if len(invalid_docs) > 0:
        sql = f"DELETE rds FROM rms_document_snapshots AS rds WHERE rds.rms_id IN ('{list2SqlList(invalid_docs)}')"
        db_update(sql)

        if db_status == "Failed":
            return jsonify({"Success": False, "error": "Invalid Document Delete Failed."})
        
    rejected_rms_id_list = [doc_info["rms_id"] for doc_info in rejected_docs.values()]
    if len(rejected_rms_id_list) > 0:
        try:
            with db() as (conn, cur):
                sql = """
                    UPDATE rms_document_attributes AS rda
                    JOIN rms_document_snapshots AS rds ON rds.document_id = %s AND rda.document_token = rds.document_token
                    SET rda.rejecter = %s, rda.reject_reason = %s
                """
                upd_params = [(rejected_id, rejected_info.get('decision_user', ''), rejected_info.get('decision_comment', ''),) for rejected_id, rejected_info in rejected_docs.items()]
                cur.executemany(sql, upd_params)

                sql = f"""
                    DELETE target FROM rms_document_snapshots AS target
                    INNER JOIN rms_document_snapshots AS ref ON target.document_id = ref.document_id AND target.document_version = ref.document_version
                    WHERE ref.rms_id IN ({placeholder(rejected_rms_id_list)}) AND target.sync_status = 2 AND target.rms_id <> ref.rms_id
                """
                cur.execute(sql, rejected_rms_id_list)
                cur.execute(f"UPDATE rms_document_snapshots SET sync_status = 2, synced_at = NOW() WHERE rms_id IN ({placeholder(rejected_rms_id_list)})", rejected_rms_id_list)

                conn.commit()

        except Exception as e:
            print(f"Document Reject Process Error: {e}")
            return jsonify({"Success": False, "error": "Document Reject Process Error."})
        
    if len(submitted_docs) > 0:
        sql = f"UPDATE rms_document_snapshots SET synced_at = NOW() WHERE rms_id IN ('{list2SqlList(submitted_docs)}')"
        db_status = db_update(sql)

        if db_status == "Failed":
            return jsonify({"Success": False, "error": "Submitted Document update Failed."})
        
    odb_update_list = signed_rms_id_list + signed_delete_id_list + invalid_docs + rejected_rms_id_list + rejected_delete_id_list + submitted_docs
    if len(odb_update_list) > 0:
        try:
            with odb() as cur_o:
                sql = f"UPDATE IDBUSER.RMS_DCC2EIP SET RMS_DCCNAME = NULL WHERE RMS_ID IN ('{list2SqlList(odb_update_list)}')"
                cur_o.execute(sql)
                cur_o.connection.commit()
        
        except Exception as e:
            print(f"Oracle rms_id Update Process Error: {e}")
            return jsonify({"Success": False, "error": "Oracle rms_id Update Process Error."})

# ----- Draft Function ----- #

@bp.post("/clear-doc-id")
def clear_doc_id():
    """
    前端在變更適用工程後呼叫，清除該 token 的 document_id。
    """
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    if not token:
        return send_response(400, False, "missing token")

    with db() as (conn, cur):
        cur.execute(f"UPDATE rms_document_attributes SET document_id = NULL WHERE document_token='{token}'")

    return jsonify({"success": True})

@bp.get("/drafts")
def list_drafts():
    user_id = request.args.get("userId", "")
    keyword = request.args.get("keyword", "")
    page = int(request.args.get("page", 1))
    pageSize = int(request.args.get("pageSize", 10))
    getPages = request.args.get("getPages", False)

    if len(keyword) > 0:
        keyword = f" AND (document_id LIKE '%{keyword}%' OR document_name LIKE '%{keyword}%' OR author LIKE '%{keyword}%' OR author_id LIKE '%{keyword}%')"

    target = "COUNT(*)" if getPages else "document_type, document_token, document_name, document_version, document_id, author, author_id, issue_date"

    if user_id == '07714' or user_id == '12868':
        # admin (07714, 12868) 看全部
        sql = f"SELECT {target} FROM rms_document_attributes WHERE (status = 3 OR status = 1 OR status = 0) AND author_id IS NOT NULL {keyword} ORDER BY issue_date DESC "
    else:
        # ★ 可視範圍卡控：依 Oracle dept tree 展開該使用者能看到的所有 EMP_NO
        # 規則: { 自己 DEPT } ∪ { descendants } ∪ { parent (限 KJ 樹內) }
        visible_emp_ids = get_visible_emp_ids(user_id)
        if not visible_emp_ids:
            return send_response(200, True, "查詢成功", {"pages": 0} if getPages else {"items": []})
        emp_list_sql = ",".join(f"'{e}'" for e in visible_emp_ids)
        sql = (
            f"SELECT {target} FROM rms_document_attributes "
            f"WHERE (status = 3 OR status = 1 OR status = 0) "
            f"  AND author_id IN ({emp_list_sql}) "
            f"  {keyword} "
            f"ORDER BY issue_date DESC "
        )

    data, info = db_data_fetch(sql if getPages else sql + f"LIMIT {pageSize} OFFSET {(page - 1) * pageSize}")

    if info != "Success":
        return send_response(500, True, "查詢失敗", {"message": "資料庫查詢失敗，請重新嘗試"})
    
    if getPages:
        return send_response(200, True, "查詢成功", {"pages": math.ceil(data[0][0] / pageSize)})

    items = []
    for item in data:
        dt = item[7].replace(tzinfo=TZ_TW)
        items.append({
            "documentType": item[0],
            "documentToken": item[1],
            "documentName": item[2],
            "documentVersion": item[3],
            "documentId": item[4],
            "author": item[5],
            "authorId": item[6],
            "issueDate": dt.isoformat(),
        })

    return send_response(200, True, "查詢成功", {"items": items})

@bp.delete("/<document_token>")
def delete_draft(document_token):
    token = (document_token or "").strip()
    if len(token) == 0:
        return jsonify({"success": False, "error": "document_token is required"}), 400
    
    try:
        with db() as (conn, cur):
            # 允許刪除草稿 (1) 與已下載 (3)； 已公告 (2) 不允許刪除 (避免破壞已公告文件的圖片連結與外部引用)
            cur.execute("DELETE FROM rms_document_attributes WHERE document_token = %s AND status IN (1, 3)", (document_token,))
            conn.commit()
            deleted = cur.rowcount or 0

    except Exception as e:
        print(f"Error result: {e}")
        return jsonify({"success": False, "error": "資料庫操作失敗，請重新嘗試"}), 500

    return jsonify({"success": True, "deleted": deleted}), 200

# ----- Document Search ----- #

@bp.get("/passed")
def list_passed():
    user_id = request.args.get("userId", "")
    document_type = request.args.get("documentType", "")
    keyword = request.args.get("keyword", "")
    page = int(request.args.get("page", 1))
    pageSize = int(request.args.get("pageSize", 10))
    getPages = request.args.get("getPages", False)

    if len(keyword) > 0:
        keyword = f" AND (document_id LIKE '%{keyword}%' OR document_name LIKE '%{keyword}%' OR author LIKE '%{keyword}%' OR author_id LIKE '%{keyword}%')"

    # ★ 可視範圍卡控 (與 /drafts 同邏輯)：admin 看全部；其餘人看 dept tree 範圍內
    user_filter = ""
    if len(user_id) > 0:
        if user_id == '12868' or user_id == '07714':
            user_filter = ""  # admin 看全部
        else:
            visible_emp_ids = get_visible_emp_ids(user_id)
            if not visible_emp_ids:
                return send_response(200, True, "查詢成功", {"pages": 0} if getPages else {"items": []})
            emp_list_sql = ",".join(f"'{e}'" for e in visible_emp_ids)
            user_filter = f" AND author_id IN ({emp_list_sql})"

    if len(document_type) > 0:
        document_type = f" AND document_type = {document_type}"

    target = "COUNT(*)" if getPages else "document_type, document_token, document_name, document_version, document_id, author, author_id, issue_date"

    sql = f"""
        WITH RankedDocuments AS (
            SELECT *, ROW_NUMBER() OVER ( PARTITION BY document_id ORDER BY issue_date DESC, document_version DESC ) as rn FROM rms_document_attributes WHERE status = 2
        )
        SELECT {target} FROM RankedDocuments
        WHERE rn = 1 {document_type} {user_filter} {keyword} ORDER BY issue_date
    """

    data, info = db_data_fetch(sql if getPages else sql + f"LIMIT {pageSize} OFFSET {(page - 1) * pageSize}")

    if info != "Success":
        return send_response(500, True, "查詢失敗", {"message": "資料庫查詢失敗，請重新嘗試"})
    
    if getPages:
        return send_response(200, True, "查詢成功", {"pages": math.ceil(data[0][0] / pageSize)})

    items = []
    for item in data:
        items.append({
            "documentType": item[0],
            "documentToken": item[1],
            "documentName": item[2],
            "documentVersion": item[3],
            "documentId": item[4],
            "author": item[5],
            "authorId": item[6],
            "issueDate": item[7],
        })

    return send_response(200, True, "查詢成功", {"items": items})

@bp.get("/submitted-and-rejected")
def list_submitted_and_rejected():
    user_id = request.args.get("user_id")
    keyword = request.args.get("keyword", "")
    page = int(request.args.get("page", 1))
    pageSize = int(request.args.get("pageSize", 10))
    getPages = request.args.get("getPages", False)

    if len(keyword) > 0:
        keyword = f"AND (a.document_id LIKE '%{keyword}%' OR a.document_name LIKE '%{keyword}%') OR a.author LIKE '%{keyword}%'"

    target = "COUNT(*)" if getPages else "a.document_type, a.document_token, a.document_name, a.document_version, a.document_id, a.author, a.author_id, s.created_at, a.rejecter, a.reject_reason, s.rms_id"

    sql = f"""
        SELECT {target} FROM rms_document_attributes AS a
        JOIN (SELECT document_token, MAX(snapshot_id) AS latest_id FROM rms_document_snapshots GROUP BY document_token) AS latest_snap ON a.document_token = latest_snap.document_token
        JOIN rms_document_snapshots AS s ON s.snapshot_id = latest_snap.latest_id
        WHERE 1=1 {keyword} ORDER BY s.created_at DESC
    """
    data, info = db_data_fetch(sql if getPages else sql + f"LIMIT {pageSize} OFFSET {(page - 1) * pageSize}")

    if info != "Success":
        return send_response(500, True, "查詢失敗", {"message": "MySQL 資料庫查詢失敗，請重新嘗試"})
    
    if getPages:
        return send_response(200, True, "查詢成功", {"pages": math.ceil(data[0][0] / pageSize)})
    
    if not data:
        return send_response(200, True, "查詢成功", {"items": []})

    search_id = "','".join([item[10] for item in data])
    sql = f"SELECT RMS_ID, EIP_CREATEDT, EIP_STATUS, DECISION_USER, DECISION_COMMENT FROM IDBUSER.RMS_DCC2EIP WHERE RMS_ID IN ('{search_id}')"
    data_status, info = odb_data_fetch(sql)

    if info != "Success":
        return send_response(500, True, "查詢失敗", {"message": "Oracle 資料庫查詢失敗，請重新嘗試"})
    
    data_status = {item[0]: {"issueDate": item[1], "eipStatus": item[2], "rejecter": item[3], "rejectReason": item[4]} for item in data_status}

    items = []
    for item in data:
        print(f"item: {item}")
        issueDate, eipStatus, rejecter, rejectReason = item[7], "已下載", "", ""
        if data_status.get(item[10]) == None:
            eipStatus = "同步失敗"
        
        elif data_status[item[10]].get("eipStatus") != None:
            eipStatus = data_status[item[10]].get("eipStatus", "已下載")
            rejecter = data_status[item[10]].get("rejecter")
            rejectReason = data_status[item[10]].get("rejectReason")
            issueDate = data_status[item[10]].get("issueDate")

        items.append({
            "documentType": item[0],
            "documentToken": item[1],
            "documentName": item[2],
            "documentVersion": item[3],
            "documentId": item[4],
            "author": item[5],
            "authorId": item[6],
            "issueDate": issueDate,
            "eipStatus": eipStatus,
            "rejecter": rejecter,
            "rejectReason": rejectReason,
            "rmsId": item[10],
        })

    return send_response(200, True, "查詢成功", {"items": items})

def _build_doc_payload_from_token(token: str) -> dict:
    """
    給定 document_token：
      - 組出 data["attribute"]：目前版本 + 最多 2 個前版本（只需要 attribute / 基本欄位）
      - 組出 data["content"]：只有「目前這一份文件」的內容 blocks + 參數 blocks
      - 組出 data["reference"]：目前這一份文件的 reference 列表
    這個結構會直接丟給 get_docx 使用。
    """
    with db(dict_cursor=True) as (conn, cur):
        # ---------- 1) attributes：沿 previous_document_token 往回追 ----------
        attrs = []
        hops = 0
        seen = set()
        current_token = token

        while current_token and current_token not in seen and hops < 3:  # 目前 + 最多 2 份舊版 = 3
            seen.add(current_token)
            cur.execute(
                "SELECT * FROM rms_document_attributes WHERE document_token=%s",
                (current_token,),
            )
            r = cur.fetchone()
            if not r:
                break

            attr_json = jload(r.get("attribute"), {}) or {}

            # 這裡我們組成一個「form」長相的 dict，對齊你前端送進 generate/word 的結構
            attrs.append({
                "documentType":     r["document_type"],
                "documentID":       r["document_id"] or "",
                "documentName":     r["document_name"] or "",
                "documentVersion":  float(r["document_version"] or 1.0),
                "attribute":        attr_json,                     # 品目 / 工程 / 式樣等等
                "department":       r["department"] or "",
                "author_id":        r["author_id"] or "",
                "author":           r["author"] or "",
                "approver":         r["approver"] or "",
                "confirmer":        r["confirmer"] or "",
                "issueDate":        r["issue_date"].strftime("%Y/%m/%d") if r["issue_date"] else "",
                "reviseReason":     r["change_reason"] or "",
                "revisePoint":      r["change_summary"] or "",
                "documentPurpose":  r["purpose"] or "",
            })

            current_token = r.get("previous_document_token")
            hops += 1

        # attrs 目前是 [最新, 前一版, 前前版...]，為了讓 REV1/2/3 比較像「由舊到新」，
        # 我們可以 reverse 一下，最後一個就是 get_docx 看到的「最新」。
        attrs.reverse()
        if not attrs:
            raise ValueError("document not found")

        # ---------- 2) content：只有「目前這份」的 blocks + 參數 ----------
        cur.execute("""
            SELECT step_type, tier_no, sub_no, content_type, header_text, header_json, content_text, content_json, files, metadata FROM rms_block_content
            WHERE document_token=%s ORDER BY step_type ASC, tier_no ASC, sub_no ASC
        """, (token,))
        rows = cur.fetchall() or []

        # 一般 blocks（製造流程 / 管理條件 / 品質內容 / 其他 等）
        block_groups = {}      # key = (step_type, tier_no)
        # 參數 blocks（step_type 2: 製造條件參數一覽表 / 5: 製造參數一覽表）
        param_groups = {}      # key = tier_no

        for r in rows:
            st  = int(r["step_type"])
            t   = int(r["tier_no"])
            sub = int(r["sub_no"])

            # 參數類：跟 load_params 的邏輯一樣，把 sub 0/1 縫回去
            if st in (2, 5):
                g = param_groups.setdefault(t, {
                    "step_type":            st,
                    "tier_no":              t,
                    "code":                 f"XXXX{t}",
                    "jsonParameterContent": None,
                    "arrayParameterData":   [],
                    "jsonConditionContent": None,
                    "arrayConditionData":   [],
                    "metadata":             None,
                })
                if sub == 0:
                    g["code"]                 = r["header_text"] or g["code"]
                    g["arrayParameterData"]   = jload(r["content_text"], []) or []
                    g["jsonParameterContent"] = jload(r["content_json"])
                    g["metadata"]             = jload(r["metadata"])
                elif sub == 1:
                    g["arrayConditionData"]   = jload(r["content_text"], []) or []
                    g["jsonConditionContent"] = jload(r["content_json"])
                continue

            # 一般內容類：跟 /<token>/blocks 的 grouped 結構一樣
            g = block_groups.setdefault((st, t), {
                "step_type": st,
                "tier":      t,
                "data":      [],
            })
            g["data"].append({
                "option":      int(r["content_type"]),
                "jsonHeader":  jload(r["header_json"]),
                "jsonContent": jload(r["content_json"]),
                "files":       jload(r["files"], []) or [],
            })

        contents = []
        # blocks 按 step_type, tier_no 排序
        for (st, t) in sorted(block_groups.keys()):
            contents.append(block_groups[(st, t)])
        # 參數 blocks 按 tier 排序
        for t in sorted(param_groups.keys()):
            contents.append(param_groups[t])

        # ---------- 3) references ----------
        cur.execute("SELECT refer_type, refer_document, refer_document_name FROM rms_references WHERE document_token=%s ORDER BY refer_type ASC, id ASC", (token,))
        ref_rows = cur.fetchall() or []
        references = [
            {
                "referenceType":        int(r["refer_type"]),
                "referenceDocumentID":  r["refer_document"],
                "referenceDocumentName": r["refer_document_name"],
            }
            for r in ref_rows
        ]

    return {
        "attribute": attrs,     # list[form-like dict]
        "content":   contents,  # list[blocks + params]
        "reference": references,
    }

def _build_docx_payload_v2(token: str) -> dict:
    """
    給「新樹渲染器」get_docx_ / get_docx_without_framework_ 用的 payload（從 DB 即時組）：
      - attribute：snake_case（對齊前端送 generate/word_ 的結構），含目前版 + 最多 2 個前版
      - content：build_tree 產出的樹 [{step_type, children}]
      - reference：{refer_type, refer_document, refer_document_name}
      - form_attribute：目的 / 文件名 / 適用工程的 tiptap 樣式
    （取代舊的 _build_doc_payload_from_token + 舊 get_docx；舊版查 tier_no/sub_no，新表已無此欄。）
    """
    with db(dict_cursor=True) as (conn, cur):
        # 1) attributes：沿 previous_document_token 往回追
        attrs = []
        hops = 0
        seen = set()
        current_token = token
        while current_token and current_token not in seen and hops < 3:
            seen.add(current_token)
            cur.execute("SELECT * FROM rms_document_attributes WHERE document_token=%s", (current_token,))
            r = cur.fetchone()
            if not r:
                break
            attr_json = jload(r.get("attribute"), {}) or {}
            attrs.append({
                "document_type":    r["document_type"],
                "document_id":      r["document_id"] or "",
                "document_name":    r["document_name"] or "",
                "document_version": float(r["document_version"] or 1.0),
                "attribute":        attr_json,
                "department":       r["department"] or "",
                "author_id":        r["author_id"] or "",
                "author":           r["author"] or "",
                "approver":         r["approver"] or "",
                "confirmer":        r["confirmer"] or "",
                "issue_date":       r["issue_date"].strftime("%Y/%m/%d") if r["issue_date"] else "",
                "change_reason":    r["change_reason"] or "",
                "change_summary":   r["change_summary"] or "",
                "purpose":          r["purpose"] or "",          # 指示書 body「目的」
                "documentPurpose":  r["purpose"] or "",          # 式樣書 body「目的」
            })
            current_token = r.get("previous_document_token")
            hops += 1
        attrs.reverse()   # [舊版..., 最新版]；get_docx_ 用 [-1] 當最新
        if not attrs:
            raise ValueError("document not found")

        # 2) content：新樹（parent_id/sort_order/depth → build_tree）
        cur.execute(
            "SELECT content_id, step_type, parent_id, sort_order, depth, content_type, "
            "header_text, header_json, content_text, content_json, table_text, table_json, files, metadata "
            "FROM rms_block_content WHERE document_token=%s ORDER BY step_type ASC, sort_order ASC",
            (token,),
        )
        content_tree = build_tree([deserialize_block_row(r) for r in (cur.fetchall() or [])])

        # 3) references（key 對齊渲染器讀法 refer_document / refer_document_name）
        cur.execute("SELECT refer_type, refer_document, refer_document_name FROM rms_references WHERE document_token=%s ORDER BY refer_type ASC, id ASC", (token,))
        references = [
            {"refer_type": int(r["refer_type"]), "refer_document": r["refer_document"], "refer_document_name": r["refer_document_name"]}
            for r in (cur.fetchall() or [])
        ]

        # 4) form_attribute（目的 / 文件名 / 適用工程 的彩色樣式）
        form_attr = _load_form_attributes(cur, token)

    return {"attribute": attrs, "content": content_tree, "reference": references, "form_attribute": form_attr}

@bp.get("/view/<token>/docx")
def view_docx_from_token(token):
    """
    依 document_token 從 DB 撈出 attribute/content/reference，
    串成 payload 丟給 get_docx，產生一份暫存 DOCX，
    回傳給前端做「全頁預覽」（前端直接 window.open 這個 URL）。
    """
    try:
        data = _build_docx_payload_v2(token)   # 新樹 + snake_case attribute + form_attribute
    except Exception as e:
        print("[view_docx_from_token] error:", e)
        return jsonify({"ok": False, "error": "document not found"}), 404

    # 檔名：優先用文件名稱 / 編號
    try:
        attr_last = data["attribute"][-1]
        raw_name  = attr_last.get("document_name") or attr_last.get("document_id") or token
        doc_name  = _safe_docname(raw_name)
    except Exception:
        doc_name = token

    # 暫存目錄
    view_dir = os.path.join(BASE_DIR, "_view")
    os.makedirs(view_dir, exist_ok=True)

    fname    = f"{doc_name}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.docx"
    out_path = os.path.join(view_dir, fname)

    # 產生 Word（新樹渲染器 get_docx_）
    if data["attribute"][-1]["document_type"] == 1:
        get_docx_(out_path, data, "docx-template/SpecificationDocument.docx")
    else:
        get_docx_(out_path, data, "docx-template/InstructionDocument.docx")

    # 回傳後刪掉暫存檔
    @after_this_request
    def remove_file(response):
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except Exception as e:
            print("[view_docx_from_token] remove temp file error:", e)
        return response

    return send_file(
        out_path,
        as_attachment=False,  # 🔑 不強制下載，讓瀏覽器／系統自己決定用什麼開
        download_name=f"{doc_name}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

def _safe_docname(name: str) -> str:
    name = (name or "").strip()
    if not name:
        return "document"
    # 簡單去掉不適合當檔名的字元
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return name[:80]

# ----- Generate Word ----- #

def _normalize_for_json(obj):
    """
    把 dict/list 裡面的 Decimal、datetime 之類轉成可被 json.dumps 的型別。
    只在 snapshot 時用，不會影響其它地方。
    """
    from datetime import datetime, date

    if isinstance(obj, Decimal):
        return float(obj)

    if isinstance(obj, (datetime, date)):
        # 你要也可以改成 str(obj) 或自訂格式
        return obj.isoformat()

    if isinstance(obj, dict):
        return {k: _normalize_for_json(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [_normalize_for_json(v) for v in obj]

    if isinstance(obj, tuple):
        return tuple(_normalize_for_json(v) for v in obj)

    if isinstance(obj, set):
        return [_normalize_for_json(v) for v in obj]   # set 改成 list

    return obj

make_rms_id = lambda: uuid.uuid4().hex[:15]

def create_snapshot_and_oracle_row(token: str, rms_id: str, user_emp_no: str):
    """
    1) 從 MySQL 撈出目前 token 的 document_row / blocks_rows / references_rows
    2) 先在 Oracle.IDBUSER.RMS_DCC2EIP 新增 RMS_* 一筆
    3) 再寫入 sfdb4070.rms_document_snapshots (meta) + rms_document_snapshot_payloads (JSON)
    """
    # --- 1) 讀 MySQL 現況（只讀，不動資料） ---
    with db(dict_cursor=True) as (conn, cur):
        cur.execute(" SELECT * FROM rms_document_attributes WHERE document_token=%s", (token,))
        doc_row = cur.fetchone()
        if not doc_row:
            raise RuntimeError(f"document_token {token} not found for snapshot")

        doc_id   = doc_row.get("document_id")
        doc_ver  = float(doc_row.get("document_version") or 1.0)
        doc_name = doc_row.get("document_name") or ""
        issue_dt = doc_row.get("issue_date") or datetime.datetime.now()

        cur.execute("SELECT * FROM rms_block_content WHERE document_token=%s", (token,))
        blocks_rows = cur.fetchall() or []

        cur.execute("SELECT * FROM rms_references WHERE document_token=%s", (token,))
        ref_rows = cur.fetchall() or []

        cur.execute("SELECT DISTINCT jt.program_code FROM rms_block_content AS t JOIN JSON_TABLE(t.metadata, '$.programs[*]' COLUMNS(program_code VARCHAR(15) PATH '$.programCode')) AS jt WHERE document_token = %s", (token, ))
        program_codes_rows = cur.fetchall() or []

    # --- 2) 先寫 Oracle.RMS_DCC2EIP ---
    with odb() as cur_o:
        cur_o.execute("INSERT INTO IDBUSER.RMS_DCC2EIP (RMS_ID, RMS_DCCNO, RMS_VER, RMS_DCCNAME, RMS_INSDT) VALUES (:1, :2, :3, :4, :5)", (rms_id, doc_id, doc_ver, doc_name, issue_dt))
        cur_o.connection.commit()

    # --- 3) 再寫 MySQL snapshot（meta + payload 分兩張表） ---
    doc_row_json  = _normalize_for_json(doc_row)
    blocks_json   = _normalize_for_json(blocks_rows)
    refs_json     = _normalize_for_json(ref_rows)
    programs_json = [info['program_code'] for info in program_codes_rows]

    try:
        doc_row_str     = jdump(doc_row_json)
        blocks_str      = jdump(blocks_json)
        refs_str        = jdump(refs_json)
        programs_str    = jdump(programs_json)
    except TypeError as e:
        print("[snapshot DEBUG] json dump failed:", e)
        raise

    with db(dict_cursor=True) as (conn, cur):
        # 3-1) 先插入輕量的 snapshots（拿到 snapshot_id）
        cur.execute("INSERT INTO rms_document_snapshots (document_token, rms_id, document_id, document_version, document_name, created_by) VALUES (%s,%s,%s,%s,%s,%s)", (token, rms_id, doc_id, doc_ver, doc_name, user_emp_no))
        snapshot_id = cur.lastrowid

        # 3-2) 再插入 payload（含 form_attributes：凍結彩色標題/目的樣式）
        form_attr_str = jdump(_load_form_attributes(cur, token))
        cur.execute("INSERT INTO rms_document_snapshot_payloads (snapshot_id, document_row, blocks_rows, references_rows, program_codes_rows, form_attributes) VALUES (%s,%s,%s,%s,%s,%s)", (snapshot_id, doc_row_str, blocks_str, refs_str, programs_str, form_attr_str))

        conn.commit()

def next_document_id(prefix: str) -> str:
    """
    依照 PROJECT_CODE 前三碼 + 三位流水號產生 document_id：
      WMA → WMA001, WMA002, ...
    """
    if not prefix or len(prefix) < 3:
        prefix = "XXX"
    prefix = prefix[:3]

    with db(dict_cursor=True) as (conn, cur):
        cur.execute("SELECT document_id FROM rms_document_attributes WHERE document_id LIKE %s ORDER BY document_id DESC LIMIT 1", (prefix + "%",))
        row = cur.fetchone()

        if not row or not row["document_id"]:
            return f"{prefix}001"

        tail = row["document_id"][-3:]
        try:
            num = int(tail)
        except ValueError:
            num = 0

        return f"{prefix}{num + 1:03d}"

def next_monthly_document_id(prefix: str = "W", mpn_mode: str = "MP") -> str:
    """
    依照 W_YY_MM_XXX (MP) 或 W-YYMMXXX (NPI) 規則產生 document_id。
    流水號分開計算 (各自取當月該格式的最大值 + 1)。
    """
    # According to mpn_mode to decide base format
    now = datetime.datetime.now()
    base = f"{prefix}-{now.year % 100:02d}-{now.month:02d}-" if mpn_mode == "MP" else f"{prefix}-{now.year % 100:02d}{now.month:02d}"

    # Get serial number of current base format from database
    try:
        with db(dict_cursor=True) as (conn, cur):
            cur.execute("SELECT document_id FROM rms_document_attributes WHERE document_id LIKE %s", (f"{base}%",))
            rows = cur.fetchall() or []
    except Exception as e:
        print(f"查找文件 ID 失敗: {e} => 因此返回空 ID")
        return ""

    # Find the biggest serial number for base format
    doc_ids = [int(row["document_id"][-3:]) for row in rows]
    next_num = max(doc_ids) + 1 if len(doc_ids) > 0 else 1

    return f"{base}{next_num:03d}"

def _update_attributes_from_latest_attr(token, latest_attr):
    f = {
        "document_type": int(latest_attr.get("documentType", 0) or 0),
        "doc_id": none_if_blank(latest_attr.get("documentID")),
        "doc_name": none_if_blank(latest_attr.get("documentName")),
        "doc_ver": dver(latest_attr.get("documentVersion", 1.0)),
        "dept": none_if_blank(latest_attr.get("department")),
        "author_id": none_if_blank(latest_attr.get("author_id")),
        "author": none_if_blank(latest_attr.get("author")),
        "approver": none_if_blank(latest_attr.get("approver")),
        "confirmer": none_if_blank(latest_attr.get("confirmer")),
        "chg_reason": none_if_blank(latest_attr.get("reviseReason")),
        "chg_summary": none_if_blank(latest_attr.get("revisePoint")),
        "purpose": none_if_blank(latest_attr.get("documentPurpose")),
    }

    with db() as (conn, cur):
        cur.execute("""
          UPDATE rms_document_attributes
          SET document_type=%s,
              document_id=%s, document_name=%s, document_version=%s,
              department=%s, author_id=%s, author=%s,
              approver=%s, confirmer=%s,
              change_reason=%s, change_summary=%s, purpose=%s
          WHERE document_token=%s
        """, (
            f["document_type"], f["doc_id"], f["doc_name"], f["doc_ver"],
            f["dept"], f["author_id"], f["author"],
            f["approver"], f["confirmer"],
            f["chg_reason"], f["chg_summary"], f["purpose"],
            token,
        ))
        conn.commit()

# Generate word file for user
@bp.post("/generate/word")
def generate_word():
    """
    Accept JSON body {token, attribute, content, reference}：
    - 若有 token：
        1) 用 _build_doc_payload_from_token(token) 把「前幾版 + 目前版」撈出來
        2) 用前端傳進來的最新 attribute/content/reference 覆蓋「最新那一版」
        3) 若為初版且尚無 document_id → 依適用工程前三碼產生一個，寫回 DB
    - 若沒有 token：退回舊行為，直接用 body 的資料產生 Word
    """
    if not request.is_json:
        return jsonify({"ok": False, "error": "JSON body required"}), 400

    data = request.get_json(silent=True) or {}
    data.setdefault("attribute", [])
    data.setdefault("content", [])
    data.setdefault("reference", [])

    token = (data.get("token") or "").strip()
    rms_id = make_rms_id()

    if token:
        try:
            # A) 一開始就從 DB 撈「前幾版 + 最新版」payload
            payload = _build_doc_payload_from_token(token)
        except Exception as e:
            print("[generate_word] _build_doc_payload_from_token error:", e)
            return send_response(404, False, "document not found")

        latest_attr = payload["attribute"][-1]
        latest_attr['documentKey'] = rms_id

        # B) 前端有送 attribute，就覆蓋「最新版」欄位
        if data["attribute"]:
            override_attr = data["attribute"][-1]
            for k, v in override_attr.items():
                latest_attr[k] = v

        # C) content / reference 若前端有傳，就覆蓋 DB 的（只影響最新版）
        if data["content"]:
            payload["content"] = data["content"]
        if data["reference"]:
            payload["reference"] = data["reference"]

        # 4) 計算/更新 document_id + documentKey（只看最新那一版）
        with db(dict_cursor=True) as (conn, cur):
            cur.execute("SELECT document_type, document_id, document_version, attribute, author_id, document_name FROM rms_document_attributes WHERE document_token=%s", (token,))
            r = cur.fetchone()
            if not r:
                return send_response(404, False, "document not found")

            doc_type  = int(r["document_type"] or 0)
            doc_id    = r["document_id"]
            doc_ver   = float(r["document_version"] or 1.0)
            attr_json = jload(r["attribute"], {}) or {}
            author_id = (r.get("author_id") or "").strip()
            doc_name0 = r.get("document_name") or ""

            latest_attr_json = latest_attr.get("attribute") or {}
            attr_json.update(latest_attr_json)

            # 初版且尚無 document_id → 依文件類型決定編碼規則
            if doc_ver == 1.0 and not doc_id:
                if doc_type == 1:
                    style_no = attr_json.get("styleNo") or ""
                    
                    # ★ 1. 優先查詢 rms_document_list 看是否已有此式樣號
                    # clean_style_no = re.sub(r'[A-Za-z]+$', '', style_no)
                    # cur.execute("SELECT document_id, document_version FROM rms_document_list WHERE REGEXP_REPLACE(style_no, '[A-Za-z]+$', '') = %s LIMIT 1", (clean_style_no,))
                    cur.execute("SELECT document_id, document_version FROM rms_document_list WHERE style_no = %s LIMIT 1", (style_no,))
                    exist_row = cur.fetchone()
                    
                    if exist_row and exist_row["document_id"]:
                        # 若找到，直接重用資料庫中的 document_id 與 document_version
                        doc_id = exist_row["document_id"]
                        old_doc_ver = float(exist_row["document_version"]) + 1.0 or 1.0
                        doc_ver = old_doc_ver if doc_ver <= old_doc_ver else doc_ver
                    else:
                        # ★ 2. 若找不到，則跑原本的「全新賦號」邏輯
                        mpn_mode = attr_json.get("MPN_MODE")
                        
                        if not mpn_mode:
                            mpn_prefix = style_no.split("-")[0] if style_no else ""
                            if mpn_prefix:
                                try:
                                    with odb() as cur_o:
                                        cur_o.execute("SELECT MPN_MODE FROM IDBUSER.EIP_MPN_TRACE_DETAIL WHERE MPN LIKE :1", (mpn_prefix + "%",))
                                        o_row = cur_o.fetchone()
                                        if o_row and o_row[0]:
                                            mpn_mode = o_row[0]
                                except Exception as e:
                                    print(f"[generate_word] 查詢 Oracle MPN_MODE 失敗 (MPN: {mpn_prefix}):", e)
                
                        if not mpn_mode:
                            mpn_mode = "MP"
                            
                        doc_id = next_monthly_document_id("W", mpn_mode)
                        # doc_ver 維持為 1.0
                else:
                    # 指示書的編碼邏輯
                    apply_project = (attr_json.get("applyProject") or "").strip()
                    machines = attr_json.get("machines", [])
                    # prefix = (apply_project[:3] or "XXX").upper()
                    # doc_id = next_document_id(prefix)
                    doc_id, doc_ver = _process_instruction_id_ver(apply_project, machines)

            # 更新主表 (把可能變動的 doc_id 與 doc_ver 一併寫入)
            cur.execute("UPDATE rms_document_attributes SET document_id=%s, document_version=%s WHERE document_token=%s", (doc_id, doc_ver, token))
            conn.commit()

        # 5) 把 docID & documentVersion 塞回「最新版 attribute」（在 payload 上）
        latest_attr["documentID"] = doc_id or ""
        latest_attr["documentVersion"] = doc_ver

        if data["attribute"]:
            data["attribute"][-1]["documentID"] = doc_id or ""
            data["attribute"][-1]["documentVersion"] = doc_ver

        # 5.5) 暫存內容（寫回 rms_document_attributes）
        _update_attributes_from_latest_attr(token, latest_attr)

        # 6) 檔名 (確保檔名上的版本號也同步更新)
        try:
            if (doc_type == 0):
                doc_name = _safe_docname(f'{latest_attr.get("documentName")}{doc_ver:.1f}')
            elif (doc_type == 1):
                style_suffix = latest_attr["attribute"].get("styleNo", "").split("-")[-1]
                doc_name = _safe_docname(f'{latest_attr.get("documentName")}{style_suffix} {doc_ver:.1f}')
        except Exception:
            doc_name = "document"

        # 7) 先做 Oracle / snapshot（如果失敗 → 不產 DOCX，直接回錯誤）
        try:
            create_snapshot_and_oracle_row(token=token, rms_id=rms_id, user_emp_no=author_id or "UNKNOWN")
        except Exception as e:
            print("[generate_word] create_snapshot_and_oracle_row FAILED:", e)
            return send_response(500, False, f"EIP 建檔 / 歷史快照失敗，請聯絡系統管理員。詳細訊息：{e}")

        # 8) Oracle + snapshot 都成功後，才產生 Word
        out_path = os.path.join(BASE_DIR, f"{doc_name}.docx")
        
        doc_type_for_word = latest_attr.get("documentType", 0)
        if doc_type_for_word == 1:
            get_docx_without_framework(out_path, payload, "docx-template/SpecificationDocumentv6.docx")
        else:
            get_docx_without_framework(out_path, payload, "docx-template/InstructionDocumentv6.docx")

        # 9) 透過 Header 將 ID 與 Version 回傳給前端
        @after_this_request
        def add_doc_headers(response):
            if doc_id:
                response.headers["X-Document-ID"] = str(doc_id)
            if doc_ver:
                response.headers["X-Document-Version"] = str(doc_ver)
                
            expose_headers = ["X-Document-ID", "X-Document-Version"]
            existing = response.headers.get("Access-Control-Expose-Headers", "")
            
            if existing:
                parts = [x.strip() for x in existing.split(",")]
                for e in expose_headers:
                    if e not in parts:
                        parts.append(e)
                response.headers["Access-Control-Expose-Headers"] = ",".join(parts)
            else:
                response.headers["Access-Control-Expose-Headers"] = ",".join(expose_headers)
                
            return response

        return send_file(
            out_path,
            as_attachment=True,
            download_name=f"{doc_name}.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

# Document preview in final step (for user to check content fill)
@bp.post("/preview/docx")
def preview_docx():
    """
    接收 {token?, attribute?, content?, reference?}：
      - 若有 token：
          1) 先用 _build_doc_payload_from_token(token) → 帶出前幾版 + 目前版
          2) 前端若傳 attribute/content/reference，就覆蓋「最新那一版」及其內容
      - 若無 token：
          保留舊行為，直接用 body 的資料 preview。
    """
    if not request.is_json:
        return jsonify({"ok": False, "error": "JSON body required"}), 400

    data = request.get_json(silent=True) or {}
    data.setdefault("attribute", [])
    data.setdefault("content", [])
    data.setdefault("reference", [])

    token = (data.get("token") or "").strip()

    # -------------------------------------------------------
    # A) 有 token：用 DB + 前幾版 + 前端覆蓋最新版
    # -------------------------------------------------------
    if token:
        try:
            payload = _build_doc_payload_from_token(token)
        except Exception as e:
            print("[preview_docx] _build_doc_payload_from_token error:", e)
            return jsonify({"ok": False, "error": "document not found"}), 404

        latest_attr = payload["attribute"][-1]

        # 前端若有傳 attribute，就覆蓋最新版欄位
        if data["attribute"]:
            override_attr = data["attribute"][-1]
            for k, v in override_attr.items():
                latest_attr[k] = v

        # content/reference 若前端有傳，就覆蓋 DB 的
        if data["content"]:
            payload["content"] = data["content"]
        if data["reference"]:
            payload["reference"] = data["reference"]

        base_payload = payload

    else:
        # ---------------------------------------------------
        # B) 沒 token：維持舊有行為，直接用 body
        # ---------------------------------------------------
        base_payload = data


    # 產生一個 payload_id，當暫存檔名的一部分
    payload_id = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    # 取檔案名稱：優先用「最新版」的文件名稱 / 文管編號
    try:
        if base_payload["attribute"]:
            attr_last = base_payload["attribute"][-1]
        else:
            attr_last = {}
        raw_name = attr_last.get("documentName") or attr_last.get("documentID") or payload_id
        doc_name = _safe_docname(raw_name)
    except Exception:
        doc_name = payload_id

    preview_dir = os.path.join(BASE_DIR, "_preview")
    os.makedirs(preview_dir, exist_ok=True)

    out_path = os.path.join(preview_dir, f"{doc_name}-{payload_id}.docx")

    # 產生 Word → 用 base_payload，而不是 data
    attr_list = base_payload.get("attribute") or []
    doc_type = 0
    if attr_list:
        doc_type = attr_list[-1].get("documentType", 0)

    if base_payload.get("attribute"):
        latest_attr_ref = base_payload["attribute"][-1]
        if "documentVersion" in latest_attr_ref:
            try:
                latest_attr_ref["documentVersion"] = float(latest_attr_ref["documentVersion"])
            except (ValueError, TypeError):
                latest_attr_ref["documentVersion"] = 1.0

    if doc_type == 1:
        get_docx(out_path, base_payload, "docx-template/SpecificationDocument.docx")
    else:
        get_docx(out_path, base_payload, "docx-template/InstructionDocument.docx")

    @after_this_request
    def remove_file(response):
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except Exception as e:
            print("[preview_docx] remove temp file error:", e)
        return response

    return send_file(
        out_path,
        as_attachment=False,
        download_name=f"{doc_name}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

def _build_doc_payload_from_docid(docid):
    if docid == None or docid == "":
        return []
    
    attributes = []
    try:
        with db(dict_cursor=True) as (conn, cur):
            sql = """
                SELECT document_version, issue_date, approver, confirmer, change_reason, change_summary FROM rms_document_attributes
                WHERE document_id = %s ORDER BY document_version DESC LIMIT 3;
            """
            cur.execute(sql, (docid,),)
            rows = cur.fetchall()

            if len(rows) <= 1:
                return []
                
            return list(rows[1:])

    except Exception as e:
        print(f"Some error occur: {e}")
        return []

def _process_instruction_id_ver(applyProject, machines):
    if applyProject == None or applyProject == "" or len(applyProject) < 3:
        print("no apply project")
        return None, None

    try: 
        with db(dict_cursor=True) as (conn, cur):
            cur.execute(f"SELECT DISTINCT document_id, document_version FROM rms_document_machine WHERE machine_id IN ({placeholder(machines)})", machines)
            rows = cur.fetchall()

            if len(rows) > 1:
                return next_document_id(applyProject[:3]), 1.0
            elif len(rows) == 1:
                return rows[0]["document_id"], float(rows[0]["document_version"])
    
    except Exception as e:
        print(f"訪問資料庫失敗: {e} => 因此返回空值")
        return None, None
    
    return next_document_id(applyProject[:3]), 1.0

def _process_specification_id_ver(mpn_mode, style_no):
    # Try get Past document information（同 style_no 沿用同 document_id）
    try:
        with db(dict_cursor=True) as (conn, cur):   # 需 dict cursor 才能用 exist_row['document_id']
            cur.execute("SELECT document_id, document_version FROM rms_document_list WHERE style_no = %s LIMIT 1", (style_no,))
            exist_row = cur.fetchone()
            if exist_row and exist_row.get("document_id"):
                return exist_row["document_id"], exist_row["document_version"]   # 有舊號 → 沿用
            # 查無此 style_no → 往下重新配號

    except Exception as e:
        print(f"訪問資料庫失敗: {e} => 因此重新配號")
    
    # Search style to get mpn mode
    if not mpn_mode:
        try:
            with odb() as cur_o:
                cur_o.execute("SELECT MPN_MODE FROM IDBUSER.EIP_MPN_TRACE_DETAIL WHERE MPN LIKE :1", (style_no.split("-")[0] + "%",))
                mpn_mode = cur_o.fetchone()
                
        except Exception as e:
            print(f"[generate_word] 查詢 Oracle MPN_MODE 失敗 (MPN: {style_no}):", e)
    
    # Set default NPI mode and get document id
    mpn_mode = "NPI" if not mpn_mode else mpn_mode
    return next_monthly_document_id("W", mpn_mode), 1.0

def _create_snapshot_and_oracle_row(token, rms_id, doc_info):
    """
    1) 從 MySQL 撈出目前 token 的 document_row / blocks_rows / references_rows
    2) 先在 Oracle.IDBUSER.RMS_DCC2EIP 新增 RMS_* 一筆
    3) 再寫入 sfdb4070.rms_document_snapshots (meta) + rms_document_snapshot_payloads (JSON)
    """
    attribute = doc_info['attribute'][0]

    # 新樹結構：doc_info['content'] = [{step_type, children}]；step2 的 ct=4 子節點各自帶 metadata.programs
    program_codes_rows = set()
    for step in doc_info['content']:
        if step.get('step_type') == 2:
            for node in step.get('children', []):
                for program in (node.get('metadata') or {}).get('programs', []):
                    program_codes_rows.add(program['programCode'])

    # --- 2) 先寫 Oracle.RMS_DCC2EIP ---
    with odb() as cur_o:
        cur_o.execute("INSERT INTO IDBUSER.RMS_DCC2EIP (RMS_ID, RMS_DCCNO, RMS_VER, RMS_DCCNAME, RMS_INSDT) VALUES (:1, :2, :3, :4, :5)", (rms_id, attribute['document_id'], attribute['document_version'], attribute['document_name'], datetime.datetime.now()))
        cur_o.connection.commit()

    # --- 3) 從 MySQL 撈「目前 DB 狀態」當快照內容 ---
    #     ⚠️ 必須與簽核快照同格式（reader _build_payload_for_docx_from_snapshot 預期）：
    #        document_row = 單一 rms_document_attributes row（dict，非 list）
    #        blocks_rows  = 攤平的 rms_block_content rows（reader 會再 build_tree），非已建好的樹
    with db(dict_cursor=True) as (conn, cur):
        cur.execute("SELECT * FROM rms_document_attributes WHERE document_token=%s", (token,))
        doc_row = cur.fetchone()
        cur.execute("SELECT * FROM rms_block_content WHERE document_token=%s", (token,))
        blocks_rows = cur.fetchall() or []
        cur.execute("SELECT * FROM rms_references WHERE document_token=%s", (token,))
        ref_rows = cur.fetchall() or []

    doc_row_json  = _normalize_for_json(doc_row)
    blocks_json   = _normalize_for_json(blocks_rows)
    refs_json     = _normalize_for_json(ref_rows)
    programs_json = list(program_codes_rows)

    try:
        doc_row_str     = jdump(doc_row_json)
        blocks_str      = jdump(blocks_json)
        refs_str        = jdump(refs_json)
        programs_str    = jdump(programs_json)

    except TypeError as e:
        print("[snapshot DEBUG] json dump failed:", e)
        raise

    with db(dict_cursor=True) as (conn, cur):
        # 3-1) 先插入輕量的 snapshots（拿到 snapshot_id）
        cur.execute("INSERT INTO rms_document_snapshots (document_token, rms_id, document_id, document_version, document_name, created_by) VALUES (%s,%s,%s,%s,%s,%s)", (token, rms_id, attribute['document_id'], attribute['document_version'], attribute['document_name'], attribute['author_id']))
        snapshot_id = cur.lastrowid

        # 3-2) 再插入 payload（含 form_attributes：凍結彩色標題/目的樣式）
        form_attr_str = jdump(_load_form_attributes(cur, token))
        cur.execute("INSERT INTO rms_document_snapshot_payloads (snapshot_id, document_row, blocks_rows, references_rows, program_codes_rows, form_attributes) VALUES (%s,%s,%s,%s,%s,%s)", (snapshot_id, doc_row_str, blocks_str, refs_str, programs_str, form_attr_str))

        conn.commit()

@bp.post("/preview/docx_")
def preview_docx_():
    data = request.get_json(silent=True) or {}
    data.setdefault("attribute", [])
    data.setdefault("content", [])
    data.setdefault("reference", [])
    token = data.get("token", "")
    doc_type = data['attribute'][0]["document_type"]

    if token == "":
        return jsonify({"ok": False, "error": "Document token is None"}), 400

    # Get / Assign document_id (if attribute not include document_id that means this is a first versiond document.)
    attributes = [data['attribute'][0]]
    if attributes[0].get("document_id") != None:
        payload = _build_doc_payload_from_docid(attributes[0]['document_id'])
        attributes += payload[1:]
        
    payload = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    out_path = os.path.join(f"{BASE_DIR}", "_preview", f"{attributes[0]['document_name']}-{payload}.docx")
    # 前端送的是 tree（[{step_type, children}]，= save 的 tree 結構）；renderer 內部仍讀 data["content"]
    get_docx_(out_path, {"attribute": attributes, "content": data.get("tree") or [], "reference": data["reference"], "form_attribute": _resolve_form_attribute(data, token)}, f"docx-template/{'SpecificationDocument' if doc_type == 1 else 'InstructionDocument'}.docx")

    return send_file(
        out_path,
        as_attachment=False,
        download_name=f"{attributes[0]['document_name'] if attributes[0]['document_name'] != '' else payload}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

@bp.post("/generate/word_")
def generate_word_():
    data = request.get_json(silent=True) or {}
    data.setdefault("attribute", [])
    data.setdefault("content", [])
    data.setdefault("reference", [])
    token = data.get("token", "")
    doc_type = data['attribute'][0]["document_type"]
    rms_id = make_rms_id()

    if token == "":
        return jsonify({"ok": False, "error": "Document token is None"}), 400

    # Get previous payload by document id
    attributes = [data['attribute'][0]]
    document_id = attributes[0]["document_id"]
    document_version = attributes[0]["document_version"]
    # 有值才當「既有文件」；None / "" / 缺欄一律視為新文件去配號（式樣書前端送 null 會被 != "" 誤判，導致沒配號、header 拿不到 document_id）
    if document_id:
        payload = _build_doc_payload_from_docid(document_id)
        attributes += payload[1:]

    # Assign document_id (if attribute not include document_id that means this is a first version document.)
    else:
        if doc_type == 0:
            document_id, document_version = _process_instruction_id_ver(attributes[0]["attribute"].get("applyProject"), attributes[0]["attribute"].get("machines", []))
        elif doc_type == 1:
            document_id, document_version = _process_specification_id_ver(attributes[0]["attribute"].get("mpnMode"), attributes[0]["attribute"].get("styleNo"))
        attributes[0]["document_id"] = document_id
        attributes[0]["document_version"] = str(document_version)
        print(f"document_id: {document_id}, document_version: {document_version}")

    # Generate Word
    payload = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    out_path = os.path.join(f"{BASE_DIR}", f"{attributes[0]['document_name']}-{payload}.docx")
    get_docx_without_framework_(out_path, {"attribute": attributes, "content": data.get("tree") or [], "reference": data["reference"], "form_attribute": _resolve_form_attribute(data, token)}, f"docx-template/{'SpecificationDocumentv6' if doc_type == 1 else 'InstructionDocumentv6'}.docx")

    # Create snapshot for download content and insert information to oracle database
    try:
        with db() as (conn, cur):
            # 更新 document_id，並把 status 提升為 3 (已下載)。
            # 僅在 status 為 0 (空文件) 或 1 (草稿) 時提升；2 (已公告) 維持不變，避免覆蓋公告狀態。
            cur.execute("UPDATE rms_document_attributes SET document_id = %s, status = CASE WHEN COALESCE(status, 0) IN (0, 1) THEN 3 ELSE status END WHERE document_token = %s", (attributes[0]["document_id"], token,))
            conn.commit()

        print(token, rms_id, attributes[0]["author_id"])
        _create_snapshot_and_oracle_row(token = token, rms_id = rms_id, doc_info = {"attribute": attributes, "content": data.get("tree") or [], "reference": data["reference"]})

    except Exception as e:
        print("[generate_word] _create_snapshot_and_oracle_row FAILED:", e)
        return send_response(500, False, f"EIP 建檔 / 歷史快照失敗，請聯絡系統管理員。詳細訊息：{e}")
    
    # Add document id and version information to frontend
    @after_this_request
    def add_docid_header(response):
        if document_id:
            response.headers["X-Document-ID"] = document_id
            response.headers["X-Document-Version"] = document_version
        existing = response.headers.get("Access-Control-Expose-Headers", "")
        expose = "X-Document-ID"
        if existing:
            if expose not in existing:
                response.headers["Access-Control-Expose-Headers"] = existing + "," + expose
        else:
            response.headers["Access-Control-Expose-Headers"] = expose
        return response

    # Send Word document
    return send_file(
        out_path,
        as_attachment=True,
        download_name=f"{attributes[0]['document_name'] if attributes[0]['document_name'] != '' else payload}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


def _build_payload_for_docx_from_snapshot(snap_row):
    token   = snap_row["document_token"]
    snap_id = snap_row["snapshot_id"]

    # 制定日期：用快照建立時間（= 當初產生文件的日期），讓預覽顯示原始日期而非 now()
    _created = snap_row.get("created_at")
    if hasattr(_created, "strftime"):
        render_date = _created.strftime("%Y/%m/%d")
    elif _created:
        render_date = str(_created)[:10].replace("-", "/")
    else:
        render_date = None

    row, info = db_data_fetch(f"SELECT document_row, blocks_rows, references_rows, form_attributes FROM rms_document_snapshot_payloads WHERE snapshot_id = '{snap_id}'", fetch_one = True)

    if info != "Success":
        raise RuntimeError(f"snapshot payload not found for snapshot_id={snap_id}")

    doc_row   = _normalize_metadata(row[0]) or {}
    blocks_rs = _normalize_metadata(row[1]) or []
    refs_rs   = _normalize_metadata(row[2]) or []
    form_attr = _normalize_metadata(row[3]) or {}   # 凍結的彩色標題/目的樣式（舊快照無此欄 → {}）

    # 相容舊版 download 快照：_create_snapshot_and_oracle_row 曾把 document_row 存成 list、blocks 存成「已建好的樹」
    if isinstance(doc_row, list):
        doc_row = (doc_row[0] if doc_row else {}) or {}
    blocks_is_tree = bool(blocks_rs) and isinstance(blocks_rs[0], dict) and ("children" in blocks_rs[0]) and ("content_id" not in blocks_rs[0])

    # ---------- 1.1 歷史版本（已經是 yyyy/mm/dd，就保留你現在的實作） ----------
    attrs: list[dict] = []

    prev_token = doc_row.get("previous_document_token")
    hops = 0
    seen = set()

    if prev_token:
        with db(dict_cursor=True) as (conn, cur):
            while prev_token and prev_token not in seen and hops < 2:
                seen.add(prev_token)
                cur.execute(
                    "SELECT * FROM rms_document_attributes WHERE document_token=%s",
                    (prev_token,),
                )
                r = cur.fetchone()
                if not r:
                    break

                attr_json = jload(r.get("attribute"), {}) or {}
                issue = r.get("issue_date")
                if hasattr(issue, "strftime"):
                    # ✅ 歷史版本：yyyy/mm/dd
                    issue_str = issue.strftime("%Y/%m/%d")
                else:
                    issue_str = issue or ""

                attrs.append({
                    "document_type":    r.get("document_type") or 0,
                    "document_id":      r.get("document_id") or "",
                    "document_name":    r.get("document_name") or "",
                    "document_version": float(r.get("document_version") or 1.0),
                    "attribute":        attr_json,
                    "department":       r.get("department") or "",
                    "author_id":        r.get("author_id") or "",
                    "author":           r.get("author") or "",
                    "approver":         r.get("approver") or "",
                    "confirmer":        r.get("confirmer") or "",
                    "issue_date":       issue_str,
                    "change_reason":    r.get("change_reason") or "",
                    "change_summary":   r.get("change_summary") or "",
                    "purpose":          r.get("purpose") or "",
                    "documentPurpose":  r.get("purpose") or "",
                })

                prev_token = r.get("previous_document_token")
                hops += 1

    attrs.reverse()

    # ---------- 1.2 目前這一版（snapshot 對應的版本） ----------
    issue = doc_row.get("issue_date")

    if isinstance(issue, str):
        # 優先試著當 ISO 解析（含 T 的情況）
        try:
            dt = datetime.datetime.fromisoformat(issue)
            issue_str = dt.strftime("%Y/%m/%d")
        except Exception:
            # 退而求其次：直接取前 10 碼，轉 yyyy/mm/dd
            # 支援 "2025-12-03 09:03:28" 或 "2025-12-03T09:03:28"
            s = issue[:10]
            issue_str = s.replace("-", "/")
    elif hasattr(issue, "strftime"):
        # MySQL datetime 物件
        issue_str = issue.strftime("%Y/%m/%d")
    else:
        issue_str = ""

    # 制定日期：優先用快照凍結的 issue_date（真正的制定日）；舊快照沒有此欄時，仍退回快照建立時間
    if issue_str:
        render_date = issue_str

    attr_json = jload(doc_row.get("attribute"), {}) or {}

    latest_form = {
        "document_type":    doc_row.get("document_type") or 0,
        "document_id":      doc_row.get("document_id") or "",
        "document_name":    doc_row.get("document_name") or "",
        "document_version": float(doc_row.get("document_version") or 1.0),
        "attribute":        attr_json,
        "department":       doc_row.get("department") or "",
        "author_id":        doc_row.get("author_id") or "",
        "author":           doc_row.get("author") or "",
        "approver":         doc_row.get("approver") or "",
        "confirmer":        doc_row.get("confirmer") or "",
        "purpose":          doc_row.get("purpose") or "",
        "documentPurpose":  doc_row.get("purpose") or "",
        "change_reason":    doc_row.get("change_reason") or "",
        "change_summary":   doc_row.get("change_summary") or "",
        "issue_date":       issue_str,
        "previous_document_token": doc_row.get("previous_document_token") or "",
    }

    attrs.append(latest_form)

    # ---------- 2) blocks：組回巢狀樹（spec §7.3 / §10）----------
    # 每筆 snapshot block row 的 JSON 欄位先解析；v1(舊 tier/sub) 先轉新階層，v2 直接用
    def _prep_block_row(r):
        return {
            "content_id": r.get("content_id"),
            "step_type": r.get("step_type"),
            "parent_id": r.get("parent_id"),
            "sort_order": r.get("sort_order"),
            "depth": r.get("depth"),
            "tier_no": r.get("tier_no"),
            "sub_no": r.get("sub_no"),
            "content_type": r.get("content_type"),
            "header_text": r.get("header_text"),
            "header_json": _normalize_metadata(r.get("header_json")),
            "content_text": _normalize_metadata(r.get("content_text")),
            "content_json": _normalize_metadata(r.get("content_json")),
            "table_text": _normalize_metadata(r.get("table_text")),
            "table_json": _normalize_metadata(r.get("table_json")),
            "files": _normalize_metadata(r.get("files")) or [],
            "metadata": _normalize_metadata(r.get("metadata")) or {},
        }

    if blocks_is_tree:
        content_items = blocks_rs   # 舊 download 快照：blocks 已是樹 [{step_type, children}]，直接用
    else:
        prepared = [_prep_block_row(r) for r in blocks_rs]
        # v1 舊快照（含 tier_no、無 parent_id）→ lazy 轉新階層；v2 直接用（spec §10.3）
        if prepared and ("tier_no" in blocks_rs[0]) and ("parent_id" not in blocks_rs[0]):
            prepared = normalize_legacy_blocks(prepared)
        content_items = build_tree(prepared)   # [{step_type, children}]

    # ---------- 3) references：只用 snapshot 的 refs_rs ----------
    references = []
    for r in refs_rs:
        try:
            ref_type = int(r.get("refer_type") or 0)
        except (TypeError, ValueError):
            ref_type = 0

        references.append({
            "refer_type": ref_type,
            "refer_document": r.get("refer_document"),
            "refer_document_name": r.get("refer_document_name"),
        })

    return {
        "token": token,
        "attribute": attrs,           # 🔑 不再只有一個 form，而是 [舊版..., 最新版]
        "content": content_items,
        "reference": references,
        "form_attribute": form_attr,  # 凍結的 form_attribute（彩色 目的/文件名/適用工程）
        "render_date": render_date,   # 制定日期：當初產生文件的日期（快照建立時間），預覽用
    }

# Document preview in signed document
@bp.get("/preview/<token>")
def preview_docx_from_snapshot(token):
    rms_id = request.args.get("rms_id")

    with db(dict_cursor=True) as (conn, cur):
        if rms_id:
            cur.execute("""
                SELECT *
                FROM rms_document_snapshots
                WHERE document_token = %s AND rms_id = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (token, rms_id))
        else:
            cur.execute("""
                SELECT *
                FROM rms_document_snapshots
                WHERE document_token = %s
                ORDER BY created_at DESC
                LIMIT 1
            """, (token,))

        snap = cur.fetchone()

    if not snap:
        return jsonify({"ok": False, "error": "snapshot not found"}), 404

    # 🔹 這裡的 snap 是「輕量 meta」，真正的 JSON 在 _build_payload_for_docx_from_snapshot 裡讀
    payload = _build_payload_for_docx_from_snapshot(snap)

    # 取文件類型 & 名稱
    attr_list = payload.get("attribute") or []
    if attr_list:
        last_attr = attr_list[-1]
        print(last_attr)
        doc_type = last_attr.get("documentType", 0)
        raw_name = last_attr.get("documentName") or last_attr.get("documentID") or "snapshot"
    else:
        doc_type = 0
        raw_name = "snapshot"
    doc_name = _safe_docname(raw_name)

    preview_dir = os.path.join(BASE_DIR, "_preview")
    os.makedirs(preview_dir, exist_ok=True)
    out_path = os.path.join(preview_dir, f"{doc_name}-{uuid.uuid4().hex[:8]}.docx")

    if doc_type == 1:
        get_docx_(out_path, payload, "docx-template/SpecificationDocument.docx")
    else:
        get_docx_(out_path, payload, "docx-template/InstructionDocument.docx")

    @after_this_request
    def remove_file(response):
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except Exception as e:
            print("[preview_docx_from_snapshot] remove temp file error:", e)
        return response

    return send_file(
        out_path,
        as_attachment=False,
        download_name=f"{doc_name}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

# ----------------------------------------------------------------------------------
def build_prefix(spec_code: str) -> str:
    """
    spec_code: e.g. "R221-01"
    回傳前 8 碼，例如: "RER22101"
    """
    sc = (spec_code or "").replace("-", "")[:6]  # R221-01 → R22101
    return f"RE{sc:0<6}"                         # 不足補 0

@bp.post("/program-codes/allocate")
def allocate_program_code():
    """
    body: { specCode, document_token, partNo (選填) }
    回傳: { specCode, programCode, prefix, serial, partNo }
    """
    body = request.get_json(silent=True) or {}
    spec_code = (body.get("specCode") or "").strip()
    document_token = (body.get("document_token") or "").strip()
    part_no = (body.get("partNo") or "").strip()

    # 1) 拿掉 partNo 的必填檢查，兼容舊版 (指示書呼叫時不會帶 partNo)
    if not spec_code or not document_token:
        return send_response(400, False, "specCode 與 document_token 為必填", None)

    prefix = build_prefix(spec_code)

    with db(dict_cursor=True) as (conn, cur):
        # 先看有沒有舊的釋放號碼可以重用（status=9）
        cur.execute("SELECT id, serial_no FROM rms_program_code WHERE spec_code = %s AND status = 9 ORDER BY serial_no ASC LIMIT 1 FOR UPDATE", (spec_code,))
        row = cur.fetchone()

        if row:
            # 重用舊號碼
            serial = row["serial_no"]
            base_program_code = f"{prefix}{serial:03d}"
            
            # 2) 判斷是否有 partNo，決定最終的 program_code 格式
            full_program_code = f"{part_no}-{base_program_code}" if part_no else base_program_code
            
            # 更新狀態並覆寫 program_code (確保重用時格式正確)
            cur.execute(
                "UPDATE rms_program_code SET status = 0, document_token = %s, program_code = %s WHERE id = %s", 
                (document_token, full_program_code, row["id"])
            )
        else:
            # 沒有可重用 → 取最大 serial_no + 1
            cur.execute("SELECT MAX(serial_no) AS max_serial FROM rms_program_code WHERE spec_code = %s FOR UPDATE", (spec_code,))
            r = cur.fetchone()
            max_serial = r["max_serial"] or 0
            serial = max_serial + 1
            
            base_program_code = f"{prefix}{serial:03d}"
            
            # 2) 判斷是否有 partNo，決定最終的 program_code 格式
            full_program_code = f"{part_no}-{base_program_code}" if part_no else base_program_code

            # 寫入資料表
            cur.execute(
                "INSERT INTO rms_program_code (spec_code, serial_no, program_code, document_token, status) VALUES (%s, %s, %s, %s, 0)", 
                (spec_code, serial, full_program_code, document_token)
            )

    print(f"full_program_code: {full_program_code}")
    data = {
        "specCode": spec_code, 
        "partNo": part_no,
        "programCode": full_program_code, 
        "prefix": prefix, 
        "serial": serial
    }
    return send_response(200, True, "程式號碼配號成功", data)

@bp.post("/program-codes/release")
def release_program_code():
    """
    body: { programCode }
    將 status 改成 9，document_token 清空 → 之後可重用
    """
    body = request.get_json(silent=True) or {}
    program_code = (body.get("programCode") or "").strip()

    if not program_code:
        return send_response(400, False, "programCode 為必填", None)

    with db(dict_cursor=True) as (conn, cur):
        cur.execute("UPDATE rms_program_code SET status = 9, document_token = NULL WHERE program_code = %s", (program_code,))
        conn.commit()

    return send_response(200, True, "程式號碼已釋放", {"programCode": program_code})

@bp.post("/program-codes/release-by-document")
def release_program_codes_by_document():
    """
    body: { document_token }
    將該文件底下 status=0(reserved) 的程式號碼全部改成 9 並清空 document_token
    用在：刪除草稿 / 作廢文件時
    """
    body = request.get_json(silent=True) or {}
    document_token = (body.get("document_token") or "").strip()

    if not document_token:
        return send_response(400, False, "document_token 為必填", None)

    with db(dict_cursor=True) as (conn, cur):
        cur.execute("UPDATE rms_program_code SET status = 9, document_token = NULL WHERE document_token = %s AND status = 0", (document_token,))
        conn.commit()

    return send_response(200, True, "程式號碼已釋放", {"document_token": document_token})

# ===== helper function ===== #
# @bp.post("/parameters/copy-spec-source")
# def copy_spec_source_mcr():
#     """
#     處理需求 7: 從 Specification Document 複製參數
#     """
#     body = request.get_json(silent=True) or {}
#     program_code = (body.get("program_code") or "").strip()

#     if not program_code:
#         return send_response(400, False, "請輸入程式代碼", None)

#     try:
#         # -------------------------------------------------------
#         # STEP 1: 找出對應的 Source Block [需求 7 & 7.4]
#         # -------------------------------------------------------
#         with db() as (conn, cur):
#             sql = """
#             SELECT 
#                 bc.content_json,
#                 bc.content_text,  -- 用於解析當下的 PMS 結構
#                 bc.metadata,
#                 d.document_token
#             FROM sfdb4070.rms_block_content bc
#             JOIN sfdb4070.rms_document_attributes d ON d.document_token = bc.document_token
#             WHERE d.status = 2            -- [需求 7] status = 2 (已簽核)
#               AND d.document_type = 1     -- [需求 7] document_type = 1 (Spec Doc)
#               AND bc.step_type = 5        -- [需求 7.4] step_type = 5
#               AND bc.sub_no = 0           -- [需求 7.4] sub_no = 0
#               AND JSON_UNQUOTE(JSON_EXTRACT(bc.metadata, '$.kind')) = 'mcr-parameter'
#               AND JSON_SEARCH(bc.metadata, 'one', %s, NULL, '$.programs[*].programCode') IS NOT NULL
#             LIMIT 1
#             """
#             cur.execute(sql, (program_code,))
#             row = cur.fetchone()

#             if not row:
#                 return send_response(200, False, "查無此代碼或文件不符合複製條件 (需為已簽核規格書)", None)

#             content_json_str, content_text_str, meta_str, doc_token = row
            
#             meta = json.loads(meta_str) if meta_str else {}
#             machine_code = meta.get("machine") or ""
#             group_code = meta.get("machineGroup") or ""

#             if not machine_code:
#                 return send_response(200, False, "來源資料異常：無機台資訊", None)

#             # -------------------------------------------------------
#             # STEP 2: [需求 7.1 & 7.3] PMS 比對
#             # -------------------------------------------------------
            
#             # 2.1 取得 Oracle 目前最新的 PMS
#             # [需求 7.1] PARAM_COMPARE='Y' AND SET_ATTRIBUTE='Y'
#             current_pms_signature = set()
#             try:
#                 with odb() as ora:
#                     ora.execute("""
#                         SELECT TRIM(SLOT_NAME), TRIM(PARAMETER_DESC)
#                         FROM IDBUSER.RMS_FLEX_PMS
#                         WHERE MACHINE_CODE = :m 
#                           AND NVL(PARAM_COMPARE, 'N') = 'Y' 
#                           AND NVL(SET_ATTRIBUTE, 'N') = 'Y'
#                     """, {"m": machine_code})
#                     for r in ora.fetchall():
#                         # [需求 7.3] 比較 SLOT_NAME 與 PARAMETER_DESC
#                         current_pms_signature.add((r[0], r[1]))
#             except Exception as e:
#                 print(f"[PMS Check] Oracle Error: {e}")
#                 return send_response(400, False, "PMS 驗證失敗：無法連接 MES", None)

#             # 2.2 解析 Source Block 的 PMS 結構 (從 content_text)
#             source_pms_signature = set()
#             try:
#                 # content_text 格式範例: [["Slot","Param",...], ["SlotA","ParamA",...]]
#                 text_arr = json.loads(content_text_str) if content_text_str else []
                
#                 # 跳過 Header (第一列)
#                 if len(text_arr) > 1:
#                     for row_data in text_arr[1:]:
#                         if len(row_data) >= 2:
#                             slot = str(row_data[0]).strip()
#                             # 需注意：前端表格中的 Parameter Desc 可能包含 "(單位)"
#                             # 如果 Oracle 的 DESC 沒有單位，這裡比對會失敗。
#                             # 建議：先嘗試比對 Slot Name，這最準確且不易受單位顯示影響
#                             # [需求 7.3] 若要嚴格比對 Desc，需確保格式一致
#                             # 這裡我們先採用 Slot Name 比對作為主要依據，因為這是硬體結構
#                             if slot:
#                                 source_pms_signature.add(slot)
#             except Exception as e:
#                 print(f"[PMS Check] Parse JSON Error: {e}")

#             # 2.3 執行比對
#             # 為了避免單位括號造成的誤判，我們這裡主要比對 Slot 是否一致
#             current_slots = {k[0] for k in current_pms_signature}
            
#             # 如果 Slot 集合不一致，視為 PMS 變更
#             if source_pms_signature != current_slots:
#                  return send_response(200, False, "PMS版本不符", {
#                     "message": f"機台 PMS 設定已變更，無法複製。\n(來源 Slot 與目前 MES 設定不符)"
#                 })

#             # -------------------------------------------------------
#             # STEP 3: 回傳資料
#             # -------------------------------------------------------
#             return send_response(200, True, "複製成功", {
#                 "blocks": {
#                     "content_json": json.loads(content_json_str) if content_json_str else None,
#                     "machine": machine_code,
#                     "machineGroup": group_code,
#                     # 注意：我們不回傳 programCode，因為前端要自己配新的 (需求 7.5)
#                 }
#             })

#     except Exception as e:
#         print(f"[ERROR] copy_spec_source: {e}")
#         return send_response(500, False, "系統錯誤", {"message": str(e)})

@bp.post("/parameters/copy-spec-source")
def copy_spec_source_mcr():
    """
        1. 查找有無該 program code 的 document (沒有直接回傳查無資料)
        2. 取得文檔中的參數以及目前最新的參數
        3. 比對製造參數"相同"以及"不同"的處理 (若機台未交集且最新參數與過去文檔參數有所不同 => 回傳製造參數不一致)
        4. 新增/刪減 PMS 點位與條件參數 => 回傳 {"param_json", "source_programs", "add_params", "del_params"}
            param_json format: list<list<str>>
            source_programs format: json
            add_params format: list<str>
            del_params format: list<str>
    """
    body = request.get_json(silent=True) or {}
    program_code = (body.get("program_code") or "").strip()
    machines = body.get("machines")

    if not program_code:
        return send_response(400, False, "請輸入程式代碼", None)

    program_code = program_code.split("-")[-1] if "-" in program_code else program_code

     # Query program code from code table
    try:
        sql = f"""
            SELECT rbc.content_text, rbc.metadata FROM rms_program_code AS rpc
            JOIN rms_block_content AS rbc ON rbc.document_token = rpc.document_token
            WHERE rpc.program_code = '{program_code}' AND JSON_CONTAINS(rbc.metadata->'$.programs', JSON_OBJECT('programCode', '{program_code}'));
        """
        with db() as (conn, cur):
            cur.execute(sql)
            content_info = cur.fetchone()

    except Exception as e:
        print("查詢 program code 失敗。")
        return send_response(500, False, "系統錯誤", {"message": str(e)})
    
    if content_info == None or len(content_info) == 0:
        return send_response(401, True, "查無參數代碼", {"message": "資料庫無該參數代碼對應的已簽核文件"})
    
    table_info = content_info[0]
    document_machines = json.loads(content_info[1]).get("machines")
    intersection_machines = list(set(machines) & set(document_machines))
    base_machine = intersection_machines[0] if intersection_machines else machines[0]

    # 2-1. Fetch latest PMS data from document PMS data
    machine_slots = []
    try:
        with odb(db_alias = "machine_db") as cur:
            cur.execute(f"SELECT SLOT_NAME, PARAMETER_DESC, UNIT, SET_ATTRIBUTE FROM SAJET.FLEX_PMS WHERE MACHINE_CODE = '{base_machine}' AND PARAM_COMPARE = 'Y' AND SET_ATTRIBUTE = 'Y' ORDER BY PMS_ID")
            machine_slots = cur.fetchall()
    except Exception as e:
        return send_response(500, False, "系統錯誤", {"message": info})
    
    target_pms_header = ['項次', '槽體', '管理項目', '規格下限(OOS-)', '操作下限(OOC-)', '設定值', '操作上限(OOC+)', '規格上限(OOS+)', '參數下放', '說明']
    target_pms_slots = set([f"{slot_info[0]}-{slot_info[1]}" + ("(%s)" % slot_info[2] if slot_info[2] != None and len(slot_info[2]) > 0 else "") for slot_info in machine_slots])
    
    source_pms_table = json.loads(table_info)

    source_pms_slots = {f"{slot_info[1]}-{slot_info[2]}": slot_info for slot_info in source_pms_table[1:]}
    add_pms = target_pms_slots - set(source_pms_slots.keys())
    del_pms = set(source_pms_slots.keys()) - target_pms_slots

    if (not intersection_machines and (add_pms or del_pms)):
        print("機台不符合: 此機台條件參數與查詢代碼不符")
        return send_response(402, True, "機台不符合", {"message": "此機台製造參數與查詢代碼不符"})
    
    # 4. Match PMS parameter
    target_pms_rows = [target_pms_header]
    for index, slot_info in enumerate(machine_slots):
        slot_key = f"{slot_info[0]}-{slot_info[1]}" + ("(%s)" % slot_info[2] if slot_info[2] != None and len(slot_info[2]) > 0 else "")
        unit = "(%s)" % slot_info[2] if slot_info[2] != None and len(slot_info[2]) > 0 else ""
        rowData = [f"{index}", slot_info[0], slot_info[1] + unit, "", "", "", "", "", "Y", ""] if source_pms_slots.get(slot_key) == None else [f"{index + 1}"] + source_pms_slots.get(slot_key)[1:]
        target_pms_rows.append(rowData)
        
    info = {"param_array": target_pms_rows, "metadata": content_info[1], "add_params": list(add_pms), "del_params": list(del_pms)}
    return send_response(200, True, "複製成功", {"blocks": info})
    
PMS_PREFIX = "(NVL(PARAM_COMPARE, 'N') = 'Y' AND NVL(SET_ATTRIBUTE, 'N') = 'Y')"
@bp.post("/parameters/copy-source")
def copy_source_mcr():
    '''
        1. 查找有無該 program code 的 document (沒有直接回傳查無資料)
        2. 取得文檔中的參數以及目前最新的參數
        3. 比對條件參數"相同"以及"不同"的處理 (若機台未交集且最新參數與過去文檔參數有所不同 => 回傳條件參數不一致)
        4. 比對製造參數"相同"以及"不同"的處理 (若機台未交集且最新參數與過去文檔參數有所不同 => 回傳製造參數不一致)
        5. 新增/刪減 PMS 點位與條件參數 => 回傳 {"param_json", "cond_json", "source_programs", "add_params", "del_params", "add_conds", "del_conds"}
            param_json format: list<list<str>>
            cond_json format: list<list<str>>
            source_programs format: json
            add_params format: list<str>
            del_params format: list<str>
            add_conds format: list<str>
            del_conds format: list<str>
    '''
    body = request.get_json(silent=True) or {}
    program_code = body.get("program_code")
    machines = body.get("machines")
    
    # Query program code from code table
    try:
        sql = f"SELECT rda.document_token, rda.attribute FROM rms_program_code rpc JOIN rms_document_attributes rda ON rda.document_token = rpc.document_token WHERE rda.status = 2 AND rpc.program_code = '{program_code}'"
        with db() as (conn, cur):
            cur.execute(sql)
            document_info = cur.fetchone()

    except Exception as e:
        print("查詢 program code 失敗。")
        return send_response(500, False, "系統錯誤", {"message": str(e)})
    
    if len(document_info) == 0:
        return send_response(200, True, "查無參數代碼", {"message": "資料庫無該參數代碼對應的已簽核文件"})
    
    document_token = document_info[0]
    document_machines = json.loads(document_info[1]).get("machines")
    intersection_machines = list(set(machines) & set(document_machines))
    base_machine = intersection_machines[0] if intersection_machines else machines[0]

    # 2-1. Fetch latest PMS data from document PMS data
    machine_slots, info = odb_data_fetch(f"SELECT SLOT_NAME, PARAMETER_DESC, UNIT FROM IDBUSER.RMS_FLEX_PMS WHERE MACHINE_CODE = '{base_machine}' AND {PMS_PREFIX} ORDER BY PMS_ID")
    if info != "Success":
        return send_response(500, False, "系統錯誤", {"message": info})
    
    target_pms_header = ['槽體', '管理項目', '規格下限(OOS-)', '操作下限(OOC-)', '設定值', '操作上限(OOC+)', '規格上限(OOS+)', '參數下放', '說明']
    target_pms_slots = set([f"{slot_info[0]}-{slot_info[1]}" + ("(%s)" % slot_info[2] if slot_info[2] != None and len(slot_info[2]) > 0 else "") for slot_info in machine_slots])
    
    # 2-2. Fetch latest condition data from database
    sql = f"SELECT rc.condition_name FROM rms_conditions AS rc JOIN rms_group_machines AS rgm ON rc.condition_id = rgm.condition_id WHERE rgm.machine_id = '{base_machine}' ORDER BY rc.condition_id"
    condition_info, info = db_data_fetch(sql)
    
    if info != "Success":
        return send_response(500, False, "系統錯誤", {"message": f"MySQL Condition 查詢失敗: {info}"})
    
    target_cond_header = ['條件名稱'] + [c[0] for c in condition_info]
    
    # 2-3. Fetch record condition and PMS data from document
    sql = f"SELECT rbc.sub_no, rbc.content_text, rbc.metadata FROM rms_block_content AS rbc JOIN JSON_TABLE(rbc.metadata, '$.programs[*]' COLUMNS(program_code VARCHAR(15) PATH '$.programCode')) AS jt where rbc.document_token = '{document_token}' AND jt.program_code = '{program_code}'"
    table_info, info = db_data_fetch(sql)
    
    if info != "Success" or not table_info:
        return send_response(500, False, "系統錯誤", {"message": "找不到對應的參數區塊內容"})
    
    source_cond_table = json.loads([info[1] for info in table_info if info[0] == 1][0])
    source_pms_table = json.loads([info[1] for info in table_info if info[0] == 0][0])
    source_programs = json.loads(table_info[0][2])

    source_cond_header_index = {cond: index for index, cond in enumerate(source_cond_table[0])}
    add_conds = set(target_cond_header) - set(source_cond_table[0])
    del_conds = set(source_cond_table[0]) - set(target_cond_header)
    source_pms_slots = {f"{slot_info[0]}-{slot_info[1]}": slot_info for slot_info in source_pms_table[1:]}
    add_pms = target_pms_slots - set(source_pms_slots.keys())
    del_pms = set(source_pms_slots.keys()) - target_pms_slots

    if (not intersection_machines and (add_conds or del_conds or add_pms or del_pms)):
        print("機台不符合: 此機台條件參數與查詢代碼不符")
        return send_response(200, True, "機台不符合", {"message": "此機台製造參數與查詢代碼不符"})
    
    # 3. Match condition parameter
    new_cond_table = [target_cond_header]
    for row in source_cond_table[1:]:
        rowData = []
        for target_cond in target_cond_header:
            value = "" if source_cond_header_index.get(target_cond) == None else row[source_cond_header_index[target_cond]]
            rowData.append(value)
        new_cond_table.append(rowData)
    
    # 4. Match PMS parameter
    target_pms_rows = [target_pms_header]
    for slot_info in machine_slots:
        slot_key = f"{slot_info[0]}-{slot_info[1]}" + ("(%s)" % slot_info[2] if slot_info[2] != None and len(slot_info[2]) > 0 else "")
        unit = "(%s)" % slot_info[2] if slot_info[2] != None and len(slot_info[2]) > 0 else ""
        rowData = [slot_info[0], slot_info[1] + unit, "", "", "", "", "", "Y", ""] if source_pms_slots.get(slot_key) == None else source_pms_slots.get(slot_key)
        target_pms_rows.append(rowData)
        
    info = {"param_json": target_pms_rows, "cond_json": new_cond_table, "source_programs": source_programs, "add_params": list(add_pms), "del_params": list(del_pms), "add_conds": list(add_conds), "del_conds": list(del_conds)}
    return send_response(200, True, "複製成功", {"blocks": info})
    
