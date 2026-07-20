from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import uuid
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

from legal_generator import GenerationError, generate_document, generate_preview_html, load_schema


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
OUTPUT_DIR = BASE_DIR / "outputs"
MAX_BODY_BYTES = 2 * 1024 * 1024
APP_VERSION = "0.6.0"
DEPLOYMENT_MODE = os.environ.get("LEGAL_DEPLOYMENT_MODE", "local").strip().lower()
if DEPLOYMENT_MODE not in {"local", "server"}:
    DEPLOYMENT_MODE = "local"


def read_retention_hours() -> int:
    default = 24 if DEPLOYMENT_MODE == "server" else 0
    try:
        return max(0, int(os.environ.get("LEGAL_JOB_RETENTION_HOURS", str(default))))
    except ValueError:
        return default


JOB_RETENTION_HOURS = read_retention_hours()
JOB_ID_PATTERN = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{8}$")
_cleanup_lock = threading.Lock()


def cleanup_expired_jobs(
    output_dir: Path = OUTPUT_DIR,
    retention_hours: int = JOB_RETENTION_HOURS,
    now: float | None = None,
) -> int:
    """Remove only expired generated-job folders, leaving all other files untouched."""
    if retention_hours <= 0 or not output_dir.exists():
        return 0
    cutoff = (time.time() if now is None else now) - retention_hours * 3600
    removed = 0
    with _cleanup_lock:
        for candidate in output_dir.iterdir():
            if candidate.is_symlink() or not candidate.is_dir() or not JOB_ID_PATTERN.fullmatch(candidate.name):
                continue
            try:
                if candidate.stat().st_mtime <= cutoff:
                    shutil.rmtree(candidate)
                    removed += 1
            except FileNotFoundError:
                continue
            except OSError as exc:
                print(f"清理临时文件失败：{candidate}（{exc}）", file=sys.stderr)
    return removed


def cleanup_periodically(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        cleanup_expired_jobs()
        stop_event.wait(3600)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "LegalDocGenerator/0.6"

    def log_message(self, format_string: str, *args: object) -> None:
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), format_string % args))

    def send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def send_security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")

    def send_file(self, path: Path, download_name: str | None = None) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime, _ = mimetypes.guess_type(path.name)
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_security_headers()
        if download_name:
            encoded = quote(download_name)
            self.send_header("Content-Disposition", f"attachment; filename=document; filename*=UTF-8''{encoded}")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if path == "/api/version":
            self.send_json({"ok": True, "version": APP_VERSION, "deployment_mode": DEPLOYMENT_MODE})
            return
        if path == "/api/health":
            self.send_json({"ok": True, "version": APP_VERSION, "deployment_mode": DEPLOYMENT_MODE})
            return
        if path == "/api/schema":
            schema = load_schema()
            self.send_json({
                "ok": True,
                "app_version": APP_VERSION,
                "deployment_mode": DEPLOYMENT_MODE,
                "job_retention_hours": JOB_RETENTION_HOURS,
                "schema": schema,
                "capabilities": {
                    "offline": DEPLOYMENT_MODE == "local",
                    "server_processing": DEPLOYMENT_MODE == "server",
                    "word_upload": False,
                    "batch": False,
                    "html_preview": True,
                    "pdf_preview": False,
                },
            })
            return
        if path.startswith("/api/file/"):
            parts = path.strip("/").split("/")
            if len(parts) != 4:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            _, _, job_id, kind = parts
            if not JOB_ID_PATTERN.fullmatch(job_id):
                self.send_error(HTTPStatus.BAD_REQUEST)
                return
            mapping = {"docx": "document.docx"}
            filename = mapping.get(kind)
            if not filename:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            download = None
            if parsed.query == "download=1":
                download = f"金融借款合同纠纷民事起诉状.{kind}"
            self.send_file(OUTPUT_DIR / job_id / filename, download)
            return

        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self.send_file(candidate)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/generate":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_json({"ok": False, "error": "请求长度无效"}, HTTPStatus.BAD_REQUEST)
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json({"ok": False, "error": "请求内容为空或过大"}, HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            fields = payload.get("fields", payload)
            if not isinstance(fields, dict):
                raise ValueError("fields must be an object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self.send_json({"ok": False, "error": f"无法读取表单数据：{exc}"}, HTTPStatus.BAD_REQUEST)
            return

        cleanup_expired_jobs()
        job_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        job_dir = OUTPUT_DIR / job_id
        docx_path = job_dir / "document.docx"
        try:
            validation = generate_document(fields, docx_path)
            preview_html = generate_preview_html(fields)
        except GenerationError as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.UNPROCESSABLE_ENTITY)
            return
        except Exception as exc:  # Defensive local-app boundary.
            self.send_json({"ok": False, "error": f"生成Word失败：{exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return

        response = {
            "ok": True,
            "job_id": job_id,
            "warnings": validation.warnings,
            "docx_url": f"/api/file/{job_id}/docx?download=1",
            "preview_mode": "html",
            "preview_html": preview_html,
        }
        self.send_json(response)


def open_browser_later(url: str) -> None:
    def opener() -> None:
        time.sleep(0.8)
        webbrowser.open(url)

    threading.Thread(target=opener, daemon=True).start()


def probe_existing_service(url: str) -> tuple[str, str | None]:
    """Return (none|current|old, version) for the configured local port."""
    local_opener = build_opener(ProxyHandler({}))
    try:
        with local_opener.open(f"{url}api/version", timeout=1) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError:
        return "old", None
    except URLError:
        return "none", None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return "old", None
    version = str(payload.get("version", ""))
    return ("current" if version == APP_VERSION else "old"), version or None


def main() -> None:
    parser = argparse.ArgumentParser(description="离线金融借款诉状生成器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_expired_jobs()
    url = f"http://{args.host}:{args.port}/"
    service_state, running_version = probe_existing_service(url)
    if service_state == "current":
        print(f"当前版本服务已经运行：{url}")
        if not args.no_browser:
            webbrowser.open(url)
        return
    if service_state == "old":
        version_note = f"（版本 {running_version}）" if running_version else ""
        print(f"检测到旧版或其他本地服务{version_note}正在占用端口 {args.port}。")
        print("请先关闭之前启动本工具的黑色命令窗口，再重新双击 run.bat。")
        raise SystemExit(2)

    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    cleanup_stop = threading.Event()
    if JOB_RETENTION_HOURS > 0:
        threading.Thread(target=cleanup_periodically, args=(cleanup_stop,), daemon=True).start()
    print(f"律书生成台已启动：{url}")
    if DEPLOYMENT_MODE == "server":
        print(f"服务器模式：生成文件将在 {JOB_RETENTION_HOURS} 小时后自动清理。")
    else:
        print("本机模式：所有材料仅在本机处理。")
    print("按 Ctrl+C 停止。")
    if not args.no_browser:
        open_browser_later(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
