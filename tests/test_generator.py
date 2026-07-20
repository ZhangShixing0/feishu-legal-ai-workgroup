from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from legal_generator import (
    REFERENCE_PATH,
    REFERENCE_SHA256,
    generate_document,
    generate_preview_html,
    load_schema,
    package_fidelity,
    sha256,
    validate,
)


class GeneratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fields = dict(load_schema()["defaults"])

    def test_reference_hash_is_expected(self) -> None:
        self.assertEqual(sha256(REFERENCE_PATH), REFERENCE_SHA256)

    def test_generation_changes_only_document_xml(self) -> None:
        before = sha256(REFERENCE_PATH)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "generated.docx"
            result = generate_document(self.fields, output)
            self.assertEqual(result.errors, [])
            self.assertTrue(output.exists())
            fidelity = package_fidelity(REFERENCE_PATH, output)
            self.assertTrue(fidelity["same_part_set"])
            self.assertTrue(fidelity["preserve_only_ok"])
            self.assertEqual(fidelity["changed_parts"], ["word/document.xml"])
            with zipfile.ZipFile(output) as archive:
                xml = archive.read("word/document.xml").decode("utf-8")
            self.assertIn("测试乙", xml)
            self.assertIn("示例银行股份有限公司示例支行", xml)
            self.assertIn("截至2025年1月31日止", xml)
            self.assertIn("280,561.03", xml)
            self.assertIn(self.fields["defendant_id_number"], xml)
            self.assertIn("约定期限：2023年7月26日起至2024年7月26日止", xml)
            self.assertIn("利率☑3.6%/年", xml)
            self.assertIn("2023年7月26日，270,900.00元", xml)
            self.assertIn("到期一次性还本付息☑", xml)
            self.assertIn("基准日已逾期190天", xml)
            self.assertNotIn('w:highlight w:val="yellow"', xml)
            self.assertIn('w:highlight w:val="red"', xml)
        self.assertEqual(sha256(REFERENCE_PATH), before)

    def test_full_word_preview_uses_same_template_and_marks_inserted_values(self) -> None:
        preview = generate_preview_html(self.fields)
        self.assertIn('class="word-document-table"', preview)
        self.assertIn('data-word-row="35"', preview)
        self.assertIn(self.fields["defendant_id_number"], preview)
        self.assertNotIn("********", preview)
        self.assertGreaterEqual(preview.count('data-preview-fill="true"'), 20)

    def test_total_amount_warning(self) -> None:
        self.fields["total_amount"] = "1.00"
        result = validate(self.fields)
        self.assertEqual(result.errors, [])
        self.assertTrue(any("标的总额" in item for item in result.warnings))

    def test_role_warning(self) -> None:
        self.fields["loan_lender"] = "测试甲"
        self.fields["loan_borrower"] = "示例银行测试支行"
        result = validate(self.fields)
        self.assertTrue(any("可能填反" in item for item in result.warnings))


if __name__ == "__main__":
    unittest.main()
