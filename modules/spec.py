from flask import Blueprint, request
from loginFunctions.utils import send_response
from oracle_db import ora_cursor
from db import db  # 引入 MySQL 連線工具
import json

bp = Blueprint("spec", __name__)

# @bp.get("/view-list")
# def get_specification_view_list():
#     # 1. 接收參數
#     try:
#         page = int(request.args.get("page", 1))
#         page_size = int(request.args.get("pageSize", 20))
#     except ValueError:
#         page = 1
#         page_size = 20

#     start_date = request.args.get("startDate")
#     end_date = request.args.get("endDate")
#     item = request.args.get("item")
#     station = request.args.get("station")
#     unit = request.args.get("unit")
    
#     filter_prod = request.args.get("filterProd") == 'true'
#     filter_proto = request.args.get("filterProto") == 'true'
#     filter_unconfirmed = request.args.get("filterUnconfirmed") == 'true'
    
#     # 2. 建構 SQL 基礎部分
#     base_sql = """FROM EZFLEX."KKME_Table" WHERE 1=1"""
#     params = {}

#     # --- 過濾條件 ---
#     if start_date:
#         base_sql += " AND T_TIME >= TO_DATE(:start_date, 'YYYYMMDD')"
#         params['start_date'] = start_date.replace("-", "")
    
#     if end_date:
#         base_sql += " AND T_TIME < TO_DATE(:end_date, 'YYYYMMDD') + 1"
#         params['end_date'] = end_date.replace("-", "")

#     if item:
#         base_sql += " AND ITEM LIKE :item"
#         params['item'] = f"%{item}%"
#     if station:
#         base_sql += " AND (STATION LIKE :station OR STATION_CH LIKE :station)"
#         params['station'] = f"%{station}%"
#     if unit:
#         base_sql += " AND CLASS_CH LIKE :unit"
#         params['unit'] = f"%{unit}%"

#     if filter_prod and not filter_proto:
#         base_sql += " AND ITEM LIKE 'Z%'"
#     elif filter_proto and not filter_prod:
#         base_sql += " AND ITEM NOT LIKE 'Z%'"
    
#     if filter_unconfirmed:
#         base_sql += " AND CHECK_TIME IS NULL"

#     # 3. 執行查詢
#     try:
#         # Step A: 取得總筆數 (Total Count)
#         count_sql = f"SELECT COUNT(*) {base_sql}"
#         total_count = 0
#         with ora_cursor(db_alias='item_db') as cur:
#             cur.execute(count_sql, params)
#             total_count = cur.fetchone()[0]

#         # Step B: 取得分頁資料 (Data) - 改用 ROWNUM 寫法 (相容 Oracle 11g)
        
#         # 1. 內層 SQL：負責排序與撈取原始資料
#         inner_sql = f"""
#             SELECT 
#                 ITEM, STATION, STATION_CH, 
#                 TO_CHAR(T_TIME, 'YYYY-MM-DD HH24:MI:SS') as T_TIME, 
#                 BOOK, PT_EMP, PRECAUTIONS, 
#                 TO_CHAR(CHECK_TIME, 'YYYY-MM-DD HH24:MI:SS') as CHECK_TIME, 
#                 CLASS_CH 
#             {base_sql}
#             ORDER BY T_TIME DESC
#         """

#         # 2. 包裝 SQL：計算 ROWNUM 並分頁
#         # 邏輯：先取前 N 筆 (ROWNUM <= end)，再去除前 M 筆 (RN > start)
#         data_sql = f"""
#             SELECT * FROM (
#                 SELECT T.*, ROWNUM RN FROM (
#                     {inner_sql}
#                 ) T WHERE ROWNUM <= :end_row
#             ) WHERE RN > :start_row
#         """
        
#         # 計算 ROWNUM 範圍 (Oracle ROWNUM 是從 1 開始)
#         # page 1, size 20 => start: 0, end: 20 -> RN > 0 AND RN <= 20
#         # page 2, size 20 => start: 20, end: 40 -> RN > 20 AND RN <= 40
#         params['start_row'] = (page - 1) * page_size
#         params['end_row'] = page * page_size

#         rows = []
#         with ora_cursor(db_alias='item_db') as cur:
#             cur.execute(data_sql, params)
            
#             # 轉 Dictionary
#             columns = [col[0] for col in cur.description]
#             # 排除 RN 欄位 (通常是最後一欄，但用 dict(zip) 其實前端不顯示也沒差)
#             for r in cur.fetchall():
#                 row_dict = dict(zip(columns, r))
#                 # 移除因為分頁產生的 RN 欄位 (可選)
#                 if 'RN' in row_dict:
#                     del row_dict['RN']
#                 rows.append(row_dict)

#         # Step C: 跨資料庫查詢文件狀態
#         if rows:
#             enrich_with_document_status(rows)

#         return send_response(200, True, "查詢成功", {
#             "items": rows,
#             "total": total_count,
#             "page": page,
#             "pageSize": page_size
#         })

#     except Exception as e:
#         print(f"Error: {e}")
#         return send_response(500, False, f"資料庫錯誤: {str(e)}", {})

# def enrich_with_document_status(oracle_rows):
#     """
#     針對 Oracle 查出來的 20 筆資料，去 MySQL 查詢對應的文件狀態
#     修正：Oracle Item 取前綴 (split('-')[0]) 來跟 MySQL 的 matnr 比對
#     """
#     # 1. 整理 Key 並建立對照表
#     # key: "ITEM_PREFIX|BOOK", value: [row_reference_list]
#     # 我們改用「前綴」當作 Key 來分組
#     row_map = {}
    
#     for row in oracle_rows:
#         full_item = row.get('ITEM', '')
#         book = row.get('BOOK', '')
        
#         # --- 關鍵修正：切割品目，只取前綴 ---
#         # 如果有 '-' 就切，沒有就維持原樣
#         item_prefix = full_item.split('-')[0]
        
#         # 組合 Key：前綴 + 式樣書號
#         # 例如: "DE1900|DE1896-ST3B"
#         key = f"{item_prefix}|{book}"
        
#         if key not in row_map:
#             row_map[key] = []
            
#         # 將原始的 row 加入清單，這樣查回來後可以一次更新所有相關的 row
#         # (例如 DE1900-04L 和 DE1900-04E 都會被加到 DE1900 這個 Key 下)
#         row_map[key].append(row)
        
#         # 預設狀態 (若沒查到)
#         row['DOC_STATUS'] = '未建立' 
#         row['DOC_STATUS_CODE'] = 0

#     if not row_map:
#         return

#     # 2. 動態建構 SQL WHERE 子句
#     where_clauses = []
#     sql_params = {}
    
#     # 這裡的 idx 只是為了產生唯一的參數名
#     for idx, key in enumerate(row_map.keys()):
#         parts = key.split('|')
#         p_item_prefix = parts[0]
#         p_book = parts[1]
        
#         # JSON 查詢條件：attribute->>'$.matnr' 等於 前綴
#         # 請確認您的 MySQL JSON Key 是 'matnr' 和 'sfhnr'
#         where_clauses.append(f"(attr.attribute->>'$.itemType' = %(item_{idx})s AND attr.attribute->>'$.styleNo' = %(book_{idx})s)")
        
#         sql_params[f'item_{idx}'] = p_item_prefix
#         sql_params[f'book_{idx}'] = p_book

#     if not where_clauses:
#         return

#     # 3. 執行 MySQL 查詢
#     filter_sql = " OR ".join(where_clauses)
    
#     mysql_sql = f"""
#         SELECT 
#             attr.document_token, 
#             attr.status, 
#             attr.attribute, 
#             COUNT(snap.snapshot_id) as snap_count, 
#             SUM(CASE WHEN snap.sync_status = 2 THEN 1 ELSE 0 END) as return_count 
#         FROM rms_document_attributes attr
#         LEFT JOIN rms_document_snapshots snap ON attr.document_token = snap.document_token
#         WHERE attr.document_type = 1 
#           AND ({filter_sql})
#         GROUP BY attr.document_token, attr.status, attr.attribute
#     """
    
#     # Debug 用 (可自行移除)
#     # print(f"MySQL Query: {mysql_sql}")
#     # print(f"Params: {sql_params}")

#     try:
#         with db(dict_cursor=True) as (_, cur):
#             cur.execute(mysql_sql, sql_params)
#             doc_results = cur.fetchall()

#             print(f"doc_results: {doc_results}")
            
#             # 4. 填回狀態
#             for doc in doc_results:
#                 # 解析 JSON attribute
#                 attr_data = doc['attribute']
#                 if isinstance(attr_data, str):
#                     attr_data = json.loads(attr_data)
                
#                 # 從 MySQL 拿回來的 matnr 和 sfhnr
#                 matnr = attr_data.get('itemType', '')
#                 sfhnr = attr_data.get('styleNo', '')
                
#                 # 組合成 Key 用來查找 row_map
#                 # MySQL 裡的 matnr 應該已經是前綴 (如 DE1900)
#                 result_key = f"{matnr}|{sfhnr}"
                
#                 # 如果這個 Key 存在於我們的對照表中
#                 if result_key in row_map:
#                     # 判斷狀態邏輯
#                     status_text = "草稿"     # 預設
#                     status_code = 1
                    
#                     db_status = doc['status']
#                     snap_count = doc['snap_count']
#                     return_count = doc['return_count'] # 被退回次數

#                     if db_status == 2:
#                         status_text = "已簽核"
#                         status_code = 3
#                     elif db_status == 1:
#                         if return_count and return_count > 0:
#                             status_text = "被退回"
#                             status_code = 4
#                         elif snap_count > 1:
#                             status_text = "送審中"
#                             status_code = 2
#                         else:
#                             status_text = "草稿"
#                             status_code = 1
                    
#                     # 批次更新：把狀態填入所有屬於這個前綴的 Oracle Row
#                     for row in row_map[result_key]:
#                         row['DOC_STATUS'] = status_text
#                         row['DOC_STATUS_CODE'] = status_code
#                         row['DOC_TOKEN'] = doc['document_token']

#     except Exception as e:
#         print(f"MySQL Status Check Error: {e}")


@bp.get("/view-list")
def get_specification_view_list():
    # 1. 接收參數
    try:
        page = int(request.args.get("page", 1))
        page_size = int(request.args.get("pageSize", 20))
    except ValueError:
        page = 1
        page_size = 20

    start_date = request.args.get("startDate")
    end_date = request.args.get("endDate")
    item = request.args.get("item")
    station = request.args.get("station")
    unit = request.args.get("unit")
    
    filter_prod = request.args.get("filterProd") == 'true'
    filter_proto = request.args.get("filterProto") == 'true'
    filter_unconfirmed = request.args.get("filterUnconfirmed") == 'true'
    
    # 2. 建構動態 WHERE 條件 (為了注入到內層 SQL)
    #    注意：這裡我們只建立 "AND ..." 的字串，方便稍後插入
    where_clause = ""
    params = {}

    if start_date:
        where_clause += " AND T_TIME >= TO_DATE(:start_date, 'YYYYMMDD')"
        params['start_date'] = start_date.replace("-", "")
    if end_date:
        where_clause += " AND T_TIME < TO_DATE(:end_date, 'YYYYMMDD') + 1"
        params['end_date'] = end_date.replace("-", "")
    if item:
        where_clause += " AND ITEM LIKE :item"
        params['item'] = f"%{item}%"
    if station:
        where_clause += " AND (STATION LIKE :station OR STATION_CH LIKE :station)"
        params['station'] = f"%{station}%"
    if unit:
        where_clause += " AND CLASS_CH LIKE :unit"
        params['unit'] = f"%{unit}%"
    
    # Checkbox 邏輯
    if filter_prod and not filter_proto:
        where_clause += " AND ITEM LIKE 'Z%'"
    elif filter_proto and not filter_prod:
        where_clause += " AND ITEM NOT LIKE 'Z%'"
    if filter_unconfirmed:
        where_clause += " AND CHECK_TIME IS NULL"

    try:
        # Step A: 取得總筆數 (Total Count)
        # 邏輯：計算有多少個不重複的 (BOOK + ITEM前綴) 組合
        count_sql = f"""
            SELECT COUNT(*) FROM (
                SELECT DISTINCT 
                    BOOK, 
                    SUBSTR(ITEM, 1, INSTR(ITEM || '-', '-') - 1) 
                FROM EZFLEX."KKME_Table" 
                WHERE 1=1 {where_clause}
            )
        """
        
        total_count = 0
        with ora_cursor(db_alias='item_db') as cur:
            cur.execute(count_sql, params)
            total_count = cur.fetchone()[0]

        # Step B: 取得分頁資料 (Ultimate SQL 整合版)
        
        # 核心 SQL: 
        # 1. Inner Layer 1: 基礎篩選 + 依完整料號分組 (找出 ROWID)
        # 2. Inner Layer 2: 依前綴分組 + 串接製程 + 找出最新 ROWID
        # 3. Outer Layer: JOIN 回原始表取 CLOB
        
        core_sql = f"""
            SELECT 
                FinalGroup.BOOK,
                FinalGroup.ITEM_PREFIX as ITEM,
                FinalGroup.PROCESS_INFO,
                FinalGroup.T_TIME,
                FinalGroup.CHECK_TIME,
                -- 利用抓到的 ROWID 回去原始表格撈資料，避開 CLOB 聚合錯誤
                Origin.CLASS_CH,
                Origin.PT_EMP,
                DBMS_LOB.SUBSTR(Origin.PRECAUTIONS, 4000, 1) as PRECAUTIONS
            FROM (
                -- Layer 2: 針對「前綴」分組，找出最新的 ROWID
                SELECT 
                    BOOK,
                    ITEM_PREFIX,
                    -- 串接製程
                    LISTAGG('(' || STATION || ')' || STATION_CH, CHR(10)) 
                        WITHIN GROUP (ORDER BY SEQ) AS PROCESS_INFO,
                    -- 找出最新的時間
                    MAX(MAX_T) as T_TIME,
                    MAX(CHECK_TIME) as CHECK_TIME,
                    -- ★ 關鍵：找出這一組裡面，時間最新的那一筆資料的 ROWID
                    MAX(LATEST_RID) KEEP (DENSE_RANK LAST ORDER BY MAX_T) as TARGET_RID
                FROM (
                    -- Layer 1: 針對「完整料號」分組，先找出每一小組的最新 ROWID
                    SELECT 
                        BOOK,
                        SUBSTR(ITEM, 1, INSTR(ITEM || '-', '-') - 1) as ITEM_PREFIX,
                        STATION,
                        STATION_CH,
                        SEQ,
                        MAX(T_TIME) as MAX_T,
                        MAX(CHECK_TIME) as CHECK_TIME,
                        -- 這裡只抓 ROWID，不抓 CLOB
                        MAX(ROWID) KEEP (DENSE_RANK LAST ORDER BY T_TIME) as LATEST_RID
                    FROM EZFLEX."KKME_Table"
                    WHERE 1=1 {where_clause}  -- ★ 注入篩選條件
                    GROUP BY 
                        BOOK, 
                        SUBSTR(ITEM, 1, INSTR(ITEM || '-', '-') - 1),
                        STATION, 
                        STATION_CH, 
                        SEQ
                )
                GROUP BY BOOK, ITEM_PREFIX
                -- 排序依據：最新修改時間
            ) FinalGroup
            -- 最後再 JOIN 回去原始表格
            JOIN EZFLEX."KKME_Table" Origin ON FinalGroup.TARGET_RID = Origin.ROWID
            ORDER BY FinalGroup.T_TIME DESC
        """

        # 分頁 SQL 包裝 (Oracle 11g/12c ROWNUM 寫法)
        data_sql = f"""
            SELECT * FROM (
                SELECT T.*, ROWNUM RN FROM (
                    {core_sql}
                ) T WHERE ROWNUM <= :end_row
            ) WHERE RN > :start_row
        """
        
        params['start_row'] = (page - 1) * page_size
        params['end_row'] = page * page_size

        rows = []
        with ora_cursor(db_alias='item_db') as cur:
            cur.execute(data_sql, params)
            columns = [col[0] for col in cur.description]
            
            for r in cur.fetchall():
                row_dict = dict(zip(columns, r))
                if 'RN' in row_dict: del row_dict['RN']
                
                # 日期轉字串
                if row_dict.get('T_TIME'):
                    row_dict['T_TIME'] = row_dict['T_TIME'].strftime('%Y-%m-%d %H:%M:%S')
                if row_dict.get('CHECK_TIME'):
                    row_dict['CHECK_TIME'] = row_dict['CHECK_TIME'].strftime('%Y-%m-%d %H:%M:%S')

                # 處理 NULL (雖然 ListAgg 通常不會回傳 None，但保險起見)
                if row_dict.get('PT_EMP') is None: row_dict['PT_EMP'] = ""
                if row_dict.get('PRECAUTIONS') is None: row_dict['PRECAUTIONS'] = ""
                
                rows.append(row_dict)

        # Step C: MySQL 狀態查詢 (保持不變)
        if rows:
            enrich_with_document_status(rows)

        return send_response(200, True, "查詢成功", {
            "items": rows,
            "total": total_count,
            "page": page,
            "pageSize": page_size
        })

    except Exception as e:
        print(f"Error: {e}")
        return send_response(500, False, f"資料庫錯誤: {str(e)}", {})


def enrich_with_document_status(oracle_rows):
    """
    針對 Oracle 查出來的資料，去 MySQL 查詢文件狀態。
    修正邏輯：
    1. Key 只使用 BOOK (對應 MySQL styleNo)
    2. 移除 Item/Matnr 的比對
    """
    
    # 1. 整理 Key 並建立對照表
    # key: "BOOK", value: [row_reference_list]
    row_map = {}
    
    for row in oracle_rows:
        book = row.get('BOOK', '')
        
        # 以 BOOK 為唯一 Key
        key = book
        
        if not key: continue # 防呆

        if key not in row_map:
            row_map[key] = []
            
        row_map[key].append(row)
        
        # 預設狀態
        row['DOC_STATUS'] = '未建立' 
        row['DOC_STATUS_CODE'] = 0

    if not row_map:
        return

    # 2. 動態建構 SQL WHERE 子句
    # 目標：(attr.attribute->>'$.styleNo' = 'BOOK1') OR (attr.attribute->>'$.styleNo' = 'BOOK2') ...
    
    where_clauses = []
    sql_params = {}
    
    for idx, book_key in enumerate(row_map.keys()):
        # 只要比對 styleNo
        where_clauses.append(f"(attr.attribute->>'$.styleNo' = %(book_{idx})s)")
        sql_params[f'book_{idx}'] = book_key

    if not where_clauses:
        return

    # 3. 執行 MySQL 查詢
    filter_sql = " OR ".join(where_clauses)
    
    mysql_sql = f"""
        SELECT 
            attr.document_token, 
            attr.status, 
            attr.attribute, 
            COUNT(snap.snapshot_id) as snap_count, 
            SUM(CASE WHEN snap.sync_status = 2 THEN 1 ELSE 0 END) as return_count 
        FROM rms_document_attributes attr
        LEFT JOIN rms_document_snapshots snap ON attr.document_token = snap.document_token
        WHERE attr.document_type = 1 
          AND ({filter_sql})
        GROUP BY attr.document_token, attr.status, attr.attribute
    """
    
    try:
        with db(dict_cursor=True) as (_, cur):
            cur.execute(mysql_sql, sql_params)
            doc_results = cur.fetchall()
            
            # 4. 填回狀態
            for doc in doc_results:
                # 解析 JSON attribute
                attr_data = doc['attribute']
                if isinstance(attr_data, str):
                    attr_data = json.loads(attr_data)
                
                # 取得 styleNo (對應 BOOK)
                style_no = attr_data.get('styleNo', '')
                
                # 查找 row_map (Key 就是 styleNo)
                if style_no in row_map:
                    # 狀態判斷邏輯
                    status_text = "草稿"
                    status_code = 1
                    
                    db_status = doc['status']
                    snap_count = doc['snap_count']
                    return_count = doc['return_count']

                    if db_status == 2:
                        status_text = "已簽核"
                        status_code = 3
                    elif db_status == 1:
                        if return_count and return_count > 0:
                            status_text = "被退回"
                            status_code = 4
                        elif snap_count > 1:
                            status_text = "送審中"
                            status_code = 2
                        else:
                            status_text = "草稿"
                            status_code = 1
                    
                    # 批次更新所有該式樣書的 Row
                    for row in row_map[style_no]:
                        # 如果該 BOOK 下有多個 ITEM，大家都會拿到相同的狀態
                        row['DOC_STATUS'] = status_text
                        row['DOC_STATUS_CODE'] = status_code
                        row['DOC_TOKEN'] = doc['document_token']

    except Exception as e:
        print(f"MySQL Status Check Error: {e}")