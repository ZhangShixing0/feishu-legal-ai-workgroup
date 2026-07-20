from __future__ import annotations

import copy
import io
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

from case_extractor import HEADERS, NUMERIC_HEADERS, clean_value, column_letter


NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_XML = "http://www.w3.org/XML/1998/namespace"
ET.register_namespace("", NS_MAIN)
ET.register_namespace("r", NS_REL)


def qn(tag: str) -> str:
    return f"{{{NS_MAIN}}}{tag}"


def split_ref(reference: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Z]+)(\d+)", reference)
    if not match:
        raise ValueError(f"无效单元格坐标：{reference}")
    return match.group(1), int(match.group(2))


def column_number(letters: str) -> int:
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - 64
    return value


def cell_text(cell: ET.Element, shared_strings: list[str]) -> str:
    cell_type = cell.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(f".//{qn('t')}"))
    value = cell.find(qn("v"))
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        try:
            return shared_strings[int(value.text)]
        except (IndexError, ValueError):
            return ""
    return value.text


def shared_string_values(data: bytes | None) -> list[str]:
    if not data:
        return []
    root = ET.fromstring(data)
    return ["".join(node.text or "" for node in item.findall(f".//{qn('t')}")) for item in root.findall(qn("si"))]


def row_cells(row: ET.Element) -> dict[int, ET.Element]:
    cells: dict[int, ET.Element] = {}
    for cell in row.findall(qn("c")):
        reference = cell.get("r")
        if reference:
            letters, _ = split_ref(reference)
            cells[column_number(letters)] = cell
    return cells


def find_sheet_path(entries: dict[str, bytes]) -> tuple[str, str]:
    workbook = ET.fromstring(entries["xl/workbook.xml"])
    relationships = ET.fromstring(entries["xl/_rels/workbook.xml.rels"])
    relationship_targets = {
        item.get("Id"): item.get("Target")
        for item in relationships.findall(f"{{{NS_PKG_REL}}}Relationship")
    }
    sheets = workbook.find(qn("sheets"))
    if sheets is None or not list(sheets):
        raise ValueError("Excel 中没有工作表。")
    first = list(sheets)[0]
    relationship_id = first.get(f"{{{NS_REL}}}id")
    target = relationship_targets.get(relationship_id, "worksheets/sheet1.xml")
    target = target.lstrip("/")
    path = target if target.startswith("xl/") else "xl/" + target
    return first.get("name", "Sheet1"), path.replace("\\", "/")


def find_header_row(sheet_root: ET.Element, shared_strings: list[str]) -> tuple[ET.Element, list[str]]:
    sheet_data = sheet_root.find(qn("sheetData"))
    if sheet_data is None:
        raise ValueError("Excel 工作表没有可读取的数据。")
    best_row = None
    best_headers: list[str] = []
    best_score = -1
    expected = {clean_value(header) for header in HEADERS}
    for row in list(sheet_data)[:30]:
        cells = row_cells(row)
        if not cells:
            continue
        last = max(cells)
        headers = [clean_value(cell_text(cells.get(index, ET.Element("c")), shared_strings)) for index in range(1, last + 1)]
        score = sum(1 for header in headers if header in expected)
        if {clean_value(item) for item in ("序号", "贷款账号", "借款人姓名")}.issubset(set(headers)):
            score += 100
        if score > best_score:
            best_row, best_headers, best_score = row, headers, score
    if best_row is None or best_score < 8:
        raise ValueError("未在 Excel 前 30 行识别到案件信息表头。")
    return best_row, best_headers


def _coerce_existing_value(header: str, value: str):
    value = clean_value(value)
    if not value:
        return ""
    if header in {clean_value(item) for item in NUMERIC_HEADERS}:
        normalized = value.replace(",", "")
        if re.fullmatch(r"-?\d+(?:\.\d+)?", normalized):
            number = float(normalized)
            return int(number) if number.is_integer() else number
    return value


def inspect_workbook_context(input_path: Path, workbook_name: str = "") -> dict:
    """Read existing case rows without changing the workbook.

    The workbook name can identify the intended row (for example, 序号616.xlsx).
    Existing values are later used as transparent fallbacks, never as material OCR.
    """
    with zipfile.ZipFile(input_path) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    shared_strings = shared_string_values(entries.get("xl/sharedStrings.xml"))
    sheet_name, sheet_path = find_sheet_path(entries)
    sheet_root = ET.fromstring(entries[sheet_path])
    header_element, headers = find_header_row(sheet_root, shared_strings)
    header_row = int(header_element.get("r", "1"))
    sheet_data = sheet_root.find(qn("sheetData"))
    assert sheet_data is not None

    indexes = {header: headers.index(header) for header in ("序号", "贷款合同编号", "贷款账号", "借款人姓名", "借款人身份证号码") if header in headers}
    cases = []
    for row in sheet_data.findall(qn("row")):
        row_number = int(row.get("r", "0"))
        if row_number <= header_row:
            continue
        cells = row_cells(row)
        values = [
            _coerce_existing_value(header, cell_text(cells.get(index + 1, ET.Element("c")), shared_strings))
            for index, header in enumerate(headers)
        ]
        if not any(str(value).strip() for value in values):
            continue
        identifiers = {
            key: clean_value(str(values[index]))
            for key, index in indexes.items()
            if index < len(values) and clean_value(str(values[index]))
        }
        cases.append({
            "sheet_name": sheet_name,
            "row_number": row_number,
            "identifiers": identifiers,
            "fields": [
                {
                    "index": index,
                    "column": column_letter(index + 1),
                    "header": header,
                    "value": values[index],
                    "cell": f"{sheet_name}!{column_letter(index + 1)}{row_number}",
                }
                for index, header in enumerate(headers[:len(HEADERS)])
            ],
        })

    name_match = re.search(r"序号\s*[-_ ]*(\d{1,8})", Path(workbook_name).stem)
    if not name_match:
        name_match = re.match(r"^(\d{1,8})(?:[-_ ]|$)", Path(workbook_name).stem)
    target_sequence = name_match.group(1) if name_match else ""
    target_case = next(
        (case for case in cases if target_sequence and case["identifiers"].get("序号") == target_sequence),
        None,
    )
    if target_case is None and len(cases) == 1:
        target_case = cases[0]
    return {
        "sheet_name": sheet_name,
        "header_row": header_row,
        "cases": cases,
        "target_sequence": target_sequence,
        "target_case": target_case,
    }


def match_workbook_case(context: dict, selected_case: dict | None) -> dict | None:
    target = context.get("target_case")
    if target is not None:
        return target
    if not selected_case:
        return None
    selected_values = {clean_value(str(value)) for value in selected_case.values() if clean_value(str(value))}
    matches = []
    for case in context.get("cases", []):
        identifiers = {clean_value(str(value)) for value in case.get("identifiers", {}).values() if clean_value(str(value))}
        if selected_values.intersection(identifiers):
            matches.append(case)
    return matches[0] if len(matches) == 1 else None


def _same_value(left, right) -> bool:
    left_text = clean_value(str(left)).replace(",", "")
    right_text = clean_value(str(right)).replace(",", "")
    try:
        return abs(float(left_text) - float(right_text)) < 0.000001
    except ValueError:
        return left_text == right_text


def merge_existing_values(fields: list[dict], workbook_case: dict | None) -> tuple[list[dict], dict]:
    if not workbook_case:
        return fields, {"preserved": 0, "conflicts": 0, "row": None}
    existing_by_index = {item["index"]: item for item in workbook_case.get("fields", [])}
    merged = []
    preserved = 0
    conflicts = 0
    for field in fields:
        result = dict(field)
        existing = existing_by_index.get(field.get("index"))
        existing_value = existing.get("value") if existing else ""
        if existing is not None and clean_value(str(existing_value)):
            if field.get("missing"):
                result.update({
                    "value": existing_value,
                    "source": existing["cell"],
                    "confidence": 0.88,
                    "method": "Excel已有值保留（材料未验证）",
                    "missing": False,
                    "preserved": True,
                })
                preserved += 1
            elif not _same_value(field.get("value"), existing_value):
                extracted_value = field.get("value")
                extracted_source = field.get("source", "")
                result.update({
                    "value": existing_value,
                    "source": f"{existing['cell']}；材料值：{extracted_value}（{extracted_source}）",
                    "confidence": 0.72,
                    "method": "Excel值与材料值不一致，已保留Excel值",
                    "missing": False,
                    "preserved": True,
                    "conflict": True,
                })
                preserved += 1
                conflicts += 1
        merged.append(result)
    return merged, {
        "preserved": preserved,
        "conflicts": conflicts,
        "row": workbook_case.get("row_number"),
        "sheet": workbook_case.get("sheet_name"),
    }


def yellow_style(styles_root: ET.Element, base_style: int, cache: dict[int, int]) -> int:
    if base_style in cache:
        return cache[base_style]
    fills = styles_root.find(qn("fills"))
    cell_xfs = styles_root.find(qn("cellXfs"))
    if fills is None or cell_xfs is None:
        return base_style
    fill_items = list(fills)
    yellow_fill_id = None
    for index, fill in enumerate(fill_items):
        foreground = fill.find(f".//{qn('fgColor')}")
        rgb = (foreground.get("rgb", "") if foreground is not None else "").upper()
        indexed = foreground.get("indexed", "") if foreground is not None else ""
        if rgb in {"FFFF00", "FFFFFF00"} or indexed == "6":
            yellow_fill_id = index
            break
    if yellow_fill_id is None:
        fill = ET.Element(qn("fill"))
        pattern = ET.SubElement(fill, qn("patternFill"), {"patternType": "solid"})
        ET.SubElement(pattern, qn("fgColor"), {"rgb": "FFFFFF00"})
        ET.SubElement(pattern, qn("bgColor"), {"indexed": "64"})
        fills.append(fill)
        yellow_fill_id = len(fill_items)
        fills.set("count", str(yellow_fill_id + 1))
    xfs = list(cell_xfs)
    base = xfs[base_style] if 0 <= base_style < len(xfs) else (xfs[0] if xfs else ET.Element(qn("xf")))
    if int(base.get("fillId", "0")) == yellow_fill_id:
        cache[base_style] = base_style
        return base_style
    variant = copy.deepcopy(base)
    variant.set("fillId", str(yellow_fill_id))
    variant.set("applyFill", "1")
    cell_xfs.append(variant)
    style_id = len(xfs)
    cell_xfs.set("count", str(style_id + 1))
    cache[base_style] = style_id
    return style_id


def set_cell_value(cell: ET.Element, value, numeric: bool) -> None:
    for child in list(cell):
        cell.remove(child)
    if numeric and isinstance(value, (int, float)) and not isinstance(value, bool):
        cell.attrib.pop("t", None)
        number = ET.SubElement(cell, qn("v"))
        number.text = str(value)
    else:
        cell.set("t", "inlineStr")
        inline = ET.SubElement(cell, qn("is"))
        text = ET.SubElement(inline, qn("t"))
        text.set(f"{{{NS_XML}}}space", "preserve")
        text.text = str(value)


def update_table_ranges(entries: dict[str, bytes], header_row: int, target_row: int, last_col: int) -> None:
    last_letter = column_letter(last_col)
    for name, data in list(entries.items()):
        if not name.startswith("xl/tables/") or not name.endswith(".xml"):
            continue
        root = ET.fromstring(data)
        reference = root.get("ref", "")
        match = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", reference)
        if not match or int(match.group(2)) != header_row:
            continue
        table_last_col = column_number(match.group(3))
        if table_last_col < last_col:
            continue
        updated = f"{match.group(1)}{header_row}:{last_letter}{max(target_row, int(match.group(4)))}"
        root.set("ref", updated)
        auto_filter = root.find(qn("autoFilter"))
        if auto_filter is not None:
            auto_filter.set("ref", updated)
        entries[name] = ET.tostring(root, encoding="utf-8", xml_declaration=True)


@dataclass
class WriteResult:
    sheet_name: str
    header_row: int
    target_row: int
    matched_existing: bool
    missing_cells: list[str]


def fill_template(input_path: Path, output_path: Path, fields: list[dict]) -> WriteResult:
    with zipfile.ZipFile(input_path) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    shared_strings = shared_string_values(entries.get("xl/sharedStrings.xml"))
    sheet_name, sheet_path = find_sheet_path(entries)
    sheet_root = ET.fromstring(entries[sheet_path])
    styles_root = ET.fromstring(entries["xl/styles.xml"])
    header_element, headers = find_header_row(sheet_root, shared_strings)
    header_row = int(header_element.get("r", "1"))
    sheet_data = sheet_root.find(qn("sheetData"))
    assert sheet_data is not None

    fields_by_header: dict[str, list[dict]] = defaultdict(list)
    for item in fields:
        fields_by_header[clean_value(str(item.get("header", "")))].append(item)
    header_occurrences: dict[str, int] = defaultdict(int)
    field_by_column: dict[int, dict] = {}
    for index, header in enumerate(headers):
        occurrence = header_occurrences[header]
        candidates = fields_by_header.get(header, [])
        if candidates:
            field_by_column[index] = candidates[min(occurrence, len(candidates) - 1)]
        header_occurrences[header] += 1
    sequence_index = headers.index(clean_value("序号")) if clean_value("序号") in headers else None
    account_index = headers.index(clean_value("贷款账号")) if clean_value("贷款账号") in headers else None
    sequence_value = clean_value(str(field_by_column.get(sequence_index, {}).get("value", ""))) if sequence_index is not None else ""
    account_value = clean_value(str(field_by_column.get(account_index, {}).get("value", ""))) if account_index is not None else ""
    sequence_col = headers.index("序号") + 1 if "序号" in headers else None
    account_col = headers.index("贷款账号") + 1 if "贷款账号" in headers else None

    rows = list(sheet_data.findall(qn("row")))
    target = None
    for row in rows:
        row_number = int(row.get("r", "0"))
        if row_number <= header_row:
            continue
        cells = row_cells(row)
        row_sequence = clean_value(cell_text(cells.get(sequence_col, ET.Element("c")), shared_strings)) if sequence_col else ""
        row_account = clean_value(cell_text(cells.get(account_col, ET.Element("c")), shared_strings)) if account_col else ""
        if (account_value and row_account == account_value) or (sequence_value and row_sequence == sequence_value):
            target = row
            break
    matched_existing = target is not None
    if target is None:
        target_row_number = max([int(row.get("r", "0")) for row in rows] + [header_row]) + 1
        target = ET.Element(qn("row"), {"r": str(target_row_number), "spans": f"1:{len(headers)}"})
        sheet_data.append(target)
    else:
        target_row_number = int(target.get("r", "0"))

    existing_cells = row_cells(target)
    prior_rows = [row for row in rows if int(row.get("r", "0")) < target_row_number and int(row.get("r", "0")) > header_row]
    style_source = row_cells(prior_rows[-1]) if prior_rows else row_cells(header_element)
    yellow_cache: dict[int, int] = {}
    missing_cells: list[str] = []
    for index, header in enumerate(headers):
        column = index + 1
        reference = f"{column_letter(column)}{target_row_number}"
        cell = existing_cells.get(column)
        if cell is None:
            cell = ET.Element(qn("c"), {"r": reference})
            base_cell = style_source.get(column)
            if base_cell is not None and base_cell.get("s") is not None:
                cell.set("s", base_cell.get("s"))
            target.append(cell)
            existing_cells[column] = cell
        item = field_by_column.get(index)
        value = item.get("value", "无") if item else "无"
        missing = bool(item.get("missing", value == "无")) if item else True
        base_style = int(cell.get("s", "0"))
        if missing:
            cell.set("s", str(yellow_style(styles_root, base_style, yellow_cache)))
            missing_cells.append(reference)
        set_cell_value(cell, value, header in {clean_value(item) for item in NUMERIC_HEADERS} and value != "无")

    target[:] = sorted(list(target), key=lambda cell: column_number(split_ref(cell.get("r", "A1"))[0]))
    target.set("spans", f"1:{len(headers)}")

    dimension = sheet_root.find(qn("dimension"))
    if dimension is not None:
        dimension.set("ref", f"A1:{column_letter(len(headers))}{max(target_row_number, max(int(row.get('r', '0')) for row in sheet_data.findall(qn('row'))))}")
    update_table_ranges(entries, header_row, target_row_number, len(headers))
    entries[sheet_path] = ET.tostring(sheet_root, encoding="utf-8", xml_declaration=True)
    entries["xl/styles.xml"] = ET.tostring(styles_root, encoding="utf-8", xml_declaration=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for name, data in entries.items():
            output.writestr(name, data)
    return WriteResult(sheet_name, header_row, target_row_number, matched_existing, missing_cells)


def inspect_row(path: Path, row_number: int) -> list[str]:
    with zipfile.ZipFile(path) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    shared_strings = shared_string_values(entries.get("xl/sharedStrings.xml"))
    _, sheet_path = find_sheet_path(entries)
    root = ET.fromstring(entries[sheet_path])
    row = root.find(f".//{qn('row')}[@r='{row_number}']")
    if row is None:
        return []
    cells = row_cells(row)
    return [cell_text(cells.get(index, ET.Element("c")), shared_strings) for index in range(1, max(cells) + 1)]
