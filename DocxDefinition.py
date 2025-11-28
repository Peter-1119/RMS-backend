import json, re
from datetime import datetime
from enum import Enum

from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_UNDERLINE
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

COLOR_DICT = {
    "red": RGBColor(255, 0, 0),
    "blue": RGBColor(0, 0, 255),
    "#000": RGBColor(0, 0, 0),
    "#000000": RGBColor(0, 0, 0),
    "#ff0000": RGBColor(255, 0, 0),
    "#0000ff": RGBColor(0, 0, 255),
}

class DOCUMENT_TYPE(Enum):
    ManufacturingDocument = 0
    SpecificationDocument = 1

class DOCUMENT_STEP(Enum):
    ManufacturingDocument = [
        {"目的": {"parent": "attribute", "code": "documentPurpose"}},
        {"製造流程": {"parent": "content", "code": 0}},
        {"管理條件": {"parent": "content", "code": 1}},
        {"製造條件參數一覽表": {"parent": "content", "code": 2}},
        {"異常處置": {"parent": "content", "code": 3}},
        {"相關文件": {"parent": "reference", "code": 0}},
        {"使用表單": {"parent": "reference", "code": 1}}
    ]
    SpecificationDocument = [
        {"目的": {"parent": "attribute", "code": "documentPurpose"}},
        {"製作條件規範": {"parent": "content", "code": 4}},
        {"製造參數一覽表": {"parent": "content", "code": 5}},
        {"適用品質與規格內容": {"parent": "content", "code": 6}},
        {"使用表單": {"parent": "reference", "code": 1}},
        {"其他": {"parent": "content", "code": 7}},
    ]


SPEC_HDRS = ['規格下限(OOS-)','操作下限(OOC-)','設定值','操作上限(OOC+)','規格上限(OOS+)']
COLOR_RED = RGBColor(255, 0, 0)
COLOR_BLUE = RGBColor(0, 0, 255)

def clear_paragraph(p):
    """把一個 paragraph 內原本所有 run/文字清掉"""
    p.text = ""
    # 保險一點也可以把底層 element 都清掉
    for r in p.runs:
        r._element.getparent().remove(r._element)

def add_simple_field(paragraph, instr):
    """
    在 paragraph 中插入一個簡單欄位，例如:
      instr = "PAGE" 或 "NUMPAGES"
    實際出現會交給 Word 自己更新。
    """
    fld = OxmlElement('w:fldSimple')
    fld.set(qn('w:instr'), instr)  # e.g. "PAGE", "NUMPAGES"

    r = OxmlElement('w:r')
    t = OxmlElement('w:t')
    t.text = "1"   # 只是 placeholder，開啟文件時 Word 會更新
    r.append(t)
    fld.append(r)

    paragraph._p.append(fld)
    return paragraph

def set_section_start_page(section, start_num: int = 0):
    """
    設定這個 section 的起始頁碼。start_num = 0 表示第一頁顯示 0，
    但如果第一頁 header 不顯示頁碼（Different first page），
    從第二頁開始看到的是 1、2、3...
    """
    sectPr = section._sectPr
    pgNumType = sectPr.find(qn('w:pgNumType'))
    if pgNumType is None:
        pgNumType = OxmlElement('w:pgNumType')
        sectPr.append(pgNumType)
    pgNumType.set(qn('w:start'), str(start_num))

def setup_page_numbers(doc):
    """
    1. 啟用第一頁不同頁首。
    2. 第一頁 header 的 table (第 0 個) 第二列第五欄 -> NUMPAGES (總頁數)
    3. 其他頁 header 的 table 第二列第五欄 -> PAGE (目前頁碼)
       並把 section 起始頁碼設成 0，讓第 2 頁顯示 1，第三頁顯示 2...
    """
    section = doc.sections[1]

    # 1) 第一頁不同頁首
    # section.different_first_page_header_footer = True

    # 2) 設定 section 起始頁碼為 0（第一頁是 0，第二頁是 1...）
    # set_section_start_page(section, start_num=0)

    # 3) 第一頁 header：總頁數 NUMPAGES
    first_header = doc.sections[0].header  # 第一頁專用 header
    if first_header and first_header.tables:
        tbl_first = first_header.tables[0]
        # 第二列第五欄： rows[1].cells[4]
        cell_total = tbl_first.rows[1].cells[10]
        if cell_total.paragraphs:
            p_total = cell_total.paragraphs[0]
        else:
            p_total = cell_total.add_paragraph()

        clear_paragraph(p_total)
        # 這裡只塞總頁數，例如 "3"
        add_simple_field(p_total, "NUMPAGES")

    # 4) 其他頁 header：頁碼 PAGE（會是 1,2,3... 因為起始頁碼設為 0）
    normal_header = doc.sections[1].header
    if normal_header and normal_header.tables:
        tbl_other = normal_header.tables[0]
        cell_page = tbl_other.rows[1].cells[5]
        if cell_page.paragraphs:
            p_page = cell_page.paragraphs[0]
        else:
            p_page = cell_page.add_paragraph()

        clear_paragraph(p_page)
        add_simple_field(p_page, "PAGE")

# Extract plain text and "first seen" color from a cell JSON (your ProseMirror-ish structure).
def _extract_text_and_color_from_cell_json(cell_json, COLOR_DICT):
    txt_parts = []
    first_color = None
    for blk in cell_json.get("content", []):
        if blk.get("type") == "paragraph":
            for t in blk.get("content", []) or []:
                if t.get("type") == "text":
                    txt_parts.append(t.get("text", ""))
                    marks = t.get("marks")
                    if first_color is None and marks:
                        ckey = marks[0].get("attrs", {}).get("color")
                        if ckey in COLOR_DICT:
                            first_color = COLOR_DICT[ckey]
    return "".join(txt_parts).strip(), first_color

# Extract header texts for row0 (works if headers are in tableHeader/tableCell/customTableCell)
def _header_texts(row0_cells):
    headers = []
    for cell in row0_cells:
        if cell.get("type") not in ["customTableCell", "tableCell", "tableHeader"]:
            continue
        htxt = []
        for blk in cell.get("content", []):
            if blk.get("type") == "paragraph":
                for t in blk.get("content", []) or []:
                    if t.get("type") == "text":
                        htxt.append(t.get("text", ""))
        headers.append("".join(htxt).strip())
    return headers

# Find indices of the 5 special headers among ALL headers. Returns dict if all present, else None.
def _find_spec_header_indices(headers):
    pos = {}
    for name in SPEC_HDRS:
        try:
            pos[name] = headers.index(name)
        except ValueError:
            return None
    return pos

# Very tolerant float extractor (find first number in text like "12.3mm" or "±2.0")
_num_pat = re.compile(r"[-+]?\d+(?:\.\d+)?")
def _to_float_or_none(s):
    if s is None: return None
    m = _num_pat.search(str(s))
    return float(m.group(0)) if m else None

_calc_significant_digits = lambda s: 0 if "." not in s else len(s.split(".")[-1])
def _compose_value(set_txt, upper_txt, lower_txt):
    s = _to_float_or_none(set_txt)
    u = _to_float_or_none(upper_txt)
    l = _to_float_or_none(lower_txt)
    if s == None or u == None or l == None:
        return ""

    s_sd = _calc_significant_digits(set_txt)
    u_sd = _calc_significant_digits(upper_txt)
    l_sd = _calc_significant_digits(lower_txt)

    plus = round(u - s, max(s_sd, u_sd)) if s_sd + u_sd != 0 else int(u - s)
    minus = round(s - l, max(s_sd, l_sd)) if s_sd + l_sd != 0 else int(s - l)
    return f"+{plus}/-{minus}\n({lower_txt}~{upper_txt})"

def set_style_fonts(style, latin="Arial", east_asia="標楷體"):
    """
    Apply Latin (ascii/hAnsi/cs) and East Asian (eastAsia) fonts to a Word style.
    Works for paragraph and character styles.
    """
    # Set high-level python-docx font (affects ascii/hAnsi sometimes, but we set XML explicitly too)
    if hasattr(style, "font"):
        style.font.name = latin

    # Ensure <w:rPr> exists
    rPr = style._element.rPr
    if rPr is None:
        rPr = OxmlElement("w:rPr")
        style._element.append(rPr)

    # Ensure <w:rFonts> exists
    rFonts = rPr.rFonts
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.append(rFonts)

    # Set Latin/complex + East Asian
    rFonts.set(qn("w:ascii"), latin)          # Latin
    rFonts.set(qn("w:hAnsi"), east_asia)          # Latin (alternate)
    rFonts.set(qn("w:cs"), latin)             # Complex script (safe to mirror Latin)
    rFonts.set(qn("w:eastAsia"), east_asia)   # East Asian (Chinese)

def apply_default_fonts(doc, latin="Arial", east_asia="標楷體"):
    # Normal
    set_style_fonts(doc.styles["Normal"], latin, east_asia)

    # Headings 1..9 (apply if present)
    for i in range(1, 10):
        name = f"Heading {i}"
        if name in doc.styles:
            set_style_fonts(doc.styles[name], latin, east_asia)

    # Common extra styles (optional; add any you use)
    for s in ["Title", "Subtitle", "Intense Quote", "Quote", "Strong", "Emphasis"]:
        if s in doc.styles:
            set_style_fonts(doc.styles[s], latin, east_asia)

def update_p(p, mapping):
    if len(p.runs) == 0:
        return
    
    full_text = "".join([pr.text for pr in p.runs])

    if mapping.get(full_text) == None:
        return
    
    if ("POINT" in full_text) or ("REASON" in full_text) or ("DOC_NAME" in full_text) or ("PROJECT" in full_text) or ("ITEM_TYPE" in full_text) or ("STYLE_NO" in full_text):
        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        
    for r in p.runs:
        r.text = ""

    p.runs[0].text = str(mapping[full_text])

def replace_in_footer(footer, title_mapping):
    p = footer.paragraphs[0]
    full_text = p.runs[0].text
    if title_mapping.get(full_text) != None:
        p.runs[0].text = title_mapping[full_text]

def replace_in_header(header, title_mapping):
    # tables in header
    for row in header.tables[0].rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                update_p(p, title_mapping)

def set_repeat_table_header(row):
    """ set repeat table row on every new page"""
    tr = row._tr
    trPr = tr.get_or_add_trPr()
    tblHeader = OxmlElement('w:tblHeader')
    tblHeader.set(qn('w:val'), "true")
    trPr.append(tblHeader)
    return row

# --- New Content Generation Helper Functions ---
def set_table_run_text(p_node, text, color = COLOR_DICT['#000']):
    run = p_node.add_run(text)
    run.font.color.rgb = color
    if color == COLOR_DICT["#0000ff"]:
        run.underline = True

    return run

def set_docx_table_cell_text(cell_node, text, color = COLOR_DICT["#000"], vCenter = False, center = False):
    p = cell_node.paragraphs[0]
    set_table_run_text(p, text, color)

    if vCenter:
        cell_node.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    if center:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    return p

def create_docx_table(cell, json_table_data):
    rows_data = [row for row in json_table_data.get("content", []) if row.get("type") == "tableRow"]
    if not rows_data:
        return

    # Build list of header cells JSON (row 0)
    row0_cells_json = [c for c in rows_data[0].get("content", []) if c.get("type") in ["customTableCell", "tableCell", "tableHeader"]]
    if not row0_cells_json:
        return

    # Get header texts (full list, including extra headers like 項次/槽體/說明 etc.)
    headers = _header_texts(row0_cells_json)
    # Find positions of the 5 special headers; if not all present -> fall back to original rendering
    pos = _find_spec_header_indices(headers)

    # If NOT a spec table, render as-is (your original behavior)
    if pos is None:
        table = cell.add_table(rows=0, cols=len(row0_cells_json))
        table.width = cell.width.cm - Cm(1)
        table.style = 'Table Grid'
        # write all rows/cells like your previous logic
        for rowIndex, row_data in enumerate(rows_data):
            cells_data = [c for c in row_data.get("content", []) if c.get("type") in ["customTableCell", "tableCell", "tableHeader"]]
            row = table.add_row()
            row.allow_row_break_across_pages = False
            if rowIndex == 0:
                set_repeat_table_header(row)
            for i in range(len(cells_data)):
                cell_data = cells_data[i]  # get cell data

                docx_cell = row.cells[i]  # get docx table cell
                width = docx_cell.width.cm

                attr = cell_data.get("attrs", {}) or {}
                dropdownValue = attr.get("dropdownValue")
                if dropdownValue:
                    p = set_docx_table_cell_text(docx_cell, dropdownValue, color = COLOR_DICT.get(attr.get("dropdownColor")), vCenter = True, center = True)
                    p.paragraph_format.keep_together = True
                    continue

                # normal paragraphs/images
                items = cell_data.get("content", []) or []
                for idx, item in enumerate(items):
                    if item.get("type") == "paragraph":
                        p = docx_cell.paragraphs[0] if idx == 0 else docx_cell.add_paragraph()
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        for text_item in item.get("content", []) or []:
                            if text_item.get("type") == "text":
                                marks = text_item.get("marks")

                                run = p.add_run(text_item.get("text",""))
                                if marks != None:
                                    run.font.color.rgb = COLOR_DICT[marks[0]["attrs"]["color"]]
                                    run.underline = True

                            elif text_item.get("type") == "image":
                                src = text_item["attrs"]["src"].split("/", 3)[-1]
                                run = p.add_run()
                                run.add_picture(src, width=Cm(max(width - 0.5, 0.5)))
        return

    # ---------- SPEC TABLE TRANSFORMATION ----------
    # Determine insertion position = the smallest index among the five headers
    insert_at = min(pos.values())
    # The set of indices to remove (the five spec headers)
    spec_idx_set = set(pos[h] for h in SPEC_HDRS)

    # Build new header sequence: copy original headers left->right, but when we hit insert_at, insert ["規格值","操作值"] once and skip all five spec columns.
    new_header_titles = []
    col_map = []  # For non-spec columns: holds original index; for inserted pair: ('SPECPAIR', None)
    for index, header in enumerate(headers):
        # Replace SPEC column to calculated results
        if index == insert_at:
            col_map.append(("SPECPAIR", None, None))
            new_header_titles += ["設定值", "操作值", "規格值"]

        # Skip SPEC column expect first replaced one
        elif index in spec_idx_set:
            continue

        # Keep origin headers, if not SPEC column
        else:
            col_map.append(("COPY", index, len(new_header_titles)))
            new_header_titles.append(header)
    
    # If insert_at > len(headers) (theoretically impossible), guard
    if not new_header_titles:
        # fallback to original render if something odd happened
        return

    # Create the new docx table with computed column count
    table = cell.add_table(rows=0, cols=len(new_header_titles))
    table.width = cell.width.cm - Cm(1)
    table.style = 'Table Grid'

    # Write new header row
    hdr = table.add_row()
    set_repeat_table_header(hdr)
    for j, title in enumerate(new_header_titles):
        hdr.cells[j].text = title
        hdr.cells[j].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
        hdr.cells[j].vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    # Prepare a reverse map for quick lookup
    idx_reg_upper = pos["規格上限(OOS+)"]
    idx_reg_lower = pos["規格下限(OOS-)"]
    idx_opr_upper = pos["操作上限(OOC+)"]
    idx_opr_lower = pos["操作下限(OOC-)"]
    idx_set = pos["設定值"]

    NumColIndex = None if "項次" not in headers else headers.index('項次')
    previousNum = None if NumColIndex == None else 1

    SlotColIndex = None if "槽體" not in headers else headers.index('槽體')
    lastSlotName = None
    lastSlotRow = None if SlotColIndex == None else 1

    # Now write each data row (skip row0 because it was header)
    for rowIndex, row_data in enumerate(rows_data):
        if rowIndex == 0:
            continue

        row = table.add_row()
        row.allow_row_break_across_pages = False
        row_cells_json = [c for c in row_data.get("content", []) if c.get("type") in ["customTableCell", "tableCell", "tableHeader"]]

        # Pre-extract text & color for all original columns (so we can reference easily)
        texts, colors = [], []
        for orig_idx, cjson in enumerate(row_cells_json):
            t, col = _extract_text_and_color_from_cell_json(cjson, COLOR_DICT)
            texts.append(t)
            colors.append(col)

        SlotName = texts[SlotColIndex]

        # Compose spec/oper values + color flags
        spec_val = _compose_value(texts[idx_set], texts[idx_reg_upper], texts[idx_reg_lower])
        oper_val = _compose_value(texts[idx_set], texts[idx_opr_upper], texts[idx_opr_lower])
        spec_is_red = COLOR_DICT['blue'] if (colors[idx_reg_upper] == COLOR_BLUE) or (colors[idx_reg_lower] == COLOR_BLUE) else COLOR_DICT['#000']
        oper_is_red = COLOR_DICT['blue'] if (colors[idx_opr_upper] == COLOR_BLUE) or (colors[idx_opr_lower] == COLOR_BLUE) else COLOR_DICT['#000']

        # Fill the new row respecting col_map
        for colIndex, (tag, dataIndex, dstIndex) in enumerate(col_map):
            if tag == "SPECPAIR":
                p = set_docx_table_cell_text(row.cells[colIndex + 0], texts[idx_set], color = colors[idx_set], vCenter = True, center = True)
                p.paragraph_format.keep_together = True
                p = set_docx_table_cell_text(row.cells[colIndex + 1], oper_val, color = oper_is_red, vCenter = True, center = True)
                p.paragraph_format.keep_together = True
                p = set_docx_table_cell_text(row.cells[colIndex + 2], spec_val, color = spec_is_red, vCenter = True, center = True)
                p.paragraph_format.keep_together = True

            elif NumColIndex != None and colIndex == NumColIndex:
                if SlotName != lastSlotName:
                    p = set_docx_table_cell_text(row.cells[dstIndex], str(previousNum), color = colors[dataIndex], vCenter = True, center = True)
                    p.paragraph_format.keep_together = True
                    previousNum += 1

                    if lastSlotName != None and lastSlotRow != rowIndex - 1:
                        A = table.cell(lastSlotRow, dstIndex)
                        B = table.cell(rowIndex - 1, dstIndex)
                        A.merge(B)

            elif SlotColIndex != None and colIndex == SlotColIndex:
                if SlotName != lastSlotName:
                    p = set_docx_table_cell_text(row.cells[dstIndex], texts[dataIndex], color = colors[dataIndex], vCenter = True, center = True)
                    p.paragraph_format.keep_together = True

                    if lastSlotName != None and lastSlotRow != rowIndex - 1:
                        A = table.cell(lastSlotRow, dstIndex)
                        B = table.cell(rowIndex - 1, dstIndex)
                        A.merge(B)

                    lastSlotRow = rowIndex

            else:
                p = set_docx_table_cell_text(row.cells[dstIndex], texts[dataIndex], color = colors[dataIndex], vCenter = True, center = True)
                p.paragraph_format.keep_together = True

        lastSlotName = SlotName

    if lastSlotName != None and rowIndex != lastSlotRow:
        if NumColIndex != None:
            A = table.cell(lastSlotRow, NumColIndex)
            B = table.cell(rowIndex, NumColIndex)
            A.merge(B)
        
        A = table.cell(lastSlotRow, SlotColIndex)
        B = table.cell(rowIndex, SlotColIndex)
        A.merge(B)

    return

def parse_json_content(parent_object, json_data, indent = None, header = False, no = None, tier=1):
    """
    Recursively parses the JSON content (jsonHeader/jsonContent) and adds elements 
    (paragraphs, list items) to the parent object (docx.table._Cell).
    Applies tier-based indentation.
    """
    if json_data is None or json_data.get("content") is None:
        return

    base_indent = Cm(0.7) 

    for block in json_data["content"]:
        block_type = block.get("type")
        
        # Use the 'tier' from the content object, or the current recursion tier.
        content_tier = block.get("tier", tier) 

        if block_type == "paragraph" or block_type == "listItem":
            p = parent_object.add_paragraph()
            p.style = 'List Paragraph' if block_type == "listItem" else 'Normal'

            if header:
                set_table_run_text(p, f"{no} ")

            # Set text for title in content table
            for item in block.get("content", []):
                if item.get("type") == "text":
                    marks = item.get("marks", [])
                    color = COLOR_DICT[marks[0].get("attrs", {"color": "#000"}).get("color")] if len(marks) > 0 else COLOR_DICT["#000"]
                    run = set_table_run_text(p, item.get("text", ""), color)
                    run.font.size = Pt(12)


            p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            
            # Apply tier-based indent. tier=1 means 1 level of content under step title.
            # Indent factor is tier - 1. (Tier 1 gets indent factor 0, Tier 2 gets 1, etc.)
            indent_factor = max(0, content_tier - 1)
            p.paragraph_format.left_indent = base_indent * indent_factor
        
        elif block_type == "table":
            # For tables nested inside content structure (less common but handled)
            create_docx_table(parent_object, block)
            
        # Recursive call for nested content
        if 'content' in block and block.get("type") not in ["table", "tableRow", "tableCell", "tableHeader"]:
            # Pass the current tier + 1 for deeper nesting
            parse_json_content(parent_object, block, content_tier + 1)


def createHeader(cell, step_content_list):
    """Adds header content (jsonHeader) for all items in the list to the cell."""
    for content_obj in step_content_list:
        no = f"{content_obj['step']}.{content_obj['tier']}" if content_obj['sub_no'] == 0 else f"{content_obj['step']}.{content_obj['tier']}.{content_obj['sub_no']}"
        tier = 2 if content_obj.get("sub_no", 0) == 0 else 3
        for item in content_obj.get("data", []):
            if item.get("jsonHeader"):
                # Headers are main headings, usually tier 1 (no base indent)
                parse_json_content(cell, item["jsonHeader"], header = True, no = no, tier = tier) 

def createContent(cell, step_content_list):
    """Adds main content (jsonContent) for all items in the list to the cell, applying tier-based indent."""
    for content_obj in step_content_list:
        tier = 3 if content_obj.get("sub_no", 0) == 0 else 4
        for item in content_obj.get("data", []):
            if item.get("jsonContent"):
                # Pass the content's tier to enable indentation
                parse_json_content(cell, item["jsonContent"], tier=tier)

def createPictures(cell, step_content_list):
    """Adds picture placeholders/links (files) for all items in the list to the cell."""
    width = cell.width.cm
    base_indent = Cm(0.7)
    for content_obj in step_content_list:
        tier = content_obj.get("tier", 1)
        indent_factor = max(0, tier - 1)
        for item in content_obj.get("data", []):
            if item.get("files"):
                for file_info in item["files"]:
                    # Cannot embed image, so use a placeholder text/link
                    # p = cell.add_paragraph(f"圖片連結: {file_info.get('url', 'N/A')}")
                    # p.runs[0].font.size = Pt(10)
                    # p.paragraph_format.left_indent = base_indent * indent_factor
                    if file_info.get("url") != None:
                        p = cell.add_paragraph()
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        src = file_info["url"].split("/", 1)[-1]
                        run = p.add_run()
                        run.add_picture(src, width = Cm(width / 2 - 0.5))

def createTable(cell, step_content_list):
    """Adds tables (jsonContent containing a table) for all items in the list to the cell."""
    for content_obj in step_content_list:
        for item in content_obj.get("data", []):
            if item.get("jsonContent"):
                # The JSON for table content is expected to be a single 'table' block inside 'content'
                table_blocks = [b for b in item["jsonContent"].get("content", []) if b.get("type") == "table"]
                for table_block in table_blocks:
                    # Table is created directly in the cell, as requested, to avoid left indent.
                    create_docx_table(cell, table_block)

        if content_obj.get("jsonConditionContent"):
            table_blocks = [b for b in content_obj["jsonConditionContent"].get("content", []) if b.get("type") == "table"]
            for table_block in table_blocks:
                # Table is created directly in the cell, as requested, to avoid left indent.
                create_docx_table(cell, table_block)
        
        if content_obj.get("jsonParameterContent"):
            table_blocks = [b for b in content_obj["jsonParameterContent"].get("content", []) if b.get("type") == "table"]
            for table_block in table_blocks:
                # Table is created directly in the cell, as requested, to avoid left indent.
                create_docx_table(cell, table_block)

def draw_instruction_content(doc, data):
    attribute = data["attribute"][-1]
    contents = data["content"]
    references = data["reference"]

    documentType = attribute["documentType"]
    cell = doc.tables[1].rows[0].cells[0]
    
    # Ensure a paragraph exists to start or continue content in the cell
    if not cell.paragraphs or cell.paragraphs[0].text:
         cell.add_paragraph()
    cell.paragraphs[0].style = doc.styles["Normal"]

    for (stepIndex, itemInfo) in enumerate(DOCUMENT_STEP[DOCUMENT_TYPE(documentType).name].value):
        (step, stepInfo), = itemInfo.items()
        
        # 1. Set the step title
        p_title = cell.paragraphs[0] if stepIndex == 0 else cell.add_paragraph()
        p_title.text = f"{stepIndex + 1}.{step}"
        p_title.runs[0].font.size = Pt(12)
        p_title.alignment = WD_ALIGN_PARAGRAPH.LEFT

        # 2. Get current step content
        if stepInfo["parent"] == "attribute":
            stepContent = attribute.get(stepInfo["code"])
            if isinstance(stepContent, str):
                stepContent = "NA" if len(stepContent) == 0 else stepContent
                p_attr = cell.add_paragraph(stepContent)
                p_attr.alignment = WD_ALIGN_PARAGRAPH.LEFT
                p_attr.runs[0].font.size = Pt(12)
                p_attr.paragraph_format.left_indent = p_attr.runs[0].font.size * 2
        
        elif stepInfo["parent"] == "content":
            # Filter content items for the current step_type
            step_content_list = [content for content in contents if content["step_type"] == stepInfo["code"]]

            if not step_content_list:
                stepContent = "NA" if len(step_content_list) == 0 else stepContent
                p_attr = cell.add_paragraph(stepContent)
                p_attr.alignment = WD_ALIGN_PARAGRAPH.LEFT
                p_attr.runs[0].font.size = Pt(12)
                p_attr.paragraph_format.left_indent = p_attr.runs[0].font.size * 2
                continue

            # Process all data items across all matching step content objects
            for content_obj in step_content_list:
                if content_obj["step_type"] == 2 or content_obj["step_type"] == 5:
                    programCode = "NA"
                    if len(content_obj["metadata"]['programs']) > 0:
                        programCode = '、'.join([code["programCode"] for code in content_obj["metadata"]['programs']])
                    p = cell.add_paragraph()
                    p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                    p.text = f"程式代碼：{programCode}"
                    createTable(cell, [content_obj])

                for index, item_data in enumerate(content_obj.get("data", [])):
                    option = item_data.get("option")
                    
                    # Wrap the single data item for consistent access by helpers
                    item_for_helper = {"data": [item_data], "step": stepIndex + 1, "tier": content_obj.get("tier", 1), "sub_no": index}
                    
                    if option == 2: # Header + Table
                        createHeader(cell, [item_for_helper]) 
                        createTable(cell, [item_for_helper]) 
                    elif option == 1: # Header + Content + Pictures
                        createHeader(cell, [item_for_helper]) 
                        createContent(cell, [item_for_helper]) 
                        createPictures(cell, [item_for_helper]) 
                    elif option == 0: # Header only
                        createHeader(cell, [item_for_helper])
            
        elif stepInfo["parent"] == "reference":
            # Filter reference items for the current referenceType
            stepContent = [reference for reference in references if reference["referenceType"] == stepInfo["code"]]
            
            if stepContent:
                # Add a reference list with indentation
                for index, ref in enumerate(stepContent):
                    # For reference documents, use a simple numbered format
                    ref_p = cell.add_paragraph(f"({index + 1}) {ref.get('referenceDocumentID', '')} - {ref.get('referenceDocumentName', '')}")
                    ref_p.runs[0].font.size = Pt(12)
                    ref_p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                    ref_p.paragraph_format.left_indent = ref_p.runs[0].font.size * 2
                    ref_p.paragraph_format.space_after = Pt(3)

            else:
                stepContent = "NA" if len(stepContent) == 0 else stepContent
                p_attr = cell.add_paragraph(stepContent)
                p_attr.alignment = WD_ALIGN_PARAGRAPH.LEFT
                p_attr.runs[0].font.size = Pt(12)
                p_attr.paragraph_format.left_indent = p_attr.runs[0].font.size * 2

def create_word_password_hash(password):
    """
    生成 Word 用於 'Restrict Editing' 的 Legacy Hash。
    """
    if not password:
        return None
    
    password_hash = 0
    if len(password) > 15:
        password = password[:15]
    
    chars = [ord(c) for c in password]
    for char_code in chars:
        password_hash = ((password_hash >> 14) & 0x01) | ((password_hash << 1) & 0x7FFF)
        password_hash ^= char_code
        
    password_hash = ((password_hash >> 14) & 0x01) | ((password_hash << 1) & 0x7FFF)
    password_hash ^= len(password)
    password_hash ^= 0xCE4B
    
    return f'{password_hash:X}'

def enable_docx_protection(doc, password):
    """
    直接修改 python-docx 的 document 物件，注入保護設定。
    """
    # 1. 取得 settings 的根節點
    settings_element = doc.settings.element

    # 2. 產生雜湊密碼
    hash_value = create_word_password_hash(password)

    # 3. 建立 documentProtection 元素
    # XML 結構: <w:documentProtection w:edit="forms" w:enforcement="1" ... />
    protection = OxmlElement('w:documentProtection')
    
    # 設定屬性
    protection.set(qn('w:edit'), 'forms')  # forms = 僅填寫表單 (即唯讀但允許表單)
    protection.set(qn('w:enforcement'), '1')
    
    # 設定加密參數 (相容 Word 的標準參數)
    protection.set(qn('w:cryptProviderType'), 'rsaAES')
    protection.set(qn('w:cryptAlgorithmClass'), 'hash')
    protection.set(qn('w:cryptAlgorithmType'), 'typeAny')
    protection.set(qn('w:cryptAlgorithmSid'), '14')
    protection.set(qn('w:cryptSpinCount'), '100000')
    protection.set(qn('w:hash'), hash_value)

    # 4. 檢查是否已經有保護標籤，若有則先移除 (避免重複)
    existing = settings_element.find(qn('w:documentProtection'))
    if existing is not None:
        settings_element.remove(existing)

    # 5. 將新標籤加入 settings
    settings_element.append(protection)

def fill_from_template(template_path, out_path, data, title_mapping, info_mapping):
    doc = Document(template_path)
    apply_default_fonts(doc, latin="Arial", east_asia="標楷體")

    # 產生內容（影響頁數）
    draw_instruction_content(doc, data)

    # 先做原本的 header/footer 文字替換
    for index, section in enumerate(doc.sections):
        # 如果你的第一頁 header 也要做文字替換，可以另外處理：
        # replace_in_header(section.first_page_header, title_mapping)  # 視需求決定
        replace_in_header(section.header, title_mapping)
        replace_in_footer(section.footer, title_mapping)
        # print(f"index: {index}, section: {section.header}")

    for row in doc.tables[0].rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                update_p(p, info_mapping)

    # ★ 內容都產生完之後，再插入頁碼欄位
    setup_page_numbers(doc)

    enable_docx_protection(doc, "123456")

    doc.save(out_path)

_code_prefix_re = re.compile(r"^\s*\(([^)]+)\)\s*(.*)$")

def clean_process_name(raw: str) -> str:
    """
    PROCESS_NAME like "(L100-01)單面前處理" -> "單面前處理"
    If there is no "(...)" prefix, returns the original, trimmed.
    """
    if not raw:
        return ""
    m = _code_prefix_re.match(raw)
    return m.group(2).strip() if m else raw

def get_docx(outpath, data, template = "docx-template/example3.docx"):
    # Get document fundamental attribute
    attribute = data["attribute"][-1]
    Doc_id = attribute["documentID"]
    Date = datetime.now().strftime("%Y/%m/%d")
    Version = f"{int(attribute['documentVersion']):.1f}"
    Title = "製造條件指示書" if attribute["documentType"] == 0 else "製造式樣書"
    Doc_name = attribute["documentName"]

    itemType = attribute["attribute"].get("itemType")
    styleNo = attribute["attribute"].get("styleNo")

    # Put placeholders like [DOC_NO], [REV], [DATE], [TOTAL_PAGES] in the header cells of your template.
    title_mapping = { "DOC_NO": Doc_id, "DATE": Date, "REV": Version, "PAGE": "1", "TITLE": Title, "DOC_NAME": Doc_name, "DOC_CODE": "FM-R-MF-AZ-052 Rev7.0"}
    if itemType != None:
        specifications = [clean_process_name(s["name"]) for s in attribute["attribute"].get("specification")]
        title_mapping["PROJECT"] = "_".join(specifications)
        title_mapping["ITEM_TYPE"]=  itemType
        title_mapping["STYLE_NO"] = styleNo.split("-", 1)[-1]
    else:
        title_mapping["PROJECT"] = attribute["attribute"]["applyProject"]

    info_mapping = {
        "REV1": "", "DATE1": "", "REASON1": "", "POINT1": "", "DEPT1": "", "APPROVER1": "", "CONFIRMER1": "", "AUTHOR1": "",
        "REV2": "", "DATE2": "", "REASON2": "", "POINT2": "", "DEPT2": "", "APPROVER2": "", "CONFIRMER2": "", "AUTHOR2": "",
        "REV3": "", "DATE3": "", "REASON3": "", "POINT3": "", "DEPT3": "", "APPROVER3": "", "CONFIRMER3": "", "AUTHOR3": ""
    }

    # Place document attribute into Word
    for index, attribute in enumerate(data["attribute"]):
        info_mapping[f"REV{index + 1}"] = f'{attribute["documentVersion"]:.1f}'
        info_mapping[f"DATE{index + 1}"] = attribute.get("issueDate", datetime.now().strftime("%Y/%m/%d"))
        info_mapping[f"REASON{index + 1}"] = attribute["reviseReason"]
        info_mapping[f"POINT{index + 1}"] = attribute["revisePoint"]
        info_mapping[f"DEPT{index + 1}"] = attribute["department"]
        info_mapping[f"APPROVER{index + 1}"] = attribute["approver"]
        info_mapping[f"CONFIRMER{index + 1}"] = attribute["confirmer"]
        info_mapping[f"AUTHOR{index + 1}"] = attribute["author"]
        info_mapping[f"REASON{index + 1}"] = f'變更理由\n{attribute["reviseReason"]}'
        info_mapping[f"POINT{index + 1}"] = f'變更要點\n{attribute["revisePoint"]}'

    # Assuming 'example__.docx' exists in the execution environment
    fill_from_template(template, outpath, data, title_mapping, info_mapping) 

# ----------------- Example usage -----------------
if __name__ == "__main__":
    # The file path has been changed to match the payload.json fetched content
    # Note: Replace '_captures/docs/20251028-114010-2b096f8a/payload.json' 
    # with the actual file path if running locally. 
    # Since I have the content, I will use the dictionary directly for demonstration.
    # with open("_captures/docs/20251030-161207-516a0cfa/payload.json", 'r', encoding = "utf-8") as f:
    with open("_captures/docs/20251103-095207-27ee69e9/payload.json", 'r', encoding = "utf-8") as f:
        data = json.load(f)

    # template = r"./example__.docx"   # <- your example file (the “second” screenshot)
    # output   = r"./header_table_fix.docx"
    template = "docx-template/example3.docx"
    output = "docxTemp/temp.docx"

    attribute = data["attribute"][-1]
    Doc_id = attribute["documentID"]
    Date = datetime.now().strftime("%Y/%m/%d")
    Version = f"{int(attribute['documentVersion']):.1f}"
    Title = "製造條件指示書" if attribute["documentType"] == 0 else "製造式樣書"
    Doc_name = attribute["documentName"]

    # Put placeholders like [DOC_NO], [REV], [DATE], [TOTAL_PAGES] in the header cells of your template.
    # title_mapping = { "DOC_NO": "WMH250", "DATE": "2025/10/29", "REV": "1.0", "PAGE": "1", "TITLE": "製造條件指示書", "DOC_NAME": "", "PROJECT": "", "DOC_CODE": "FM-R-MF-AZ-052 Rev7.0"}
    title_mapping = { "DOC_NO": Doc_id, "DATE": Date, "REV": Version, "PAGE": "1", "TITLE": Title, "DOC_NAME": Doc_name, "PROJECT": "", "DOC_CODE": "FM-R-MF-AZ-052 Rev7.0"}
    info_mapping = {
        "REV1": "", "DATE1": "", "REASON1": "", "POINT1": "", "DEPT1": "", "APPROVER1": "", "CONFIRMER1": "", "AUTHOR1": "",
        "REV2": "", "DATE2": "", "REASON2": "", "POINT2": "", "DEPT2": "", "APPROVER2": "", "CONFIRMER2": "", "AUTHOR2": "",
        "REV3": "", "DATE3": "", "REASON3": "", "POINT3": "", "DEPT3": "", "APPROVER3": "", "CONFIRMER3": "", "AUTHOR3": ""
    }

    for index, attribute in enumerate(data["attribute"]):
        info_mapping[f"REV{index + 1}"] = f'{attribute["documentVersion"]:.1f}'
        info_mapping[f"DATE{index + 1}"] = attribute.get("issueDate", datetime.now().strftime("%Y/%m/%d"))
        info_mapping[f"REASON{index + 1}"] = attribute["reviseReason"]
        info_mapping[f"POINT{index + 1}"] = attribute["revisePoint"]
        info_mapping[f"DEPT{index + 1}"] = attribute["department"]
        info_mapping[f"APPROVER{index + 1}"] = attribute["approver"]
        info_mapping[f"CONFIRMER{index + 1}"] = attribute["confirmer"]
        info_mapping[f"AUTHOR{index + 1}"] = attribute["author"]
        info_mapping[f"REASON{index + 1}"] = f'變更理由\n{attribute["reviseReason"]}'
        info_mapping[f"POINT{index + 1}"] = f'變更要點\n{attribute["revisePoint"]}'

    # Assuming 'example__.docx' exists in the execution environment
    fill_from_template(template, output, data, title_mapping, info_mapping) 
    print("The script with the completed draw_instruction_content function is ready.")
    print("Please ensure your local environment has 'example__.docx' and python-docx installed to run the full script.")
    