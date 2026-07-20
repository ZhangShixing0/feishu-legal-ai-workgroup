from __future__ import annotations

import json
import mimetypes
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from .config import AppCredentials


class RemoteServiceError(RuntimeError):
    pass


class FeishuRestClient:
    API_BASE = "https://open.feishu.cn/open-apis"

    def __init__(self, credentials: AppCredentials, session: requests.Session | None = None):
        self.credentials = credentials
        self.session = session or requests.Session()
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    def _tenant_token(self) -> str:
        with self._token_lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token
            if not self.credentials.configured:
                raise RemoteServiceError("飞书应用凭证尚未配置。")
            response = self.session.post(
                f"{self.API_BASE}/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self.credentials.app_id,
                    "app_secret": self.credentials.app_secret,
                },
                timeout=15,
            )
            payload = self._json_or_error(response, "获取飞书访问凭证")
            token = str(payload.get("tenant_access_token", ""))
            if not token:
                raise RemoteServiceError(f"飞书未返回访问凭证：{payload.get('msg', '未知错误')}")
            self._token = token
            self._token_expires_at = time.time() + int(payload.get("expire", 7200))
            return token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._tenant_token()}"}

    @staticmethod
    def _json_or_error(response: requests.Response, action: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RemoteServiceError(f"{action}失败：飞书返回了非 JSON 内容。") from exc
        if not response.ok or int(payload.get("code", 0)) != 0:
            raise RemoteServiceError(
                f"{action}失败：{payload.get('msg') or response.reason or response.status_code}"
            )
        return payload

    def send_text(self, chat_id: str, text: str) -> str:
        return self._send_message(chat_id, "text", {"text": text})

    def send_card(self, chat_id: str, card: dict[str, Any]) -> str:
        return self._send_message(chat_id, "interactive", card)

    def send_file(self, chat_id: str, path: Path) -> str:
        file_key = self.upload_file(path)
        return self._send_message(chat_id, "file", {"file_key": file_key})

    def _send_message(self, chat_id: str, msg_type: str, content: dict[str, Any]) -> str:
        request_uuid = str(uuid.uuid4())
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.post(
                    f"{self.API_BASE}/im/v1/messages",
                    params={"receive_id_type": "chat_id"},
                    headers={**self._headers(), "Content-Type": "application/json"},
                    json={
                        "receive_id": chat_id,
                        "msg_type": msg_type,
                        "content": json.dumps(content, ensure_ascii=False),
                        "uuid": request_uuid,
                    },
                    timeout=(10, 30),
                )
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < 2:
                        time.sleep(2**attempt)
                        continue
                payload = self._json_or_error(response, "发送飞书消息")
                return str((payload.get("data") or {}).get("message_id", ""))
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(2**attempt)
                    continue
        raise RemoteServiceError(f"发送飞书消息失败，重试 3 次后仍无法连接：{last_error}") from last_error

    def download_message_file(self, message_id: str, file_key: str, destination: Path) -> Path:
        safe_message_id = quote(message_id, safe="")
        safe_file_key = quote(file_key, safe="")
        response = self.session.get(
            f"{self.API_BASE}/im/v1/messages/{safe_message_id}/resources/{safe_file_key}",
            params={"type": "file"},
            headers=self._headers(),
            timeout=120,
            stream=True,
        )
        if not response.ok:
            raise RemoteServiceError(f"下载群文件失败：HTTP {response.status_code}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as target:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    target.write(chunk)
        return destination

    def upload_file(self, path: Path) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        with path.open("rb") as source:
            response = self.session.post(
                f"{self.API_BASE}/im/v1/files",
                headers=self._headers(),
                data={"file_type": "stream", "file_name": path.name},
                files={"file": (path.name, source, mime)},
                timeout=120,
            )
        payload = self._json_or_error(response, "上传飞书文件")
        file_key = str((payload.get("data") or {}).get("file_key", ""))
        if not file_key:
            raise RemoteServiceError("上传飞书文件失败：响应中没有 file_key。")
        return file_key


class ExtractorClient:
    def __init__(self, base_url: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def start_analysis(self, archive: Path, workbook: Path) -> dict[str, Any]:
        with archive.open("rb") as archive_file, workbook.open("rb") as workbook_file:
            response = self.session.post(
                f"{self.base_url}/api/analyze",
                files={
                    "archive": (archive.name, archive_file, "application/zip"),
                    "workbook": (
                        workbook.name,
                        workbook_file,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    ),
                },
                timeout=180,
            )
        return self._payload(response, "提交材料提取任务")

    def get_job(self, job_id: str) -> dict[str, Any]:
        response = self.session.get(f"{self.base_url}/api/jobs/{job_id}", timeout=30)
        return self._payload(response, "读取材料提取进度")

    @staticmethod
    def _payload(response: requests.Response, action: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RemoteServiceError(f"{action}失败：服务返回了非 JSON 内容。") from exc
        if not response.ok or not payload.get("ok"):
            raise RemoteServiceError(f"{action}失败：{payload.get('error') or response.reason}")
        return payload


class GeneratorClient:
    def __init__(self, base_url: str, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def generate(self, fields: dict[str, Any], destination: Path) -> dict[str, Any]:
        response = self.session.post(
            f"{self.base_url}/api/generate",
            json={"fields": fields},
            timeout=120,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RemoteServiceError("生成诉状失败：服务返回了非 JSON 内容。") from exc
        if not response.ok or not payload.get("ok"):
            raise RemoteServiceError(f"生成诉状失败：{payload.get('error') or response.reason}")
        docx_url = str(payload.get("docx_url", ""))
        if not docx_url:
            raise RemoteServiceError("生成诉状失败：响应中没有 Word 下载地址。")
        download_url = docx_url if docx_url.startswith("http") else f"{self.base_url}{docx_url}"
        file_response = self.session.get(download_url, timeout=120, stream=True)
        if not file_response.ok:
            raise RemoteServiceError(f"下载生成的 Word 失败：HTTP {file_response.status_code}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as target:
            for chunk in file_response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    target.write(chunk)
        return payload
