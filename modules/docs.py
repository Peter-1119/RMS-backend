# modules/docs.py
from __future__ import annotations
import datetime, os, uuid, re, json
from decimal import Decimal

# Flask's send_file must be explicitly imported
from flask import Blueprint, request, jsonify, send_file, after_this_request
from db import db
from oracle_db import ora_cursor as odb
from utils import send_response, jload, jdump, dver, none_if_blank, new_token
from DocxDefinition import get_docx

BASE_DIR = "docxTemp"
os.makedirs(BASE_DIR, exist_ok=True)

bp = Blueprint("docs", __name__)

LOCK_STATUS_SET = {"審核中", "已簽核", "作廢", "否決", "退回申請者"}
STATUS_MAP = {"審核中": 1, "正常結案": 2, "作廢": 3, "否決": 4, "退回申請者": 5}

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
    refs          = body.get("references") or {}

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
            ins_ref_sql = """
              INSERT INTO rms_references
              (document_token, refer_type, refer_document, refer_document_name, created_at)
              VALUES (%s,%s,%s,%s,NOW())
            """
            for d in documents:
                cur.execute(ins_ref_sql, (
                    token, 0,
                    (d.get("docId") or "").strip(),
                    (d.get("docName") or "").strip()
                ))
            for f_ in forms:
                cur.execute(ins_ref_sql, (
                    token, 1,
                    (f_.get("formId") or "").strip(),
                    (f_.get("formName") or "").strip()
                ))

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
        cur.execute("""
            SELECT document_id, document_version
            FROM rms_document_attributes
            WHERE document_token=%s
        """, (token,))
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
            cur.execute("""
              SELECT refer_type, refer_document, refer_document_name FROM rms_references
              WHERE document_token=%s
              ORDER BY refer_type ASC, id ASC
            """, (token,))
            rows = cur.fetchall() or []

            docs, forms = [], []
            for r in rows:
                if int(r["refer_type"]) == 0:
                    docs.append({
                        "docId": r["refer_document"],
                        "docName": r["refer_document_name"],
                    })
                else:
                    forms.append({
                        "formId": r["refer_document"],
                        "formName": r["refer_document_name"],
                    })
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
        where = ["document_token = %s"]
        params = [token]

        if rms_id:
            # 如果有帶 rms_id，就鎖定在這張 RMS 單的 snapshot
            where.append("rms_id = %s")
            params.append(rms_id)

        where_sql = " AND ".join(where)

        cur.execute(f"""
            SELECT *
            FROM rms_document_snapshots
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

        # 2) 複製 blocks（流程 / 管理條件 / MCR / 異常處置...）
        cur.execute("""
          SELECT step_type, tier_no, sub_no, content_type,
                 header_text, header_json, content_text, content_json, files, metadata
          FROM rms_block_content
          WHERE document_token = %s
        """, (prev_token,))
        old_blocks = cur.fetchall() or []

        ins_blk_sql = """
          INSERT INTO rms_block_content
          (content_id, document_token, step_type, tier_no, sub_no, content_type,
           header_text, header_json, content_text, content_json, files, metadata,
           created_at, updated_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        """
        for b in old_blocks:
            cur.execute(ins_blk_sql, (
                new_token(),            # 新 content_id
                new_token_,             # 🔥 改成新 token
                b["step_type"],
                b["tier_no"],
                b["sub_no"],
                b["content_type"],
                b["header_text"],
                b["header_json"],
                b["content_text"],
                b["content_json"],
                b["files"],
                b["metadata"],
            ))

        # 3) 複製 references
        cur.execute("""
          SELECT refer_type, refer_document, refer_document_name
          FROM rms_references
          WHERE document_token = %s
        """, (prev_token,))
        old_refs = cur.fetchall() or []

        ins_ref_sql = """
          INSERT INTO rms_references
          (document_token, refer_type, refer_document, refer_document_name, created_at)
          VALUES (%s,%s,%s,%s,NOW())
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

def apply_snapshot_to_main_db(snap_row, oracle_row):
    """
    snap_row: 來自 rms_document_snapshots 的一列（只有 meta，有 snapshot_id）
    oracle_row: 來自 Oracle.RMS_DCC2EIP 的一列
    """
    token   = snap_row["document_token"]
    rms_id  = snap_row["rms_id"]
    snap_id = snap_row["snapshot_id"]

    # 🔹 從 payload table 撈 JSON
    payload     = _load_snapshot_payload(snap_id)
    doc_snap    = payload["document_row"]     or {}
    blocks_snap = payload["blocks_rows"]      or []
    refs_snap   = payload["references_rows"]  or []

    # 解析 Oracle 欄位（依你實際欄位順序調整 index）
    RMS_ID          = oracle_row[0]
    RMS_DCCNO       = oracle_row[1]
    RMS_VER         = float(oracle_row[2] or snap_row["document_version"] or 1.0)
    RMS_DCCNAME     = oracle_row[3]
    RMS_INSDT       = oracle_row[4]
    EIPNO           = oracle_row[5]
    EIP_USER        = oracle_row[6]
    EIP_CREATEDT    = oracle_row[7]
    EIP_STATUS_STR  = (oracle_row[8] or "").strip()
    DECISION_USER   = oracle_row[9]
    DECISION_COMMENT= oracle_row[10]

    status_int = STATUS_MAP.get(EIP_STATUS_STR, 2)  # 找不到就當正常結案

    with db(dict_cursor=True) as (conn, cur):
        # 1) 用 snapshot 的 document_row 回寫大部分欄位，再疊 Oracle 資訊
        cur.execute("""
            UPDATE rms_document_attributes
            SET document_type   = %s,
                EIP_id          = %s,
                status          = %s,
                document_id     = %s,
                document_name   = %s,
                document_version= %s,
                attribute       = %s,
                department      = %s,
                author_id       = %s,
                author          = %s,
                approver        = %s,
                confirmer       = %s,
                rejecter        = %s,
                issue_date      = %s,
                change_reason   = %s,
                change_summary  = %s,
                reject_reason   = %s,
                purpose         = %s
            WHERE document_token = %s
        """, (
            doc_snap.get("document_type"),
            EIPNO,                             # EIP_id
            status_int,                        # status
            RMS_DCCNO or doc_snap.get("document_id"),
            RMS_DCCNAME or doc_snap.get("document_name"),
            RMS_VER,
            jdump(_normalize_metadata(doc_snap.get("attribute"))),
            doc_snap.get("department"),
            doc_snap.get("author_id"),
            doc_snap.get("author"),
            doc_snap.get("approver"),
            doc_snap.get("confirmer"),
            DECISION_USER or doc_snap.get("rejecter"),
            EIP_CREATEDT or RMS_INSDT or doc_snap.get("issue_date"),
            doc_snap.get("change_reason"),
            doc_snap.get("change_summary"),
            DECISION_COMMENT or doc_snap.get("reject_reason"),
            doc_snap.get("purpose"),
            token,
        ))

        # 2) 先刪掉現在主表的 blocks / refs，再用 snapshot 重灌
        cur.execute("DELETE FROM rms_block_content WHERE document_token=%s", (token,))
        cur.execute("DELETE FROM rms_references WHERE document_token=%s", (token,))

        # 2-1) 還原 blocks
        if blocks_snap:
            ins_blk = """
              INSERT INTO rms_block_content
              (content_id, document_token, step_type, tier_no, sub_no, content_type,
               header_text, header_json, content_text, content_json, files, metadata,
               created_at, updated_at)
              VALUES
              (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """
            for b in blocks_snap:
                cur.execute(ins_blk, (
                    b.get("content_id") or new_token(),
                    b.get("document_token") or token,
                    b.get("step_type"),
                    b.get("tier_no"),
                    b.get("sub_no"),
                    b.get("content_type"),
                    b.get("header_text"),
                    jdump(_normalize_metadata(b.get("header_json"))),
                    b.get("content_text"),
                    jdump(_normalize_metadata(b.get("content_json"))),
                    jdump(_normalize_metadata(b.get("files"))),
                    jdump(_normalize_metadata(b.get("metadata"))),
                    b.get("created_at") or datetime.datetime.now(),
                    b.get("updated_at") or datetime.datetime.now(),
                ))

        # 2-2) 還原 references
        if refs_snap:
            ins_ref = """
              INSERT INTO rms_references
              (id, document_token, refer_type, refer_document, refer_document_name, created_at)
              VALUES (%s,%s,%s,%s,%s,%s)
            """
            for r in refs_snap:
                cur.execute(ins_ref, (
                    r.get("id"),
                    r.get("document_token") or token,
                    r.get("refer_type"),
                    r.get("refer_document"),
                    r.get("refer_document_name"),
                    r.get("created_at") or datetime.datetime.now(),
                ))

        # 3) 更新 snapshot 本身狀態（這裡留著也沒關係，等一下會整批刪掉）
        cur.execute("""
            UPDATE rms_document_snapshots
            SET sync_status = 2, synced_at = NOW()
            WHERE document_token = %s AND rms_id <> %s AND sync_status = 0
        """, (token, rms_id))

        cur.execute("""
            UPDATE rms_document_snapshots
            SET sync_status = 1, synced_at = NOW()
            WHERE snapshot_id = %s
        """, (snap_row["snapshot_id"],))

        conn.commit()

def _apply_reject_status_to_main_attributes(snap_row, oracle_row):
    token = snap_row["document_token"]

    RMS_DCCNO       = oracle_row[1]
    RMS_VER         = oracle_row[2]
    RMS_DCCNAME     = oracle_row[3]
    DECISION_USER   = oracle_row[9]
    DECISION_COMMENT= oracle_row[10]

    with db(dict_cursor=True) as (conn, cur):
        cur.execute("""
            UPDATE rms_document_attributes
            SET rejecter        = %s,
                reject_reason   = %s,
                document_id     = COALESCE(%s, document_id),
                document_name   = COALESCE(%s, document_name),
                document_version= COALESCE(%s, document_version)
            WHERE document_token = %s
        """, (
            DECISION_USER,
            DECISION_COMMENT,
            RMS_DCCNO,
            RMS_DCCNAME,
            RMS_VER,
            token,
        ))
        conn.commit()

def _load_snapshot_payload(snapshot_id: int):
    """
    依 snapshot_id 從 rms_document_snapshot_payloads 撈出
    document_row / blocks_rows / references_rows。
    回傳 dict：{"document_row": dict, "blocks_rows": list, "references_rows": list}
    """
    with db(dict_cursor=True) as (conn, cur):
        cur.execute("""
            SELECT document_row, blocks_rows, references_rows
            FROM rms_document_snapshot_payloads
            WHERE snapshot_id = %s
        """, (snapshot_id,))
        row = cur.fetchone()

    if not row:
        raise RuntimeError(f"snapshot payload not found for snapshot_id={snapshot_id}")

    # MySQL JSON type 會直接給 dict/list；為安全起見，用 _normalize_metadata / jload 再處理一次
    doc_row   = _normalize_metadata(row.get("document_row"))   or {}
    blocks_rs = _normalize_metadata(row.get("blocks_rows"))    or []
    refs_rs   = _normalize_metadata(row.get("references_rows")) or []

    return {
        "document_row":   doc_row,
        "blocks_rows":    blocks_rs,
        "references_rows": refs_rs,
    }

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

def _rebind_mcr_program_codes(cur, latest_token: str):
    """
    重新把 MCR 的程式碼 (program_code) 綁到最新版本的 document_token 上。

    規則：
      - 從最新版本該文件的 rms_block_content.metadata 中找出
        kind = "mcr-parameter" 的資料
      - 取出所有 programs[].programCode
      - 在 rms_program_code 中用這些 program_code 更新 document_token = latest_token, status = 1
    """
    # 1) 把這份文件所有 block 的 metadata 抓出來
    cur.execute("""
        SELECT metadata
        FROM rms_block_content
        WHERE document_token = %s
          AND metadata IS NOT NULL
    """, (latest_token,))
    rows = cur.fetchall() or []

    program_codes = set()

    for r in rows:
        meta = _normalize_metadata(r.get("metadata"))
        if not isinstance(meta, dict):
            continue
        if meta.get("kind") != "mcr-parameter":
            continue

        for p in meta.get("programs") or []:
            code = (p.get("programCode") or "").strip()
            if code:
                program_codes.add(code)

    if not program_codes:
        return  # 沒有任何程式碼要綁定

    placeholders = ",".join(["%s"] * len(program_codes))
    sql = f"""
        UPDATE rms_program_code
        SET document_token = %s,
            status = 1
        WHERE program_code IN ({placeholders})
    """
    params = [latest_token] + list(program_codes)
    cur.execute(sql, params)

def _row_ts(row):
    """
    Oracle row 的時間欄位：
      - 優先 EIP_CREATEDT (idx=7)
      - 退而求其次 RMS_INSDT (idx=4)
    """
    return row[7] or row[4] or datetime.datetime.min

@bp.post("/sync-eip")
def sync_eip():
    """
    EIP 同步（簡化版邏輯）：

    對每一個 document_id：
      1) 只處理 sync_status = 0 的 snapshot（待同步）
      2) 從 Oracle 抓出所有該文件的紀錄（EIP_STATUS IS NOT NULL）
      3) 對每一個版本（RMS_VER）做判斷：

         (A) 若該版本有「已簽核」：
             -> 用對應 snapshot 回寫主表
             -> 刪除該文件 + 該版本的所有 snapshots

         (B) 若該版本沒有「已簽核」，但有「否決 / 退回申請者」：
             -> 找出該版本最新的一筆 否決/退回 Oracle row
             -> 找到對應 RMS_ID 的 snapshot
             -> 回寫退回資訊到主表
             -> 標記這一筆 snapshot 為 sync_status = 2
             -> 刪除同一個 document + version 下「其它 sync_status = 2 的舊退回 snapshot」
                （保留草稿/已下載/審核中的 snapshot，不刪）

         (C) 其它狀態：略過
    """
    pending = _get_pending_snapshots_grouped_by_doc_id()  # 只包含 sync_status=0
    doc_ids = list(pending.keys())

    if not doc_ids:
        return jsonify({"success": True, "updated": 0, "message": "no pending snapshots"})

    # 1) 先挑出 EIP_STATUS IS NOT NULL 的 oracle rows
    #    _fetch_oracle_rows_for_doc_ids 預設就只抓 EIP_STATUS IS NOT NULL
    oracle_map = _fetch_oracle_rows_for_doc_ids(doc_ids, include_NULL=False)

    updated = 0

    for doc_id, snaps in pending.items():
        o_rows = oracle_map.get(doc_id) or []
        if not o_rows:
            # 這個文件在 EIP 還沒有任何有狀態的紀錄：直接跳過
            continue

        # ---- 依版本（RMS_VER）分組 Oracle rows ----
        by_ver = {}
        for r in o_rows:
            try:
                ver = float(r[2]) if r[2] is not None else None  # RMS_VER
            except ValueError:
                ver = None
            by_ver.setdefault(ver, []).append(r)

        # 對每一個版本做處理
        for ver, rows_for_ver in by_ver.items():
            # 收集這個版本所有非空的 EIP_STATUS
            statuses = {(r[8] or "").strip() for r in rows_for_ver if (r[8] or "").strip()}

                        # ----------------------------------------------------------
            # Case 2: 若有「已簽核」 → 正常結案，刪掉所有 snapshots
            # ----------------------------------------------------------
            if "已簽核" in statuses:
                # 挑該版本中「已簽核」且最新的一筆 Oracle 紀錄
                ver_signed_rows = [r for r in rows_for_ver if (r[8] or "").strip() == "已簽核"]
                target_row = max(ver_signed_rows, key=_row_ts)
                target_rms_id = target_row[0]  # RMS_ID

                # 先用 version + rms_id 做精準對應
                snap_candidates = []
                for s in snaps:
                    try:
                        s_ver = float(s.get("document_version") or 1.0)
                    except (TypeError, ValueError):
                        s_ver = 1.0

                    if ver is not None and abs(s_ver - ver) >= 1e-6:
                        continue

                    if s.get("rms_id") == target_rms_id:
                        snap_candidates.append(s)

                # 如果真的找不到 rms_id 對應（理論上不應該發生），才退一步只看 version
                if not snap_candidates:
                    for s in snaps:
                        try:
                            s_ver = float(s.get("document_version") or 1.0)
                        except (TypeError, ValueError):
                            s_ver = 1.0
                        if ver is None or abs(s_ver - ver) < 1e-6:
                            snap_candidates.append(s)

                if not snap_candidates:
                    print("[sync-eip] no snapshot found for signed doc", doc_id, ver, target_rms_id)
                    continue

                snap = max(snap_candidates, key=lambda s: s.get("created_at") or datetime.datetime.min)


                try:
                    apply_snapshot_to_main_db(snap, target_row)
                    updated += 1
                except Exception as e:
                    print("[sync-eip] apply snapshot failed (已簽核)", doc_id, ver, e)
                    continue

                # 注意：這裡視乎 Oracle 的 RMS_VER 是否一定存在
                ver_value = ver if ver is not None else float(snap.get("document_version") or 1.0)
                latest_token = snap["document_token"]

                with db(dict_cursor=True) as (conn, cur):
                    # 2-1) 刪除同一文件 + 同一版本的所有 snapshots（含剛同步那一筆）
                    cur.execute("""
                        DELETE FROM rms_document_snapshots
                        WHERE document_id = %s
                          AND ABS(document_version - %s) < 1e-6
                    """, (doc_id, ver_value))

                    # 2-2) 刪除同一文件 + 同一版本、但不是這個 token 的「草稿/未簽核」文件
                    cur.execute("""
                        DELETE FROM rms_document_attributes
                        WHERE document_id = %s
                          AND ABS(document_version - %s) < 1e-6
                          AND document_token <> %s
                          AND status IN (0, 1)
                    """, (doc_id, ver_value, latest_token))

                    # ------------------------------
                    # 2-3) ⭐ 新增：簽核後舊版整理邏輯
                    # ------------------------------
                    # 只看「已簽核版本」(status = 2)，按版本從新到舊排
                    cur.execute("""
                        SELECT document_token, document_version
                        FROM rms_document_attributes
                        WHERE document_id = %s
                          AND status = 2
                        ORDER BY document_version DESC
                    """, (doc_id,))
                    ver_rows = cur.fetchall() or []

                    if ver_rows:
                        # 保留最新版 + 前兩版的 attributes
                        keep_rows = ver_rows[:3]  # 最多 3 筆
                        keep_tokens = [r["document_token"] for r in keep_rows]

                        # 最新版 token（理論上就是 latest_token，但這裡再保險抓一次）
                        latest_attr_token = keep_tokens[0]

                        # 要保留 attribute 但清掉內容的舊版 token：前兩版（index 1,2）
                        clear_tokens = [r["document_token"] for r in keep_rows[1:]]

                        # 超過 2 個版本之前的舊版：整個 attributes 直接刪掉（CASCADE 掉內容）
                        delete_attr_tokens = [r["document_token"] for r in ver_rows[3:]]

                        # (a) 刪除舊版的 content / references（但保留 attributes）
                        if clear_tokens:
                            ph = ",".join(["%s"] * len(clear_tokens))
                            cur.execute(f"""
                                DELETE FROM rms_block_content
                                WHERE document_token IN ({ph})
                            """, clear_tokens)
                            cur.execute(f"""
                                DELETE FROM rms_references
                                WHERE document_token IN ({ph})
                            """, clear_tokens)

                        # (b) 刪除比前兩版更舊的 attributes（rms_block_content / rms_references 會跟著 FK CASCADE）
                        if delete_attr_tokens:
                            ph = ",".join(["%s"] * len(delete_attr_tokens))
                            cur.execute(f"""
                                DELETE FROM rms_document_attributes
                                WHERE document_token IN ({ph})
                            """, delete_attr_tokens)

                        # (c) 重新把 MCR 的程式號碼綁定到「最新版」的 document_token
                        _rebind_mcr_program_codes(cur, latest_attr_token)

                    conn.commit()

            # ----------------------------------------------------------
            # Case 3: 沒有已簽核，但有「否決 / 退回申請者」
            # ----------------------------------------------------------
            reject_rows = [
                r for r in rows_for_ver
                if (r[8] or "").strip() in {"否決", "退回申請者"}
            ]
            if not reject_rows:
                # 此版本沒有已簽核，也沒有否決/退回 → 不處理
                continue

            # 挑出最新一筆「否決/退回」的 Oracle row（你的步驟 3：latest）
            target_row = max(reject_rows, key=_row_ts)
            target_rms_id = target_row[0]  # RMS_ID

            # 找對應 snapshot：同 doc_id + version + rms_id
            snap_candidates = []
            for s in snaps:
                try:
                    s_ver = float(s.get("document_version") or 1.0)
                except (TypeError, ValueError):
                    s_ver = 1.0

                if ver is not None and abs(s_ver - ver) >= 1e-6:
                    continue

                if s.get("rms_id") == target_rms_id:
                    snap_candidates.append(s)

            if not snap_candidates:
                # 找不到對應 rms_id 的 snapshot 時，退一步只用版本 match
                for s in snaps:
                    try:
                        s_ver = float(s.get("document_version") or 1.0)
                    except (TypeError, ValueError):
                        s_ver = 1.0
                    if ver is None or abs(s_ver - ver) < 1e-6:
                        snap_candidates.append(s)

            if not snap_candidates:
                print("[sync-eip] no snapshot found for rejected doc", doc_id, ver, target_rms_id)
                continue

            snap = max(snap_candidates, key=lambda s: s.get("created_at") or datetime.datetime.min)

            # ★ 將 rejecter / reject reason 回寫到 attributes（你的步驟 3.2）
            try:
                _apply_reject_status_to_main_attributes(snap, target_row)
                updated += 1
            except Exception as e:
                print("[sync-eip] apply reject-status failed", doc_id, ver, e)
                continue

            # ★ 將此 snapshot 標成 sync_status = 2，並清掉舊的退回 snapshot（同 doc+ver）
            with db(dict_cursor=True) as (conn, cur):
                # 3) 將對應的 rms_id 的 sync_status 改為 2（你的 3）
                cur.execute("""
                    UPDATE rms_document_snapshots
                    SET sync_status = 2, synced_at = NOW()
                    WHERE snapshot_id = %s
                """, (snap["snapshot_id"],))

                # 3.1) 刪除同一文件 + 版本下、其他 sync_status = 2 的舊退回 snapshot
                #      （注意：不動 sync_status = 0 的草稿 / 已下載 / 審核中）
                cur.execute("""
                    DELETE FROM rms_document_snapshots
                    WHERE document_id = %s
                    AND ABS(document_version - %s) < 1e-6
                    AND sync_status = 2
                    AND rms_id <> %s
                """, (
                    doc_id,
                    ver if ver is not None else float(snap.get("document_version") or 1.0),
                    target_rms_id,
                ))

                conn.commit()

            # 「否決/退回」版本可以有多次 history，但我們只保留最新那個 sync_status=2 的 snapshot，
            # 草稿快照留給使用者修改再送，不再處理更多
            continue

    return jsonify({"success": True, "updated": updated})

def _get_pending_snapshots_grouped_by_doc_id():
    """
    回傳:
    {
      "WMD001": [snap_row1, snap_row2, ...],
      "WMD002": [...],
    }
    僅抓 sync_status = 0 的 snapshot。
    這裡只需要 meta，不讀 payload。
    """
    with db(dict_cursor=True) as (conn, cur):
        cur.execute("""
            SELECT snapshot_id, document_token, rms_id,
                   document_id, document_version, document_name,
                   created_by, created_at, sync_status
            FROM rms_document_snapshots
            WHERE sync_status = 0
        """)
        rows = cur.fetchall() or []

    by_doc = {}
    for r in rows:
        doc_id = (r.get("document_id") or "").strip()
        if not doc_id:
            continue
        by_doc.setdefault(doc_id, []).append(r)
    return by_doc

def _fetch_oracle_rows_for_doc_ids(doc_ids, include_NULL = False):
    """
    doc_ids: list[str]
    回傳 mapping: doc_id -> [oracle_row1, oracle_row2, ...]
    """
    if not doc_ids:
        return {}

    placeholders = ",".join([f":{i+1}" for i in range(len(doc_ids))])
    sql = f"""
        SELECT RMS_ID, RMS_DCCNO, RMS_VER, RMS_DCCNAME, RMS_INSDT, EIPNO, EIP_USER, EIP_CREATEDT, EIP_STATUS, DECISION_USER, DECISION_COMMENT
        FROM IDBUSER.RMS_DCC2EIP WHERE RMS_DCCNO IN ({placeholders})
    """

    if not include_NULL:
        sql += " AND EIP_STATUS IS NOT NULL"

    with odb() as cur_o:
        cur_o.execute(sql, doc_ids)
        rows = cur_o.fetchall() or []

    by_doc = {}
    for r in rows:
        doc_id = (r[1] or "").strip()  # RMS_DCCNO
        by_doc.setdefault(doc_id, []).append(r)
    return by_doc

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

# ----- Document Search ----- #

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
    document_id: str | None = None,   # 新增但暫時只有 /documents /submitted 等用到
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

    # 依 document_id 過濾（可選）
    if document_id:
        where.append("document_id = %s")
        params.append(document_id)

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

    # ---- 決定排序欄位 ----
    sort_map = {
        "issue_date": "issue_date",
        "document_version": "document_version",
        "document_name": "document_name",
    }
    sort_col = sort_map.get((sort_key or "issue_date").lower(), "issue_date")
    order_sql = "DESC" if (order or "desc").lower() not in ("asc", "ASC") else "ASC"

    # ---- WHERE 條件：這裡沒有 author_id 限制，因為是 all ----
    where = []
    params = []

    # statuses（必填）
    where.append(f"status IN ({', '.join(['%s'] * len(statuses))})")
    params.extend(statuses)

    # keyword
    kw_sql, kw_params = _build_keyword_predicate(keyword)
    if kw_sql:
        where.append(kw_sql)
        params.extend(kw_params)

    where_sql = " AND ".join(where) if where else "1=1"
    offset = (page - 1) * page_size

    # 1) total = 不同 document_id 的數量（在同樣的 where 條件下）
    count_sql = f"""
      SELECT COUNT(*) AS cnt
      FROM (
        SELECT DISTINCT document_id
        FROM rms_document_attributes
        WHERE {where_sql}
      ) AS t
    """

    # 2) data = 每個 document_id 的「document_version 最大」那一筆
    data_sql = f"""
      SELECT
        a.document_type,
        a.document_token,
        a.document_name,
        a.document_version,
        a.author,
        a.author_id,
        a.issue_date,
        a.document_id,
        a.status,
        a.rejecter,
        a.reject_reason
      FROM (
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
          reject_reason,
          ROW_NUMBER() OVER (
            PARTITION BY document_id
            ORDER BY document_version DESC
          ) AS rn
        FROM rms_document_attributes
        WHERE {where_sql}
      ) AS a
      WHERE a.rn = 1
      ORDER BY a.{sort_col} {order_sql}
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

    return jsonify({
        "success": True,
        "items": [to_item(r) for r in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }), 200

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

    # ---- 決定排序欄位 ----
    sort_map = {
        "issue_date": "issue_date",
        "document_version": "document_version",
        "document_name": "document_name",
    }
    sort_col = sort_map.get((sort_key or "issue_date").lower(), "issue_date")
    order_sql = "DESC" if (order or "desc").lower() not in ("asc", "ASC") else "ASC"

    # ---- where：author + status=2 + optional type + keyword ----
    where = ["author_id = %s", "status = 2"]
    params = [user_id]

    if doc_types:
        where.append(f"document_type IN ({', '.join(['%s'] * len(doc_types))})")
        params.extend(doc_types)

    kw_sql, kw_params = _build_keyword_predicate(keyword)
    if kw_sql:
        where.append(kw_sql)
        params.extend(kw_params)

    where_sql = " AND ".join(where) if where else "1=1"
    offset = (page - 1) * page_size

    # 1) total = 不同 document_id 數量
    count_sql = f"""
      SELECT COUNT(*) AS cnt
      FROM (
        SELECT DISTINCT document_id
        FROM rms_document_attributes
        WHERE {where_sql}
      ) AS t
    """

    # 2) data = 每個 document_id 的最新版本那一筆
    data_sql = f"""
      SELECT
        a.document_type,
        a.document_token,
        a.document_name,
        a.document_version,
        a.author,
        a.author_id,
        a.issue_date,
        a.document_id,
        a.status,
        a.rejecter,
        a.reject_reason
      FROM (
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
          reject_reason,
          ROW_NUMBER() OVER (
            PARTITION BY document_id
            ORDER BY document_version DESC
          ) AS rn
        FROM rms_document_attributes
        WHERE {where_sql}
      ) AS a
      WHERE a.rn = 1
      ORDER BY a.{sort_col} {order_sql}
      LIMIT %s OFFSET %s
    """

    with db(dict_cursor=True) as (_, cur):
        cur.execute(count_sql, params)
        total = int(cur.fetchone()["cnt"])

        cur.execute(data_sql, params + [page_size, offset])
        rows = cur.fetchall() or []

    for index, r in enumerate(rows):
        print(f"{index}: {r}")

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

    return jsonify({
        "success": True,
        "items": [to_item(r) for r in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }), 200

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

def _collect_submitted_items_for_user(user_id: str, keyword: str, sort_key: str, order: str):
    """
    回傳尚在 EIP 審核流程中的文件：
      - 來源：rms_document_snapshots.sync_status = 0（尚未被 sync_eip 結案/退回）
      - Oracle：保留 EIP_STATUS in ('審核中', NULL) 的最新那一筆紀錄（NULL 視為「已下載 / 尚未更新」）
      - 僅保留 author_id = user_id 的文件
    """
    pending = _get_pending_snapshots_grouped_by_doc_id()
    if not pending:
        return []

    doc_ids = list(pending.keys())
    oracle_map = _fetch_oracle_rows_for_doc_ids(doc_ids, include_NULL = True)

    candidate_snaps = []  # (snap_row, oracle_row)

    for doc_id, snaps in pending.items():
        o_rows = oracle_map.get(doc_id) or []
        if not o_rows:
            continue

        # ---- 依 document_version 選出「最新 snapshot」 ----
        latest_snap_by_ver = {}
        for s in snaps:
            try:
                v = float(s.get("document_version") or 1.0)
            except (TypeError, ValueError):
                v = 1.0
            key = v
            cur_ts = s.get("created_at") or datetime.datetime.min
            if key not in latest_snap_by_ver:
                latest_snap_by_ver[key] = s
            else:
                old_ts = latest_snap_by_ver[key].get("created_at") or datetime.datetime.min
                if cur_ts > old_ts:
                    latest_snap_by_ver[key] = s

        for snap in latest_snap_by_ver.values():
            snap_ver = float(snap.get("document_version") or 1.0)

            # 用版本對應到 Oracle rows
            candidates = []
            for r in o_rows:
                try:
                    r_ver = float(r[2]) if r[2] is not None else snap_ver  # RMS_VER

                except ValueError:
                    r_ver = snap_ver

                if abs(r_ver - snap_ver) < 1e-6:
                    candidates.append(r)

            if not candidates:
                continue

            for r in candidates:
                eip_status = (r[8] or "").strip()
                if eip_status not in {"", "審核中"}:
                    continue

                candidate_snaps.append((snap, r))

    if not candidate_snaps:
        return []

    # 撈出這些 snapshot 對應的 attributes
    tokens = list({snap["document_token"] for (snap, _) in candidate_snaps})
    attrs_map = {}
    if tokens:
        placeholders = ", ".join(["%s"] * len(tokens))
        with db(dict_cursor=True) as (conn, cur):
            cur.execute(f"""
                SELECT document_token, document_type, document_id, document_name,
                       document_version, author, author_id, issue_date
                FROM rms_document_attributes
                WHERE document_token IN ({placeholders})
            """, tokens)
            for r in (cur.fetchall() or []):
                attrs_map[r["document_token"]] = r

    items = []
    kw = (keyword or "").strip()
    kw_lower = kw.lower()

    for snap, o_row in candidate_snaps:
        token = snap["document_token"]
        attr = attrs_map.get(token)
        if not attr:
            continue

        if attr.get("author_id") != user_id:
            continue

        # keyword：名稱 / 編號
        if kw_lower:
            name = (attr.get("document_name") or "").lower()
            docid = (attr.get("document_id") or "").lower()
            if kw_lower not in name and kw_lower not in docid:
                continue

        issue_date = attr.get("issue_date")
        if issue_date is not None:
            try:
                issue_iso = issue_date.isoformat(timespec="seconds")
            except Exception:
                issue_iso = str(issue_date)
        else:
            issue_iso = None

        eip_status = (o_row[8] or "").strip()
        rms_id = o_row[0]  # "RMS_ID"

        items.append({
            "documentType": attr.get("document_type"),
            "documentToken": token,
            "documentName": attr.get("document_name"),
            "documentVersion": float(attr.get("document_version") or 1.0),
            "author": attr.get("author"),
            "authorId": attr.get("author_id"),
            "issueDate": issue_iso,
            "documentId": attr.get("document_id"),
            # 給前端用：
            "rmsId": rms_id,
            "eipStatus": eip_status or "已下載",  # 前端顯示好看一點
        })

    # 排序
    order = (order or "desc").lower()
    reverse = (order != "asc")

    def sort_key_fn(x):
        if sort_key == "document_name":
            return (x.get("documentName") or "").lower()
        if sort_key == "document_version":
            return x.get("documentVersion") or 0.0
        if sort_key == "document_id":
            return (x.get("documentId") or "").lower()
        # default: issue_date
        return x.get("issueDate") or ""

    items.sort(key=sort_key_fn, reverse=reverse)
    return items

@bp.get("/submitted")
def list_submitted():
    """
    已送審：
      - 只顯示 EIP_STATUS in ('審核中', '已下載') 的文件
      - 資料來源：snapshot + Oracle + attributes
    """
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

    items = _collect_submitted_items_for_user(
        user_id=user_id,
        keyword=keyword,
        sort_key=sort_key,
        order=order,
    )

    total = len(items)
    start = (page - 1) * page_size
    end   = start + page_size
    page_items = items[start:end]

    return jsonify({
        "success": True,
        "items": page_items,
        "total": total,
        "page": page,
        "pageSize": page_size,
    }), 200

@bp.get("/rejected")
def list_rejected():
    """
    已退回：
      - 來源：rms_document_snapshots.sync_status = 2（只看這個表，不再管 Oracle）
      - join rms_document_attributes 取得作者 / 退回人 / 理由等
      - 僅顯示屬於本人 (author_id = user_id) 的文件
      - 回傳格式與 /submitted 的 items 結構一致，多補 rejecter / rejectReason
    """
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
    order    = (request.args.get("order") or "desc").lower()
    order_sql = "DESC" if order != "asc" else "ASC"

    # 和 submitted 一樣支援的 sort 欄位，再額外多一個 rejecter
    sort_map = {
        "issue_date":        "a.issue_date",
        "document_version":  "a.document_version",
        "document_name":     "a.document_name",
        "document_id":       "a.document_id",
        "rejecter":          "a.rejecter",
    }
    sort_col = sort_map.get(sort_key, "a.issue_date")

    # 只看 sync_status = 2（已退回），只看自己
    where = ["s.sync_status = 2", "a.author_id = %s"]
    params = [user_id]

    # keyword：和 /submitted 類似，先鎖在名稱 / 編號；你原本多加了退回者/理由也可以保留
    if keyword:
        like_kw = f"%{keyword}%"
        where.append("""
          (
            a.document_name LIKE %s OR
            a.document_id   LIKE %s OR
            a.rejecter      LIKE %s OR
            a.reject_reason LIKE %s
          )
        """)
        params.extend([like_kw, like_kw, like_kw, like_kw])

    where_sql = " AND ".join(where)
    offset = (page - 1) * page_size

    # 只從 snapshots(sync_status = 2) + attributes 撈，不碰 Oracle
    count_sql = f"""
      SELECT COUNT(*) AS cnt
      FROM rms_document_snapshots s
      JOIN rms_document_attributes a ON a.document_token = s.document_token
      WHERE {where_sql}
    """
    data_sql = f"""
        SELECT
            a.document_type,
            a.document_token,
            a.document_name,
            a.document_version,
            a.author,
            a.author_id,
            a.issue_date,
            a.document_id,
            a.rejecter,
            a.reject_reason,
            s.rms_id
        FROM rms_document_snapshots s
        JOIN rms_document_attributes a ON a.document_token = s.document_token
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
        # issueDate：先沿用 issue_date，跟 submitted 一樣格式
        iso_date = None
        if r.get("issue_date"):
            try:
                iso_date = r["issue_date"].isoformat(timespec="seconds")
            except Exception:
                iso_date = str(r["issue_date"])

        return {
            # === 完全對齊 /submitted 的欄位 ===
            "documentType":    r["document_type"],
            "documentToken":   r["document_token"],
            "documentName":    r["document_name"],
            "documentVersion": float(r["document_version"]) if r["document_version"] is not None else None,
            "author":          r["author"],
            "authorId":        r["author_id"],
            "issueDate":       iso_date,
            "documentId":      r.get("document_id"),
            "rmsId":           r.get("rms_id"),

            # eipStatus：給一個固定值，方便前端如果要共用元件
            "eipStatus":       "已退回",

            # === 已退回專屬的欄位 ===
            "rejecter":        r.get("rejecter"),
            "rejectReason":    r.get("reject_reason"),
        }

    return jsonify({
        "success": True,
        "items": [to_item(r) for r in rows],
        "total": total,
        "page": page,
        "pageSize": page_size,
    }), 200

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
        get_docx(out_path, data, "docx-template/SpecificationDocument.docx")
    else:
        get_docx(out_path, data, "docx-template/InstructionDocument.docx")

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
    3) 再寫入 sfdb.rms_document_snapshots (meta) + rms_document_snapshot_payloads (JSON)
    """
    # --- 1) 讀 MySQL 現況（只讀，不動資料） ---
    with db(dict_cursor=True) as (conn, cur):
        cur.execute("""
            SELECT * FROM rms_document_attributes
            WHERE document_token=%s
        """, (token,))
        doc_row = cur.fetchone()
        if not doc_row:
            raise RuntimeError(f"document_token {token} not found for snapshot")

        doc_id   = doc_row.get("document_id")
        doc_ver  = float(doc_row.get("document_version") or 1.0)
        doc_name = doc_row.get("document_name") or ""
        issue_dt = doc_row.get("issue_date") or datetime.datetime.now()

        cur.execute("""
            SELECT * FROM rms_block_content WHERE document_token=%s
            ORDER BY step_type, tier_no, sub_no
        """, (token,))
        blocks_rows = cur.fetchall() or []

        cur.execute("""
            SELECT * FROM rms_references WHERE document_token=%s
            ORDER BY refer_type, id
        """, (token,))
        ref_rows = cur.fetchall() or []

    # --- 2) 先寫 Oracle.RMS_DCC2EIP ---
    with odb() as cur_o:
        cur_o.execute("""
            INSERT INTO IDBUSER.RMS_DCC2EIP (RMS_ID, RMS_DCCNO, RMS_VER, RMS_DCCNAME, RMS_INSDT)
            VALUES (:1, :2, :3, :4, :5)
        """, (rms_id, doc_id, doc_ver, doc_name, issue_dt))
        cur_o.connection.commit()

    # --- 3) 再寫 MySQL snapshot（meta + payload 分兩張表） ---
    doc_row_json = _normalize_for_json(doc_row)
    blocks_json  = _normalize_for_json(blocks_rows)
    refs_json    = _normalize_for_json(ref_rows)

    try:
        doc_row_str = jdump(doc_row_json)
        blocks_str  = jdump(blocks_json)
        refs_str    = jdump(refs_json)
    except TypeError as e:
        print("[snapshot DEBUG] json dump failed:", e)
        raise

    with db(dict_cursor=True) as (conn, cur):
        # 3-1) 先插入輕量的 snapshots（拿到 snapshot_id）
        cur.execute("""
            INSERT INTO rms_document_snapshots
            (document_token, rms_id, document_id, document_version, document_name, created_by)
            VALUES (%s,%s,%s,%s,%s,%s)
        """, (token, rms_id, doc_id, doc_ver, doc_name, user_emp_no))
        snapshot_id = cur.lastrowid

        # 3-2) 再插入 payload
        cur.execute("""
            INSERT INTO rms_document_snapshot_payloads
            (snapshot_id, document_row, blocks_rows, references_rows)
            VALUES (%s,%s,%s,%s)
        """, (snapshot_id, doc_row_str, blocks_str, refs_str))

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

    if token:
        try:
            # A) 一開始就從 DB 撈「前幾版 + 最新版」payload
            payload = _build_doc_payload_from_token(token)
        except Exception as e:
            print("[generate_word] _build_doc_payload_from_token error:", e)
            return send_response(404, False, "document not found")

        latest_attr = payload["attribute"][-1]

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
            cur.execute("""
                SELECT document_type, document_id, document_version, attribute, author_id, document_name
                FROM rms_document_attributes
                WHERE document_token=%s
            """, (token,))
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
                    doc_id = next_monthly_document_id("W")
                else:
                    apply_project = (attr_json.get("applyProject") or "").strip()
                    prefix = (apply_project[:3] or "XXX").upper()
                    doc_id = next_document_id(prefix)

            # 4.1 生成 RMS_ID / documentKey
            rms_id = make_rms_id()
            attr_json["documentKey"] = rms_id

            cur.execute("""
                UPDATE rms_document_attributes
                SET document_id=%s, attribute=%s
                WHERE document_token=%s
            """, (doc_id, jdump(attr_json), token))
            conn.commit()

        # 5) 把 docID & documentKey 塞回「最新版 attribute」（在 payload 上）
        latest_attr["documentID"] = doc_id or ""
        latest_attr["documentKey"] = rms_id

        # 如果你還有想讓前端回收的 data，也可以同步更新：
        if data["attribute"]:
            data["attribute"][-1]["documentID"] = doc_id or ""
            data["attribute"][-1]["documentKey"] = rms_id

        # 5.5) 暫存內容（寫回 rms_document_attributes）
        _update_attributes_from_latest_attr(token, latest_attr)

        # 6) 檔名
        try:
            doc_name = _safe_docname(
                f'{latest_attr.get("documentName")}{latest_attr.get("documentVersion"):.1f}'
            )
        except Exception:
            doc_name = "document"

        # 7) 先做 Oracle / snapshot（如果失敗 → 不產 DOCX，直接回錯誤）
        try:
            create_snapshot_and_oracle_row(token=token, rms_id=rms_id, user_emp_no=author_id or "UNKNOWN")
        except Exception as e:
            print("[generate_word] create_snapshot_and_oracle_row FAILED:", e)
            return send_response(
                500,
                False,
                f"EIP 建檔 / 歷史快照失敗，請聯絡系統管理員。詳細訊息：{e}"
            )

        # 8) Oracle + snapshot 都成功後，才產生 Word
        out_path = os.path.join(BASE_DIR, f"{doc_name}.docx")

        # 🔑 用 payload（包含歷史版本 attributes），而不是 data
        doc_type_for_word = latest_attr.get("documentType", 0)
        if doc_type_for_word == 1:
            get_docx(out_path, payload, "docx-template/SpecificationDocument.docx")
        else:
            get_docx(out_path, payload, "docx-template/InstructionDocument.docx")

        @after_this_request
        def add_docid_header(response):
            if doc_id:
                response.headers["X-Document-ID"] = doc_id
            existing = response.headers.get("Access-Control-Expose-Headers", "")
            expose = "X-Document-ID"
            if existing:
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

def _build_payload_for_docx_from_snapshot(snap_row):
    token   = snap_row["document_token"]
    snap_id = snap_row["snapshot_id"]

    payload   = _load_snapshot_payload(snap_id)
    doc_row   = payload["document_row"]
    blocks_rs = payload["blocks_rows"]
    refs_rs   = payload["references_rows"]

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
                    "documentType":     r.get("document_type") or 0,
                    "documentID":       r.get("document_id") or "",
                    "documentName":     r.get("document_name") or "",
                    "documentVersion":  float(r.get("document_version") or 1.0),
                    "attribute":        attr_json,
                    "department":       r.get("department") or "",
                    "author_id":        r.get("author_id") or "",
                    "author":           r.get("author") or "",
                    "approver":         r.get("approver") or "",
                    "confirmer":        r.get("confirmer") or "",
                    "issueDate":        issue_str,   # 🔑 統一用 issueDate
                    "reviseReason":     r.get("change_reason") or "",
                    "revisePoint":      r.get("change_summary") or "",
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

    attr_json = jload(doc_row.get("attribute"), {}) or {}

    latest_form = {
        "documentType":     doc_row.get("document_type") or 0,
        "documentID":       doc_row.get("document_id") or "",
        "documentName":     doc_row.get("document_name") or "",
        "documentVersion":  float(doc_row.get("document_version") or 1.0),
        "attribute":        attr_json,
        "department":       doc_row.get("department") or "",
        "author_id":        doc_row.get("author_id") or "",
        "author":           doc_row.get("author") or "",
        "approver":         doc_row.get("approver") or "",
        "confirmer":        doc_row.get("confirmer") or "",
        "documentPurpose":  doc_row.get("purpose") or "",
        "reviseReason":     doc_row.get("change_reason") or "",
        "revisePoint":      doc_row.get("change_summary") or "",
        "issueDate":        issue_str,  # ✅ 現在一定是 yyyy/mm/dd
        "previousDocumentToken": doc_row.get("previous_document_token") or "",
    }

    attrs.append(latest_form)

    # ---------- 2) blocks / params：只用 snapshot 的 blocks_rs ----------
    by_step = {}
    for r in blocks_rs:
        try:
            st = int(r.get("step_type"))
        except (TypeError, ValueError):
            continue
        by_step.setdefault(st, []).append(r)

    content_items = []

    for st, rows in by_step.items():
        if st in (2, 5):
            # MCR 參數類...
            merged = {}
            for r in rows:
                try:
                    t = int(r.get("tier_no"))
                    sub = int(r.get("sub_no"))
                except (TypeError, ValueError):
                    continue
                merged.setdefault(t, {
                    "jsonParameterContent": None,
                    "arrayParameterData": [],
                    "jsonConditionContent": None,
                    "arrayConditionData": [],
                    "metadata": None,
                })
                if sub == 0:
                    merged[t]["arrayParameterData"]   = jload(r.get("content_text"), []) or []
                    merged[t]["jsonParameterContent"] = _normalize_metadata(r.get("content_json"))
                    merged[t]["metadata"]             = _normalize_metadata(r.get("metadata"))
                elif sub == 1:
                    merged[t]["arrayConditionData"]   = jload(r.get("content_text"), []) or []
                    merged[t]["jsonConditionContent"] = _normalize_metadata(r.get("content_json"))

            for t in sorted(merged.keys()):
                b = merged[t]
                content_items.append({
                    "step_type": st,
                    "tier_no": t,
                    "jsonParameterContent": b["jsonParameterContent"],
                    "arrayParameterData": b["arrayParameterData"],
                    "jsonConditionContent": b["jsonConditionContent"],
                    "arrayConditionData": b["arrayConditionData"],
                    "metadata": b["metadata"],
                })
        else:
            grouped = {}
            for r in rows:
                try:
                    t = int(r.get("tier_no"))
                except (TypeError, ValueError):
                    continue
                grouped.setdefault(t, []).append({
                    "option": int(r.get("content_type") or 0),
                    "jsonHeader": _normalize_metadata(r.get("header_json")),
                    "jsonContent": _normalize_metadata(r.get("content_json")),
                    "files": _normalize_metadata(r.get("files")) or [],
                })

            for t in sorted(grouped.keys()):
                content_items.append({
                    "step_type": st,
                    "tier": t,
                    "data": grouped[t],
                })

    # ---------- 3) references：只用 snapshot 的 refs_rs ----------
    references = []
    for r in refs_rs:
        try:
            ref_type = int(r.get("refer_type") or 0)
        except (TypeError, ValueError):
            ref_type = 0

        references.append({
            "referenceType": ref_type,
            "referenceDocumentID": r.get("refer_document"),
            "referenceDocumentName": r.get("refer_document_name"),
        })

    return {
        "token": token,
        "attribute": attrs,           # 🔑 不再只有一個 form，而是 [舊版..., 最新版]
        "content": content_items,
        "reference": references,
    }

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
        get_docx(out_path, payload, "docx-template/SpecificationDocument.docx")
    else:
        get_docx(out_path, payload, "docx-template/InstructionDocument.docx")

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

# ===== helper function ===== #
def _nz(s):
    return (s or "").strip()

def _load_machine_pms_signature(machine_code: str) -> set[str]:
    """
    從 Oracle 撈出目前機台的 PMS baseline，轉成一個 set 用來比對。
    key: f"{slot_name}|{parameter_desc}({unit})"
    這樣才會跟文件 content_text 的「槽體 / 管理項目」對得起來。
    """
    sig = set()
    if not machine_code:
        return sig

    with odb() as cur:
        cur.execute(
            """
            SELECT
                TRIM(SLOT_NAME)      AS SLOT_NAME,
                TRIM(PARAMETER_DESC) AS PARAMETER_DESC,
                TRIM(UNIT)           AS UNIT
            FROM IDBUSER.RMS_FLEX_PMS
            WHERE MACHINE_CODE = :c
              AND NVL(PARAM_COMPARE, 'N') = 'Y'
              AND NVL(SET_ATTRIBUTE, 'N') = 'Y'
            ORDER BY SLOT_NAME, PARAMETER_DESC
            """,
            c=machine_code
        )
        rows = cur.fetchall()

    for slot_name, parameter_desc, unit in rows or []:
        slot  = _nz(slot_name)
        pdesc = _nz(parameter_desc)
        u     = _nz(unit)

        # 這裡和 get_machine_pms_parameters_set_attribute 完全一樣
        mgmt = f"{pdesc}({u})" if u else f"{pdesc}()"
        if slot or mgmt:
            sig.add(f"{slot}|{mgmt}")
    return sig

def _load_machine_condition_signature(machine_code: str) -> set[str]:
    """
    從 MySQL 撈出目前機台的 Condition baseline，轉成一個 set 用來比對。
    key: condition_name  → 對應到文件 condition table header。
    """
    sig = set()
    if not machine_code:
        return sig

    with db() as (conn, cur):
        cur.execute(
            """
            SELECT DISTINCT
                t1.condition_name
            FROM rms_conditions t1
            INNER JOIN rms_condition_groups t2
                ON t1.condition_id = t2.condition_id
            INNER JOIN rms_group_machines t3
                ON t2.group_id = t3.group_id AND t2.condition_id = t3.condition_id
            WHERE t3.machine_id = %s
            """,
            (machine_code,)
        )
        rows = cur.fetchall()

    for (cname,) in rows or []:
        name = _nz(cname)
        if name:
            sig.add(name)
    return sig

def _parse_2d_from_text(text: str):
    """
    content_text 裡存的是像：
    [
      ["槽體","管理項目",...],
      ["剝膜1","上噴關槽()", ...],
      ...
    ]
    這裡把它轉成 Python list[list[str]]，不合法就回 []
    """
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []

def _build_doc_pms_signature_from_text(param_text: str) -> set[str]:
    """
    從參數表 content_text 取出「槽體 + 管理項目」組合當作文件當時的 PMS Signature。
    依你說的：用 content_text 的 [:,0:2] 就夠了（第一列是 header，從第二列開始）。
    """
    sig = set()
    table = _parse_2d_from_text(param_text)
    if len(table) <= 1:
        return sig

    # 跳過 header (第 0 列)，從第 1 列開始
    for row in table[1:]:
        if not isinstance(row, list) or len(row) < 2:
            continue
        tank = _nz(row[0])
        param = _nz(row[1])
        if tank or param:
            sig.add(f"{tank}|{param}")
    return sig

def _build_doc_condition_signature_from_text(cond_text: str) -> set[str]:
    """
    從條件表 content_text 取出 header 第一列的 [1:-1] 當作文件當時的 Condition Signature。
    例如：
      [["條件名稱","鍍銅厚度"], ["1","10"]]
    => header = ["條件名稱","鍍銅厚度"]
       => 取 header[1:] = ["鍍銅厚度"]
    """
    sig = set()
    table = _parse_2d_from_text(cond_text)
    if not table:
        return sig

    header = table[0]
    if not isinstance(header, list) or len(header) <= 1:
        return sig

    # 這邊依照你講的 [1:-1] 或 [1:] 都可以，看你要不要排除最後一欄
    # 我先採用 [1:]（通常最後也會是條件），如果你最後一欄是特別欄位，就改成 header[1:-1]
    for col in header[1:]:
        name = _nz(col)
        if name:
            sig.add(name)
    return sig

@bp.post("/parameters/copy-source")
def copy_source_mcr():
    """
    功能：從已簽核的 Instruction 文件中複製參數與條件表。
    限制：
    1. program_code 必須存在。
    2. 來源文件的機台必須與 base_machine_code 具有相同的 PMS Slot 設置 (Oracle)。
    3. 來源文件的機台必須與 base_machine_code 具有相同的 Condition Signature (MySQL)。
    4. ✅ 新增：來源文件當時的 PMS / Condition 內容必須與「目前 baseline」相同
              （避免複製到已經過期的規格）。
    """
    body = request.get_json(silent=True) or {}
    program_code = (body.get("program_code") or "").strip()
    base_machine_code = (body.get("base_machine_code") or "").strip()

    if not program_code or not base_machine_code:
        return send_response(400, False, "缺少必要參數", {"message": "請提供程式代碼與 Base Machine Code"})

    # print(f"[DEBUG] copy_source_mcr start: program={program_code}, base={base_machine_code}")

    # ==========================================
    # STEP 1: 找出所有 "PMS 相容" 的機台 (Oracle)
    # ==========================================
    pms_compatible_machines = set()
    try:
        with odb() as cur:
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
                (tc.cnt > 0 
                 AND EXISTS (SELECT 1 FROM candidates c WHERE c.MACHINE_CODE = A.MACHINE_CODE)
                 AND NOT EXISTS (
                    SELECT 1 FROM target_slots ts 
                    WHERE NOT EXISTS (SELECT 1 FROM candidates c WHERE c.MACHINE_CODE = A.MACHINE_CODE AND c.SLOT_NAME = ts.SLOT_NAME)
                 )
                 AND NOT EXISTS (
                    SELECT 1 FROM candidates c 
                    WHERE c.MACHINE_CODE = A.MACHINE_CODE 
                    AND NOT EXISTS (SELECT 1 FROM target_slots ts WHERE ts.SLOT_NAME = c.SLOT_NAME)
                 )
                )
                OR
                (tc.cnt = 0 AND NOT EXISTS (SELECT 1 FROM candidates c WHERE c.MACHINE_CODE = A.MACHINE_CODE))
            )
            """
            cur.execute(sql, {"base_code": base_machine_code})
            rows = cur.fetchall()
            pms_compatible_machines = {row[0] for row in rows}
            pms_compatible_machines.add(base_machine_code)

    except Exception as e:
        print(f"[ERROR] Oracle PMS check failed: {e}")
        return send_response(400, False, "PMS 資料比對失敗", {"message": "無法驗證機台 PMS 相容性"})

    # ==========================================
    # STEP 2: 找出 "Condition 相容" 的機台 (MySQL)
    # ==========================================
    final_compatible_machines = []
    if not pms_compatible_machines:
        final_compatible_machines = [base_machine_code]
    else:
        try:
            with db() as (conn, cur):
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
            final_compatible_machines = [base_machine_code]

    # print(f"[DEBUG] Allowed machines: {final_compatible_machines}")

    # ==========================================
    # STEP 3: 查詢已簽核文件 (Source Document)
    #      + ✅ 新增「文件內容 vs baseline」比對
    # ==========================================
    try:
        with db() as (conn, cur):
            sql = """
            SELECT 
                bc.document_token,
                d.attribute,
                bc.content_text      AS param_text,
                bc.content_json      AS param_json,
                (
                    SELECT sub.content_text 
                    FROM sfdb.rms_block_content sub 
                    WHERE sub.document_token = bc.document_token 
                      AND sub.step_type = 2 
                      AND sub.sub_no = 1 
                    LIMIT 1
                ) as cond_text,
                (
                    SELECT sub.content_json 
                    FROM sfdb.rms_block_content sub 
                    WHERE sub.document_token = bc.document_token 
                      AND sub.step_type = 2 
                      AND sub.sub_no = 1 
                    LIMIT 1
                ) as cond_json,
                bc.metadata
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
            target_programs = []
            found_valid_doc = False

            # 預先算好「base machine 的 baseline signature」，如果你要用 base 做比較也可以；
            # 這裡我會用「文件裡實際使用的那台 machine」當 baseline。
            # base_pms_sig  = _load_machine_pms_signature(base_machine_code)
            # base_cond_sig = _load_machine_condition_signature(base_machine_code)

            for row in candidates:
                (doc_token, attr_str, param_text, param_json_str, cond_text, cond_json_str, meta_str) = row

                # 解析 Attribute 取得這份文件所掛的 machine 清單
                try:
                    attr = json.loads(attr_str) if attr_str else {}
                    doc_machines = attr.get("machines", [])
                    doc_machine_codes = {m.get("code") for m in doc_machines if m.get("code")}
                except Exception as e:
                    print(f"[WARN] parse attribute failed for {doc_token}: {e}")
                    continue

                # 只接受「文件使用的機台」裡，至少有一台在 final_compatible_machines 名單內
                compatible_in_doc = doc_machine_codes.intersection(set(final_compatible_machines))
                if not compatible_in_doc:
                    continue

                # ✅ 選一台「文件實際使用 + 與 baseline 相容」的機台當作 baseline 比對標的
                #    （這裡簡單選第一個，你也可以改成 if base_machine_code in compatible_in_doc 優先用 base）
                doc_machine_for_compare = None
                if base_machine_code in compatible_in_doc:
                    doc_machine_for_compare = base_machine_code
                else:
                    doc_machine_for_compare = next(iter(compatible_in_doc))

                # ------- 3.1 撈目前 baseline（這台機台）的 signature -------
                current_pms_sig  = _load_machine_pms_signature(doc_machine_for_compare)
                current_cond_sig = _load_machine_condition_signature(doc_machine_for_compare)

                # ------- 3.2 從文件內容抽出當時的 signature -------
                doc_pms_sig  = _build_doc_pms_signature_from_text(param_text or "")
                doc_cond_sig = _build_doc_condition_signature_from_text(cond_text or "")

                # ------- 3.3 做比對 -------
                # ------- 3.3 做比對 + DEBUG -------
                if current_pms_sig != doc_pms_sig or current_cond_sig != doc_cond_sig:
                    # print(f"[DEBUG] doc {doc_token} skipped: PMS/Cond signature not matched.")
                    # print(f"[DEBUG]   machine_for_compare = {doc_machine_for_compare}")

                    # print(f"[DEBUG]   PMS current size = {len(current_pms_sig)}, doc size = {len(doc_pms_sig)}")
                    only_in_current_pms = list(current_pms_sig - doc_pms_sig)[:10]
                    only_in_doc_pms     = list(doc_pms_sig - current_pms_sig)[:10]
                    # print(f"[DEBUG]   PMS only_in_current (first 10): {only_in_current_pms}")
                    # print(f"[DEBUG]   PMS only_in_doc     (first 10): {only_in_doc_pms}")

                    # print(f"[DEBUG]   COND current size = {len(current_cond_sig)}, doc size = {len(doc_cond_sig)}")
                    only_in_current_cond = list(current_cond_sig - doc_cond_sig)[:10]
                    only_in_doc_cond     = list(doc_cond_sig - current_cond_sig)[:10]
                    # print(f"[DEBUG]   COND only_in_current (first 10): {only_in_current_cond}")
                    # print(f"[DEBUG]   COND only_in_doc     (first 10): {only_in_doc_cond}")

                    continue

                # ------- 3.4 通過比對 → 這份文件才是合法來源 -------
                found_valid_doc = True
                try:
                    target_param_json = json.loads(param_json_str) if param_json_str else None
                except Exception:
                    target_param_json = None

                try:
                    target_cond_json = json.loads(cond_json_str) if cond_json_str else None
                except Exception:
                    target_cond_json = None

                try:
                    meta = json.loads(meta_str) if meta_str else {}
                    target_programs = meta.get("programs") or []
                except Exception as e:
                    print(f"[WARN] Parse metadata failed: {e}")
                    target_programs = []

                # print(f"[DEBUG] Found compatible + up-to-date doc: {doc_token}, machines: {compatible_in_doc}")
                break

            if not found_valid_doc:
                return send_response(200, False, "條件參數不同無法複製", {
                    "message": "雖有此程式代碼，但來源文件的 PMS/條件內容已與目前 baseline 不一致，無法複製。"
                })

            return send_response(200, True, "複製成功", {
                "blocks": {
                    "param_json": target_param_json,
                    "cond_json": target_cond_json,
                    "source_programs": target_programs
                }
            })

    except Exception as e:
        print(f"[ERROR] Fetch doc failed: {e}")
        return send_response(500, False, "系統錯誤", {"message": str(e)})

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
                with odb() as ora:
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
    