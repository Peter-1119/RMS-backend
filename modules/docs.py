# modules/docs.py
from __future__ import annotations
import json, datetime, os, uuid, re, subprocess, tempfile
from pathlib import Path
from io import BytesIO

# Flask's send_file must be explicitly imported
from flask import Blueprint, request, jsonify, send_file 
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
        "instruction": "Instruction",
        "specification": "Specification",
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

def get_soffice_cmd() -> str:
    """
    回傳 LibreOffice soffice 執行檔路徑：
    - Linux: 直接用 'soffice'（在 PATH 裡）
    - Windows: 先看環境變數 SOFFICE_PATH，沒有就用預設路徑
    """
    if os.name != "nt":  # posix (Linux)
        return "soffice"

    # Windows
    env_path = os.getenv("SOFFICE_PATH")
    if env_path and Path(env_path).is_file():
        return env_path

    default = Path(r"C:\Program Files\LibreOffice\program\soffice.exe")
    if default.is_file():
        return str(default)

    # 最後交給 PATH
    return "soffice"

def docx_to_pdf_bytes(docx_bytes: bytes, filename_stem: str) -> bytes:
    """
    給一份 DOCX 的 bytes，丟到暫存資料夾，用 LibreOffice 轉成 PDF，
    回傳 PDF 的 bytes。暫存檔案會在函式結束後自動刪除。
    """
    soffice = get_soffice_cmd()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        docx_path = tmpdir_path / f"{filename_stem}.docx"
        pdf_path = tmpdir_path / f"{filename_stem}.pdf"

        # 1) 把 DOCX bytes 寫到暫存檔
        with open(docx_path, "wb") as f:
            f.write(docx_bytes)

        # 2) 呼叫 LibreOffice 轉檔
        cmd = [
            soffice,
            "--headless",
            "--convert-to", "pdf",
            "--outdir", str(tmpdir_path),
            str(docx_path),
        ]
        subprocess.run(cmd, check=True)

        if not pdf_path.is_file():
            raise RuntimeError(f"PDF not generated: {pdf_path}")

        # 3) 讀回 PDF bytes
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        # 離開 with TemporaryDirectory，所有檔案自動刪除
        return pdf_bytes

@bp.post("/preview/pdf")
def preview_pdf():
    """
    接 JSON {attribute, content, reference}，
    用 get_docx 產生 DOCX（在記憶體），再用 LibreOffice 轉 PDF 回傳。
    全程只使用 TemporaryDirectory，不會留下檔案。
    """
    if not request.is_json:
        return jsonify({"success": False, "error": "JSON body required"}), 400

    data = request.get_json(silent=True) or {}
    data.setdefault("attribute", [])
    data.setdefault("content", [])
    data.setdefault("reference", [])

    payload_id = f"{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"

    try:
        attr_last = data["attribute"][-1]
        doc_name = _safe_docname(
            attr_last.get("documentName") or
            attr_last.get("documentID") or
            payload_id
        )
    except Exception:
        doc_name = payload_id

    # 1) 先用 get_docx 產 DOCX 到記憶體
    docx_buffer = BytesIO()
    # 假設你的 get_docx 可以接 file-like object，如果現在只能接 path，
    # 就改成先寫到 TemporaryDirectory 裡的檔案再讀，邏輯一樣。
    get_docx(docx_buffer, data)
    docx_bytes = docx_buffer.getvalue()

    # 2) 轉成 PDF bytes（用 TemporaryDirectory，不留檔）
    try:
        pdf_bytes = docx_to_pdf_bytes(docx_bytes, filename_stem=doc_name)
    except Exception as e:
        return jsonify({"success": False, "error": f"convert failed: {e}"}), 500

    # 3) 用 BytesIO 包裝後送出去（不當附件，給前端 iframe/blob 預覽用）
    pdf_io = BytesIO(pdf_bytes)
    pdf_io.seek(0)

    headers = {
        "Cache-Control": "no-store",
        "Content-Disposition": f'inline; filename="{doc_name}.pdf"',
        "X-Content-Type-Options": "nosniff",
    }
    return send_file(
        pdf_io,
        mimetype="application/pdf",
        as_attachment=False,
        download_name=f"{doc_name}.pdf",
        headers=headers,
    )

