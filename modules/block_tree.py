# modules/block_tree.py
#
# 文件區塊「樹狀階層」核心純函數庫。
# 對應設計共識：docs-design/block-hierarchy-redesign-spec.md
#
# 本模組 *不碰資料庫*，全部是可單元測試的純函數，供以下三處共用：
#   1. save API：前端巢狀樹 → DFS 攤平成 DB row（flatten_tree）
#   2. load API：DB relational row → 巢狀樹（build_tree）
#   3. 編號：Word 匯出 / 前端預覽共用同一套規則（format_node_number）
#   4. 遷移 / 簽核回寫：舊 (step/tier/sub) → 新 (parent/sort/depth)（normalize_legacy_blocks）
#
# 設計要點索引：
#   §4   content_type 列舉           §5/§5.1/§5.2 編號規則 + 溢位 + 測試向量
#   §5.3 L1 章節號對照（寫死）        §7   巢狀樹 contract
#   §10.4 舊→新轉換規則               §13  depth 上限 8
#   §18 F6 converter 全函數 + 節點數對帳（對不上 raise，絕不靜默吞節點）

import json

from utils import new_token

# ============================================================
# 常數
# ============================================================
MAX_DEPTH = 8                      # §13
PARAM_STEP_TYPES = {2, 5}          # §6.B 參數表 leaf（指示書 MCR / 式樣書製造參數）

# content_type（§4）
CT_EMPTY = 0       # 無內容（純標題 + 子層）
CT_TEXT = 1        # 文字
CT_TABLE = 2       # 表格
CT_INSERT_DOC = 3  # 插入文件（唯讀標題節點，不綁 reference，§19 F9）
CT_PARAM = 4       # 參數表（step 2/5，leaf）

# 新 DB 落地欄位（取代舊 BLOCK_COLUMNS；移除 tier_no/sub_no，新增 parent_id/sort_order/depth）
# 對應 spec §20.3 / §21.1。client_id、children 為傳輸層專用、不入庫。
NEW_BLOCK_COLUMNS = [
    "content_id", "document_token", "step_type", "parent_id", "sort_order", "depth",
    "content_type", "header_text", "header_json", "content_text", "content_json",
    "table_text", "table_json", "files", "metadata",
]

# L1 章節號對照（§5.3 / §20.2）— 章節號 = DOCUMENT_STEP 排列順序，非 step_type 公式。
# 指示書：0→2 1→3 2→4 3→5 ；式樣書：4→2 5→3 6→4 7→6（other 跳過被「使用表單」佔走的 5.）
CHAPTER_BY_STEP = {0: 2, 1: 3, 2: 4, 3: 5, 4: 2, 5: 3, 6: 4, 7: 6}

# 內容類欄位（在攤平 / 轉換時要原樣搬運的 payload 欄）
_PAYLOAD_KEYS = [
    "content_type", "header_text", "header_json", "content_text", "content_json",
    "table_text", "table_json", "files", "metadata",
]


def chapter_for_step(step_type):
    """回傳該 step_type 的 L1 章節號（int）。未知 step_type → KeyError（刻意 fail loud）。"""
    return CHAPTER_BY_STEP[step_type]


# ============================================================
# 編號（§5 / §5.1 / §5.2）
# ============================================================
def _to_alpha(n):
    """1→A, 26→Z, 27→AA …（bijective base-26，§5.1）。"""
    if n < 1:
        raise ValueError(f"alpha index must be >=1, got {n}")
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(ord("A") + r) + s
    return s


def _to_roman(n):
    """1→I, 4→IV …（標準羅馬，不設上限，§5.1）。"""
    if n < 1:
        raise ValueError(f"roman index must be >=1, got {n}")
    table = [
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ]
    out = []
    for val, sym in table:
        while n >= val:
            out.append(sym)
            n -= val
    return "".join(out)


def format_node_number(chapter, index_path):
    """
    依 (章節號, 從 L2 起的 1-based index 路徑) 算出節點編號字串。

    index_path 長度 = depth - 1：
      [i2]            → L2 → "{chapter}.{i2}"            （dotted 累進）
      [i2, i3]        → L3 → "{chapter}.{i2}.{i3}"       （dotted 累進）
      [..., i4]       → L4 → "({i4})"                    （只顯示自身，以下同）
      [..., i5]       → L5 → "({A..})"   大寫英
      [..., i6]       → L6 → "({a..})"   小寫英
      [..., i7]       → L7 → "({I..})"   大寫羅馬
      [..., i8]       → L8 → "({i..})"   小寫羅馬

    對應 §5.2 測試向量；超過 L8 → ValueError。
    """
    depth = 1 + len(index_path)
    if depth == 2:
        return f"{chapter}.{index_path[0]}"
    if depth == 3:
        return f"{chapter}.{index_path[0]}.{index_path[1]}"
    last = index_path[-1]
    if depth == 4:
        return f"({last})"
    if depth == 5:
        return f"({_to_alpha(last)})"
    if depth == 6:
        return f"({_to_alpha(last).lower()})"
    if depth == 7:
        return f"({_to_roman(last)})"
    if depth == 8:
        return f"({_to_roman(last).lower()})"
    raise ValueError(f"depth {depth} 超過上限 {MAX_DEPTH}")


# ============================================================
# 巢狀樹 ←→ relational row（§7）
# ============================================================
def flatten_tree(step_trees, document_token, id_factory=new_token):
    """
    存檔用：前端巢狀樹 → DB row list（DFS 攤平）。

    輸入 step_trees（§7.1）：
        [ { "step_type": 1, "children": [ {node}, ... ] }, ... ]
      每個 node：
        { "client_id", "content_type", "header_json", "header_text",
          "content_json", "content_text", "table_json", "table_text",
          "files", "metadata", "children": [...] }

    規則（§F5）：
      - content_id 由後端 id_factory 分配（replace 下每存重發）
      - parent_id 填父節點剛分到的 content_id（L2 為 None）
      - sort_order = 同 parent 下陣列 index（1-based）
      - depth 走訪累加，超過 MAX_DEPTH → raise（§13）

    回傳：(rows, echo_map)
      rows     : list[dict]，欄位齊 NEW_BLOCK_COLUMNS，可直接 executemany
      echo_map : { client_id: content_id }（§7.2 回傳給前端寫回）
    """
    rows = []
    echo_map = {}

    def walk(node, step_type, parent_id, depth, sort_order):
        if depth > MAX_DEPTH:
            raise ValueError(f"節點 depth {depth} 超過上限 {MAX_DEPTH}（step_type={step_type}）")
        cid = id_factory()
        client_id = node.get("client_id")
        if client_id is not None:
            echo_map[client_id] = cid

        row = {k: node.get(k) for k in _PAYLOAD_KEYS}
        row["content_type"] = int(node.get("content_type", CT_EMPTY) or CT_EMPTY)
        row["content_id"] = cid
        row["document_token"] = document_token
        row["step_type"] = step_type
        row["parent_id"] = parent_id
        row["sort_order"] = sort_order
        row["depth"] = depth
        rows.append(row)

        children = node.get("children") or []
        for idx, child in enumerate(children, start=1):
            walk(child, step_type, cid, depth + 1, idx)

    for step in step_trees:
        step_type = int(step["step_type"])
        for idx, node in enumerate(step.get("children") or [], start=1):
            walk(node, step_type, None, 2, idx)  # L2 起算

    return rows, echo_map


def build_tree(flat_rows):
    """
    載入用：DB relational row → 巢狀樹（§7.3）。
    依 parent_id 掛回父節點 children[]，同層用 sort_order 排序，root 按 step_type 分組。

    回傳：[ { "step_type": int, "children": [node, ...] }, ... ]，step_type 升冪。
    每個 node 帶 content_id + payload 欄 + children[]。
    """
    by_id = {}
    for r in flat_rows:
        node = {k: r.get(k) for k in _PAYLOAD_KEYS}
        node["content_id"] = r["content_id"]
        node["children"] = []
        node["_sort_order"] = r.get("sort_order") or 0
        by_id[r["content_id"]] = node

    step_roots = {}  # step_type -> [root nodes]
    for r in flat_rows:
        node = by_id[r["content_id"]]
        parent_id = r.get("parent_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(node)
        else:
            step_roots.setdefault(int(r["step_type"]), []).append(node)

    def sort_children(node):
        node["children"].sort(key=lambda n: n["_sort_order"])
        for c in node["children"]:
            sort_children(c)
        node.pop("_sort_order", None)

    out = []
    for step_type in sorted(step_roots.keys()):
        roots = step_roots[step_type]
        roots.sort(key=lambda n: n["_sort_order"])
        for root in roots:
            sort_children(root)
        out.append({"step_type": step_type, "children": roots})
    return out


# ============================================================
# 舊 (step/tier/sub) → 新 (parent/sort/depth) 轉換（§10.4 / §18 F6）
# ============================================================
def normalize_legacy_blocks(legacy_rows):
    """
    把舊格式 block rows（含 tier_no / sub_no）轉成新格式 flat rows（含 parent_id / sort_order / depth）。
    供「一次性遷移」與「簽核 lazy 回寫」共用（§10.6 共用 converter）。

    轉換規則（§10.4，舊資料 ≤ L3）：
      - step_type            → L1 root 分組（不變）
      - tier_no              → L2 節點（depth=2, parent_id=None, sort_order=tier 排名）
      - sub_no（一般 step）   → 同一 tier 內：第一筆 = L2 節點本體，其餘 = L3 子節點（depth=3）
      - sub_no 0/1（step2/5） → 合併成一個 param_table 節點（content_type=4），
                               table_json = { parameterTable, conditionTable }，condition row 併入後丟棄

    ⚠️ 兩個必須由真實資料驗證的假設（先實作、上線前比對）：
      A. 一般 step 內「第一筆 sub_no = L2 本體、其餘 = L3 子節點」的語意。
      B. 參數表來源欄位：本函數優先讀 content_json/content_text（snapshot 可來回的欄位，
         見 docs.py BLOCK_CONTENT_ORDER 不含 table_json），table_json 為輔。新格式統一寫入 table_json。

    §18 F6 全函數 + 節點數對帳：輸出節點數必須 == 輸入 row 數 − 被合併的 condition row 數，
    對不上即 raise（絕不靜默吞節點）。

    回傳：list[dict]（欄位齊 NEW_BLOCK_COLUMNS 中除 document_token 外的內容欄；document_token 原樣保留若有）
    """
    out = []
    merged_condition_count = 0

    # group by step_type -> tier_no（保序）
    by_step = {}
    for r in legacy_rows:
        by_step.setdefault(int(r["step_type"]), {}).setdefault(int(r["tier_no"]), []).append(r)

    for step_type in sorted(by_step.keys()):
        tiers = by_step[step_type]
        for tier_rank, tier_no in enumerate(sorted(tiers.keys()), start=1):
            sub_rows = sorted(tiers[tier_no], key=lambda r: int(r["sub_no"]))

            if step_type in PARAM_STEP_TYPES:
                # --- 參數表：sub0(參數) + sub1(條件) → 單一 param_table 節點 ---
                sub0 = next((r for r in sub_rows if int(r["sub_no"]) == 0), sub_rows[0])
                sub1 = next((r for r in sub_rows if int(r["sub_no"]) == 1), None)

                param_table = sub0.get("table_json") or sub0.get("content_json")
                cond_table = None
                if sub1 is not None:
                    cond_table = sub1.get("table_json") or sub1.get("content_json")
                    merged_condition_count += 1

                node = _new_row(
                    sub0, step_type, parent_id=None, sort_order=tier_rank, depth=2,
                    content_type=CT_PARAM,
                    table_json={"parameterTable": param_table, "conditionTable": cond_table},
                )
                # 帶上 sub1 的 2D 條件表（供 migrate 在沒有 tiptap 時反建 conditionTable）
                node["_cond_2d"] = sub1.get("content_text") if sub1 is not None else None
                # 其餘非 0/1 的 sub（理論上不存在）→ 視為 L3 子節點，保守保留
                for child_rank, r in enumerate(
                    [r for r in sub_rows if int(r["sub_no"]) not in (0, 1)], start=1
                ):
                    out.append(_new_row(r, step_type, node["content_id"], child_rank, 3))
                out.append(node)
            else:
                # --- 一般 step：第一筆 = L2 本體，其餘 = L3 子節點 ---
                head = sub_rows[0]
                l2 = _new_row(head, step_type, parent_id=None, sort_order=tier_rank, depth=2)
                out.append(l2)
                for child_rank, r in enumerate(sub_rows[1:], start=1):
                    out.append(_new_row(r, step_type, l2["content_id"], child_rank, 3))

    # §18 F6 節點數對帳
    expected = len(legacy_rows) - merged_condition_count
    if len(out) != expected:
        raise ValueError(
            f"normalize_legacy_blocks 節點數對帳失敗：輸出 {len(out)} != 預期 {expected} "
            f"（輸入 {len(legacy_rows)} − 合併條件 {merged_condition_count}）"
        )
    return out


def _new_row(src, step_type, parent_id, sort_order, depth, content_type=None, table_json="__keep__"):
    """從舊 row 組出新 row：沿用原 content_id，搬運 payload，覆寫座標欄。"""
    row = {k: src.get(k) for k in _PAYLOAD_KEYS}
    row["content_id"] = src.get("content_id") or new_token()
    if "document_token" in src:
        row["document_token"] = src["document_token"]
    row["step_type"] = step_type
    row["parent_id"] = parent_id
    row["sort_order"] = sort_order
    row["depth"] = depth
    if content_type is not None:
        row["content_type"] = content_type
    if table_json != "__keep__":
        row["table_json"] = table_json
    return row


# ============================================================
# 遷移專用轉換（sfdb4070 → sfdb）：內容分類 / 定值項目 / step5 program-code
# 對應 docs-design/db-migration-considerations.md §3。
# 輸入/輸出皆「已解析」dict（JSON 欄位為 dict/list，非字串）；caller 負責 jload/jdump。
# ============================================================
FIXED_VALUE_HEADER = "定值項目"
FIXED_VALUE_UNSET = "☐"   # 預設 False（未設為定值）
FIXED_VALUE_SET = "☑"
_MGMT_HEADER = "管理項目"   # 定值項目插在此欄之後
_CELL_TYPES = ("tableHeader", "tableCell", "customTableCell")


def _as_list(v):
    """content_text/table_text 可能是 list 或 JSON 字串 → 統一回 list。"""
    if isinstance(v, list):
        return v
    if isinstance(v, str) and v.strip():
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else []
        except (ValueError, TypeError):
            return []
    return []


def _find_table_node(doc):
    """從 tiptap doc / table 節點找出 table 節點本體；找不到回 None。"""
    if not isinstance(doc, dict):
        return None
    if doc.get("type") == "table":
        return doc
    for item in doc.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "table":
            return item
    return None


def _cell_text(cell):
    """取出一個 cell 的第一段文字。"""
    if not isinstance(cell, dict):
        return ""
    for para in cell.get("content") or []:
        for inline in para.get("content") or []:
            if inline.get("type") == "text":
                return inline.get("text", "")
    return ""


def _make_cell(text, is_header):
    """組一個標準 tiptap cell。"""
    return {
        "type": "tableHeader" if is_header else "tableCell",
        "attrs": {"colspan": 1, "rowspan": 1, "colwidth": None},
        "content": [{"type": "paragraph", "content": ([{"type": "text", "text": text}] if text else [])}],
    }


def _make_fixed_value_header():
    """定值項目 表頭：tableHeader，對齊同表其他表頭（cellType=text、contenteditable=false）。"""
    return {
        "type": "tableHeader",
        "attrs": {"colspan": 1, "rowspan": 1, "cellType": "text", "colwidth": None, "contenteditable": False},
        "content": [{"type": "paragraph", "content": [{"type": "text", "text": FIXED_VALUE_HEADER}]}],
    }


def _make_fixed_value_cell(checked=False, custom=True):
    """
    定值項目 資料格：checkbox。狀態在 attrs.checked（布林），content 為空 paragraph（無文字）——
    不可硬塞 ☐ 文字，否則前端 parseParamTable 認不得 → 解析失敗 → emit 錯誤 table → PMS 比對失敗。
      custom=True  → customTableCell（製造/式樣參數，表中其他格為 customTableCell，含 dropdown 屬性）。
      custom=False → tableCell（管理條件/基本生產條件，表中其他格為 tableCell，無 dropdown 屬性）。
    """
    if custom:
        return {
            "type": "customTableCell",
            "attrs": {
                "class": None,
                "checked": bool(checked),
                "colspan": 1,
                "rowspan": 1,
                "cellType": "checkbox",
                "colwidth": None,
                "dropdownColor": "#000000",
                "dropdownValue": "",
                "contenteditable": False,
                "dropdownOptions": [],
            },
            "content": [{"type": "paragraph"}],
        }
    return {
        "type": "tableCell",
        "attrs": {
            "cellType": "checkbox",
            "checked": bool(checked),
            "class": None,
            "colspan": 1,
            "colwidth": None,
            "contenteditable": False,
            "rowspan": 1,
        },
        "content": [{"type": "paragraph"}],
    }


def _header_cell_texts(table_node):
    """回 [header_texts]（第一列 cell 的文字）。"""
    rows = [r for r in (table_node.get("content") or []) if isinstance(r, dict) and r.get("type") == "tableRow"]
    if not rows:
        return []
    cells = [c for c in (rows[0].get("content") or []) if isinstance(c, dict) and c.get("type") in _CELL_TYPES]
    return [_cell_text(c) for c in cells]


def insert_fixed_value_column_tiptap(table_doc, custom_cell=True):
    """
    在 tiptap 參數表「管理項目」欄之後插入「定值項目」欄（header=tableHeader，資料列=checkbox）。
    custom_cell=True → 資料格用 customTableCell（製造/式樣參數）；False → tableCell（管理條件）。
    冪等：已有「定值項目」→ 原樣回；找不到「管理項目」→ 原樣回（非目標表）。
    ⚠️ 假設規則格 grid（無 colspan/rowspan 合併）；合併格資料需另行驗證。
    """
    if not isinstance(table_doc, dict):
        return table_doc
    table_node = _find_table_node(table_doc)
    if table_node is None:
        return table_doc
    header_texts = _header_cell_texts(table_node)
    if FIXED_VALUE_HEADER in header_texts:
        return table_doc                      # 冪等
    if _MGMT_HEADER not in header_texts:
        return table_doc                      # 非目標表
    insert_pos = header_texts.index(_MGMT_HEADER) + 1

    rows = [r for r in (table_node.get("content") or []) if isinstance(r, dict) and r.get("type") == "tableRow"]
    for ridx, row in enumerate(rows):
        cells = row.get("content") or []
        cell_positions = [i for i, c in enumerate(cells) if isinstance(c, dict) and c.get("type") in _CELL_TYPES]
        at = cell_positions[insert_pos] if insert_pos < len(cell_positions) else (cell_positions[-1] + 1 if cell_positions else 0)
        new_cell = _make_fixed_value_header() if ridx == 0 else _make_fixed_value_cell(False, custom=custom_cell)
        cells.insert(at, new_cell)
        row["content"] = cells
    return table_doc


def insert_fixed_value_column_2d(rows_2d):
    """2D 陣列版：管理項目 後插「定值項目」欄，資料列預設 ☐。冪等。"""
    rows_2d = _as_list(rows_2d)
    if not rows_2d or not isinstance(rows_2d[0], list):
        return rows_2d
    header = rows_2d[0]
    if FIXED_VALUE_HEADER in header:
        return rows_2d
    if _MGMT_HEADER not in header:
        return rows_2d
    pos = header.index(_MGMT_HEADER) + 1
    out = []
    for ridx, row in enumerate(rows_2d):
        row = list(row)
        row.insert(pos, FIXED_VALUE_HEADER if ridx == 0 else FIXED_VALUE_UNSET)
        out.append(row)
    return out


def classify_content_json(content_json):
    """判斷 content_json 是 table / paragraph / empty（舊庫表格塞在 content_json）。"""
    if not isinstance(content_json, dict):
        return "empty"
    content = content_json.get("content") or []
    if not content:
        return "empty"
    if any(isinstance(b, dict) and b.get("type") == "table" for b in content):
        return "table"
    return "paragraph"


def _prefix_step5_programs(metadata, item_type):
    """step5 program code 加 ${itemType}- 前綴（冪等）。"""
    if not isinstance(metadata, dict) or not item_type:
        return
    for p in metadata.get("programs") or []:
        code = p.get("programCode")
        if code and not str(code).startswith(f"{item_type}-"):
            p["programCode"] = f"{item_type}-{code}"


def _rewrap_table_doc(doc):
    """確保 table 節點在 content[0]（前端 parseParamTable 寫死讀 content[0]）。無 table → None。"""
    t = _find_table_node(doc)
    return {"type": "doc", "content": [t]} if t else None


def _table_has_cells(doc):
    """table 是否有實際儲存格。空表（tableRow 無 cell）→ False，視為無資料（避免前端 parseParamTable 對空表 .map 炸）。"""
    t = _find_table_node(doc)
    if not t:
        return False
    for r in t.get("content") or []:
        if isinstance(r, dict) and r.get("type") == "tableRow":
            if [c for c in (r.get("content") or []) if isinstance(c, dict) and c.get("type") in _CELL_TYPES]:
                return True
    return False


def _tiptap_from_2d(rows_2d):
    """2D 陣列 → tiptap table doc（首列 tableHeader，其餘 tableCell）。空 → None。"""
    rows_2d = _as_list(rows_2d)
    if not rows_2d or not isinstance(rows_2d[0], list):
        return None
    trows = []
    for ridx, row in enumerate(rows_2d):
        cells = [_make_cell("" if c is None else str(c), ridx == 0) for c in row]
        trows.append({"type": "tableRow", "content": cells})
    return {"type": "doc", "content": [{"type": "table", "content": trows}]}


def _migrate_param_node(row, item_type):
    """
    step2/5 參數表節點(ct=4)。舊庫 param 多數沒有 tiptap、資料在 2D 陣列(content_text)，故：
      - parameterTable：有 tiptap → 補定值項目 + re-wrap(table 在 content[0])；無 → 從 sub0 的 2D 反建。
      - conditionTable：有 tiptap → re-wrap；無 → 從 sub1 的 2D(_cond_2d) 反建（不補定值項目）。
      - table_text = sub0 的 2D（補定值項目）。step5 加 program-code 前綴。
    """
    tj = row.get("table_json") if isinstance(row.get("table_json"), dict) else {}

    # sub0 參數 2D（補定值項目）
    param_2d = _as_list(row.get("content_text"))
    if param_2d:
        param_2d = insert_fixed_value_column_2d(param_2d)

    # parameterTable：有實際儲存格的 tiptap → 用它；否則從 2D 反建；都是空表 → None（前端走 isParamNA，不炸）
    p_tt = tj.get("parameterTable")
    if _table_has_cells(p_tt):
        p_tt = _rewrap_table_doc(insert_fixed_value_column_tiptap(p_tt))
    else:
        built = _tiptap_from_2d(param_2d)
        p_tt = built if _table_has_cells(built) else None

    # conditionTable：同上（不補定值項目，§3.4）
    c_tt = tj.get("conditionTable")
    if _table_has_cells(c_tt):
        c_tt = _rewrap_table_doc(c_tt)
    else:
        built = _tiptap_from_2d(_as_list(row.get("_cond_2d")))
        c_tt = built if _table_has_cells(built) else None

    row["table_json"] = {"parameterTable": p_tt, "conditionTable": c_tt}
    row["table_text"] = param_2d if (param_2d and any(r for r in param_2d)) else None
    row["content_text"] = None
    row["content_json"] = None
    row.pop("_cond_2d", None)
    if int(row.get("step_type", -1)) == 5:
        _prefix_step5_programs(row.get("metadata"), item_type)


def _migrate_generic_node(row):
    """一般節點：content_json 是表格 → 搬 table_json + ct=2；段落 → 留 content_json + ct=1。"""
    kind = classify_content_json(row.get("content_json"))
    if kind == "table":
        row["table_json"] = row.get("content_json")
        row["content_json"] = None
        row["content_type"] = CT_TABLE
        two_d = _as_list(row.get("content_text"))
        if two_d:
            row["table_text"] = two_d
            row["content_text"] = None
    elif kind == "paragraph":
        if row.get("content_type") not in (CT_TEXT, CT_INSERT_DOC):
            row["content_type"] = CT_TEXT
    # empty → 維持原 content_type（多為 0 純標題）


def _migrate_pms_node(row):
    """step1 tier1 基本生產條件：標記 management + 補定值項目（content 已先被 _migrate_generic_node 分類）。"""
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    md["source"] = "management"
    row["metadata"] = md
    if isinstance(row.get("table_json"), dict):
        # 管理條件的表格是 tableCell 系（非 customTableCell）→ 定值格用 tableCell 變體
        row["table_json"] = insert_fixed_value_column_tiptap(row["table_json"], custom_cell=False)
    two_d = _as_list(row.get("table_text"))
    if two_d:
        row["table_text"] = insert_fixed_value_column_2d(two_d)


def _synthetic_pms_legacy(legacy_rows):
    """組一個「空的基本生產條件(PMS)」舊式 row，放在 step1 tier1。對齊新系統的空 PMS placeholder。"""
    synth = {
        "content_id": new_token(),
        "step_type": 1, "tier_no": 1, "sub_no": 0,
        "content_type": CT_TABLE,                                          # = 2（對齊新系統空 PMS row）
        "header_text": None, "header_json": None,
        "content_text": None,
        "content_json": {"type": "doc", "content": [{"type": "paragraph"}]},
        "table_text": None, "table_json": None,
        "files": [], "metadata": {"source": "management"},
    }
    doc = next((r.get("document_token") for r in legacy_rows if r.get("document_token")), None)
    if doc is not None:
        synth["document_token"] = doc
    return synth


def should_synthesize_pms(legacy_rows, is_instruction):
    """
    這份文件是否需要補一個空 PMS（基本生產條件）。
    規則：**指示書（document_type==0）一律要有 step1 tier1 PMS，沒有例外**——
    只要缺 step1 tier1（含整個 step1 都沒有）就補。式樣書（is_instruction=False）不補。
    觸發條件是「這份是指示書」，與 step2 等其他 step 的有無無關。
    """
    if not is_instruction:
        return False
    return not any(int(r["step_type"]) == 1 and int(r["tier_no"]) == 1 for r in legacy_rows)


def _ensure_pms_first(legacy_rows, is_instruction):
    """
    指示書缺 step1 tier1 PMS → 補一個空 PMS 在最前，讓新表 step1 sort_order=1 一律是 PMS，
    並避免一般區塊（舊 tier_no≥2）被正規化往前擠到 sort_order=1、被誤判成基本生產條件。
    回傳 (legacy_rows, 合成 PMS 的 content_id 或 None)。
    """
    if not should_synthesize_pms(legacy_rows, is_instruction):
        return legacy_rows, None
    synth = _synthetic_pms_legacy(legacy_rows)
    return [synth] + list(legacy_rows), synth["content_id"]


def _finalize_synthetic_pms(row):
    """合成的空 PMS 定型：content_type=2、空 paragraph、source=management，不跑內容分類（否則會被改成 ct=1）。"""
    row["content_type"] = CT_TABLE
    row["content_json"] = {"type": "doc", "content": [{"type": "paragraph"}]}
    row["content_text"] = None
    row["table_json"] = None
    row["table_text"] = None
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    md["source"] = "management"
    row["metadata"] = md


def migrate_legacy_blocks(legacy_rows, item_type=None, is_instruction=False):
    """
    一次性遷移 / sync-eip 寫回共用：structural（normalize）+ 內容分類 + 定值項目 + step5 program-code。
    定值項目套用對象：step1 tier1（基本生產條件）、step2、step5 的 parameterTable（§3.4）。
    is_instruction=True（指示書, document_type==0）且缺 step1 tier1 → 補一個空基本生產條件（沒有例外）。
    """
    legacy_rows, synth_pms_id = _ensure_pms_first(legacy_rows, is_instruction)
    rows = normalize_legacy_blocks(legacy_rows)
    for row in rows:
        if synth_pms_id is not None and row.get("content_id") == synth_pms_id:
            _finalize_synthetic_pms(row)            # 合成 PMS：定型、不分類、不誤插定值項目
            continue
        if row.get("content_type") == CT_PARAM:
            _migrate_param_node(row, item_type)
        else:
            _migrate_generic_node(row)
            if int(row.get("step_type", -1)) == 1 and row.get("depth") == 2 and row.get("sort_order") == 1:
                _migrate_pms_node(row)   # step1 tier1 = 基本生產條件
    return rows


# ============================================================
# 簡易自我測試（python -m modules.block_tree）：驗 §5.2 編號向量
# ============================================================
if __name__ == "__main__":
    cases = [
        (2, [1], "2.1"),
        (2, [1, 3], "2.1.3"),
        (2, [9, 9, 1], "(1)"),         # L4 只顯示自身
        (2, [1, 1, 1, 27], "(AA)"),    # L5 idx=27
        (2, [1, 1, 1, 1, 28], "(ab)"), # L6 idx=28
        (2, [1, 1, 1, 1, 1, 4], "(IV)"),   # L7
        (2, [1, 1, 1, 1, 1, 1, 9], "(ix)"),  # L8
    ]
    ok = True
    for chapter, path, expect in cases:
        got = format_node_number(chapter, path)
        flag = "OK " if got == expect else "FAIL"
        if got != expect:
            ok = False
        print(f"[{flag}] chapter={chapter} path={path} -> {got!r} (expect {expect!r})")

    # ---- 遷移轉換測試 ----
    def _hc(t):
        return {"type": "tableHeader", "attrs": {}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]}]}
    def _dc(t):
        return {"type": "tableCell", "attrs": {}, "content": [{"type": "paragraph", "content": [{"type": "text", "text": t}]}]}

    def _check(name, cond):
        global ok
        if not cond:
            ok = False
        print(f"[{'OK ' if cond else 'FAIL'}] {name}")

    # 2D 定值項目插入（以你給的 step5 範例去頭欄反推 before）
    before_2d = [["槽體", "管理項目", "規格下限(OOS-)", "操作下限(OOC-)", "設定值", "操作上限(OOC+)", "規格上限(OOS+)", "說明"],
                 ["生產資訊", "真空值(Torr)", "1", "2", "3", "4", "5", ""]]
    after_2d = insert_fixed_value_column_2d(before_2d)
    _check("2D 定值項目 header 位置", after_2d[0] == ["槽體", "管理項目", "定值項目", "規格下限(OOS-)", "操作下限(OOC-)", "設定值", "操作上限(OOC+)", "規格上限(OOS+)", "說明"])
    _check("2D 定值項目 預設 unset", after_2d[1][2] == FIXED_VALUE_UNSET)
    _check("2D 定值項目 冪等", insert_fixed_value_column_2d(after_2d)[0].count("定值項目") == 1)

    # tiptap 定值項目插入
    tiptap = {"type": "doc", "content": [{"type": "table", "content": [
        {"type": "tableRow", "content": [_hc("槽體"), _hc("管理項目"), _hc("設定值")]},
        {"type": "tableRow", "content": [_dc("生產資訊"), _dc("真空值"), _dc("3")]},
    ]}]}
    out_tt = insert_fixed_value_column_tiptap(tiptap)
    hdr = [_cell_text(c) for c in out_tt["content"][0]["content"][0]["content"]]
    _check("tiptap 定值項目 header 位置", hdr == ["槽體", "管理項目", "定值項目", "設定值"])
    fv_cell = out_tt["content"][0]["content"][1]["content"][2]
    _check("tiptap 定值項目 為 checkbox customTableCell", fv_cell["type"] == "customTableCell" and fv_cell["attrs"]["cellType"] == "checkbox")
    _check("tiptap 定值項目 預設 unchecked", fv_cell["attrs"]["checked"] is False)
    _check("tiptap 定值項目 content 為空 paragraph（無文字）", fv_cell["content"] == [{"type": "paragraph"}])
    _check("tiptap 定值項目 冪等", insert_fixed_value_column_tiptap(out_tt)["content"][0]["content"][0]["content"].__len__() == 4)

    # content 分類
    _check("分類 paragraph", classify_content_json({"type": "doc", "content": [{"type": "paragraph"}]}) == "paragraph")
    _check("分類 table", classify_content_json({"type": "doc", "content": [{"type": "table"}]}) == "table")
    _check("分類 empty", classify_content_json({"type": "doc", "content": []}) == "empty")

    # step5 program-code 前綴 + 冪等
    md = {"programs": [{"programCode": "RE123456001"}]}
    _prefix_step5_programs(md, "ABC")
    _prefix_step5_programs(md, "ABC")
    _check("step5 program-code 前綴+冪等", md["programs"][0]["programCode"] == "ABC-RE123456001")

    # migrate_legacy_blocks 端到端：step5 param
    legacy_param = [{
        "content_id": "p0", "step_type": 5, "tier_no": 1, "sub_no": 0, "content_type": 2,
        "content_json": {"type": "doc", "content": [{"type": "table", "content": [
            {"type": "tableRow", "content": [_hc("槽體"), _hc("管理項目"), _hc("設定值")]},
            {"type": "tableRow", "content": [_dc("生產資訊"), _dc("真空值"), _dc("3")]}]}]},
        "content_text": [["槽體", "管理項目", "設定值"], ["生產資訊", "真空值", "3"]],
        "metadata": {"programs": [{"programCode": "RE999"}]},
    }]
    mp = migrate_legacy_blocks(legacy_param, item_type="STYLE")[0]
    _check("param→ct4", mp["content_type"] == 4)
    _check("param parameterTable 補定值項目", "定值項目" in [_cell_text(c) for c in mp["table_json"]["parameterTable"]["content"][0]["content"][0]["content"]])
    _pcell = mp["table_json"]["parameterTable"]["content"][0]["content"][1]["content"][2]
    _check("param 定值項目 資料格為 checkbox customTableCell", _pcell["type"] == "customTableCell" and _pcell["attrs"]["cellType"] == "checkbox" and _pcell["attrs"]["checked"] is False)
    _check("param table_text 補定值項目", mp["table_text"][0][2] == "定值項目")
    _check("param step5 program-code", mp["metadata"]["programs"][0]["programCode"] == "STYLE-RE999")
    _check("param content_* 清空", mp["content_json"] is None and mp["content_text"] is None)

    # migrate_legacy_blocks 端到端：step1 tier1 PMS（table content_json）+ 一般段落
    legacy_mgmt = [
        {"content_id": "m0", "step_type": 1, "tier_no": 1, "sub_no": 0, "content_type": 2,
         "content_json": {"type": "doc", "content": [{"type": "table", "content": [
             {"type": "tableRow", "content": [_hc("槽體"), _hc("管理項目"), _hc("設定值")]},
             {"type": "tableRow", "content": [_dc("生產資訊"), _dc("真空值"), _dc("3")]}]}]},
         "content_text": [["槽體", "管理項目", "設定值"], ["生產資訊", "真空值", "3"]], "metadata": {}},
        {"content_id": "m1", "step_type": 1, "tier_no": 2, "sub_no": 0, "content_type": 1,
         "content_json": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "一般文字"}]}]},
         "content_text": "一般文字", "metadata": {}},
    ]
    mm = migrate_legacy_blocks(legacy_mgmt)
    pms = next(r for r in mm if r["content_id"] == "m0")
    gen = next(r for r in mm if r["content_id"] == "m1")
    _check("PMS source=management", pms["metadata"].get("source") == "management")
    _pms_tbl = _find_table_node(pms["table_json"])
    _pms_hdr = [_cell_text(c) for c in _pms_tbl["content"][0]["content"]]
    _check("PMS 表格搬 table_json + 定值項目", isinstance(pms.get("table_json"), dict) and "定值項目" in _pms_hdr)
    _pms_fv = _pms_tbl["content"][1]["content"][_pms_hdr.index("定值項目")]
    _check("PMS 定值格為 tableCell checkbox（非 customTableCell）",
           _pms_fv["type"] == "tableCell" and _pms_fv["attrs"]["cellType"] == "checkbox"
           and _pms_fv["attrs"]["checked"] is False and "dropdownColor" not in _pms_fv["attrs"])
    _check("PMS content_type=2", pms["content_type"] == 2)
    _check("一般段落留 content_json + ct=1", gen["content_type"] == 1 and isinstance(gen.get("content_json"), dict) and gen.get("table_json") is None)
    _check("有PMS：不會多補節點", len(mm) == 2)

    # 缺 PMS：step1 只有 tier2/3（無 tier1）→ 補空 PMS 在 sort_order=1、一般區塊往後挪、不被誤判
    legacy_nopms = [
        {"content_id": "g2", "step_type": 1, "tier_no": 2, "sub_no": 0, "content_type": 1,
         "content_json": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "原3.2"}]}]},
         "content_text": "原3.2", "metadata": {}},
        {"content_id": "g3", "step_type": 1, "tier_no": 3, "sub_no": 0, "content_type": 1,
         "content_json": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "原3.3"}]}]},
         "content_text": "原3.3", "metadata": {}},
    ]
    nm = migrate_legacy_blocks(legacy_nopms, is_instruction=True)
    _check("缺PMS：節點數 +1（補空PMS）", len(nm) == 3)
    pms_s = next(r for r in nm if int(r["step_type"]) == 1 and r["sort_order"] == 1)
    _check("缺PMS：sort_order=1 為合成空PMS（source=management, ct=2, 非g2/g3）",
           pms_s["metadata"].get("source") == "management" and pms_s["content_type"] == CT_TABLE
           and pms_s["content_id"] not in ("g2", "g3"))
    _check("缺PMS：空PMS content_json 為空 paragraph、無 table",
           pms_s["content_json"] == {"type": "doc", "content": [{"type": "paragraph"}]} and pms_s["table_json"] is None)
    g2 = next(r for r in nm if r["content_id"] == "g2")
    _check("缺PMS：原 tier2 移到 sort_order=2、未被標 management、仍 ct=1",
           g2["sort_order"] == 2 and g2["metadata"].get("source") != "management" and g2["content_type"] == CT_TEXT)

    # 式樣書（step 4-7）無 step1 → 不無中生有
    legacy_spec = [{"content_id": "s0", "step_type": 5, "tier_no": 1, "sub_no": 0, "content_type": 2,
                    "content_json": {"type": "doc", "content": [{"type": "table", "content": [
                        {"type": "tableRow", "content": [_hc("槽體"), _hc("管理項目"), _hc("設定值")]},
                        {"type": "tableRow", "content": [_dc("生產資訊"), _dc("真空值"), _dc("3")]}]}]},
                    "content_text": [["槽體", "管理項目", "設定值"], ["生產資訊", "真空值", "3"]],
                    "metadata": {"programs": [{"programCode": "RE1"}]}}]
    sm = migrate_legacy_blocks(legacy_spec, item_type="X", is_instruction=False)
    _check("式樣書(is_instruction=False)無step1：不補PMS", len(sm) == 1 and all(int(r["step_type"]) != 1 for r in sm))

    # 指示書（is_instruction=True）即使完全沒有 step1 → 一律補空 PMS（沒有例外）
    legacy_instr_nostep1 = [
        {"content_id": "f0", "step_type": 0, "tier_no": 1, "sub_no": 0, "content_type": 1,
         "content_json": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "製造流程"}]}]},
         "content_text": "製造流程", "metadata": {}},
        {"content_id": "f3", "step_type": 3, "tier_no": 1, "sub_no": 0, "content_type": 1,
         "content_json": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "異常處理"}]}]},
         "content_text": "異常處理", "metadata": {}},
    ]
    im = migrate_legacy_blocks(legacy_instr_nostep1, is_instruction=True)
    _im_pms = [r for r in im if int(r["step_type"]) == 1]
    _check("指示書無step1：一律補一個空PMS（step1/sort1/management/ct2）",
           len(_im_pms) == 1 and _im_pms[0]["sort_order"] == 1 and _im_pms[0]["depth"] == 2
           and _im_pms[0]["metadata"].get("source") == "management" and _im_pms[0]["content_type"] == CT_TABLE)
    _check("指示書無step1：原 step0/3 區塊保留", len(im) == 3 and any(r["content_id"] == "f0" for r in im))

    # migrate_legacy_blocks 端到端：舊庫真實樣（content_json=NULL，param 只在 content_text 2D；step2 有 sub1 條件 2D）
    legacy_real = [
        {"content_id": "r0", "step_type": 2, "tier_no": 1, "sub_no": 0, "content_type": 2,
         "content_json": None,   # ← 舊庫多數 param 的 content_json 是 null
         "content_text": [["槽體", "管理項目", "規格下限(OOS-)", "設定值", "規格上限(OOS+)", "說明"],
                          ["生產資訊", "真空值", "1", "3", "5", ""]],
         "metadata": {"programs": [{"programCode": "RE555"}]}},
        {"content_id": "r1", "step_type": 2, "tier_no": 1, "sub_no": 1, "content_type": 2,
         "content_json": None,
         "content_text": [["條件名稱", "銅厚(oz)"], ["組合1", "1/2"]],
         "metadata": {}},
    ]
    # 純測 param 轉換：is_instruction=False（不觸發 PMS 合成）；用 content_id 取 param 節點
    _real_rows = migrate_legacy_blocks(legacy_real, item_type="STYLE", is_instruction=False)
    _check("is_instruction=False：不補 PMS", not any(int(r["step_type"]) == 1 for r in _real_rows))
    rp = next(r for r in _real_rows if r["content_id"] == "r0")
    _param = rp["table_json"]["parameterTable"]
    _cond = rp["table_json"]["conditionTable"]
    _check("反建 parameterTable 是 tiptap(table 在 content[0])", isinstance(_param, dict) and _param["content"][0]["type"] == "table")
    _check("反建 parameterTable 含定值項目", "定值項目" in [_cell_text(c) for c in _param["content"][0]["content"][0]["content"]])
    _check("反建 conditionTable(不含定值項目)", isinstance(_cond, dict) and _cond["content"][0]["type"] == "table" and "定值項目" not in [_cell_text(c) for c in _cond["content"][0]["content"][0]["content"]])
    _check("反建 table_text 補定值項目", rp["table_text"][0][2] == "定值項目")
    _check("反建路徑 _cond_2d 已清掉", "_cond_2d" not in rp)

    # 空參數表（舊資料：content_json 為「沒有 cell 的空 table」、content_text=[[]]）→ parameterTable=null（前端走 isParamNA）
    legacy_empty = [{
        "content_id": "e0", "step_type": 2, "tier_no": 1, "sub_no": 0, "content_type": 2,
        "content_json": {"type": "doc", "content": [{"type": "table", "content": [
            {"type": "tableRow", "attrs": {"class": None}}]}]},   # tableRow 無 content
        "content_text": [[]],
        "metadata": {"programs": [{"programCode": "RE000"}]},
    }]
    ep = next(r for r in migrate_legacy_blocks(legacy_empty, item_type="STYLE", is_instruction=False) if r["content_id"] == "e0")
    _check("空表 → parameterTable=None", ep["table_json"]["parameterTable"] is None)
    _check("空表 → table_text=None", ep["table_text"] is None)
    _check("空表仍 ct=4（保留 program 綁定）", ep["content_type"] == 4 and ep["metadata"]["programs"][0]["programCode"])
    # 前端 parseParamTable 相容性：content[0].content[0].content 必須是可 map 的陣列
    _check("parseParamTable 相容", isinstance(_param["content"][0]["content"][0]["content"], list) and len(_param["content"][0]["content"][0]["content"]) > 0)

    print("ALL PASS" if ok else "SOME FAILED")
