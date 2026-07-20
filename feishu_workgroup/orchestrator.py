from __future__ import annotations

import hmac
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from .cards import generated_card, material_review_card, supplement_card
from .clients import ExtractorClient, FeishuRestClient, GeneratorClient
from .config import Config
from .mapping import (
    REQUIRED_FIELD_IDS,
    SUPPLEMENT_FIELD_IDS,
    extraction_to_generator_fields,
    label_for,
    missing_required_fields,
)
from .store import CaseRecord, CaseStore


class ValidationProblem(ValueError):
    def __init__(self, message: str, fields: list[str] | None = None):
        super().__init__(message)
        self.fields = fields or []


class WorkgroupOrchestrator:
    def __init__(
        self,
        config: Config,
        store: CaseStore,
        material_bot: FeishuRestClient,
        draft_bot: FeishuRestClient,
        extractor: ExtractorClient,
        generator: GeneratorClient,
        executor: ThreadPoolExecutor | None = None,
    ):
        self.config = config
        self.store = store
        self.material_bot = material_bot
        self.draft_bot = draft_bot
        self.extractor = extractor
        self.generator = generator
        self.executor = executor or ThreadPoolExecutor(max_workers=4, thread_name_prefix="feishu-v1")
        self._case_lock = threading.RLock()

    def submit_message(self, payload: dict[str, Any]) -> None:
        event_id = str((payload.get("header") or {}).get("event_id", ""))
        message_id = str((((payload.get("event") or {}).get("message") or {}).get("message_id", "")))
        dedupe_id = f"message:{message_id}" if message_id else (f"event:{event_id}" if event_id else "")
        if not self.store.mark_event(dedupe_id):
            return
        self.executor.submit(self._process_message_safely, payload)

    def submit_card_action(self, payload: dict[str, Any]) -> dict[str, Any]:
        event_id = str((payload.get("header") or {}).get("event_id", ""))
        if event_id and not self.store.mark_event(f"card:{event_id}"):
            return {"toast": {"type": "info", "content": "该操作已经受理。"}}
        event = payload.get("event") or {}
        action = event.get("action") or {}
        value = action.get("value") or {}
        if value.get("action") != "approve_step1" or not value.get("case_id"):
            return {"toast": {"type": "warning", "content": "无法识别本次操作。"}}
        actor = str(
            (event.get("operator") or {}).get("open_id")
            or (event.get("operator") or {}).get("user_id")
            or "unknown"
        )
        self.executor.submit(self._approve_step1_safely, str(value["case_id"]), actor)
        return {
            "toast": {
                "type": "info",
                "content": "已收到确认，正在检查核对结果并交接。",
            }
        }

    def _process_message_safely(self, payload: dict[str, Any]) -> None:
        try:
            self._process_message(payload)
        except Exception as exc:
            event = payload.get("event") or {}
            chat_id = str((event.get("message") or {}).get("chat_id", ""))
            if chat_id:
                self.material_bot.send_text(chat_id, f"AI材料专员处理消息失败：{exc}")
            print(f"[feishu-workgroup] message error: {exc}")

    def _process_message(self, payload: dict[str, Any]) -> None:
        event = payload.get("event") or {}
        message = event.get("message") or {}
        sender = event.get("sender") or {}
        if sender.get("sender_type") == "bot":
            return
        chat_id = str(message.get("chat_id", ""))
        if not chat_id or not self._chat_allowed(chat_id):
            return
        sender_id = str(
            (sender.get("sender_id") or {}).get("open_id")
            or (sender.get("sender_id") or {}).get("user_id")
            or "unknown"
        )
        message_type = str(message.get("message_type", ""))
        content = _json_content(message.get("content"))
        if message_type == "text":
            text = str(content.get("text", "")).strip()
            if "新建案件" in text:
                self._new_case(chat_id, sender_id)
            elif "重发审核卡片" in text:
                self.executor.submit(self._resend_review_card_safely, chat_id)
            elif "状态" in text:
                self._send_status(chat_id)
            elif "帮助" in text:
                self.material_bot.send_text(
                    chat_id,
                    "第一版用法：发送“新建案件”，然后把一个ZIP材料包和一个XLSX案件表发到本群。",
                )
            return
        if message_type == "file":
            self._accept_file(
                chat_id=chat_id,
                sender_id=sender_id,
                message_id=str(message.get("message_id", "")),
                file_key=str(content.get("file_key", "")),
                file_name=str(content.get("file_name", "")),
            )

    def _chat_allowed(self, chat_id: str) -> bool:
        return not self.config.allowed_chat_ids or chat_id in self.config.allowed_chat_ids

    def _new_case(self, chat_id: str, sender_id: str) -> CaseRecord:
        with self._case_lock:
            active = self.store.get_active_case(chat_id)
            if active:
                self.material_bot.send_text(
                    chat_id,
                    f"当前群已有活动案件 {active.id}（{status_label(active.status)}）。第一版请先完成该案件。",
                )
                return active
            case = self.store.create_case(chat_id, sender_id)
        self.material_bot.send_text(
            chat_id,
            f"已新建案件 {case.id}。请依次发送一个 .zip 案件材料包和一个 .xlsx 案件信息表。",
        )
        return case

    def _send_status(self, chat_id: str) -> None:
        case = self.store.get_active_case(chat_id)
        if not case:
            self.material_bot.send_text(chat_id, "当前群没有活动案件。发送“新建案件”即可开始。")
            return
        files = []
        if case.archive_path:
            files.append("ZIP已收到")
        if case.workbook_path:
            files.append("XLSX已收到")
        file_text = "、".join(files) or "尚未收到文件"
        self.material_bot.send_text(
            chat_id,
            f"案件 {case.id}：{status_label(case.status)}；{file_text}。",
        )

    def _send_review_card(self, case: CaseRecord) -> None:
        if not case.extractor_job_id or not case.extractor_result:
            raise RuntimeError("案件尚无可发送的材料提取结果。")
        review_url = (
            f"{self.config.extractor_review_url}/?"
            f"{urlencode({'job_id': case.extractor_job_id})}"
        )
        self.material_bot.send_card(
            case.chat_id,
            material_review_card(case.id, case.extractor_result, review_url),
        )
        self.store.update_case(
            case.id,
            action="review_card_sent",
            status="review_step1",
            error="",
        )

    def _resend_review_card_safely(self, chat_id: str) -> None:
        case = self.store.get_active_case(chat_id)
        if not case or case.status != "review_step1":
            self.material_bot.send_text(chat_id, "当前没有等待材料审核的案件。")
            return
        try:
            self._send_review_card(case)
        except Exception as exc:
            self.store.update_case(
                case.id,
                action="review_card_delivery_failed",
                status="review_step1",
                error=f"材料已提取，但审核卡片发送失败：{exc}",
            )
            self.material_bot.send_text(
                chat_id,
                f"案件 {case.id} 的材料已经提取完成，但审核卡片暂时发送失败。请稍后再发送“重发审核卡片”。",
            )

    def _accept_file(
        self,
        chat_id: str,
        sender_id: str,
        message_id: str,
        file_key: str,
        file_name: str,
    ) -> None:
        suffix = Path(file_name).suffix.lower()
        if suffix not in {".zip", ".xlsx"}:
            return
        if not message_id or not file_key:
            raise ValidationProblem("飞书文件消息缺少 message_id 或 file_key。")
        with self._case_lock:
            case = self.store.get_active_case(chat_id)
            if case is None:
                case = self.store.create_case(chat_id, sender_id)
                self.material_bot.send_text(chat_id, f"已自动新建案件 {case.id}。")
            if case.status != "collecting":
                self.material_bot.send_text(
                    chat_id,
                    f"案件 {case.id} 已进入“{status_label(case.status)}”，本次文件未加入案件。",
                )
                return
            destination = case.directory / safe_filename(file_name, f"upload{suffix}")

        self.material_bot.download_message_file(message_id, file_key, destination)
        with destination.open("rb") as source:
            signature = source.read(2)
        if signature != b"PK":
            destination.unlink(missing_ok=True)
            raise ValidationProblem(f"收到的 {suffix.upper().lstrip('.')} 文件内容无效。")

        with self._case_lock:
            values = {"archive_path" if suffix == ".zip" else "workbook_path": str(destination)}
            case = self.store.update_case(
                case.id,
                actor_id=sender_id,
                action="file_received",
                **values,
            )
            ready = bool(case.archive_path and case.workbook_path)
            if ready and case.status == "collecting":
                case = self.store.update_case(
                    case.id,
                    action="extraction_started",
                    status="extracting",
                    error="",
                )
                self.executor.submit(self._run_extraction_safely, case.id)

        if ready:
            self.material_bot.send_text(chat_id, f"案件 {case.id} 的两个文件已收齐，开始提取。")
        else:
            waiting = "XLSX案件表" if suffix == ".zip" else "ZIP材料包"
            self.material_bot.send_text(chat_id, f"案件 {case.id} 已收到 {file_name}，还需要 {waiting}。")

    def _run_extraction_safely(self, case_id: str) -> None:
        case = self.store.get_case(case_id)
        try:
            accepted = self.extractor.start_analysis(
                Path(case.archive_path), Path(case.workbook_path)
            )
            job_id = str(accepted["job_id"])
            self.store.update_case(case_id, extractor_job_id=job_id, action="extractor_job_created")
            deadline = time.monotonic() + self.config.max_wait_seconds
            while time.monotonic() < deadline:
                payload = self.extractor.get_job(job_id)
                status = payload.get("status")
                if status == "completed":
                    result = payload.get("result") or {}
                    case = self.store.save_extractor_result(case_id, result)
                    try:
                        self._send_review_card(case)
                    except Exception as notification_error:
                        self.store.update_case(
                            case_id,
                            action="review_card_delivery_failed",
                            status="review_step1",
                            error=f"材料已提取，但审核卡片发送失败：{notification_error}",
                        )
                        try:
                            self.material_bot.send_text(
                                case.chat_id,
                                f"案件 {case.id} 的材料已经提取完成，但审核卡片暂时发送失败。请发送“重发审核卡片”重试。",
                            )
                        except Exception as fallback_error:
                            print(f"[feishu-workgroup] fallback notification error: {fallback_error}")
                    return
                if status == "failed":
                    raise RuntimeError(payload.get("error") or "材料提取失败。")
                time.sleep(self.config.poll_seconds)
            raise TimeoutError("材料提取等待超时，请稍后发送“状态”查询。")
        except Exception as exc:
            self.store.update_case(
                case_id,
                action="extraction_failed",
                status="failed",
                error=str(exc),
            )
            try:
                self.material_bot.send_text(case.chat_id, f"案件 {case.id} 提取失败：{exc}")
            except Exception as notification_error:
                print(f"[feishu-workgroup] failure notification error: {notification_error}")
            print(f"[feishu-workgroup] extraction error: {exc}")

    def _approve_step1_safely(self, case_id: str, actor_id: str) -> None:
        try:
            case = self.store.get_case(case_id)
            if case.status != "review_step1":
                self.material_bot.send_text(
                    case.chat_id,
                    f"案件 {case.id} 当前为“{status_label(case.status)}”，不能重复交接。",
                )
                return
            latest = self.extractor.get_job(case.extractor_job_id)
            if not latest.get("exported"):
                self.material_bot.send_text(
                    case.chat_id,
                    f"案件 {case.id} 尚未在核对页生成 Excel。请先完成核对并点击“生成并下载 Excel”。",
                )
                return
            result = latest.get("result") or case.extractor_result
            fields = extraction_to_generator_fields(result)
            case = self.store.save_generator_fields(case_id, fields, actor_id)
            missing = missing_required_fields(fields)
            form_url = self.case_form_url(case)
            self.draft_bot.send_card(
                case.chat_id,
                supplement_card(case.id, missing, form_url),
            )
        except Exception as exc:
            try:
                case = self.store.get_case(case_id)
                self.material_bot.send_text(case.chat_id, f"案件 {case.id} 交接失败：{exc}")
            finally:
                print(f"[feishu-workgroup] handoff error: {exc}")

    def case_form_url(self, case: CaseRecord) -> str:
        return (
            f"{self.config.public_base_url}/cases/{case.id}/supplement?"
            f"{urlencode({'token': case.access_token})}"
        )

    def get_case_for_token(self, case_id: str, token: str) -> CaseRecord:
        case = self.store.get_case(case_id)
        if not token or not hmac.compare_digest(token, case.access_token):
            raise PermissionError("链接无效或已被截断。")
        return case

    def generate_from_form(
        self,
        case_id: str,
        token: str,
        submitted: dict[str, str],
        actor_id: str = "secure-form",
    ) -> CaseRecord:
        case = self.get_case_for_token(case_id, token)
        if case.status not in {"supplementing", "failed"}:
            if case.status == "done":
                return case
            raise ValidationProblem(f"案件当前为“{status_label(case.status)}”，暂不能生成。")
        allowed = set(SUPPLEMENT_FIELD_IDS) | set(REQUIRED_FIELD_IDS)
        fields = dict(case.generator_fields)
        for field_id in allowed:
            if field_id in submitted:
                value = submitted[field_id].strip()[:2000]
                if value:
                    fields[field_id] = value
                elif field_id in fields:
                    fields.pop(field_id)
        missing = missing_required_fields(fields)
        if missing:
            labels = "、".join(label_for(item) for item in missing)
            raise ValidationProblem(f"请补全以下必填信息：{labels}", missing)

        self.store.update_case(
            case_id,
            actor_id=actor_id,
            action="generation_started",
            generator_fields_json=json.dumps(fields, ensure_ascii=False),
            status="generating",
            error="",
        )
        borrower = safe_filename(str(fields.get("defendant_name", "借款人")), "借款人")
        destination = case.directory / f"民事起诉状-{borrower}-{case.id}.docx"
        try:
            response = self.generator.generate(fields, destination)
            warnings = [str(item) for item in response.get("warnings") or []]
            case = self.store.update_case(
                case_id,
                actor_id=actor_id,
                action="document_generated",
                status="done",
                docx_path=str(destination),
                warnings_json=json.dumps(warnings, ensure_ascii=False),
                error="",
            )
            self.draft_bot.send_file(case.chat_id, destination)
            self.draft_bot.send_card(case.chat_id, generated_card(case.id, warnings))
            return case
        except Exception as exc:
            self.store.update_case(
                case_id,
                action="generation_failed",
                status="failed",
                docx_path=str(destination) if destination.exists() else "",
                error=str(exc),
            )
            raise


def _json_content(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def safe_filename(value: str, fallback: str) -> str:
    name = Path(value).name.strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or fallback


def status_label(status: str) -> str:
    return {
        "collecting": "等待文件",
        "extracting": "正在提取",
        "review_step1": "等待第一步核对",
        "handoff": "正在交接",
        "supplementing": "等待补充起诉信息",
        "generating": "正在生成Word",
        "done": "已完成",
        "failed": "处理失败",
    }.get(status, status)
