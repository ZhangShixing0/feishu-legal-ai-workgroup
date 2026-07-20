from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import queue
import re
import shutil
import socket
import threading
import time
import uuid
import webbrowser
from email import policy
from email.parser import BytesParser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from case_extractor import HEADERS, analyze_case
from xlsx_template import (
    fill_template,
    inspect_row,
    inspect_workbook_context,
    match_workbook_case,
    merge_existing_values,
)


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR / "outputs"
OCR_SCRIPT = BASE_DIR / "windows_ocr.ps1"
VERSION = "1.2.1"
HOST = "127.0.0.1"
PORT = int(os.environ.get("CASE_EXTRACTOR_PORT", "8777"))
MAX_UPLOAD = int(os.environ.get("CASE_EXTRACTOR_MAX_UPLOAD_MB", "600")) * 1024 * 1024
RETENTION_HOURS = 24
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
JOB_FINGERPRINTS: dict[str, str] = {}
ANALYSIS_QUEUE: queue.Queue[str] = queue.Queue()
ACTIVE_JOB_ID: str | None = None


def clean_old_jobs() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - RETENTION_HOURS * 3600
    for path in OUTPUT_DIR.iterdir():
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def safe_filename(name: str, fallback: str) -> str:
    name = Path(name or "").name
    name = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "_", name).strip(" .")
    return name[:160] or fallback


def parse_multipart(content_type: str, body: bytes) -> dict[str, dict]:
    raw = (
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    message = BytesParser(policy=policy.default).parsebytes(raw)
    if not message.is_multipart():
        raise ValueError("上传请求格式不正确。")
    result: dict[str, dict] = {}
    for part in message.iter_parts():
        if part.get_content_disposition() != "form-data":
            continue
        field_name = part.get_param("name", header="content-disposition")
        if not field_name:
            continue
        result[field_name] = {
            "filename": part.get_filename() or "",
            "content_type": part.get_content_type(),
            "data": part.get_payload(decode=True) or b"",
        }
    return result


def public_analysis(analysis: dict) -> dict:
    payload = {key: value for key, value in analysis.items() if key != "sources"}
    payload["ok"] = True
    return payload


def set_job_progress(
    job_id: str,
    phase: str,
    title: str,
    detail: str,
    percent: int,
    status: str | None = None,
) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        job["progress"] = {
            "phase": phase,
            "title": title,
            "detail": detail,
            "percent": max(0, min(100, int(percent))),
            "updated": time.time(),
        }
        if status:
            job["status"] = status


def report_analysis_progress(job_id: str, message: str) -> None:
    if message.startswith("正在安全解压"):
        set_job_progress(job_id, "extract", "正在安全解压材料", message, 8, "running")
        return
    if message.startswith("正在读取 PDF"):
        set_job_progress(job_id, "read", "正在准备文本与OCR", message, 15, "running")
        return
    match = re.match(r"正在读取\s+(\d+)/(\d+)：(.+?)(?:；(.+))?$", message)
    if not match:
        set_job_progress(job_id, "read", "正在读取案件材料", message, 18, "running")
        return
    current = int(match.group(1))
    total = max(1, int(match.group(2)))
    file_name = match.group(3)
    extra = match.group(4) or ""
    file_fraction = 0.15
    page_match = re.search(r"OCR页\s+(\d+)/(\d+)", extra)
    mode_match = re.search(r"OCR识别\s+(\d+)/(\d+)", extra)
    if page_match:
        file_fraction = int(page_match.group(1)) / max(1, int(page_match.group(2)))
    elif mode_match:
        file_fraction = int(mode_match.group(1)) / max(1, int(mode_match.group(2)))
    percent = 15 + round(68 * ((current - 1 + file_fraction) / total))
    detail = f"第 {current}/{total} 个文件：{file_name}"
    if extra:
        detail += f" · {extra}"
    set_job_progress(job_id, "ocr", "正在读取文本与图片", detail, percent, "running")


def run_analysis_job(job_id: str) -> None:
    global ACTIVE_JOB_ID
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return
        ACTIVE_JOB_ID = job_id
        job["status"] = "running"
        job["started"] = time.time()
        job_dir = Path(job["dir"])
        zip_path = Path(job["archive"])
        workbook_path = Path(job["workbook"])
        zip_name = job["zip_name"]
        xlsx_name = job["workbook_name"]
        fingerprint = job["fingerprint"]
    set_job_progress(job_id, "extract", "正在安全解压材料", "任务已开始处理", 5, "running")
    try:
        workbook_context = inspect_workbook_context(workbook_path, xlsx_name)
        target_case = workbook_context.get("target_case")
        case_hint = target_case.get("identifiers") if target_case else None
        if case_hint is None and workbook_context.get("target_sequence"):
            case_hint = {"序号": workbook_context["target_sequence"]}
        analysis = analyze_case(
            zip_path,
            job_dir / "materials",
            OCR_SCRIPT,
            progress=lambda message: report_analysis_progress(job_id, message),
            case_hint=case_hint,
        )
        set_job_progress(job_id, "match", "正在匹配Excel字段", "正在合并材料证据与Excel已有值", 88, "running")
        workbook_case = match_workbook_case(workbook_context, analysis.get("selected_case"))
        analysis["fields"], existing_summary = merge_existing_values(analysis["fields"], workbook_case)
        found = sum(1 for field in analysis["fields"] if not field["missing"])
        review = sum(
            1 for field in analysis["fields"]
            if not field["missing"] and field["confidence"] < 0.85
        )
        analysis["summary"].update({
            "found": found,
            "missing": len(analysis["fields"]) - found,
            "review": review,
            "preserved": existing_summary["preserved"],
            "conflicts": existing_summary["conflicts"],
            "workbook_row": existing_summary["row"],
        })
        if existing_summary["preserved"]:
            analysis["warnings"].append(
                f"Excel现有行保留了 {existing_summary['preserved']} 个材料未验证或与材料不一致的值；"
                "这些字段已在来源栏明确标注，请重点核对。"
            )
        analysis["job_id"] = job_id
        analysis["zip_name"] = zip_name
        analysis["workbook_name"] = xlsx_name
        analysis["privacy"] = "材料仅在当前运行环境处理，不发送给第三方服务。"
        (job_dir / "analysis.json").write_text(
            json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["analysis"] = analysis
                job["status"] = "completed"
                job["completed"] = time.time()
                job["error"] = ""
        set_job_progress(job_id, "complete", "提取完成", "正在显示核对清单", 100, "completed")
    except Exception as exc:
        error = str(exc) or "材料处理失败，请检查文件后重试。"
        shutil.rmtree(job_dir, ignore_errors=True)
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if job:
                job["status"] = "failed"
                job["error"] = error
                job["completed"] = time.time()
                job["progress"] = {
                    "phase": "failed",
                    "title": "材料处理未完成",
                    "detail": error,
                    "percent": job.get("progress", {}).get("percent", 0),
                    "updated": time.time(),
                }
            if JOB_FINGERPRINTS.get(fingerprint) == job_id:
                JOB_FINGERPRINTS.pop(fingerprint, None)
    finally:
        with JOBS_LOCK:
            if ACTIVE_JOB_ID == job_id:
                ACTIVE_JOB_ID = None


def analysis_worker() -> None:
    while True:
        job_id = ANALYSIS_QUEUE.get()
        try:
            run_analysis_job(job_id)
        finally:
            ANALYSIS_QUEUE.task_done()


def start_analysis_worker() -> None:
    worker = threading.Thread(target=analysis_worker, name="case-analysis-worker", daemon=True)
    worker.start()


class AppHandler(BaseHTTPRequestHandler):
    server_version = f"CaseExtractor/{VERSION}"

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def send_error_json(self, message: str, status: int = 400) -> None:
        self.send_json({"ok": False, "error": message}, status)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/health":
            with JOBS_LOCK:
                active_job = ACTIVE_JOB_ID
            self.send_json({
                "ok": True,
                "service": "案件材料提取填表工具",
                "version": VERSION,
                "offline": True,
                "active_job": bool(active_job),
                "queued_jobs": ANALYSIS_QUEUE.qsize(),
            })
            return
        if path.startswith("/api/jobs/"):
            self.handle_job_status(path.rsplit("/", 1)[-1])
            return
        if path.startswith("/api/download/"):
            self.handle_download(path.rsplit("/", 1)[-1])
            return
        self.serve_static(path)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/analyze":
            self.handle_analyze()
            return
        if parsed.path.startswith("/api/export/"):
            self.handle_export(parsed.path.rsplit("/", 1)[-1])
            return
        self.send_error_json("接口不存在。", HTTPStatus.NOT_FOUND)

    def read_body(self, maximum: int = MAX_UPLOAD) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("请求长度无效。") from exc
        if length <= 0:
            raise ValueError("没有收到上传内容。")
        if length > maximum:
            limit_mb = maximum // (1024 * 1024)
            raise ValueError(f"上传文件合计超过 {limit_mb}MB，请拆分后重试。")
        body = self.rfile.read(length)
        if len(body) != length:
            raise ValueError("上传中断，请重新选择文件。")
        return body

    def handle_analyze(self) -> None:
        job_dir = None
        try:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                raise ValueError("请同时上传 ZIP 材料和 XLSX 表格。")
            parts = parse_multipart(content_type, self.read_body())
            zip_part = parts.get("archive")
            xlsx_part = parts.get("workbook")
            if not zip_part or not xlsx_part:
                raise ValueError("ZIP 材料和 XLSX 表格缺一不可。")
            zip_name = safe_filename(zip_part["filename"], "materials.zip")
            xlsx_name = safe_filename(xlsx_part["filename"], "template.xlsx")
            if not zip_name.lower().endswith(".zip"):
                raise ValueError("材料文件必须是 .zip 压缩包。")
            if not xlsx_name.lower().endswith(".xlsx"):
                raise ValueError("表格文件必须是 .xlsx。")
            if not zip_part["data"].startswith(b"PK") or not xlsx_part["data"].startswith(b"PK"):
                raise ValueError("文件内容与扩展名不一致，请检查后重试。")

            digest = hashlib.sha256()
            digest.update(zip_part["data"])
            digest.update(b"\0case-workbook\0")
            digest.update(xlsx_part["data"])
            fingerprint = digest.hexdigest()
            with JOBS_LOCK:
                existing_job_id = JOB_FINGERPRINTS.get(fingerprint)
                existing_job = JOBS.get(existing_job_id) if existing_job_id else None
                if existing_job and existing_job.get("status") in {"preparing", "queued", "running", "completed"}:
                    self.send_json({
                        "ok": True,
                        "job_id": existing_job_id,
                        "status": existing_job["status"],
                        "duplicate": True,
                        "message": "相同材料已经提交，已恢复原任务，不会重复启动OCR。",
                    }, HTTPStatus.ACCEPTED)
                    return
                job_id = uuid.uuid4().hex
                job_dir = OUTPUT_DIR / job_id
                zip_path = job_dir / zip_name
                workbook_path = job_dir / xlsx_name
                ahead = ANALYSIS_QUEUE.qsize() + (1 if ACTIVE_JOB_ID else 0)
                detail = "任务即将开始处理" if ahead == 0 else f"前面还有 {ahead} 个任务，系统会按顺序处理"
                JOBS[job_id] = {
                    "dir": job_dir,
                    "archive": zip_path,
                    "workbook": workbook_path,
                    "zip_name": zip_name,
                    "workbook_name": xlsx_name,
                    "fingerprint": fingerprint,
                    "status": "preparing",
                    "progress": {
                        "phase": "queued",
                        "title": "正在创建后台任务",
                        "detail": detail,
                        "percent": 2,
                        "updated": time.time(),
                    },
                    "error": "",
                    "created": time.time(),
                }
                JOB_FINGERPRINTS[fingerprint] = job_id
            job_dir.mkdir(parents=True, exist_ok=False)
            zip_path.write_bytes(zip_part["data"])
            workbook_path.write_bytes(xlsx_part["data"])
            set_job_progress(job_id, "queued", "任务已进入处理队列", detail, 3, "queued")
            ANALYSIS_QUEUE.put(job_id)
            self.send_json({
                "ok": True,
                "job_id": job_id,
                "status": "queued",
                "duplicate": False,
                "message": detail,
            }, HTTPStatus.ACCEPTED)
        except Exception as exc:
            if job_dir is not None:
                shutil.rmtree(job_dir, ignore_errors=True)
            if "job_id" in locals():
                with JOBS_LOCK:
                    failed_job = JOBS.pop(job_id, None)
                    if failed_job and JOB_FINGERPRINTS.get(failed_job.get("fingerprint")) == job_id:
                        JOB_FINGERPRINTS.pop(failed_job.get("fingerprint"), None)
            self.send_error_json(str(exc) or "材料处理失败，请检查文件后重试。")

    def handle_job_status(self, job_id: str) -> None:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
            if not job:
                snapshot = None
            else:
                snapshot = {
                    "status": job.get("status", "failed"),
                    "progress": dict(job.get("progress") or {}),
                    "error": job.get("error", ""),
                    "analysis": job.get("analysis"),
                    "exported": bool(job.get("output")),
                }
        if not snapshot:
            self.send_error_json("任务不存在或服务已重启，请重新上传材料。", HTTPStatus.NOT_FOUND)
            return
        payload = {
            "ok": True,
            "job_id": job_id,
            "status": snapshot["status"],
            "progress": snapshot["progress"],
            "exported": snapshot["exported"],
        }
        if snapshot["status"] == "failed":
            payload["error"] = snapshot["error"] or "材料处理失败，请重新上传。"
        elif snapshot["status"] == "completed" and snapshot["analysis"]:
            payload["result"] = public_analysis(snapshot["analysis"])
        self.send_json(payload)

    def handle_export(self, job_id: str) -> None:
        try:
            with JOBS_LOCK:
                job = JOBS.get(job_id)
            if not job or job.get("status") != "completed":
                raise ValueError("任务尚未完成或已失效，请重新上传材料。")
            payload = json.loads(self.read_body(5 * 1024 * 1024).decode("utf-8"))
            incoming = payload.get("fields")
            if not isinstance(incoming, list) or len(incoming) != len(HEADERS):
                raise ValueError("字段数量不正确，请重新分析。")
            fields = []
            for index, item in enumerate(incoming):
                if not isinstance(item, dict) or item.get("header") != HEADERS[index]:
                    raise ValueError("字段顺序不正确，请重新分析。")
                value = item.get("value", "无")
                if value is None or (isinstance(value, str) and not value.strip()):
                    value = "无"
                if isinstance(value, str):
                    value = value.strip()[:1000]
                fields.append({
                    **item,
                    "index": index,
                    "header": HEADERS[index],
                    "value": value,
                    "missing": str(value).strip() == "无",
                })
            sequence = str(fields[0]["value"])
            name = str(fields[7]["value"])
            output_name = safe_filename(f"提取结果-{sequence}-{name}.xlsx", "提取结果.xlsx")
            output_path = job["dir"] / output_name
            result = fill_template(job["workbook"], output_path, fields)
            checked = inspect_row(output_path, result.target_row)
            if len(checked) < len(HEADERS):
                raise RuntimeError("生成后的 Excel 行不完整，请重新尝试。")
            with JOBS_LOCK:
                analysis = dict(job.get("analysis") or {})
                analysis["fields"] = fields
                summary = dict(analysis.get("summary") or {})
                summary["found"] = sum(1 for field in fields if not field["missing"])
                summary["missing"] = sum(1 for field in fields if field["missing"])
                analysis["summary"] = summary
                job["analysis"] = analysis
                job["output"] = output_path
                job["write_result"] = result
            (job["dir"] / "analysis.json").write_text(
                json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.send_json({
                "ok": True,
                "download_url": f"api/download/{job_id}",
                "file_name": output_name,
                "target": f"{result.sheet_name}!{result.target_row}:{result.target_row}",
                "matched_existing": result.matched_existing,
                "missing_cells": len(result.missing_cells),
            })
        except Exception as exc:
            self.send_error_json(str(exc) or "Excel 生成失败。")

    def handle_download(self, job_id: str) -> None:
        with JOBS_LOCK:
            job = JOBS.get(job_id)
        output_path = job.get("output") if job else None
        if not output_path or not Path(output_path).is_file():
            self.send_error_json("结果文件不存在，请先生成 Excel。", HTTPStatus.NOT_FOUND)
            return
        path = Path(output_path)
        data = path.read_bytes()
        encoded_name = path.name.encode("utf-8").hex()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename=case-result.xlsx; filename*=UTF-8''{quote_filename(path.name)}")
        self.send_header("X-File-Name-Hex", encoded_name)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def serve_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        target = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            self.send_error_json("路径无效。", HTTPStatus.BAD_REQUEST)
            return
        if not target.is_file():
            self.send_error_json("页面不存在。", HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        mime, _ = mimetypes.guess_type(target.name)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (mime or "application/octet-stream") + ("; charset=utf-8" if target.suffix in {".html", ".css", ".js"} else ""))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)


def quote_filename(name: str) -> str:
    from urllib.parse import quote
    return quote(name, safe="")


def find_available_port(preferred: int) -> int:
    strict = os.environ.get("CASE_EXTRACTOR_STRICT_PORT") == "1"
    candidates = range(preferred, preferred + (1 if strict else 20))
    for port in candidates:
        with socket.socket() as test:
            try:
                test.bind((HOST, port))
                return port
            except OSError:
                continue
    if strict:
        raise OSError(f"固定端口 {preferred} 已被占用，服务拒绝改用其他端口。")
    raise OSError(f"{preferred}-{preferred + 19} 端口均被占用，请关闭其他本地工具后重试。")


def main() -> None:
    clean_old_jobs()
    start_analysis_worker()
    port = find_available_port(PORT)
    server = ThreadingHTTPServer((HOST, port), AppHandler)
    server.daemon_threads = True
    url = f"http://{HOST}:{port}/"
    print("案件材料提取填表工具已启动")
    print(f"请在浏览器打开：{url}")
    print("所有材料仅在当前运行环境处理，不发送给第三方服务。按 Ctrl+C 可停止。")
    if os.environ.get("CASE_EXTRACTOR_NO_BROWSER") != "1":
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
