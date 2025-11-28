# modules/parameters.py
from flask import Blueprint, request
from db import db
from oracle_db import ora_cursor
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


def _parse_condition_table_text(text_content):
    """
    解析 content_text 為 [["Header1", "Header2"], ["Val1", "Val2"]] 格式的字串
    返回: {"Header1": "Val1", "Header2": "Val2"}
    """
    if not text_content:
        return {}
    try:
        # 假設存儲格式是 JSON string list of lists
        import json
        data = json.loads(text_content)
        if isinstance(data, list) and len(data) >= 2:
            headers = data[0] # 第一列是標題
            # 找到 "條件名稱" 或使用者選擇的那一列 (通常是第二列，或者是標記為使用者選擇的那列)
            # 根據提示：第二列後續的內容為使用者選擇的條件式樣
            values = data[1] 
            
            result = {}
            for i, h in enumerate(headers):
                if i < len(values):
                    # 排除 "條件名稱" 這個 column 本身，只取後面的條件
                    if h == "條件名稱": 
                        continue
                    result[h] = str(values[i]).strip()
            return result
    except Exception as e:
        print(f"解析條件表文字失敗: {e}")
    return {}

@bp.post("/parameters/search")
def search_parameters():
    body = request.get_json(silent=True) or {}

    # 1. 參數解析
    doc_status = _parse_int(body.get("status"), 2)
    if doc_status is None: doc_status = 2

    # 搜尋條件
    specific_code = (body.get("specific_code") or "").strip() # 適用工程 (SpecCode)
    machine_code  = (body.get("machine_code") or "").strip()
    item_code     = (body.get("item_code") or "").strip()
    program_code  = (body.get("program_code") or "").strip()
    
    # 分頁
    page      = _parse_int(body.get("page"), 1) or 1
    page_size = _parse_int(body.get("page_size"), 10) or 10
    page_size = max(1, min(page_size, 100))
    offset    = (page - 1) * page_size

    # 動態條件 (來自前端 Select)
    raw_conditions = body.get("conditions") or [] 
    # 判斷是否選擇了任一條件參數 (若有，則強制只查 Instruction)
    has_condition_filter = any((c.get("parameter_name") or "").strip() for c in raw_conditions)

    try:
        with db() as (conn, cur):
            # -------------------------------------------------------
            # A. 取得機台條件 Headers (用於前端 Table Header 顯示)
            # -------------------------------------------------------
            machine_condition_headers = []
            if machine_code:
                cur.execute("""
                    SELECT DISTINCT rc.condition_name
                    FROM rms_conditions rc
                    JOIN rms_group_machines rgm ON rc.condition_id = rgm.condition_id
                    WHERE rgm.machine_id = %s
                    ORDER BY rc.condition_name
                """, (machine_code,))
                machine_condition_headers = [row[0] for row in cur.fetchall()]

            # -------------------------------------------------------
            # B. 建構 SQL (Base Logic)
            # -------------------------------------------------------
            # 核心邏輯：
            # 1. 主體是 rms_block_content (bc) 的 sub_no=0 (參數表)
            # 2. 透過 JSON_TABLE 展開 bc.metadata -> programs
            # 3. Join rms_document_attributes (d) 取得文件屬性
            # 4. (關鍵) 若是 Instruction，需關聯 sub_no=1 的 block 取得條件表內容
            
            base_sql = """
                FROM rms_block_content AS bc
                CROSS JOIN JSON_TABLE(
                    bc.metadata,
                    '$.programs[*]'
                    COLUMNS (
                        specCode VARCHAR(50) PATH '$.specCode',
                        specName VARCHAR(255) PATH '$.specName',
                        programCode VARCHAR(255) PATH '$.programCode'
                    )
                ) AS p
                LEFT JOIN rms_document_attributes AS d 
                   ON bc.document_token = d.document_token
            """

            # -------------------------------------------------------
            # C. 建構 WHERE 條件
            # -------------------------------------------------------
            where = []
            params = []
            
            # 基本限制
            where.append("d.status = %s")
            params.append(doc_status)
            where.append("bc.sub_no = 0") # 只查參數 Block
            where.append("JSON_UNQUOTE(JSON_EXTRACT(bc.metadata, '$.kind')) = 'mcr-parameter'")

            # --- 條件 2.2: 若有品目 -> 只查 Spec Document (step_type=5) ---
            if item_code:
                where.append("bc.step_type = 5")
                where.append("JSON_UNQUOTE(JSON_EXTRACT(d.attribute, '$.itemType')) = %s")
                params.append(item_code)
            
            # --- 條件 2.3.2: 若有選條件參數 -> 只查 Instruction Document (step_type=2) ---
            elif has_condition_filter:
                where.append("bc.step_type = 2")
            
            # --- 條件 2.3.1: 否則搜尋兩者 (step_type IN (2, 5)) ---
            else:
                where.append("bc.step_type IN (2, 5)")

            # --- 條件 2.4 / 1: 機台過濾 ---
            # Spec (step=5): 機台在 metadata.machine
            # Instruction (step=2): 機台在 document_attribute.machines List 中
            if machine_code:
                where.append("""
                    (
                        (bc.step_type = 5 AND JSON_UNQUOTE(JSON_EXTRACT(bc.metadata, '$.machine')) = %s)
                        OR
                        (bc.step_type = 2 AND JSON_CONTAINS(d.attribute, JSON_OBJECT('code', %s), '$.machines'))
                    )
                """)
                params.extend([machine_code, machine_code])

            # --- 適用工程 (SpecCode) ---
            # 改為直接查 JSON 展開後的 specCode (metadata 或 programs 裡都有)
            if specific_code:
                where.append("p.specCode = %s")
                params.append(specific_code)

            # --- 程式代碼 ---
            if program_code:
                where.append("p.programCode LIKE %s")
                params.append(f"%{program_code}%")

            # --- 進階條件過濾 (針對 Instruction 的條件表) ---
            # 因為條件表在 sub_no=1，我們需要用 EXISTS 子查詢來過濾
            for c in raw_conditions:
                pname = (c.get("parameter_name") or "").strip()
                if not pname: continue
                
                # 這裡使用 LIKE 來匹配 content_text (如 prompt 所述: [["條件", "厚度"], ["1", "10"]])
                # 簡單匹配：確保該 document 有一個 sub_no=1 的 block 包含該數值
                # 注意：這是一個模糊匹配，若要精準需解析 JSON，但在 SQL 層級 LIKE 較快且符合 prompt "content_text LIKE"
                where.append("""
                    EXISTS (
                        SELECT 1 FROM rms_block_content sub_bc
                        WHERE sub_bc.document_token = bc.document_token
                          AND sub_bc.step_type = 2
                          AND sub_bc.sub_no = 1
                          AND sub_bc.content_text LIKE %s
                    )
                """)
                params.append(f"%{pname}%")

            where_sql = " AND ".join(where) if where else "1=1"

            # -------------------------------------------------------
            # D. Count 總數
            # -------------------------------------------------------
            count_sql = f"SELECT COUNT(*) {base_sql} WHERE {where_sql}"
            cur.execute(count_sql, params)
            total = cur.fetchone()[0] or 0

            if total == 0:
                return send_response(200, False, "查無資料", {
                    "total": 0, "page": page, "page_size": page_size,
                    "condition_headers": machine_condition_headers, "items": []
                })

            # -------------------------------------------------------
            # E. 撈取資料 (包含條件表的內容)
            # -------------------------------------------------------
            # 這裡我們使用一個子查詢來撈取 Instruction 的 Condition Table (step=2, sub=1)
            data_sql = f"""
                SELECT 
                    bc.document_token,
                    d.document_type,
                    d.attribute,
                    bc.metadata,
                    p.specCode,
                    p.specName,
                    p.programCode,
                    -- 嘗試抓取 Instruction 的條件表內容 (step=2, sub=1)
                    (
                        SELECT sub_bc.content_text 
                        FROM rms_block_content sub_bc
                        WHERE sub_bc.document_token = bc.document_token
                          AND sub_bc.step_type = 2
                          AND sub_bc.sub_no = 1
                        LIMIT 1
                    ) as condition_table_text
                {base_sql}
                WHERE {where_sql}
                ORDER BY d.issue_date DESC, p.programCode ASC
                LIMIT %s OFFSET %s
            """
            params.extend([page_size, offset])
            cur.execute(data_sql, params)
            rows = cur.fetchall()

            # -------------------------------------------------------
            # F. 組裝回傳資料
            # -------------------------------------------------------
            items = []
            for row in rows:
                (token, doc_type, attr_json, meta_json, spec_code, spec_name, prog_code, cond_text) = row
                
                # 解析 Attribute
                attr = attr_json if isinstance(attr_json, dict) else (json.loads(attr_json) if attr_json else {})
                meta = meta_json if isinstance(meta_json, dict) else (json.loads(meta_json) if meta_json else {})

                # 1. 處理機台名稱顯示
                display_machine = ""
                if doc_type == 1: # Spec
                    # Spec: machineName 在 metadata 中
                    display_machine = meta.get("machineName") or meta.get("machine") or ""
                else: # Instruction
                    # Instruction: 機台在 attribute.machines (Array)
                    # 我們嘗試找出符合搜尋條件的機台，若沒搜尋機台，則列出全部或第一個
                    machines_list = attr.get("machines") or []
                    match_m = None
                    if machine_code:
                        match_m = next((m for m in machines_list if m.get("code") == machine_code), None)
                    
                    if match_m:
                        display_machine = match_m.get("name") or match_m.get("code")
                    elif machines_list:
                        # 沒搜機台時，顯示第一個，或標示多個
                        first = machines_list[0]
                        display_machine = first.get("name") or first.get("code")
                        if len(machines_list) > 1:
                            display_machine += "..."

                # 2. 處理條件欄位 (Conditions)
                conditions_map = {}
                if doc_type == 0 and cond_text: # Instruction 才有條件表
                     # 解析 [["Header".,.], ["Val"...]]
                     conditions_map = _parse_condition_table_text(cond_text)

                # 3. 處理 Spec Name (題目要求 Instruction 也要顯示 specName 而非 applyProject)
                # 我們已經從 JSON_TABLE (p.specName) 拿到了，直接用即可
                display_spec_name = spec_name 

                items.append({
                    "document_token": token,
                    "document_type": doc_type,
                    "specific_name": display_spec_name, # 適用工程 (顯示名稱)
                    "machine_code": display_machine,    # 機台 (顯示名稱)
                    "item_code": attr.get("itemType") if doc_type == 1 else "", # 品目 (Spec only)
                    "program_code": prog_code,
                    "spec_code": spec_code,
                    "conditions": conditions_map        # 動態條件 Mapping
                })

            return send_response(200, False, "查詢成功", {
                "total": total,
                "page": page,
                "page_size": page_size,
                "condition_headers": machine_condition_headers,
                "items": items,
            })

    except Exception as e:
        print("ERROR:", e)
        return send_response(500, True, "查詢失敗", {"message": str(e)})
    
    
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

