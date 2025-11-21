# modules/docs.py
from __future__ import annotations
import json, datetime, os, uuid, re
from io import BytesIO

# Flask's send_file must be explicitly imported
from flask import Blueprint, request, jsonify, send_file, after_this_request
from db import db
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
            code = b.get("code") or f"XXXX{tier}"

            # Parameter table (sub 0)
            param_json = b.get("jsonParameterContent")  # TipTap JSON (optional)
            param_arr  = b.get("arrayParameterData") or []  # 2D array
            cur.execute(ins, (
                new_token(), token, step_type, tier, 0, 2,
                code, None,
                jdump(param_arr),  # content_text
                jdump(param_json), # content_json
                jdump([]),         # files
                jdump({"kind": "mcr-parameter", **b.get("metadata", {})})
            ))

            # Condition table (sub 1)
            cond_json = b.get("jsonConditionContent")
            cond_arr  = b.get("arrayConditionData") or []
            cur.execute(ins, (
                new_token(), token, step_type, tier, 1, 2,
                code, None,
                jdump(cond_arr),
                jdump(cond_json),
                jdump([]),
                jdump({"kind": "mcr-condition"})
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

def _safe_docname(s: str, fallback: str = "document") -> str:
    s = (s or "").strip()
    if not s:
        return fallback
    # keep Han/letters/digits/space/-_()
    s = "".join(ch for ch in s if ch.isalnum() or ch in " _-()[]【】（）")
    s = re.sub(r"\s+", "_", s)
    return s[:80] or fallback

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

@bp.post("/generate/word")
def generate_word():
    """
    Accept JSON body {attribute, content, reference}, save under a new payload_id,
    generate a DOCX, and return the file.
    """
    if not request.is_json:
        return jsonify({"ok": False, "error": "JSON body required"}), 400

    data = request.get_json(silent=True) or {}
    data.setdefault("attribute", [])
    data.setdefault("content", [])
    data.setdefault("reference", [])

    payload_id = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    # filename
    try:
        attr_last = data["attribute"][-1]
        doc_name  = _safe_docname(attr_last.get("documentName") or attr_last.get("documentID") or payload_id)
    except Exception:
        doc_name = payload_id

    out_path = os.path.join(BASE_DIR, f"{doc_name}.docx")
    get_docx(out_path, data)

    return send_file(out_path, as_attachment=True, download_name=f"{doc_name}.docx", mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")

@bp.post("/preview/docx")
def preview_docx():
    """
    接收 {attribute, content, reference} JSON，產生一份暫存 DOCX，
    給前端 blob 預覽使用（不強制下載）。
    """
    if not request.is_json:
        return jsonify({"ok": False, "error": "JSON body required"}), 400

    data = request.get_json(silent=True) or {}
    data.setdefault("attribute", [])
    data.setdefault("content", [])
    data.setdefault("reference", [])

    # 產生一個 payload_id，當檔名/暫存檔用
    payload_id = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    # 取文件名稱（跟 generate_word 一樣邏輯）
    try:
        attr_last = data["attribute"][-1]
        raw_name  = attr_last.get("documentName") or attr_last.get("documentID") or payload_id
        doc_name  = _safe_docname(raw_name)
    except Exception:
        doc_name = payload_id

    # 放暫存的資料夾：BASE_DIR/_preview
    preview_dir = os.path.join(BASE_DIR, "_preview")
    os.makedirs(preview_dir, exist_ok=True)

    out_path = os.path.join(preview_dir, f"{doc_name}-{payload_id}.docx")

    # 用你現有的 get_docx 產生 Word
    get_docx(out_path, data)

    # 回傳後把暫存檔刪掉
    @after_this_request
    def remove_file(response):
        try:
            if os.path.exists(out_path):
                os.remove(out_path)
        except Exception as e:
            # 這裡不要影響回應，只記 log 即可
            print("[preview_docx] remove temp file error:", e)
        return response

    return send_file(
        out_path,
        as_attachment=False,  # 🔑 不強制下載，前端用 fetch+blob 來處理
        download_name=f"{doc_name}.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

def _safe_docname(name: str) -> str:
    """
    簡單版 safe name，可用你原本的實作。
    """
    import re
    name = name or "document"
    name = re.sub(r"[^\w\-一-龥]", "_", name)
    return name[:64]

@bp.post("/preview/pdf")
def preview_pdf():
    """
    接收 JSON {attribute, content, reference}，
    用 get_docx 產 DOCX，再用 LibreOffice 轉 PDF，
    回傳 inline PDF（不存檔，暫存資料夾用完就自動刪掉）。
    """
    if not request.is_json:
        return jsonify({"success": False, "error": "JSON body required"}), 400

    data = request.get_json(silent=True) or {}
    data.setdefault("attribute", [])
    data.setdefault("content", [])
    data.setdefault("reference", [])

    payload_id = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    try:
        attr_list = data.get("attribute") or []
        if attr_list:
            attr_last = attr_list[-1]
            doc_name = _safe_docname(
                attr_last.get("documentName")
                or attr_last.get("documentID")
                or payload_id
            )
        else:
            doc_name = payload_id

        # 產 PDF bytes（全程在 TemporaryDirectory 裡）
        pdf_bytes = docx_file_to_pdf_bytes(get_docx, data, filename_stem=doc_name)

        debug_path = f"/tmp/preview-debug-{payload_id}.pdf"
        with open(debug_path, "wb") as f:
            f.write(pdf_bytes)
        print("DEBUG preview pdf saved:", debug_path)

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

    pdf_io = BytesIO(pdf_bytes)
    pdf_io.seek(0)

    # 先用 send_file 建立 response
    resp = send_file(
        pdf_io,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"{doc_name}.pdf",
    )

    # 再補你要的 header
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Disposition"] = f'inline; filename="{doc_name}.pdf"'
    resp.headers["X-Content-Type-Options"] = "nosniff"

    return resp
