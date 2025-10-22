# modules/docs.py
from flask import Blueprint, request, jsonify
from db import db
from utils import send_response, jload, jdump, dver, none_if_blank, new_token
from config import STEP

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
              VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,%s,%s)
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
                jdump({"kind": "mcr-parameter"})
            ))

            # Condition table (sub 1)
            cond_json = b.get("jsonConditionContent")
            cond_arr  = b.get("arrayConditionData") or []
            cur.execute(ins, (
                new_token(), token, step_type, tier, 1, 2,
                None, None,
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
        })
        if sub == 0:
            out[t]["code"] = r["header_text"] or out[t]["code"]
            out[t]["arrayParameterData"] = jload(r["content_text"], []) or []
            out[t]["jsonParameterContent"] = jload(r["content_json"])
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
        })

    return jsonify({"success": True, "blocks": blocks})

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

# ---- Process Flow (kept simple, still step 0) -----------------
# @bp.post("/process-flow/save")
# def save_process_flow():
#     body = request.get_json(silent=True) or {}
#     token = (body.get("token") or "").strip()
#     if not token: return send_response(400, False, "Missing token")

#     tier_no = int(body.get("tier_no") or 1)
#     sub_no  = int(body.get("sub_no") or 0)
#     pf      = body.get("processFlow") or {}
#     mode    = (pf.get("mode") or "table").strip()
#     cols    = int(pf.get("cols") or 9)
#     header_json = pf.get("header_json")
#     items   = pf.get("items") or []
#     file    = pf.get("file")

#     CT_PF_TABLE, CT_PF_IMAGE = 10, 11
#     if mode not in ("table","image"):
#         return send_response(400, False, "Invalid mode")

#     if mode == "table":
#         ctype, cjson, fjson = CT_PF_TABLE, jdump({"cols": cols, "items": items}), None
#     else:
#         if not (file and file.get("asset_id")): return send_response(400, False, "Missing file.asset_id")
#         ctype, cjson, fjson = CT_PF_IMAGE, jdump({"file": file}), jdump([file])

#     with db() as (conn, cur):
#         cur.execute("""
#           SELECT content_id FROM rms_block_content
#           WHERE document_token=%s AND step_type=%s AND tier_no=%s AND sub_no=%s
#           FOR UPDATE
#         """, (token, STEP["PROCESS_FLOW"], tier_no, sub_no))
#         row = cur.fetchone()
#         if row:
#             cid = row[0]
#             cur.execute("""
#               UPDATE rms_block_content
#               SET content_type=%s, header_text=NULL, header_json=%s,
#                   content_text=NULL, content_json=%s, files=%s,
#                   metadata=%s, updated_at=NOW()
#               WHERE content_id=%s
#             """, (ctype, jdump(header_json), cjson, fjson, jdump({"kind":"process-flow"}), cid))
#             # refresh asset link if image (optional: clear old links)
#             cur.execute("""
#               DELETE l FROM rms_asset_links l
#               LEFT JOIN rms_block_content bc ON bc.content_id=l.content_id
#               WHERE bc.document_token=%s AND bc.step_type=%s AND bc.tier_no=%s AND bc.sub_no=%s
#             """, (token, STEP["PROCESS_FLOW"], tier_no, sub_no))
#             if mode=="image":
#                 cur.execute("INSERT INTO rms_asset_links (asset_id, document_token, content_id, created_at) VALUES (%s,%s,%s,NOW())",
#                             (file["asset_id"], token, cid))
#         else:
#             cid = new_token()
#             cur.execute("""
#               INSERT INTO rms_block_content
#               (content_id, document_token, step_type, tier_no, sub_no, content_type,
#                header_text, header_json, content_text, content_json, files, metadata,
#                created_at, updated_at)
#               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
#             """, (cid, token, STEP["PROCESS_FLOW"], tier_no, sub_no, ctype,
#                   None, jdump(header_json), None, cjson, fjson, jdump({"kind":"process-flow"})))
#             if mode=="image":
#                 cur.execute("INSERT INTO rms_asset_links (asset_id, document_token, content_id, created_at) VALUES (%s,%s,%s,NOW())",
#                             (file["asset_id"], token, cid))
#     return jsonify({"success": True})

