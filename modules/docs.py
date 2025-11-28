# modules/docs.py
from __future__ import annotations
import datetime, os, uuid, re, json

# Flask's send_file must be explicitly imported
from flask import Blueprint, request, jsonify, send_file, after_this_request
from db import db
from oracle_db import ora_cursor as odb
from utils import send_response, jload, jdump, dver, none_if_blank, new_token
from DocxDefinition import get_docx

BASE_DIR = "docxTemp"
os.makedirs(BASE_DIR, exist_ok=True)

bp = Blueprint("docs", __name__)

# ---- Attributes ------------------------------------------------
@bp.post("/init")
def init_doc():
    body = request.get_json(silent=True) or {}
    doc_type = int(body.get("document_type", 0))
    token = new_token()
    with db() as (conn, cur):
        cur.execute("""
          INSERT INTO rms_document_attributes
          (document_type, EIP_id, status, document_token, document_version, issue_date)
          VALUES (%s,%s,%s,%s,1.00,NOW())
        """, (doc_type, None, 0, token))
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

@bp.post("/attributes/save")
def save_attributes():
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip() or new_token()
    form  = body.get("form") or {}

    # map
    f = {
      "document_type": int(form.get("documentType", 0)),
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

    with db() as (conn, cur):
        cur.execute("""
          UPDATE rms_document_attributes
          SET document_type=%s, previous_document_token=%s,
              document_id=%s, document_name=%s, document_version=%s,
              attribute=%s, department=%s, author_id=%s, author=%s,
              approver=%s, confirmer=%s, change_reason=%s, change_summary=%s, purpose=%s,
              issue_date=NOW()
          WHERE document_token=%s
        """, (f["document_type"], f["prev_token"],
              f["doc_id"], f["doc_name"], f["doc_ver"],
              f["attr_json"], f["dept"], f["author_id"], f["author"],
              f["approver"], f["confirmer"], f["chg_reason"], f["chg_summary"], f["purpose"],
              token))
        if cur.rowcount == 0:
            cur.execute("""
              INSERT INTO rms_document_attributes
              (document_type, EIP_id, status, document_token, previous_document_token,
               document_id, document_name, document_version, attribute, department,
               author_id, author, approver, confirmer, issue_date, change_reason, change_summary, purpose)
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s)
            """, (f["document_type"], None, 0, token, f["prev_token"],
                  f["doc_id"], f["doc_name"], f["doc_ver"], f["attr_json"], f["dept"],
                  f["author_id"], f["author"], f["approver"], f["confirmer"],
                  f["chg_reason"], f["chg_summary"], f["purpose"]))

        cur.execute("SELECT * FROM rms_document_attributes WHERE document_token=%s", (token,))
        row = cur.fetchone()

        attr = jload(row[8], {}) if row else {}
    resp_form = {
        "documentType": row[0] if row else 0,
        "documentID": row[5] if row else "",
        "documentName": row[6] if row else "",
        "documentVersion": float(row[7] or 1.0) if row else 1.0,
        "attribute": attr,
        "department": row[9] if row else "",
        "author_id": row[10] if row else "",
        "author": row[11] if row else "",
        "approver": row[12] if row else "",
        "confirmer": row[13] if row else "",
        "documentPurpose": row[19] if row else "",
        "reviseReason": row[16] if row else "",
        "revisePoint": row[17] if row else "",
        "previousDocumentToken": row[4] if row else "",  # 🔸 新增
    }

    issue = row[15].strftime("%Y-%m-%d %H:%M:%S") if (row and row[15]) else None
    return jsonify({"success": True, "token": token, "issueTime": issue, "form": resp_form})

@bp.get("/<token>/attributes")
def load_attributes(token):
    with db(dict_cursor=True) as (conn, cur):
        cur.execute("SELECT * FROM rms_document_attributes WHERE document_token=%s", (token,))
        r = cur.fetchone()
        if not r: return send_response(404, False, "Not found")
        attr = jload(r.get("attribute"), {}) or {}
        return jsonify({
            "success": True,
            "token": r["document_token"],
            "status": r["status"],
            "issueTime": r["issue_date"].strftime("%Y-%m-%d %H:%M:%S") if r["issue_date"] else None,
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
                "previousDocumentToken": r["previous_document_token"] or "",  # 🔸 新增
            }
        })

# ---- Dynamic Blocks (generic) --------------------------------
@bp.get("/<token>/blocks")
def load_blocks(token):
    step_type = request.args.get("step_type", type=int)
    if step_type is None:                          # allow 0, only reject missing
        return send_response(400, False, "missing step_type")
    with db(dict_cursor=True) as (conn, cur):
        cur.execute("""
          SELECT tier_no, sub_no, content_type, header_json, content_json, files
          FROM rms_block_content
          WHERE document_token=%s AND step_type=%s
          ORDER BY tier_no ASC, sub_no ASC
        """, (token, step_type))
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
    data = [{"id": f"{step_type}-{t}", "step": step_type, "tier": t, "data": grouped[t]} for t in sorted(grouped)]
    return jsonify({"success": True, "blocks": data})

# POST /blocks/save
@bp.post("/blocks/save")
def save_blocks():
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    step_type = body.get("step_type")
    if not token or step_type is None:             # allow 0
        return send_response(400, False, "missing token or step_type")
    step_type = int(step_type)

    blocks = body.get("blocks") or []
    with db() as (conn, cur):
        cur.execute("DELETE FROM rms_block_content WHERE document_token=%s AND step_type=%s", (token, step_type))
        ins = """
          INSERT INTO rms_block_content
          (content_id, document_token, step_type, tier_no, sub_no, content_type,
           header_text, header_json, content_text, content_json, files, metadata,
           created_at, updated_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        """
        for blk in blocks:
            tier = int(blk.get("tier", 1))
            for idx, it in enumerate(blk.get("data") or [], start=1):
                cur.execute(ins, (
                    new_token(), token, step_type, tier, idx, int(it.get("option", 0)),
                    None, jdump(it.get("jsonHeader")), None, jdump(it.get("jsonContent")),
                    jdump(it.get("files") or []), jdump({"source":"dynamic"})
                ))
    return jsonify({"success": True, "count": sum(len(b.get('data') or []) for b in blocks)})

# ---- Manufacturing Condition Rules (step_type = 2) ------------
@bp.post("/params/save")
def save_params():
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    blocks = body.get("blocks") or []
    step_type = int(body.get("step_type", 2))  # default 2 for MCR
    if not token:
        return send_response(400, False, "missing token")

    with db() as (conn, cur):
        # wipe this step
        cur.execute("DELETE FROM rms_block_content WHERE document_token=%s AND step_type=%s", (token, step_type))

        ins = """
          INSERT INTO rms_block_content
          (content_id, document_token, step_type, tier_no, sub_no, content_type,
           header_text, header_json, content_text, content_json, files, metadata,
           created_at, updated_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        """

        for b in blocks:
            tier = int(b.get("tier_no", 1))

            # Parameter table (sub 0)
            param_json = b.get("jsonParameterContent")  # TipTap JSON (optional)
            param_arr  = b.get("arrayParameterData") or []  # 2D array
            cur.execute(ins, (
                new_token(), token, step_type, tier, 0, 2,
                None, None,
                jdump(param_arr),  # content_text
                jdump(param_json), # content_json
                jdump([]),         # files
                jdump({"kind": "mcr-parameter", **b.get("metadata", {})})
            ))

            # Condition table (sub 1)
            if step_type == 2:
                cond_json = b.get("jsonConditionContent")
                cond_arr  = b.get("arrayConditionData") or []
                cur.execute(ins, (
                    new_token(), token, step_type, tier, 1, 2,
                    None, None,
                    jdump(cond_arr),
                    jdump(cond_json),
                    jdump([]),
                    jdump({"kind": "mcr-condition", **b.get("metadata", {})})
                ))

    return jsonify({"success": True, "count": len(blocks)})

@bp.get("/<token>/params")
def load_params(token):
    step_type = int(request.args.get("step_type", 2))  # default 2 for MCR
    with db(dict_cursor=True) as (conn, cur):
        cur.execute("""
          SELECT tier_no, sub_no, header_text, content_text, content_json, metadata
          FROM rms_block_content
          WHERE document_token=%s AND step_type=%s
          ORDER BY tier_no ASC, sub_no ASC
        """, (token, step_type))
        rows = cur.fetchall() or []

    # Group by tier_no and stitch sub 0/1 back together
    out = {}
    for r in rows:
        t = int(r["tier_no"])
        sub = int(r["sub_no"])
        out.setdefault(t, {
            "code": f"XXXX{t}",
            "jsonParameterContent": None,
            "arrayParameterData": [],
            "jsonConditionContent": None,
            "arrayConditionData": [],
            "metadata": None
        })
        if sub == 0:
            out[t]["code"] = r["header_text"] or out[t]["code"]
            out[t]["arrayParameterData"] = jload(r["content_text"], []) or []
            out[t]["jsonParameterContent"] = jload(r["content_json"])
            out[t]["metadata"] = jload(r["metadata"])
        elif sub == 1:
            out[t]["arrayConditionData"] = jload(r["content_text"], []) or []
            out[t]["jsonConditionContent"] = jload(r["content_json"])

    blocks = []
    for i, t in enumerate(sorted(out.keys()), start=1):
        b = out[t]
        blocks.append({
            "id": f"p-{t}",
            "code": b["code"] or f"XXXX{t}",
            "jsonParameterContent": b["jsonParameterContent"],
            "arrayParameterData": b["arrayParameterData"],
            "jsonConditionContent": b["jsonConditionContent"],
            "arrayConditionData": b["arrayConditionData"],
            "metadata": b["metadata"]
        })

    return jsonify({"success": True, "blocks": blocks})

from oracle_db import ora_cursor  # 下段輪巡會用到，順便先 import

@bp.post("/revise")
def create_revision():
    """
    建立新一版：
      - 由前一版 previous_token 複製一份
      - document_version + 1.00
      - status = 0 (新的草稿)
      - previous_document_token 指向舊 token
      - document_id 直接沿用舊版（可能是 NULL，表示初版尚未產生文件）
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
        cur.execute("""
          INSERT INTO rms_document_attributes
          (document_type, EIP_id, status, document_token, previous_document_token,
           document_id, document_name, document_version, attribute, department,
           author_id, author, approver, confirmer, issue_date,
           change_reason, change_summary, reject_reason, purpose)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s,%s)
        """, (
            r["document_type"], None, 0, new_token_, prev_token, doc_id, r["document_name"], new_ver, 
            r["attribute"], r["department"], r["author_id"], r["author"], r["approver"], r["confirmer"], "", "", None, r["purpose"],
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

def _status_from_eip_flags(signed_val, rejected_val):
    signed = str(signed_val).upper() == "TRUE"
    rejected = str(rejected_val).upper() == "TRUE"

    if not signed and not rejected:
        return 1  # 已送審（EIP 有資料但尚未簽核 / 退回）
    if signed and not rejected:
        return 2  # 已簽核
    if not signed and rejected:
        return 3  # 已退回
    # 其他組合目前不定義，就維持原狀
    return None

@bp.post("/sync-eip")
def sync_eip():
    """
    從 Oracle IDBUSER.EIP_DOCUMENT_TABLE 同步狀態到 MySQL：
      - 以 (Document_ID, Document_version, Document_name) 對應
      - 更新 EIP_id / status / rejecter / reject_reason
    """
    updated = 0

    # 1) 從 Oracle 抓資料
    with ora_cursor() as cur_ora:
        cur_ora.execute("""
          SELECT
            EIP_ID,
            Document_ID,
            Document_version,
            Document_name,
            signed,
            rejected,
            rejecter,
            rejected_reason
          FROM IDBUSER.EIP_DOCUMENT_TABLE
        """)
        rows = cur_ora.fetchall() or []

    if not rows:
        return jsonify({"success": True, "updated": 0})

    # 2) 一筆一筆對到 MySQL
    with db(dict_cursor=True) as (conn, cur):
        for r in rows:
            # oracledb 預設回 tuple，照欄位順序取
            eip_id          = r[0]
            doc_id          = r[1]
            doc_ver         = float(r[2])
            doc_name        = r[3]
            signed_val      = r[4]
            rejected_val    = r[5]
            rejecter        = r[6]
            rejected_reason = r[7]

            cur.execute("""
              SELECT document_token, status
              FROM rms_document_attributes
              WHERE document_id=%s
                AND document_version=%s
                AND document_name=%s
            """, (doc_id, doc_ver, doc_name))
            my = cur.fetchone()
            if not my:
                continue

            new_status = _status_from_eip_flags(signed_val, rejected_val)
            if new_status is None:
                continue

            cur.execute("""
              UPDATE rms_document_attributes
              SET EIP_id=%s,
                  status=%s,
                  rejecter=%s,
                  reject_reason=%s
              WHERE document_token=%s
            """, (
                eip_id,
                new_status,
                rejecter if new_status == 3 else None,
                rejected_reason if new_status == 3 else None,
                my["document_token"],
            ))
            updated += 1

        conn.commit()

    return jsonify({"success": True, "updated": updated})

def next_document_id(prefix: str) -> str:
    """
    依照 PROJECT_CODE 前三碼 + 三位流水號產生 document_id：
      WMA → WMA001, WMA002, ...
    """
    if not prefix or len(prefix) < 3:
        prefix = "XXX"
    prefix = prefix[:3]

    with db(dict_cursor=True) as (conn, cur):
        cur.execute("""
          SELECT document_id
          FROM rms_document_attributes
          WHERE document_id LIKE %s
          ORDER BY document_id DESC
          LIMIT 1
        """, (prefix + "%",))
        row = cur.fetchone()

        if not row or not row["document_id"]:
            return f"{prefix}001"

        tail = row["document_id"][-3:]
        try:
            num = int(tail)
        except ValueError:
            num = 0

        return f"{prefix}{num + 1:03d}"

def next_monthly_document_id(prefix: str = "W") -> str:
    """
    依照 W_YY_MM_XXX 規則產生 document_id：
      W_25_11_001, W_25_11_002, ...
    """
    now = datetime.datetime.now()
    yy = now.year % 100
    mm = now.month

    base = f"{prefix}_{yy:02d}_{mm:02d}_"

    with db(dict_cursor=True) as (conn, cur):
        cur.execute("""
          SELECT document_id
          FROM rms_document_attributes
          WHERE document_id LIKE %s
          ORDER BY document_id DESC
          LIMIT 1
        """, (base + "%",))
        row = cur.fetchone()

        if not row or not row["document_id"]:
            return f"{base}001"

        tail = row["document_id"][-3:]
        try:
            num = int(tail)
        except ValueError:
            num = 0

        return f"{base}{num + 1:03d}"

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
        cur.execute("""
          UPDATE rms_document_attributes
          SET document_id=NULL
          WHERE document_token=%s
        """, (token,))
    return jsonify({"success": True})


@bp.get("/drafts")
def list_drafts():
    """
    Query params:
      - user_id      (required):  要查的作者/使用者 id -> 對應 DB 欄位 author_id
      - status       (optional):  預設 0 當作草稿；如需查核/發佈可改值
      - keyword      (optional):  針對 document_name、document_id 模糊查詢
      - page         (optional):  預設 1
      - page_size    (optional):  預設 20
      - sort         (optional):  排序欄位，允許: issue_date, document_version, document_name
      - order        (optional):  asc/desc，預設 desc
    Response:
      {
        "success": true,
        "items": [
          {
            "documentToken": "...",
            "documentName": "...",
            "documentVersion": 1.20,
            "author": "...",
            "authorId": "...",
            "issueDate": "2025-11-04T18:00:00",
            "documentId": "WMH250"          # 方便前端顯示（可拿掉）
          }
        ],
        "total": 123,
        "page": 1,
        "pageSize": 20
      }
    """
    user_id   = request.args.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "user_id is required"}), 400

    # defaults
    try:
        status    = int(request.args.get("status", 0))
    except ValueError:
        return jsonify({"success": False, "error": "status must be int"}), 400

    keyword   = (request.args.get("keyword") or "").strip()
    try:
        page      = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return jsonify({"success": False, "error": "page/page_size must be int"}), 400

    sort_map  = {
        "issue_date": "issue_date",
        "document_version": "document_version",
        "document_name": "document_name",
    }
    sort_key  = request.args.get("sort", "issue_date").lower()
    order     = request.args.get("order", "desc").lower()
    sort_col  = sort_map.get(sort_key, "issue_date")
    order_sql = "DESC" if order not in ("asc", "ASC") else "ASC"

    offset = (page - 1) * page_size

    base_where = ["author_id = %s", "status = %s"]
    params = [user_id, status]

    if keyword:
        base_where.append("(document_name LIKE %s OR document_id LIKE %s)")
        like_kw = f"%{keyword}%"
        params.extend([like_kw, like_kw])

    where_sql = " AND ".join(base_where)

    count_sql = f"""
      SELECT COUNT(*) AS cnt
      FROM rms_document_attributes
      WHERE {where_sql}
    """

    data_sql = f"""
      SELECT
        document_type, document_token, document_name, document_version, author, author_id, issue_date, document_id
      FROM rms_document_attributes
      WHERE {where_sql}
      ORDER BY {sort_col} {order_sql}
      LIMIT %s OFFSET %s
    """

    with db(dict_cursor=True) as (conn, cur):
        # total count
        cur.execute(count_sql, params)
        total = int(cur.fetchone()["cnt"])

        # page data
        cur.execute(data_sql, params + [page_size, offset])
        rows = cur.fetchall() or []

    def to_item(row):
        # issue_date 轉 ISO（沒有就 None）
        iso_date = None
        if row.get("issue_date"):
            try:
                iso_date = row["issue_date"].isoformat(timespec="seconds")
            except Exception:
                iso_date = str(row["issue_date"])

        # 回傳前端需要的 camelCase
        return {
            "documentType": row["document_type"],
            "documentToken": row["document_token"],
            "documentName": row["document_name"],
            "documentVersion": float(row["document_version"]) if row["document_version"] is not None else None,
            "author": row["author"],
            "authorId": row["author_id"],
            "issueDate": iso_date,
            "documentId": row.get("document_id"),
        }

    items = [to_item(r) for r in rows]

    return jsonify({
        "success": True,
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    })

@bp.delete("/<document_token>")
def delete_draft(document_token):
    """
    Delete a draft by its document_token.
    Only rows with status = 0 (draft) can be deleted.

    Path:
      DELETE /docs/<document_token>

    Response:
      200 { success: True, deleted: 1 }
      404 { success: False, error: "not found" }              # no such token
      409 { success: False, error: "not a draft" }            # exists but status != 0
    """
    token = (document_token or "").strip()
    if not token:
        return jsonify({"success": False, "error": "document_token is required"}), 400

    with db(dict_cursor=True) as (conn, cur):
        # Is there a record?
        cur.execute("SELECT status FROM rms_document_attributes WHERE document_token=%s", (token,))
        row = cur.fetchone()

        if not row:
            return jsonify({"success": False, "error": "not found"}), 404

        # Only allow deleting drafts
        if int(row.get("status", 1)) != 0:
            return jsonify({"success": False, "error": "not a draft"}), 409

        # Delete
        cur.execute("DELETE FROM rms_document_attributes WHERE document_token=%s AND status=0", (token,))
        conn.commit()
        deleted = cur.rowcount or 0

    # (Optional) clean temp files if you keep any by token under BASE_DIR
    try:
        # Example: remove /docxTemp/<token>.docx if you create such files.
        # from pathlib import Path
        # p = Path(BASE_DIR) / f"{token}.docx"
        # if p.exists():
        #     p.unlink()
        pass
    except Exception:
        # Non-fatal: ignore file cleanup errors
        pass

    return jsonify({"success": True, "deleted": deleted}), 200

def _build_keyword_predicate(keyword: str):
    """
    Returns (sql_snippet, params) for robust keyword search.
    - Matches: document_name, author, document_id (LIKE)
    - Also matches document_version:
        * if keyword is numeric (int/float), add exact equality on document_version
        * always also add LIKE(cast(document_version as char)) for partial text matches
    """
    if not keyword:
        return "", []

    likes = []
    params = []

    # name / id / author LIKE
    likes.append("document_name LIKE %s")
    params.append(f"%{keyword}%")
    likes.append("document_id LIKE %s")
    params.append(f"%{keyword}%")
    likes.append("author LIKE %s")
    params.append(f"%{keyword}%")

    # version: support numeric equality + textual LIKE
    numeric = None
    try:
        numeric = float(keyword)
    except Exception:
        pass

    # MySQL: CAST(document_version AS CHAR) for LIKE
    likes.append("CAST(document_version AS CHAR) LIKE %s")
    params.append(f"%{keyword}%")

    eq = []
    if numeric is not None:
        eq.append("document_version = %s")
        params.append(numeric)

    # Combine
    if eq:
        where_piece = "(" + " OR ".join(likes + eq) + ")"
    else:
        where_piece = "(" + " OR ".join(likes) + ")"
    return where_piece, params

def _parse_doc_types(s: str | None) -> list[str] | None:
    """
    Accepts:
      - None / ""  -> no filtering
      - single or comma list: "Instruction", "Specification", or mix
    Returns a normalized list using DB values: ["Instruction", "Specification"].
    Raises ValueError if any entry is invalid.
    """
    if s is None or str(s).strip() == "":
        return None

    allowed = {
        "instruction": 0,
        "specification": 1,
    }
    out = []
    for part in str(s).split(","):
        key = part.strip().lower()
        if not key:
            continue
        if key not in allowed:
            raise ValueError("document_type must be in {Instruction, Specification}")
        out.append(allowed[key])
    if not out:
        return None
    return out

def _parse_statuses(v):
    """
    Accepts either:
      - single int string: "0"
      - comma list: "1,3"
    Returns a validated list of ints (subset of {0,1,2,3}), or raises ValueError.
    """
    if v is None:
        raise ValueError("status is required")
    try:
        parts = [p.strip() for p in str(v).split(",")]
        nums = [int(p) for p in parts if p != ""]
    except Exception:
        raise ValueError("status must be int or comma-separated ints")
    allowed = {0, 1, 2, 3}
    for n in nums:
        if n not in allowed:
            raise ValueError("status must be in {0,1,2,3}")
    if not nums:
        raise ValueError("status is required")
    return nums

def _parse_statuses(s: str) -> list[int]:
    if s is None or str(s).strip() == "":
        raise ValueError("status is required")
    out = []
    for part in str(s).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            raise ValueError(f"invalid status: {part}")
    if not out:
        raise ValueError("status is required")
    return out

def _list_documents_impl(
    *,
    user_id: str | None,         # allow None for "all" search
    statuses: list[int],
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
    sort_key: str = "issue_date",
    order: str = "desc",
    doc_types: list[str] | None = None,
    scope: str = "mine",         # "mine" | "all"
):
    sort_map = {
        "issue_date": "issue_date",
        "document_version": "document_version",
        "document_name": "document_name",
    }
    sort_col = sort_map.get((sort_key or "issue_date").lower(), "issue_date")
    order_sql = "DESC" if (order or "desc").lower() not in ("asc", "ASC") else "ASC"

    where = []
    params = []

    # scope
    if scope == "mine":
        if not user_id:
            raise ValueError("user_id is required for scope=mine")
        where.append("author_id = %s")
        params.append(user_id)

    # statuses (required)
    where.append(f"status IN ({', '.join(['%s'] * len(statuses))})")
    params.extend(statuses)

    # doc types (optional)
    if doc_types:
        where.append(f"document_type IN ({', '.join(['%s'] * len(doc_types))})")
        params.extend(doc_types)

    # robust keyword
    kw_sql, kw_params = _build_keyword_predicate(keyword)
    if kw_sql:
        where.append(kw_sql)
        params.extend(kw_params)

    where_sql = " AND ".join(where) if where else "1=1"
    offset = (page - 1) * page_size

    count_sql = f"""
      SELECT COUNT(*) AS cnt
      FROM rms_document_attributes
      WHERE {where_sql}
    """
    data_sql = f"""
      SELECT
        document_type,
        document_token,
        document_name,
        document_version,
        author,
        author_id,
        issue_date,
        document_id,
        status,
        rejecter,
        reject_reason
      FROM rms_document_attributes
      WHERE {where_sql}
      ORDER BY {sort_col} {order_sql}
      LIMIT %s OFFSET %s
    """

    with db(dict_cursor=True) as (_, cur):
        cur.execute(count_sql, params)
        total = int(cur.fetchone()["cnt"])

        cur.execute(data_sql, params + [page_size, offset])
        rows = cur.fetchall() or []

    def to_item(r):
        iso_date = None
        if r.get("issue_date"):
            try:
                iso_date = r["issue_date"].isoformat(timespec="seconds")
            except Exception:
                iso_date = str(r["issue_date"])
        return {
            "documentType": r["document_type"],
            "documentToken": r["document_token"],
            "documentName": r["document_name"],
            "documentVersion": float(r["document_version"]) if r["document_version"] is not None else None,
            "author": r["author"],
            "authorId": r["author_id"],
            "issueDate": iso_date,
            "documentId": r.get("document_id"),
            "status": r.get("status"),
            "rejecter": r.get("rejecter"),
            "rejectReason": r.get("reject_reason"),
        }

    return {
        "success": True,
        "items": [to_item(r) for r in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }

@bp.get("/all")
def list_all_documents():
    # statuses required (same as /documents)
    try:
        statuses = _parse_statuses(request.args.get("status"))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    keyword = (request.args.get("keyword") or "").strip()
    try:
        page      = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return jsonify({"success": False, "error": "page/page_size must be int"}), 400

    sort_key = (request.args.get("sort") or "issue_date")
    order    = (request.args.get("order") or "desc")

    data = _list_documents_impl(
        user_id=None,           # no author filter → all authors
        statuses=statuses,
        keyword=keyword,        # strong search: name/author/version/id
        page=page,
        page_size=page_size,
        sort_key=sort_key,
        order=order,
        doc_types=None,         # <— IMPORTANT: do not filter by type
        scope="all",
    )
    return jsonify(data), 200

@bp.get("/passed")
def list_passed():
    # 固定 status = 2 (通過/已簽核)
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "user_id is required"}), 400

    # document_type: optional ("Instruction", "Specification"), comma-separated ok
    try:
        doc_types = _parse_doc_types(request.args.get("document_type"))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    keyword = (request.args.get("keyword") or "").strip()
    try:
        page      = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return jsonify({"success": False, "error": "page/page_size must be int"}), 400

    sort_key = (request.args.get("sort") or "issue_date")
    order    = (request.args.get("order") or "desc")

    data = _list_documents_impl(
        user_id=user_id,
        statuses=[2],              # <— PASSED
        keyword=keyword,
        page=page,
        page_size=page_size,
        sort_key=sort_key,
        order=order,
        doc_types=doc_types,       # <— filter if provided
    )
    return jsonify(data), 200

@bp.get("/documents")
def list_documents():
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "user_id is required"}), 400

    try:
        statuses = _parse_statuses(request.args.get("status"))
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 400

    keyword = (request.args.get("keyword") or "").strip()
    try:
        page      = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return jsonify({"success": False, "error": "page/page_size must be int"}), 400

    sort_key = (request.args.get("sort") or "issue_date")
    order    = (request.args.get("order") or "desc")

    data = _list_documents_impl(
        user_id=user_id,
        statuses=statuses,
        keyword=keyword,
        page=page,
        page_size=page_size,
        sort_key=sort_key,
        order=order,
        scope="mine",
    )
    return jsonify(data), 200

@bp.get("/submitted")
def list_submitted():
    # 固定 status = 1
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "user_id is required"}), 400

    keyword = (request.args.get("keyword") or "").strip()
    try:
        page      = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return jsonify({"success": False, "error": "page/page_size must be int"}), 400

    sort_key = (request.args.get("sort") or "issue_date")
    order    = (request.args.get("order") or "desc")

    data = _list_documents_impl(
        user_id=user_id,
        statuses=[1],
        keyword=keyword,
        page=page,
        page_size=page_size,
        sort_key=sort_key,
        order=order,
    )
    return jsonify(data), 200

@bp.get("/rejected")
def list_rejected():
    # 固定 status = 3
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"success": False, "error": "user_id is required"}), 400

    keyword = (request.args.get("keyword") or "").strip()
    try:
        page      = max(1, int(request.args.get("page", 1)))
        page_size = min(100, max(1, int(request.args.get("page_size", 20))))
    except ValueError:
        return jsonify({"success": False, "error": "page/page_size must be int"}), 400

    sort_key = (request.args.get("sort") or "issue_date")
    order    = (request.args.get("order") or "desc")

    data = _list_documents_impl(
        user_id=user_id,
        statuses=[3],
        keyword=keyword,
        page=page,
        page_size=page_size,
        sort_key=sort_key,
        order=order,
    )
    return jsonify(data), 200

# ---- References ----------------------------------------------
@bp.post("/references/save")
def save_references():
    body = request.get_json(silent=True) or {}
    token = (body.get("token") or "").strip()
    if not token: return send_response(400, False, "missing token")
    documents = body.get("documents") or []
    forms     = body.get("forms") or []
    with db() as (conn, cur):
        cur.execute("DELETE FROM rms_references WHERE document_token=%s", (token,))
        ins = """
          INSERT INTO rms_references (document_token, refer_type, refer_document, refer_document_name, created_at)
          VALUES (%s,%s,%s,%s,NOW())
        """
        for d in documents:
            cur.execute(ins, (token, 0, (d.get("docId") or "").strip(), (d.get("docName") or "").strip()))
        for f in forms:
            cur.execute(ins, (token, 1, (f.get("formId") or "").strip(), (f.get("formName") or "").strip()))
    return jsonify({"success": True})

@bp.get("/<token>/references")
def load_references(token):
    with db(dict_cursor=True) as (conn, cur):
        cur.execute("""
          SELECT refer_type, refer_document, refer_document_name
          FROM rms_references WHERE document_token=%s ORDER BY refer_type ASC, id ASC
        """, (token,))
        rows = cur.fetchall() or []
    docs, forms = [], []
    for r in rows:
        if int(r["refer_type"]) == 0:
            docs.append({"docId": r["refer_document"], "docName": r["refer_document_name"]})
        else:
            forms.append({"formId": r["refer_document"], "formName": r["refer_document_name"]})
    return jsonify({"success": True, "documents": docs, "forms": forms})

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
            SELECT step_type, tier_no, sub_no, content_type,
                   header_text, header_json,
                   content_text, content_json,
                   files, metadata
            FROM rms_block_content
            WHERE document_token=%s
            ORDER BY step_type ASC, tier_no ASC, sub_no ASC
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
        cur.execute("""
            SELECT refer_type, refer_document, refer_document_name
            FROM rms_references
            WHERE document_token=%s
            ORDER BY refer_type ASC, id ASC
        """, (token,))
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

@bp.get("/view/<token>/docx")
def view_docx_from_token(token):
    """
    依 document_token 從 DB 撈出 attribute/content/reference，
    串成 payload 丟給 get_docx，產生一份暫存 DOCX，
    回傳給前端做「全頁預覽」（前端直接 window.open 這個 URL）。
    """
    try:
        data = _build_doc_payload_from_token(token)
    except Exception as e:
        print("[view_docx_from_token] error:", e)
        return jsonify({"ok": False, "error": "document not found"}), 404

    # 檔名：優先用文件名稱 / 編號
    try:
        attr_last = data["attribute"][-1]
        raw_name  = attr_last.get("documentName") or attr_last.get("documentID") or token
        doc_name  = _safe_docname(raw_name)
    except Exception:
        doc_name = token

    # 暫存目錄
    view_dir = os.path.join(BASE_DIR, "_view")
    os.makedirs(view_dir, exist_ok=True)

    fname    = f"{doc_name}-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}.docx"
    out_path = os.path.join(view_dir, fname)

    # 產生 Word
    if data["attribute"][-1]["documentType"] == 1:
        get_docx(out_path, data, "docx-template/example4.docx")
    else:
        get_docx(out_path, data)

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

    # -------------------------------------------------------
    # A) 有 token：走「DB + 前幾版」路線
    # -------------------------------------------------------
    if token:
        try:
            payload = _build_doc_payload_from_token(token)  # {attribute, content, reference}
        except Exception as e:
            print("[generate_word] _build_doc_payload_from_token error:", e)
            return send_response(404, False, "document not found")

        # 1) 先抓出最新那一版（attribute 最後一個）
        latest_attr = payload["attribute"][-1]

        # 2) 若前端有傳 attribute，就用最後一個覆蓋「最新那一版」的欄位
        if data["attribute"]:
            override_attr = data["attribute"][-1]
            # 只覆蓋有定義的 key，避免整個丟掉前幾版必須欄位
            for k, v in override_attr.items():
                # 如果想保留前幾版資訊，只動 attribute / documentPurpose / reviseReason 等欄位
                latest_attr[k] = v

        # 3) 若前端有 content/reference，代表使用者目前畫面有「最新草稿」內容，要覆蓋 DB 內容
        if data["content"]:
            payload["content"] = data["content"]
        if data["reference"]:
            payload["reference"] = data["reference"]

        # ---------------------------------------------------
        # 4) 計算/更新 document_id（只看最新那一版）
        # ---------------------------------------------------
        with db(dict_cursor=True) as (conn, cur):
            cur.execute("""
            SELECT document_type, document_id, document_version, attribute
            FROM rms_document_attributes
            WHERE document_token=%s
            """, (token,))
            r = cur.fetchone()
            if not r:
                return send_response(404, False, "document not found")

            doc_type = int(r["document_type"] or 0)
            doc_id   = r["document_id"]
            doc_ver  = float(r["document_version"] or 1.0)
            attr_json = jload(r["attribute"], {}) or {}

            latest_attr_json = latest_attr.get("attribute") or {}
            attr_json.update(latest_attr_json)

            # 初版且尚無 document_id → 依文件類型決定編碼規則
            if doc_ver == 1.0 and not doc_id:
                if doc_type == 1:
                    # Specification：W_YY_MM_XXX
                    doc_id = next_monthly_document_id("W")
                else:
                    # Instruction：適用工程前三碼 + 流水號
                    apply_project = (attr_json.get("applyProject") or "").strip()
                    prefix = (apply_project[:3] or "XXX").upper()
                    doc_id = next_document_id(prefix)

            cur.execute("""UPDATE rms_document_attributes SET document_id=%s, attribute=%s WHERE document_token=%s""", (doc_id, jdump(attr_json), token))
            conn.commit()

        # 5) 把 docID 塞回最新那一版給 get_docx 用
        latest_attr["documentID"] = doc_id or ""
        if data["attribute"]:
            data["attribute"][-1]["documentID"] = doc_id or ""

        # 6) 檔名：用最新那一版
        try:
            # doc_name = _safe_docname(latest_attr.get("documentName") or latest_attr.get("documentID") or doc_id or "document")
            doc_name = _safe_docname(f'{latest_attr.get("documentName")}{latest_attr.get("documentVersion"):.1f}')
        except Exception:
            doc_name = "document"

        out_path = os.path.join(BASE_DIR, f"{doc_name}.docx")
        # 產生 Word
        if data["attribute"][-1]["documentType"] == 1:
            get_docx(out_path, data, "docx-template/example4.docx")
        else:
            get_docx(out_path, data)

        @after_this_request
        def add_docid_header(response):
            if doc_id:
                response.headers["X-Document-ID"] = doc_id
            # 讓瀏覽器允許 JS 讀取這個自訂 header（跨網域情況下很重要）
            existing = response.headers.get("Access-Control-Expose-Headers", "")
            expose = "X-Document-ID"
            if existing:
                # 避免重複，加在後面
                if expose not in existing:
                    response.headers["Access-Control-Expose-Headers"] = existing + "," + expose
            else:
                response.headers["Access-Control-Expose-Headers"] = expose
            return response


        return send_file(
            out_path,
            as_attachment=True,
            download_name=f"{doc_name}.docx",
            mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    # -------------------------------------------------------
    # B) 沒有 token：保留舊的 fallback 行為
    # -------------------------------------------------------
    # 這支分支可以很簡單：沿用你之前的 generate_word 寫法（不整合 DB）
    try:
        attr_last = data["attribute"][-1]
        # doc_name = _safe_docname(attr_last.get("documentName") or attr_last.get("documentID") or "document")
        doc_name = _safe_docname(f'{attr_last.get("documentName")}{attr_last.get("documentVersion"):.1f}')
    except Exception:
        doc_name = "document"

    out_path = os.path.join(BASE_DIR, f"{doc_name}.docx")
    # 產生 Word
    if data["attribute"][-1]["documentType"] == 1:
        get_docx(out_path, data, "docx-template/example4.docx")
    else:
        get_docx(out_path, data)

    return send_file(
        out_path,
        as_attachment=True,
        download_name=f"{doc_name}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

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

    # 產生 Word
    if data["attribute"][-1]["documentType"] == 1:
        get_docx(out_path, data, "docx-template/example4.docx")
    else:
        get_docx(out_path, data)

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
    body: { specCode, document_token }
    回傳: { specCode, programCode, prefix, serial }
    """
    body = request.get_json(silent=True) or {}
    spec_code = (body.get("specCode") or "").strip()
    document_token = (body.get("document_token") or "").strip()

    if not spec_code or not document_token:
        return send_response(400, False, "specCode & document_token 為必填", None)

    prefix = build_prefix(spec_code)

    with db(dict_cursor=True) as (conn, cur):
        # ❌ 不要用 conn.start_transaction()，MySQLdb 沒這個 method
        # conn.start_transaction()

        # 1) 先看有沒有舊的釋放號碼可以重用（status=9）
        cur.execute("""
            SELECT id, serial_no, program_code
            FROM rms_program_code
            WHERE spec_code = %s AND status = 9
            ORDER BY serial_no ASC
            LIMIT 1
            FOR UPDATE
        """, (spec_code,))
        row = cur.fetchone()

        if row:
            # 重用舊號碼，改成 reserved 狀態
            cur.execute("""
                UPDATE rms_program_code
                SET status = 0,
                    document_token = %s
                WHERE id = %s
            """, (document_token, row["id"]))
            # 這裡可以不寫 conn.commit()，交給 db() 做
            serial = row["serial_no"]
            program_code = row["program_code"]
        else:
            # 2) 沒有可重用 → 取最大 serial_no + 1
            cur.execute("""
                SELECT MAX(serial_no) AS max_serial
                FROM rms_program_code
                WHERE spec_code = %s
                FOR UPDATE
            """, (spec_code,))
            r = cur.fetchone()
            max_serial = r["max_serial"] or 0
            serial = max_serial + 1
            program_code = f"{prefix}{serial:03d}"

            # 寫入資料表
            cur.execute("""
                INSERT INTO rms_program_code
                    (spec_code, serial_no, program_code, document_token, status)
                VALUES (%s, %s, %s, %s, 0)
            """, (spec_code, serial, program_code, document_token))
            # 一樣可以不用手動 conn.commit()

    data = {
        "specCode": spec_code,
        "programCode": program_code,
        "prefix": prefix,
        "serial": serial,
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
        cur.execute("""
            UPDATE rms_program_code
            SET status = 9, document_token = NULL
            WHERE program_code = %s
        """, (program_code,))
        # 你也可以檢查 rowcount 判斷有沒有真的更新到
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
        cur.execute("""
            UPDATE rms_program_code
            SET status = 9, document_token = NULL
            WHERE document_token = %s AND status = 0
        """, (document_token,))
        conn.commit()

    return send_response(200, True, "程式號碼已釋放", {"document_token": document_token})

@bp.post("/parameters/copy-source")
def copy_source_mcr():
    """
    功能：從已簽核的 Instruction 文件中複製參數與條件表。
    限制：
    1. program_code 必須存在。
    2. 來源文件的機台必須與 base_machine_code 具有相同的 PMS Slot 設置 (Oracle)。
    3. 來源文件的機台必須與 base_machine_code 具有相同的 Condition Signature (MySQL)。
    """
    body = request.get_json(silent=True) or {}
    program_code = (body.get("program_code") or "").strip()
    base_machine_code = (body.get("base_machine_code") or "").strip()

    if not program_code or not base_machine_code:
        return send_response(400, False, "缺少必要參數", {"message": "請提供程式代碼與 Base Machine Code"})

    print(f"[DEBUG] copy_source_mcr start: program={program_code}, base={base_machine_code}")

    # ==========================================
    # STEP 1: 找出所有 "PMS 相容" 的機台 (Oracle)
    # ==========================================
    pms_compatible_machines = set()
    try:
        with ora_cursor() as cur:
            # 這裡沿用 filter-by-baseline 的邏輯，找出 PMS Slot 結構完全一致的機台
            # 簡化 SQL：只要找出 "與 base_machine_code 擁有相同 slot 集合" 的機台
            # (以下 SQL 為邏輯示意，若原 filter-by-baseline SQL 運作正常可直接套用)
            sql = """
            WITH target_slots AS (
                SELECT SLOT_NAME FROM IDBUSER.RMS_FLEX_PMS WHERE MACHINE_CODE = :base_code
            ),
            target_count AS ( SELECT COUNT(*) as cnt FROM target_slots ),
            candidates AS (
                SELECT MACHINE_CODE, SLOT_NAME FROM IDBUSER.RMS_FLEX_PMS
            )
            SELECT DISTINCT A.MACHINE_CODE
            FROM IDBUSER.RMS_SYS_MACHINE A
            JOIN target_count tc ON 1=1
            WHERE A.ENABLED = 'Y' AND A.EQM_ID <> 'NA'
            AND (
                -- Case 1: Base has slots
                (tc.cnt > 0 
                 AND EXISTS (SELECT 1 FROM candidates c WHERE c.MACHINE_CODE = A.MACHINE_CODE)
                 -- A has all slots of Base
                 AND NOT EXISTS (
                    SELECT 1 FROM target_slots ts 
                    WHERE NOT EXISTS (SELECT 1 FROM candidates c WHERE c.MACHINE_CODE = A.MACHINE_CODE AND c.SLOT_NAME = ts.SLOT_NAME)
                 )
                 -- Base has all slots of A
                 AND NOT EXISTS (
                    SELECT 1 FROM candidates c 
                    WHERE c.MACHINE_CODE = A.MACHINE_CODE 
                    AND NOT EXISTS (SELECT 1 FROM target_slots ts WHERE ts.SLOT_NAME = c.SLOT_NAME)
                 )
                )
                OR
                -- Case 2: Base has NO slots (only matches others with no slots)
                (tc.cnt = 0 AND NOT EXISTS (SELECT 1 FROM candidates c WHERE c.MACHINE_CODE = A.MACHINE_CODE))
            )
            """
            cur.execute(sql, {"base_code": base_machine_code})
            rows = cur.fetchall()
            pms_compatible_machines = {row[0] for row in rows}
            
            # 確保 base 自己一定在名單內
            pms_compatible_machines.add(base_machine_code)

    except Exception as e:
        print(f"[ERROR] Oracle PMS check failed: {e}")
        return send_response(400, False, "PMS 資料比對失敗", {"message": "無法驗證機台 PMS 相容性"})

    # ==========================================
    # STEP 2: 找出 "Condition 相容" 的機台 (MySQL)
    # ==========================================
    # 在 PMS 相容的名單中，進一步篩選條件式樣 (Condition Signature) 相同的機台
    final_compatible_machines = []
    
    if not pms_compatible_machines:
        # 如果 Oracle 沒資料，至少自己跟自己相容
        final_compatible_machines = [base_machine_code]
    else:
        try:
            with db() as (conn, cur):
                # 建構動態 UNION ALL 查詢來模擬 CTE
                pms_list = list(pms_compatible_machines)
                union_parts = [f"SELECT '{m}' as m_code" for m in pms_list]
                union_sql = " UNION ALL ".join(union_parts)

                sql = f"""
                WITH input_machines AS (
                    {union_sql}
                ),
                machine_sigs AS (
                    SELECT 
                        im.m_code,
                        (
                            SELECT GROUP_CONCAT(rgm.condition_id ORDER BY rgm.condition_id SEPARATOR ',')
                            FROM sfdb.rms_group_machines rgm
                            WHERE rgm.machine_id = im.m_code
                        ) as sig
                    FROM input_machines im
                ),
                base_sig AS (
                    SELECT sig FROM machine_sigs WHERE m_code = %s
                )
                SELECT ms.m_code
                FROM machine_sigs ms
                JOIN base_sig bs ON (ms.sig IS NULL AND bs.sig IS NULL) OR (ms.sig = bs.sig)
                """
                cur.execute(sql, (base_machine_code,))
                rows = cur.fetchall()
                final_compatible_machines = [r[0] for r in rows]

        except Exception as e:
            print(f"[ERROR] MySQL Condition check failed: {e}")
            # Fallback: 如果 DB 查失敗，保守起見只允許 Base Machine 自己
            final_compatible_machines = [base_machine_code]

    print(f"[DEBUG] Allowed machines: {final_compatible_machines}")

    # ==========================================
    # STEP 3: 查詢已簽核文件 (Source Document)
    # ==========================================
    # 策略：
    # 1. 搜尋所有包含該 program_code 的已簽核 Instruction (status=2, type=0)
    # 2. 檢查該文件的 "machines" 屬性是否包含在 final_compatible_machines 內
    
    try:
        with db() as (conn, cur):
            sql = """
            SELECT 
                bc.document_token,
                d.attribute,
                bc.content_json as param_json,
                (
                    SELECT sub.content_json 
                    FROM sfdb.rms_block_content sub 
                    WHERE sub.document_token = bc.document_token 
                      AND sub.step_type = 2 
                      AND sub.sub_no = 1 
                    LIMIT 1
                ) as cond_json,
                bc.metadata  -- [New] 新增撈取 metadata
            FROM sfdb.rms_block_content bc
            JOIN sfdb.rms_document_attributes d ON d.document_token = bc.document_token
            WHERE d.status = 2
              AND d.document_type = 0
              AND bc.step_type = 2
              AND bc.sub_no = 0
              AND JSON_UNQUOTE(JSON_EXTRACT(bc.metadata, '$.kind')) = 'mcr-parameter'
              AND JSON_SEARCH(bc.metadata, 'one', %s, NULL, '$.programs[*].programCode') IS NOT NULL
            ORDER BY d.issue_date DESC
            """
            
            cur.execute(sql, (program_code,))
            candidates = cur.fetchall()

            target_param_json = None
            target_cond_json = None
            target_programs = [] # [New] 用來存儲來源的製程清單
            found_machine = False

            for row in candidates:
                doc_token, attr_str, param_str, cond_str, meta_str = row # [New] 接收 meta_str
                
                try:
                    attr = json.loads(attr_str) if attr_str else {}
                    doc_machines = attr.get('machines', [])
                    doc_machine_codes = set(m.get('code') for m in doc_machines if m.get('code'))
                except:
                    continue

                compatible_in_doc = doc_machine_codes.intersection(set(final_compatible_machines))
                
                if compatible_in_doc:
                    found_machine = True
                    target_param_json = json.loads(param_str) if param_str else None
                    target_cond_json = json.loads(cond_str) if cond_str else None
                    
                    # [New] 解析 metadata 取得 programs
                    try:
                        meta = json.loads(meta_str) if meta_str else {}
                        # 取得來源的 programs (包含 specCode, specName)
                        # 我們只需要 spec 資訊，舊的 programCode 在這裡其實不需要傳回前端，
                        # 因為前端要申請新的，但為了完整性可以先傳回。
                        target_programs = meta.get("programs") or []
                    except Exception as e:
                        print(f"[WARN] Parse metadata failed: {e}")
                        target_programs = []

                    print(f"[DEBUG] Found compatible doc: {doc_token}, machines: {compatible_in_doc}")
                    break
            
            if not found_machine:
                return send_response(200, False, "條件參數不同無法複製", {
                    "message": "雖有此代碼，但所屬機台的條件/PMS與目前機台不相容，無法複製。"
                })

            return send_response(200, True, "複製成功", {
                "blocks": {
                    "param_json": target_param_json,
                    "cond_json": target_cond_json,
                    "source_programs": target_programs # [New] 回傳製程清單
                }
            })

    except Exception as e:
        print(f"[ERROR] Fetch doc failed: {e}")
        return send_response(500, False, "系統錯誤", {"message": str(e)})
    
# modules/docs.py
@bp.post("/parameters/copy-spec-source")
def copy_spec_source_mcr():
    """
    處理需求 7: 從 Specification Document 複製參數
    """
    body = request.get_json(silent=True) or {}
    program_code = (body.get("program_code") or "").strip()

    if not program_code:
        return send_response(400, False, "請輸入程式代碼", None)

    try:
        # -------------------------------------------------------
        # STEP 1: 找出對應的 Source Block [需求 7 & 7.4]
        # -------------------------------------------------------
        with db() as (conn, cur):
            sql = """
            SELECT 
                bc.content_json,
                bc.content_text,  -- 用於解析當下的 PMS 結構
                bc.metadata,
                d.document_token
            FROM sfdb.rms_block_content bc
            JOIN sfdb.rms_document_attributes d ON d.document_token = bc.document_token
            WHERE d.status = 2            -- [需求 7] status = 2 (已簽核)
              AND d.document_type = 1     -- [需求 7] document_type = 1 (Spec Doc)
              AND bc.step_type = 5        -- [需求 7.4] step_type = 5
              AND bc.sub_no = 0           -- [需求 7.4] sub_no = 0
              AND JSON_UNQUOTE(JSON_EXTRACT(bc.metadata, '$.kind')) = 'mcr-parameter'
              AND JSON_SEARCH(bc.metadata, 'one', %s, NULL, '$.programs[*].programCode') IS NOT NULL
            LIMIT 1
            """
            cur.execute(sql, (program_code,))
            row = cur.fetchone()

            if not row:
                return send_response(200, False, "查無此代碼或文件不符合複製條件 (需為已簽核規格書)", None)

            content_json_str, content_text_str, meta_str, doc_token = row
            
            meta = json.loads(meta_str) if meta_str else {}
            machine_code = meta.get("machine") or ""
            group_code = meta.get("machineGroup") or ""

            if not machine_code:
                return send_response(200, False, "來源資料異常：無機台資訊", None)

            # -------------------------------------------------------
            # STEP 2: [需求 7.1 & 7.3] PMS 比對
            # -------------------------------------------------------
            
            # 2.1 取得 Oracle 目前最新的 PMS
            # [需求 7.1] PARAM_COMPARE='Y' AND SET_ATTRIBUTE='Y'
            current_pms_signature = set()
            try:
                with ora_cursor() as ora:
                    ora.execute("""
                        SELECT TRIM(SLOT_NAME), TRIM(PARAMETER_DESC)
                        FROM IDBUSER.RMS_FLEX_PMS
                        WHERE MACHINE_CODE = :m 
                          AND NVL(PARAM_COMPARE, 'N') = 'Y' 
                          AND NVL(SET_ATTRIBUTE, 'N') = 'Y'
                    """, {"m": machine_code})
                    for r in ora.fetchall():
                        # [需求 7.3] 比較 SLOT_NAME 與 PARAMETER_DESC
                        current_pms_signature.add((r[0], r[1]))
            except Exception as e:
                print(f"[PMS Check] Oracle Error: {e}")
                return send_response(400, False, "PMS 驗證失敗：無法連接 MES", None)

            # 2.2 解析 Source Block 的 PMS 結構 (從 content_text)
            source_pms_signature = set()
            try:
                # content_text 格式範例: [["Slot","Param",...], ["SlotA","ParamA",...]]
                text_arr = json.loads(content_text_str) if content_text_str else []
                
                # 跳過 Header (第一列)
                if len(text_arr) > 1:
                    for row_data in text_arr[1:]:
                        if len(row_data) >= 2:
                            slot = str(row_data[0]).strip()
                            # 需注意：前端表格中的 Parameter Desc 可能包含 "(單位)"
                            # 如果 Oracle 的 DESC 沒有單位，這裡比對會失敗。
                            # 建議：先嘗試比對 Slot Name，這最準確且不易受單位顯示影響
                            # [需求 7.3] 若要嚴格比對 Desc，需確保格式一致
                            # 這裡我們先採用 Slot Name 比對作為主要依據，因為這是硬體結構
                            if slot:
                                source_pms_signature.add(slot)
            except Exception as e:
                print(f"[PMS Check] Parse JSON Error: {e}")

            # 2.3 執行比對
            # 為了避免單位括號造成的誤判，我們這裡主要比對 Slot 是否一致
            current_slots = {k[0] for k in current_pms_signature}
            
            # 如果 Slot 集合不一致，視為 PMS 變更
            if source_pms_signature != current_slots:
                 return send_response(200, False, "PMS版本不符", {
                    "message": f"機台 PMS 設定已變更，無法複製。\n(來源 Slot 與目前 MES 設定不符)"
                })

            # -------------------------------------------------------
            # STEP 3: 回傳資料
            # -------------------------------------------------------
            return send_response(200, True, "複製成功", {
                "blocks": {
                    "content_json": json.loads(content_json_str) if content_json_str else None,
                    "machine": machine_code,
                    "machineGroup": group_code,
                    # 注意：我們不回傳 programCode，因為前端要自己配新的 (需求 7.5)
                }
            })

    except Exception as e:
        print(f"[ERROR] copy_spec_source: {e}")
        return send_response(500, False, "系統錯誤", {"message": str(e)})
    