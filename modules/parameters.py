# modules/parameters.py
from flask import Blueprint, request
from db import db
from loginFunctions.utils import send_response
import json, math

bp = Blueprint("parameters", __name__, url_prefix="/parameters")

# ==========================================
# 1. 搜尋 API (Step 3)
# ==========================================
@bp.get("/search")
def search():
    # 1. 接收參數
    specific = request.args.get("specific")
    machine = request.args.get("machine")
    item = request.args.get("item")
    code = request.args.get("code")
    
    # 分頁參數
    try:
        page = max(1, int(request.args.get("page", 1)))
        page_size = int(request.args.get("pageSize", 20))
    except:
        page = 1
        page_size = 20

    # 動態條件
    conditions_filter = {k: v for k, v in request.args.items() if k not in ["specific", "machine", "item", "code", "page", "pageSize"]}

    # 防呆
    if not any([specific, machine, item, code]) and not conditions_filter:
        return send_response(400, False, "請至少輸入一個查詢條件", {})

    # [邏輯控制 1] 是否需要展開條件表？
    # 規則：只有在「有輸入 Machine」且「沒有輸入 Item (指示書)」時，才展開條件
    # 如果只輸入 Code 或 Specific，machine 為 None，這裡會是 False -> 不展開
    is_instruction_mode = (not item)
    should_explode_conditions = (machine is not None and len(machine) > 0) and is_instruction_mode

    results = []
    total_count = 0

    try:
        with db(dict_cursor=True) as (_, cur):
            # =================================================================================
            # 動態組裝 SQL：根據是否展開條件，決定 JOIN 的內容
            # =================================================================================
            
            # 1. Select Clause
            # [修正 2] raw_content_text 來源改為條件表 (rbc_cond)
            # 如果不展開條件，這裡就給 NULL，避免混淆
            cond_text_field = "rbc_cond.content_text" if should_explode_conditions else "NULL"
            cond_row_field = "jt_cond.row_data" if should_explode_conditions else "NULL"

            select_clause = f"""
                SELECT 
                    rbc.content_id,
                    rbc.document_token,
                    rda.document_name,
                    rda.document_version,
                    rda.document_type,
                    rda.attribute,
                    
                    jt_prog.spec_name as process_name,
                    jt_prog.program_code,
                    jt_mach.machine_code,
                    
                    {cond_row_field} as condition_row_json,
                    {cond_text_field} as raw_content_text
            """

            # 2. From & Join Clause
            # 基礎 JOIN
            from_clause = """
                FROM rms_block_content rbc
                JOIN rms_document_attributes rda ON rbc.document_token = rda.document_token
                
                -- [Explode 1] 展開 Programs
                JOIN JSON_TABLE(
                    rbc.metadata, 
                    '$.programs[*]' 
                    COLUMNS (
                        program_code VARCHAR(50) PATH '$.programCode',
                        spec_name VARCHAR(100) PATH '$.specName'
                    )
                ) AS jt_prog ON 1=1

                -- [Explode 2] 展開 Machines
                JOIN JSON_TABLE(
                    IF(rda.document_type = 1, rbc.metadata->'$.machines', rda.attribute->'$.machines'),
                    '$[*]' 
                    COLUMNS (
                        machine_code VARCHAR(50) PATH '$'
                    )
                ) AS jt_mach ON 1=1
            """

            # [修正 1] 動態加入條件表的 JOIN
            # 只有需要展開時，才去 JOIN 條件表
            if should_explode_conditions:
                from_clause += """
                    -- [Self Join] 預先關聯條件表 Block
                    LEFT JOIN rms_block_content rbc_cond 
                        ON rbc.document_token = rbc_cond.document_token 
                        AND rbc_cond.step_type = 2 
                        AND rbc_cond.sub_no = 1
                    
                    -- [Explode 3] 展開條件表
                    JOIN JSON_TABLE(
                        rbc_cond.content_text,
                        '$[*]'
                        COLUMNS (
                            row_idx FOR ORDINALITY,
                            row_data JSON PATH '$'
                        )
                    ) AS jt_cond ON 1=1
                """

            # 3. Where Clause
            where_clauses = ["rbc.step_type IN (2, 5)", "rbc.sub_no = 0"]
            params = []

            # 如果展開條件，必須排除 Header (row_idx > 1)
            if should_explode_conditions:
                where_clauses.append("jt_cond.row_idx > 1")

            # 一般過濾條件
            if item:
                where_clauses.append("rda.document_type = 1")
                where_clauses.append("rda.attribute->>'$.itemType' LIKE %s")
                params.append(f"%{item}%")
            elif conditions_filter:
                # 有搜條件，強制指示書
                where_clauses.append("rda.document_type = 0")

            if specific:
                where_clauses.append("jt_prog.spec_name LIKE %s")
                params.append(f"%{specific}%")

            if code:
                where_clauses.append("jt_prog.program_code LIKE %s")
                params.append(f"%{code}%")

            if machine:
                where_clauses.append("jt_mach.machine_code = %s")
                params.append(machine)

            # 組合 Where
            where_sql = "WHERE " + " AND ".join(where_clauses)

            # ===================================================
            # 執行 Query
            # ===================================================
            count_sql = f"SELECT COUNT(*) as total {from_clause} {where_sql}"
            cur.execute(count_sql, params)
            total_count = cur.fetchone()['total']

            data_sql = f"{select_clause} {from_clause} {where_sql} LIMIT %s OFFSET %s"
            cur.execute(data_sql, params + [page_size, (page - 1) * page_size])
            rows = cur.fetchall()

            # --- 後處理 ---
            for row in rows:
                cond_dict = {}
                
                # 只有在需要展開時，這裡才會有值
                if row.get('condition_row_json') and row.get('raw_content_text'):
                    try:
                        # 這裡的 raw_content_text 已經修正為 rbc_cond (條件表)
                        full_table = json.loads(row['raw_content_text'])
                        headers = full_table[0] if len(full_table) > 0 else []
                        curr_row_data = json.loads(row['condition_row_json'])
                        
                        is_match = True
                        for idx, h in enumerate(headers):
                            val = curr_row_data[idx] if idx < len(curr_row_data) else ""
                            cond_dict[h] = val
                            # 動態條件過濾
                            if h in conditions_filter and conditions_filter[h] not in str(val):
                                is_match = False
                        
                        if not is_match: continue

                    except Exception as e:
                        print(f"Parse Condition Error: {e}")

                results.append({
                    "content_id": row['content_id'], 
                    "document_name": row['document_name'],
                    "version": row['document_version'],
                    "process": row['process_name'],
                    "machine": row['machine_code'],
                    "program_code": row['program_code'],
                    "item": json.loads(row['attribute']).get('itemType', '') if row['attribute'] else '',
                    "conditions": cond_dict,
                    "document_token": row['document_token']
                })

        return send_response(200, True, "搜尋成功", {
            "items": results,
            "total": total_count,
            "page": page,
            "pageSize": page_size,
            "totalPages": math.ceil(total_count / page_size)
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return send_response(500, False, f"搜尋發生錯誤: {str(e)}", {})
    
# ==========================================
# 2. 取得單一 Block 內容 (Step 4)
# ==========================================
@bp.get("/block/<content_id>")
def get_block_by_id(content_id):
    try:
        with db() as (conn, cur):
            # 1. 只撈 content_text
            cur.execute("SELECT content_text FROM rms_block_content WHERE content_id = %s", (content_id,))
            result = cur.fetchone()

        # 2. 防呆：如果資料庫找不到這筆 ID，先 return，不然下面的 result[0] 會報錯
        if not result:
            return send_response(400, False, "查無此參數區塊", {"rows": []})
        
        # 3. 解析 JSON (如果 content_text 是空字串或 None，就回傳空陣列)
        # 這裡將 key 命名為 "rows" 是為了配合前端 ParametersSearch.vue 的寫法
        rows = json.loads(result[0]) if result[0] else []

        return send_response(200, True, "取得成功", {"rows": rows})
        
    except Exception as e:
        return send_response(500, False, f"資料庫錯誤: {e}", {})