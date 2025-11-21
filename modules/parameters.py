# modules/parameters.py
from flask import Blueprint, request
from db import db
from loginFunctions.utils import send_response
import json

bp = Blueprint("parameters", __name__)


def _parse_int(v, default=None):
    try:
        return int(v)
    except Exception:
        return default


def _extract_text_from_node(node):
    """
    簡單提取 TipTap node 裡面的 plain text。
    只處理我們在表格裡會遇到的情境：text / nested content。
    """
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text") or ""
    text_parts = []
    for child in node.get("content", []):
        text_parts.append(_extract_text_from_node(child))
    return "".join(text_parts).strip()


def _find_first_table(doc_json):
    """
    在 TipTap doc 裡找第一個 type = 'table' 的節點。
    """
    if not isinstance(doc_json, dict):
        return None

    if doc_json.get("type") == "table":
        return doc_json

    for child in doc_json.get("content", []):
        found = _find_first_table(child)
        if found is not None:
            return found
    return None


def _find_best_condition_table(doc_json, expected_headers):
    """
    在整份 TipTap doc 裡面，找出「header 跟 expected_headers 最接近」的 table。
    expected_headers 例如：["銅電式樣","製品式樣","流程","原銅厚度"]
    """
    if not isinstance(doc_json, dict):
        return None

    tables = []

    def dfs(node):
        if not isinstance(node, dict):
            return
        if node.get("type") == "table":
            tables.append(node)
        for child in node.get("content") or []:
            dfs(child)

    dfs(doc_json)

    if not tables:
        return None

    best_table = None
    best_score = 0

    for t in tables:
        rows = t.get("content") or []
        if not rows:
            continue
        header_cells = rows[0].get("content") or []
        headers = [_extract_text_from_node(c).strip() for c in header_cells]

        # 跟 expected_headers 的交集數量當作 score
        score = sum(1 for h in headers if h in expected_headers)
        if score > best_score:
            best_score = score
            best_table = t

    return best_table


def extract_machine_condition_rows_from_table(content_json_str, headers_hint=None):
    """
    專門解析「機台條件表格」：

    headers_hint: 例如 ["銅電式樣","製品式樣","流程","原銅厚度"]
      若有傳入，就會優先找跟這些欄位最接近的 table
    """
    try:
        doc = json.loads(content_json_str)
    except Exception:
        return []

    # 若有傳 headers_hint，先找最符合的那張 table
    table = None
    if headers_hint:
        table = _find_best_condition_table(doc, headers_hint)

    # 找不到或沒給 hint，就退回舊的「第一張 table」
    if table is None:
        table = _find_first_table(doc)

    if not table:
        return []

    rows = table.get("content") or []
    if len(rows) < 2:
        return []

    # 第一列是 header
    header_cells = rows[0].get("content") or []
    headers = [_extract_text_from_node(c).strip() for c in header_cells]

    data_rows = []
    for rnode in rows[1:]:
        if rnode.get("type") != "tableRow":
            continue
        cells = rnode.get("content") or []
        row_dict = {}
        for i, cell in enumerate(cells):
            if i >= len(headers):
                break
            key = headers[i]

            # 1) 先抓 cell 裡的純文字（text node）
            val = _extract_text_from_node(cell).strip()

            # 2) 如果是空字串，再從 attrs 裡找值（對應 dropdown 自訂欄位）
            if not val:
                attrs = cell.get("attrs") or {}
                # 這邊列出幾個常見的 key，你可以依你自訂的 extension 再加
                for attr_key in ("value", "text", "label", "display", "dropdownValue", "paramValue"):
                    v = attrs.get(attr_key)
                    if v:
                        val = str(v).strip()
                        break

            row_dict[key] = val

        data_rows.append(row_dict)

    return data_rows


def extract_parameter_conditions(cur, document_token):
    """
    若你之後還想用「content_text 裡面像 '銅電式樣：全鍍'」這種格式，
    這個函式可以保留（目前 search_parameters 已改用 table 解析方式）。
    """
    sql = """
        SELECT content_text
        FROM rms_block_content
        WHERE document_token = %s
          AND step_type IN (2,5)
          AND content_text IS NOT NULL
    """
    cur.execute(sql, (document_token,))
    rows = cur.fetchall()

    out = {}
    for (text,) in rows:
        if not text:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "：" in line:
                key, val = line.split("：", 1)
                key = key.strip()
                val = val.strip()
                if key:
                    out[key] = val
    return out


@bp.post("/parameters/search")
def search_parameters():
    """
    STEP 3：配方檢索（支援草稿、製程對應 applyProject、動態條件欄位）
    """
    body = request.get_json(silent=True) or {}

    # status（預設查正式 2，現在前端會傳 0 → 查草稿）
    doc_status = _parse_int(body.get("status"), 2)
    if doc_status is None:
        doc_status = 2

    apply_project = (body.get("apply_project") or "").strip()
    specific_code = (body.get("specific_code") or "").strip()
    machine_code  = (body.get("machine_code") or "").strip()
    item_code     = (body.get("item_code") or "").strip()
    program_code  = (body.get("program_code") or "").strip()

    page      = _parse_int(body.get("page"), 1) or 1
    page_size = _parse_int(body.get("page_size"), 10) or 10
    page_size = max(1, min(page_size, 100))
    offset    = (page - 1) * page_size

    raw_conditions = body.get("conditions") or []
    # 是否有帶任何「條件參數」進來（有代表只查 Instruction document_type=0）
    cond_has_any = any((c.get("parameter_name") or "").strip() for c in raw_conditions)

    try:
        with db() as (conn, cur):

            # ------------------ 先抓這台機台有哪些條件欄位 ------------------
            machine_condition_headers = []
            if machine_code:
                cur.execute("""
                    SELECT DISTINCT rc.condition_name
                    FROM rms_conditions rc
                    JOIN rms_group_machines rgm
                      ON rc.condition_id = rgm.condition_id
                    WHERE rgm.machine_id = %s
                    ORDER BY rc.condition_name
                """, (machine_code,))
                machine_condition_headers = [row[0] for row in cur.fetchall()]

            # debug 用：看一下 headers 是什麼
            print("machine_condition_headers:", machine_condition_headers)

            # ------------------ 組 WHERE ------------------
            where = ["d.status = %s"]
            params = [doc_status]

            # 1) specific_code：Instruction / Specification 分別用不同欄位過濾
            if specific_code:
                where.append("""
                    (
                    -- Instruction：attribute.applyProject 對 rms_spec_flat.project
                    (d.document_type = 0
                    AND JSON_UNQUOTE(JSON_EXTRACT(d.attribute, '$.applyProject')) IN (
                            SELECT project FROM rms_spec_flat WHERE spec_code = %s
                    )
                    )
                    OR
                    -- Specification：attribute.specification[*].code 直接對 specific_code
                    (d.document_type = 1
                    AND JSON_SEARCH(d.attribute, 'one', %s, NULL, '$.specification[*].code') IS NOT NULL
                    )
                    )
                """)
                params.extend([specific_code, specific_code])


            # 2) apply_project：額外再縮小
            if apply_project:
                where.append("JSON_UNQUOTE(JSON_EXTRACT(d.attribute, '$.applyProject')) LIKE %s")
                params.append(f"%{apply_project}%")

            # 3) 文件類型判斷：
            if cond_has_any:
                where.append("d.document_type = 0")
            elif item_code:
                where.append("d.document_type = 1")
                where.append("JSON_UNQUOTE(JSON_EXTRACT(d.attribute, '$.itemType')) = %s")
                params.append(item_code)

            # 4) 程式代碼
            if program_code:
                like = f"%{program_code}%"
                where.append("""
                    EXISTS (
                      SELECT 1 FROM rms_block_content bc_p
                       WHERE bc_p.document_token = d.document_token
                         AND bc_p.step_type IN (2,5)
                         AND (bc_p.header_text LIKE %s OR bc_p.content_text LIKE %s)
                    )
                """)
                params.extend([like, like])

            # 5) 機台條件
            if machine_code:
                where.append("""
                    (
                      (d.document_type = 0 AND JSON_CONTAINS(d.attribute, JSON_OBJECT('code', %s), '$.machines'))
                      OR
                      (
                        d.document_type = 1
                        AND EXISTS (
                          SELECT 1 FROM rms_block_content bc_m
                           WHERE bc_m.document_token = d.document_token
                             AND bc_m.step_type IN (2,5)
                             AND JSON_EXTRACT(bc_m.metadata, '$.kind')   = 'mcr-parameter'
                             AND JSON_EXTRACT(bc_m.metadata, '$.machine') = %s
                        )
                      )
                    )
                """)
                params.extend([machine_code, machine_code])

            # 6) 條件參數：只要有帶 parameter_name，就要求 content_text LIKE
            for c in raw_conditions:
                pname = (c.get("parameter_name") or "").strip()
                if not pname:
                    continue
                like = f"%{pname}%"
                where.append("""
                    EXISTS (
                      SELECT 1 FROM rms_block_content bc_c
                       WHERE bc_c.document_token = d.document_token
                         AND bc_c.step_type IN (2,5)
                         AND bc_c.content_text LIKE %s
                    )
                """)
                params.append(like)

            where_sql = " AND ".join(where) if where else "1=1"

            # ------------------ total ------------------
            count_sql = f"SELECT COUNT(*) FROM rms_document_attributes d WHERE {where_sql}"
            cur.execute(count_sql, params)
            total = cur.fetchone()[0] or 0

            if total == 0:
                return send_response(200, False, "查無資料", {
                    "total": 0,
                    "page": page,
                    "page_size": page_size,
                    "condition_headers": machine_condition_headers,
                    "items": []
                })

            # ------------------ data ------------------
            data_sql = f"""
                SELECT
                  d.document_token,
                  d.document_type,
                  JSON_UNQUOTE(JSON_EXTRACT(d.attribute, '$.applyProject')) AS apply_project,
                  JSON_UNQUOTE(JSON_EXTRACT(d.attribute, '$.itemType'))     AS item_code,
                  d.attribute                                               AS attribute_json,
                  (
                    SELECT bc.header_text
                    FROM rms_block_content bc
                    WHERE bc.document_token = d.document_token
                      AND bc.step_type IN (2,5)
                    ORDER BY bc.step_type, bc.tier_no, bc.sub_no
                    LIMIT 1
                  ) AS program_code
                FROM rms_document_attributes d
                WHERE {where_sql}
                ORDER BY d.issue_date DESC
                LIMIT %s OFFSET %s
            """
            params_data = params + [page_size, offset]
            cur.execute(data_sql, params_data)
            rows = cur.fetchall()

            items = []

            # ------------------ 組 response items ------------------
            items = []

            # ------------------ 組 response items ------------------
            for (document_token, doc_type, apply_proj_val, item_code_val, attribute_json_str, prog_code_val) in rows:
                # 先轉成 int，避免 None / Decimal 之類的狀況
                doc_type_int = int(doc_type) if doc_type is not None else None

                # 預設先用 applyProject（Instruction 會用到）
                specific_name = apply_proj_val

                # 解析 attribute JSON（為了拿 specification list）
                attr = {}
                if attribute_json_str:
                    try:
                        attr = json.loads(attribute_json_str)
                    except Exception:
                        attr = {}

                # === A. Specification document (document_type = 1) ===
                if doc_type_int == 1:
                    # 從 attribute.specification list 裡抓製程名稱
                    spec_name = None
                    spec_list = attr.get("specification") or []
                    for sp in spec_list:
                        # sp: {"code": "R221-01", "name": "(R221-01)RTR 乾膜前處理"}
                        n = (sp.get("name") or "").strip()
                        if n:
                            spec_name = n
                            break

                    # 如果沒有抓到，就保持 None；前端會看到空欄位
                    specific_name = spec_name

                    items.append({
                        "document_token": document_token,
                        "document_type": doc_type_int,
                        "specific_name": specific_name,           # ★ spec 的「適用工程」＝製程名稱
                        "machine_code": machine_code or None,     # 用前端傳進來的 machine_code 顯示
                        "item_code": item_code_val,
                        "program_code": prog_code_val,
                        "conditions": {}                          # spec 沒有條件
                    })
                    continue  # ✅ 不要跑 Instruction 的條件邏輯

                # === B. Instruction document (document_type = 0) ===
                # 下面保留你原本的邏輯，只把 specific_name 改成共用變數

                # 取出這份文件所有 content_json
                cur.execute("""
                    SELECT content_json
                    FROM rms_block_content
                    WHERE document_token = %s
                    AND step_type IN (2,5)
                    AND content_json IS NOT NULL
                """, (document_token,))
                cond_rows = cur.fetchall()

                all_condition_rows = []
                for (content_json_str,) in cond_rows:
                    if not content_json_str:
                        continue
                    rows_from_this_block = extract_machine_condition_rows_from_table(
                        content_json_str,
                        headers_hint=machine_condition_headers if machine_condition_headers else None
                    )
                    if rows_from_this_block:
                        all_condition_rows = rows_from_this_block
                        break  # 找到一張有對到欄位的 table 就停

                print("doc:", document_token, "all_condition_rows:", all_condition_rows)

                if not all_condition_rows:
                    continue

                if cond_has_any:
                    filtered_rows = []
                    for row_dict in all_condition_rows:
                        ok = True
                        for c in raw_conditions:
                            pname = (c.get("parameter_name") or "").strip()
                            cname = (c.get("condition_name") or "").strip()
                            if not pname or not cname:
                                continue
                            if (row_dict.get(cname) or "").strip() != pname:
                                ok = False
                                break
                        if ok:
                            filtered_rows.append(row_dict)

                    all_condition_rows = filtered_rows
                    if not all_condition_rows:
                        continue

                for cond_row_dict in all_condition_rows:
                    if machine_condition_headers:
                        if not any((cond_row_dict.get(h) or "").strip() for h in machine_condition_headers):
                            continue

                    full_cond_map = {}
                    if machine_condition_headers:
                        for h in machine_condition_headers:
                            full_cond_map[h] = (cond_row_dict.get(h) or "").strip()
                    else:
                        for k, v in cond_row_dict.items():
                            full_cond_map[k] = (v or "").strip()

                    items.append({
                        "document_token": document_token,
                        "document_type": doc_type_int,
                        "specific_name": specific_name,       # Instruction 繼續用 applyProject
                        "machine_code": machine_code or None,
                        "item_code": item_code_val,
                        "program_code": prog_code_val,
                        "conditions": full_cond_map
                    })


        return send_response(200, False, "查詢成功", {
            "total": total,
            "page": page,
            "page_size": page_size,
            "condition_headers": machine_condition_headers,
            "items": items,
        })

    except Exception as e:
        print("ERROR in /parameters/search:", e)
        return send_response(500, True, "查詢失敗", {
            "message": f"資料庫錯誤: {e}"
        })


@bp.get("/parameters/<document_token>/blocks")
def get_parameter_blocks(document_token):
    """
    STEP 4：取得被選中的配方的參數表。
    """
    step_type = _parse_int(request.args.get("step_type"), 2) or 2

    try:
        with db() as (conn, cur):
            # 只取 sub_no = 0 的那一格當作主製程參數 table
            cur.execute("""
                SELECT content_json
                FROM rms_block_content
                WHERE document_token = %s
                  AND step_type = %s
                  AND sub_no = 0
                ORDER BY tier_no
                LIMIT 1
            """, (document_token, step_type))
            row = cur.fetchone()

        if not row:
            return send_response(200, False, "查無此文件的參數表", {
                "rows": []
            })

        content_json_str = row[0]
        if not content_json_str:
            return send_response(200, False, "此章節沒有 content_json", {
                "rows": []
            })

        try:
            doc = json.loads(content_json_str)
        except Exception:
            return send_response(500, True, "JSON 解析失敗", {
                "rows": [],
                "message": "content_json 不是合法 JSON，請檢查資料。"
            })

        table = _find_first_table(doc)
        if table is None:
            return send_response(200, False, "此章節內沒有找到表格", {
                "rows": []
            })

        rows_out = []
        row_nodes = table.get("content") or []
        if len(row_nodes) <= 1:
            return send_response(200, False, "參數表沒有資料列", {
                "rows": []
            })

        data_rows = row_nodes[1:]

        column_keys = [
            "tank_name",   # 槽體名稱
            "param_name",  # 參數名稱
            "spec_upper",  # 規格上限
            "op_upper",    # 操作上限
            "center",      # 中值
            "op_lower",    # 操作下限
            "spec_lower",  # 規格下限
            "unit",        # 單位
            "down_flag",   # 參數下放
            "remark",      # 說明
        ]

        for rnode in data_rows:
            if rnode.get("type") != "tableRow":
                continue
            cells = (rnode.get("content") or [])
            row_dict = {}

            for idx, cell in enumerate(cells):
                if idx >= len(column_keys):
                    break
                key = column_keys[idx]
                text = _extract_text_from_node(cell)
                row_dict[key] = text

            for k in column_keys:
                row_dict.setdefault(k, "")

            if not any(row_dict.values()):
                continue

            rows_out.append(row_dict)

        return send_response(200, False, "取得參數表成功", {
            "rows": rows_out
        })

    except Exception as e:
        print("ERROR in /parameters/<token>/blocks:", e)
        return send_response(500, True, "取得參數表失敗", {
            "rows": [],
            "message": f"資料庫錯誤: {e}"
        })
