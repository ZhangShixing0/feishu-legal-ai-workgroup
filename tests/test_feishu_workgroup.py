from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from feishu_workgroup.config import AppCredentials, Config
from feishu_workgroup.mapping import extraction_to_generator_fields, missing_required_fields
from feishu_workgroup.orchestrator import WorkgroupOrchestrator
from feishu_workgroup.store import CaseStore
from feishu_workgroup.web import render_form, render_success


def sample_extraction_result() -> dict:
    values = {
        "贷款合同编号": "HT-2026-001",
        "借款人姓名": "测试甲",
        "未偿本金余额": "100000.00",
        "未偿利息余额": "3600.00",
        "合计": "103600.00",
        "贷款金额": "100000.00",
        "贷款发放日": "2025-01-01",
        "贷款到期日": "2026-01-01",
        "截至基准日执行利率": "3.6",
        "贷款发放机构全称": "示例银行股份有限公司测试支行",
    }
    return {
        "fields": [
            {"header": header, "value": value, "missing": False, "confidence": 0.99}
            for header, value in values.items()
        ],
        "summary": {"found": len(values), "missing": 58, "review": 0, "conflicts": 0},
        "warnings": [],
    }


class InlineExecutor:
    def submit(self, function, *args, **kwargs):
        return function(*args, **kwargs)


class FakeBot:
    def __init__(self):
        self.texts: list[tuple[str, str]] = []
        self.cards: list[tuple[str, dict]] = []
        self.files: list[tuple[str, Path]] = []

    def send_text(self, chat_id: str, text: str) -> str:
        self.texts.append((chat_id, text))
        return "om_text"

    def send_card(self, chat_id: str, card: dict) -> str:
        self.cards.append((chat_id, card))
        return "om_card"

    def send_file(self, chat_id: str, path: Path) -> str:
        self.files.append((chat_id, path))
        return "om_file"

    def download_message_file(self, message_id: str, file_key: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"PK\x03\x04fixture")
        return destination


class FlakyCardBot(FakeBot):
    def __init__(self):
        super().__init__()
        self.fail_cards = True
        self.card_attempts = 0

    def send_card(self, chat_id: str, card: dict) -> str:
        self.card_attempts += 1
        if self.fail_cards:
            raise TimeoutError("temporary Feishu timeout")
        return super().send_card(chat_id, card)


class FakeExtractor:
    def __init__(self):
        self.result = sample_extraction_result()

    def start_analysis(self, archive: Path, workbook: Path) -> dict:
        self.archive = archive
        self.workbook = workbook
        return {"ok": True, "job_id": "job-1", "status": "queued"}

    def get_job(self, job_id: str) -> dict:
        return {
            "ok": True,
            "job_id": job_id,
            "status": "completed",
            "exported": True,
            "result": self.result,
        }


class FakeGenerator:
    def generate(self, fields: dict, destination: Path) -> dict:
        destination.write_bytes(b"PK\x03\x04docx")
        self.fields = dict(fields)
        return {"ok": True, "warnings": ["测试提示"]}


def message_payload(event_id: str, message_id: str, message_type: str, content: str) -> dict:
    return {
        "header": {"event_id": event_id},
        "event": {
            "sender": {
                "sender_type": "user",
                "sender_id": {"open_id": "ou_user"},
            },
            "message": {
                "message_id": message_id,
                "message_type": message_type,
                "chat_id": "oc_test",
                "content": content,
            },
        },
    }


class ConfigTests(unittest.TestCase):
    def test_local_mode_allows_loopback_form_url(self) -> None:
        config = Config(
            material_app=AppCredentials("material", "secret"),
            draft_app=AppCredentials("draft", "secret"),
            data_dir=Path("data"),
            host="127.0.0.1",
            port=8788,
            public_base_url="http://127.0.0.1:8788",
            extractor_api_url="http://127.0.0.1:8777",
            extractor_review_url="http://127.0.0.1:8777",
            generator_api_url="http://127.0.0.1:8765",
            allowed_chat_ids=frozenset(),
            poll_seconds=0,
            max_wait_seconds=10,
            local_mode=True,
        )
        self.assertEqual(config.missing_live_settings(), [])


class MappingTests(unittest.TestCase):
    def test_extraction_mapping_fills_equivalent_complaint_fields(self) -> None:
        fields = extraction_to_generator_fields(sample_extraction_result())
        self.assertEqual(fields["defendant_name"], "测试甲")
        self.assertEqual(fields["loan_borrower"], "测试甲")
        self.assertEqual(fields["contract_sign_date"], "2025-01-01")
        self.assertEqual(fields["loan_lender"], "示例银行股份有限公司测试支行")
        self.assertEqual(fields["original_creditor"], "示例银行股份有限公司测试支行")
        self.assertIn("plaintiff_name", missing_required_fields(fields))
        self.assertIn("balance_date", missing_required_fields(fields))


class StoreTests(unittest.TestCase):
    def test_store_persists_case_and_deduplicates_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CaseStore(Path(directory))
            case = store.create_case("oc_1", "ou_1")
            original_token = case.access_token
            store.update_case(case.id, archive_path="C:/case.zip")
            rotated = store.rotate_access_token(case.id)
            self.assertNotEqual(rotated.access_token, original_token)
            reopened = CaseStore(Path(directory)).get_case(case.id)
            self.assertEqual(reopened.archive_path, "C:/case.zip")
            self.assertTrue(store.mark_event("message:om_1"))
            self.assertFalse(store.mark_event("message:om_1"))


class WebFormTests(unittest.TestCase):
    def test_supplement_form_is_mobile_accessible_and_keeps_proxy_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CaseStore(Path(directory))
            case = store.create_case("oc_1", "ou_1")
            fields = extraction_to_generator_fields(sample_extraction_result())
            case = store.save_generator_fields(case.id, fields, "ou_1")
            page = render_form(case, case.access_token)
            self.assertIn('name="viewport"', page)
            self.assertIn('label for="plaintiff_name"', page)
            self.assertIn('name="loan_lender"', page)
            self.assertIn('min-height:48px', page)
            success = render_success(case, case.access_token)
            self.assertIn('href="document?token=', success)


class OrchestratorFlowTests(unittest.TestCase):
    def test_card_timeout_keeps_extracted_case_recoverable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                material_app=AppCredentials("material", "secret"),
                draft_app=AppCredentials("draft", "secret"),
                data_dir=Path(directory),
                host="127.0.0.1",
                port=8788,
                public_base_url="https://example.test/ai-workgroup",
                extractor_api_url="http://127.0.0.1:8777",
                extractor_review_url="https://example.test/extractor",
                generator_api_url="http://127.0.0.1:8765",
                allowed_chat_ids=frozenset({"oc_test"}),
                poll_seconds=0,
                max_wait_seconds=10,
            )
            store = CaseStore(Path(directory))
            material_bot = FlakyCardBot()
            case = store.create_case("oc_test", "ou_user")
            case = store.update_case(
                case.id,
                archive_path=str(Path(directory) / "materials.zip"),
                workbook_path=str(Path(directory) / "cases.xlsx"),
                status="extracting",
            )
            orchestrator = WorkgroupOrchestrator(
                config,
                store,
                material_bot,
                FakeBot(),
                FakeExtractor(),
                FakeGenerator(),
                executor=InlineExecutor(),
            )

            orchestrator._run_extraction_safely(case.id)
            saved = store.get_case(case.id)
            self.assertEqual(saved.status, "review_step1")
            self.assertTrue(saved.extractor_result)
            self.assertIn("审核卡片发送失败", saved.error)

            material_bot.fail_cards = False
            orchestrator.submit_message(
                message_payload(
                    "evt-resend",
                    "om-resend",
                    "text",
                    '{"text":"重发审核卡片"}',
                )
            )
            recovered = store.get_case(case.id)
            self.assertEqual(recovered.status, "review_step1")
            self.assertEqual(recovered.error, "")
            self.assertEqual(len(material_bot.cards), 1)

    def test_group_flow_hands_off_and_generates_word(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = Config(
                material_app=AppCredentials("material", "secret"),
                draft_app=AppCredentials("draft", "secret"),
                data_dir=Path(directory),
                host="127.0.0.1",
                port=8788,
                public_base_url="https://example.test/ai-workgroup",
                extractor_api_url="http://127.0.0.1:8777",
                extractor_review_url="https://example.test/extractor",
                generator_api_url="http://127.0.0.1:8765",
                allowed_chat_ids=frozenset({"oc_test"}),
                poll_seconds=0,
                max_wait_seconds=10,
            )
            store = CaseStore(Path(directory))
            material_bot = FakeBot()
            draft_bot = FakeBot()
            generator = FakeGenerator()
            orchestrator = WorkgroupOrchestrator(
                config,
                store,
                material_bot,
                draft_bot,
                FakeExtractor(),
                generator,
                executor=InlineExecutor(),
            )

            orchestrator.submit_message(
                message_payload("evt-1", "om-1", "text", '{"text":"新建案件"}')
            )
            orchestrator.submit_message(
                message_payload(
                    "evt-2",
                    "om-2",
                    "file",
                    '{"file_key":"file-zip","file_name":"材料.zip"}',
                )
            )
            orchestrator.submit_message(
                message_payload(
                    "evt-3",
                    "om-3",
                    "file",
                    '{"file_key":"file-xlsx","file_name":"案件.xlsx"}',
                )
            )
            case = store.get_active_case("oc_test")
            self.assertIsNotNone(case)
            self.assertEqual(case.status, "review_step1")
            self.assertEqual(len(material_bot.cards), 1)

            orchestrator.submit_card_action(
                {
                    "header": {"event_id": "card-1"},
                    "event": {
                        "operator": {"open_id": "ou_lawyer"},
                        "action": {"value": {"action": "approve_step1", "case_id": case.id}},
                    },
                }
            )
            case = store.get_active_case("oc_test")
            self.assertEqual(case.status, "supplementing")
            self.assertEqual(len(draft_bot.cards), 1)

            generated = orchestrator.generate_from_form(
                case.id,
                case.access_token,
                {
                    "plaintiff_name": "示例资产管理（测试）有限公司",
                    "balance_date": "2026-07-16",
                    "contract_name": "个人借款合同",
                },
            )
            self.assertEqual(generated.status, "done")
            self.assertTrue(Path(generated.docx_path).is_file())
            self.assertEqual(len(draft_bot.files), 1)
            self.assertEqual(generator.fields["plaintiff_name"], "示例资产管理（测试）有限公司")


if __name__ == "__main__":
    unittest.main()
