from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from unittest import mock
from pathlib import Path
from xml.etree import ElementTree as ET

TOOL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOL_DIR))

from case_extractor import (
    HEADERS,
    SourceDocument,
    analyze_fields,
    offline_ocr,
    safe_extract_zip,
    select_case_documents,
)
from xlsx_template import (
    NS_MAIN,
    fill_template,
    inspect_row,
    inspect_workbook_context,
    merge_existing_values,
)
import server


def inline_cell(reference: str, value: str, style: int = 0) -> str:
    return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{value}</t></is></c>'


def make_template(path: Path) -> None:
    last_col = "BP"
    header_cells = "".join(inline_cell(f"{column}{1}", header, 0) for column, header in zip(column_letters(len(HEADERS)), HEADERS))
    row_cells = [inline_cell("A2", "1", 0), inline_cell("D2", "old-account", 0)]
    sheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="{NS_MAIN}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <dimension ref="A1:{last_col}2"/><sheetData>
    <row r="1" spans="1:{len(HEADERS)}">{header_cells}</row>
    <row r="2" spans="1:{len(HEADERS)}">{"".join(row_cells)}</row>
  </sheetData>
</worksheet>'''
    styles = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{NS_MAIN}">
  <fonts count="1"><font><sz val="11"/><name val="宋体"/></font></fonts>
  <fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>
  <borders count="1"><border/></borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs>
</styleSheet>'''
    workbook = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{NS_MAIN}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="案件表" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'''
    table = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<table xmlns="{NS_MAIN}" id="1" name="CaseTable" displayName="CaseTable" ref="A1:{last_col}2" totalsRowShown="0"><autoFilter ref="A1:{last_col}2"/></table>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/></Types>'''
    package_rels = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", package_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr("xl/styles.xml", styles)
        archive.writestr("xl/tables/table1.xml", table)


def column_letters(count: int) -> list[str]:
    result = []
    for number in range(1, count + 1):
        value = ""
        current = number
        while current:
            current, remainder = divmod(current - 1, 26)
            value = chr(65 + remainder) + value
        result.append(value)
    return result


class ExtractorTests(unittest.TestCase):
    @mock.patch("case_extractor.tesseract_ocr", return_value="识别成功")
    @mock.patch.dict("os.environ", {"CASE_EXTRACTOR_OCR_BACKEND": "tesseract"})
    def test_server_ocr_uses_tesseract_backend(self, mocked_ocr) -> None:
        progress = mock.Mock()
        text, method = offline_ocr(
            Path("sample.png"), Path("windows_ocr.ps1"), thorough=True, progress=progress
        )
        self.assertEqual(text, "识别成功")
        self.assertEqual(method, "Tesseract离线OCR")
        mocked_ocr.assert_called_once_with(Path("sample.png"), True, progress)

    def test_safe_extract_rejects_parent_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "bad.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("../escape.txt", "bad")
            with self.assertRaises(ValueError):
                safe_extract_zip(source, root / "out")

    def test_field_rules_cover_contract_and_no_material_paths(self) -> None:
        text = """
        中国建设银行个人消费借款合同 合同编号：00000000000000000003
        借款人：测试甲 身份证件名称及号码：居民身份证/000000199004260012
        住所：湖北省武汉市示例区测试路2号 通讯地址：湖北省武汉市示例区测试路2号
        联系电话及传真：19900000003 贷款人：示例银行示例支行
        借款额度为人民币（大写）贰拾肆万元整元。有效期间自2023年08月08日至2024年08月08日。
        采用固定利率方式，利率按照3.6%。本合同项下借款用于综合消费。一次性还本付息。
        """
        documents = [
            SourceDocument("借款合同.pdf", "pdf", text, "PDF文本层"),
            SourceDocument("4、催收情况/12-委外催收协议（如有）/委外催收协议-无.txt", "path", "", "材料路径"),
            SourceDocument("5、诉讼情况/13-诉讼、仲裁、执行文件（如有）/诉讼、仲裁、执行文件（无）.txt", "path", "", "材料路径"),
            SourceDocument("2、贷款合同信息/4-担保材料—无/担保材料-无.txt", "path", "", "材料路径"),
        ]
        fields = analyze_fields(documents, "616-测试甲-00000000000000000000000001.zip")
        values = {item["header"]: item["value"] for item in fields}
        self.assertEqual(values["序号"], 616)
        self.assertEqual(values["贷款账号"], "00000000000000000000000001")
        self.assertEqual(values["借款人身份证号码"], "000000199004260012")
        self.assertEqual(values["贷款金额"], 240000)
        self.assertEqual(values["是否委外催收"], "否")
        self.assertEqual(values["是否起诉"], "否")
        self.assertEqual(values["担保方式"], "信用")

    def test_multi_case_package_selects_excel_target_only(self) -> None:
        documents = [
            SourceDocument("Word文书/328-测试乙-DEMO00000000000001-民事起诉状.docx", "docx", "测试乙", "Word文本层"),
            SourceDocument("Word文书/616-测试甲-000000199004260012-民事起诉状.docx", "docx", "测试甲", "Word文本层"),
        ]
        selected, case, warnings = select_case_documents(
            documents,
            "批量起诉状.zip",
            {
                "序号": "616",
                "借款人姓名": "测试甲",
                "贷款账号": "00000000000000000000000001",
                "贷款合同编号": "000000199004260012",
            },
        )
        self.assertEqual(case["sequence"], "616")
        self.assertEqual(len(selected), 1)
        self.assertIn("测试甲", selected[0].path)
        self.assertTrue(warnings)
        with self.assertRaises(ValueError):
            select_case_documents(documents, "批量起诉状.zip")

    def test_complaint_rules_and_reference_year_age(self) -> None:
        text = """
        被告（自然人）姓名：测试甲 性别：男 出生日期：1990-04-26 职务：记者
        联系电话：19900000003 住所地（户籍所在地）：湖北省武汉市示例区测试路2号
        证件类型：身份证 证件号码：000000199004260012
        贷款人：示例银行股份有限公司示例支行 借款人：测试甲
        借款金额约定：240,000.00元 实际发放：240,000.00元
        约定期限：2023年8月8日起至2024年8月8日止 利率☑3.6%/年
        截至2025年2月1日止，尚欠本金240,000.00元，欠利息15,312.37元、复利0.00元、罚息0.00元。
        最后一次还款：0.00元，时间：未提供。基准日已逾期177天。
        进入不良至基准日累计已还本金：0.00元。担保方式：信用。
        """
        documents = [SourceDocument("616-测试甲-000000199004260012-民事起诉状.docx", "docx", text, "Word文本层")]
        fields = analyze_fields(
            documents,
            "批量起诉状.zip",
            {"sequence": "616", "name": "测试甲", "contract": "000000199004260012"},
        )
        values = {item["header"]: item["value"] for item in fields}
        self.assertEqual(values["借款人姓名"], "测试甲")
        self.assertEqual(values["借款人年龄"], 35)
        self.assertEqual(values["借款合同约定送达地址"], "湖北省武汉市示例区测试路2号")
        self.assertEqual(values["未偿本金余额"], 240000)
        self.assertEqual(values["未偿利息余额"], 15312.37)
        self.assertEqual(values["合计"], 255312.37)
        self.assertEqual(values["基准日逾期天数"], 177)
        self.assertEqual(values["最后一次还款金额"], 0)
        self.assertEqual(values["担保方式"], "信用")


class WorkbookTests(unittest.TestCase):
    def test_append_then_match_and_mark_missing_yellow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "template.xlsx"
            first = root / "first.xlsx"
            second = root / "second.xlsx"
            make_template(template)
            fields = []
            for index, header in enumerate(HEADERS):
                value = "无"
                missing = True
                if header == "序号":
                    value, missing = 616, False
                elif header == "贷款账号":
                    value, missing = "00000000000000000000000001", False
                elif header == "借款人姓名":
                    value, missing = "测试甲", False
                fields.append({"index": index, "header": header, "value": value, "missing": missing})
            result = fill_template(template, first, fields)
            self.assertEqual(result.target_row, 3)
            self.assertFalse(result.matched_existing)
            row = inspect_row(first, 3)
            self.assertEqual(row[0], "616")
            self.assertEqual(row[1], "无")
            self.assertEqual(row[3], "00000000000000000000000001")
            with zipfile.ZipFile(first) as archive:
                table = ET.fromstring(archive.read("xl/tables/table1.xml"))
                self.assertEqual(table.get("ref"), "A1:BP3")
                sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
                b3 = sheet.find(f".//{{{NS_MAIN}}}c[@r='B3']")
                self.assertIsNotNone(b3)
                self.assertNotEqual(b3.get("s", "0"), "0")
            second_result = fill_template(first, second, fields)
            self.assertTrue(second_result.matched_existing)
            self.assertEqual(second_result.target_row, 3)

    def test_existing_excel_values_are_preserved_and_conflicts_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            template = Path(directory) / "序号1.xlsx"
            make_template(template)
            context = inspect_workbook_context(template, template.name)
            self.assertEqual(context["target_case"]["row_number"], 2)
            fields = [
                {
                    "index": index,
                    "column": column_letters(len(HEADERS))[index],
                    "header": header,
                    "value": "无",
                    "source": "压缩包中未发现可靠信息",
                    "confidence": 0.0,
                    "method": "缺失项自动填充并标黄",
                    "missing": True,
                }
                for index, header in enumerate(HEADERS)
            ]
            fields[0].update({"value": 1, "missing": False, "confidence": 1.0})
            fields[3].update({"value": "new-account", "missing": False, "confidence": 0.95, "source": "材料"})
            merged, summary = merge_existing_values(fields, context["target_case"])
            self.assertEqual(merged[3]["value"], "old-account")
            self.assertTrue(merged[3]["conflict"])
            self.assertEqual(summary["conflicts"], 1)


class ServerJobTests(unittest.TestCase):
    def setUp(self) -> None:
        with server.JOBS_LOCK:
            server.JOBS.clear()
            server.JOB_FINGERPRINTS.clear()
            server.ACTIVE_JOB_ID = None

    def tearDown(self) -> None:
        with server.JOBS_LOCK:
            server.JOBS.clear()
            server.JOB_FINGERPRINTS.clear()
            server.ACTIVE_JOB_ID = None

    def test_ocr_page_message_updates_real_progress(self) -> None:
        with server.JOBS_LOCK:
            server.JOBS["job-1"] = {"status": "running", "progress": {}}
        server.report_analysis_progress(
            "job-1", "正在读取 2/10：扫描合同.pdf；OCR页 5/10"
        )
        with server.JOBS_LOCK:
            job = dict(server.JOBS["job-1"])
        self.assertEqual(job["status"], "running")
        self.assertEqual(job["progress"]["phase"], "ocr")
        self.assertEqual(job["progress"]["percent"], 25)
        self.assertIn("第 2/10 个文件", job["progress"]["detail"])
        self.assertIn("OCR页 5/10", job["progress"]["detail"])

    def test_public_analysis_excludes_internal_sources(self) -> None:
        payload = server.public_analysis({"job_id": "job-2", "fields": [], "sources": ["secret"]})
        self.assertTrue(payload["ok"])
        self.assertNotIn("sources", payload)


if __name__ == "__main__":
    unittest.main()
