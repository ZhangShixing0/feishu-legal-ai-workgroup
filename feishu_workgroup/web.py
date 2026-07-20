from __future__ import annotations

import html
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from .mapping import (
    FIELD_METADATA,
    READ_ONLY_SUMMARY_IDS,
    REQUIRED_FIELD_IDS,
    SUPPLEMENT_FIELD_IDS,
    label_for,
    missing_required_fields,
)
from .orchestrator import ValidationProblem, WorkgroupOrchestrator
from .store import CaseRecord


def build_server(
    host: str,
    port: int,
    orchestrator: WorkgroupOrchestrator,
) -> ThreadingHTTPServer:
    class Handler(WorkgroupHandler):
        pass

    Handler.orchestrator = orchestrator
    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


class WorkgroupHandler(BaseHTTPRequestHandler):
    orchestrator: WorkgroupOrchestrator
    server_version = "FeishuLegalWorkgroup/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[feishu-web] {self.address_string()} {format % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._json({"ok": True, "service": "feishu-legal-workgroup", "version": "0.1.0"})
            return
        route = self._case_route(parsed.path)
        if not route:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        case_id, action = route
        token = (parse_qs(parsed.query).get("token") or [""])[0]
        try:
            case = self.orchestrator.get_case_for_token(case_id, token)
            if action == "supplement":
                if case.status == "done":
                    self._html(render_success(case, token))
                else:
                    self._html(render_form(case, token))
                return
            if action == "document":
                self._send_document(case)
                return
        except PermissionError as exc:
            self._html(render_message("链接无法使用", str(exc)), HTTPStatus.FORBIDDEN)
        except KeyError:
            self._html(render_message("案件不存在", "案件编号无效或已经被清理。"), HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        route = self._case_route(parsed.path)
        if not route or route[1] != "supplement":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        case_id, _ = route
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 64 * 1024:
            self._html(render_message("提交失败", "表单为空或内容过大。"), HTTPStatus.BAD_REQUEST)
            return
        form: dict[str, str] = {}
        token = ""
        try:
            raw = self.rfile.read(length).decode("utf-8")
            form = {key: values[-1] for key, values in parse_qs(raw, keep_blank_values=True).items()}
            token = form.pop("token", "")
            case = self.orchestrator.generate_from_form(case_id, token, form)
            self._html(render_success(case, token))
        except PermissionError as exc:
            self._html(render_message("链接无法使用", str(exc)), HTTPStatus.FORBIDDEN)
        except ValidationProblem as exc:
            try:
                case = self.orchestrator.get_case_for_token(case_id, token)
            except Exception:
                self._html(render_message("提交失败", str(exc)), HTTPStatus.BAD_REQUEST)
            else:
                merged = dict(case.generator_fields)
                merged.update(form)
                self._html(
                    render_form(case, token, values=merged, error=str(exc), invalid=exc.fields),
                    HTTPStatus.UNPROCESSABLE_ENTITY,
                )
        except Exception as exc:
            try:
                case = self.orchestrator.get_case_for_token(case_id, token)
            except Exception:
                self._html(render_message("生成失败", str(exc)), HTTPStatus.INTERNAL_SERVER_ERROR)
            else:
                self._html(
                    render_form(
                        case,
                        token,
                        error=f"生成失败：{exc}。请核对后重新提交。",
                    ),
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                )

    @staticmethod
    def _case_route(path: str) -> tuple[str, str] | None:
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "cases" and parts[2] in {"supplement", "document"}:
            return parts[1], parts[2]
        return None

    def _send_document(self, case: CaseRecord) -> None:
        path = Path(case.docx_path)
        if not path.is_file():
            self._html(render_message("Word尚未生成", "请先完成补充信息并提交。"), HTTPStatus.NOT_FOUND)
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(path.name)}")
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _html(self, content: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")


def render_form(
    case: CaseRecord,
    token: str,
    values: dict[str, Any] | None = None,
    error: str = "",
    invalid: list[str] | None = None,
) -> str:
    values = values or case.generator_fields
    invalid_set = set(invalid or [])
    missing = missing_required_fields(values)
    editable_ids = list(SUPPLEMENT_FIELD_IDS)
    for field_id in missing:
        if field_id not in editable_ids:
            editable_ids.append(field_id)

    summary_items = "".join(
        (
            '<div class="summary-item">'
            f'<dt>{html.escape(label_for(field_id))}</dt>'
            f'<dd>{html.escape(str(values.get(field_id) or "未提取"))}</dd>'
            "</div>"
        )
        for field_id in READ_ONLY_SUMMARY_IDS
    )
    fields_html = "".join(
        render_field(field_id, values.get(field_id, ""), field_id in invalid_set)
        for field_id in editable_ids
    )
    error_html = (
        f'<div class="alert" role="alert" tabindex="-1"><strong>请检查后重试</strong><p>{html.escape(error)}</p></div>'
        if error
        else ""
    )
    body = f"""
    <a class="skip-link" href="#main">跳到表单</a>
    <main id="main" class="shell">
      <header class="page-header">
        <p class="eyebrow">AI 诉状专员 · 第二步</p>
        <h1>补充并确认起诉信息</h1>
        <p>案件编号 <span class="case-id">{html.escape(case.id)}</span>。第一步提取值已带入，请重点核对主体、基准日和计息规则。</p>
      </header>
      {error_html}
      <section class="card" aria-labelledby="summary-title">
        <h2 id="summary-title">第一步关键数据</h2>
        <dl class="summary-grid">{summary_items}</dl>
      </section>
      <form class="card form-card" method="post" novalidate>
        <input type="hidden" name="token" value="{html.escape(token, quote=True)}">
        <div class="section-heading">
          <div><h2>起诉状专用信息</h2><p>带“必填”的项目缺失时不能生成 Word。</p></div>
          <span class="required-note">* 必填</span>
        </div>
        <div class="field-grid">{fields_html}</div>
        <div class="notice"><strong>提交即生成草稿</strong><p>系统会把 Word 发回飞书群，但仍需律师完成最终专业审查。</p></div>
        <button class="primary-button" type="submit" id="submit-button">确认信息并生成 Word</button>
      </form>
    </main>
    """
    return page("补充起诉信息", body)


def render_field(field_id: str, value: Any, invalid: bool) -> str:
    meta = FIELD_METADATA[field_id]
    label = str(meta["label"])
    required = field_id in REQUIRED_FIELD_IDS
    input_type = "date" if meta.get("type") == "date" else "text"
    inputmode = "decimal" if meta.get("type") in {"money", "number"} else "text"
    described_by = f"{field_id}-help"
    error_attr = ' aria-invalid="true"' if invalid else ""
    required_attr = " required" if required else ""
    marker = '<span aria-hidden="true">*</span><span class="sr-only">必填</span>' if required else ""
    helper = field_helper(field_id)
    common = (
        f'id="{field_id}" name="{field_id}" '
        f'aria-describedby="{described_by}"{error_attr}{required_attr}'
    )
    if field_id == "calculation_method":
        control = f'<textarea {common} rows="3">{html.escape(str(value or ""))}</textarea>'
    else:
        control = (
            f'<input {common} type="{input_type}" inputmode="{inputmode}" '
            f'value="{html.escape(str(value or ""), quote=True)}">'
        )
    invalid_class = " field-error" if invalid else ""
    wide_class = " field-wide" if field_id == "calculation_method" else ""
    return (
        f'<div class="field{invalid_class}{wide_class}">'
        f'<label for="{field_id}">{html.escape(label)} {marker}</label>'
        f"{control}"
        f'<p id="{described_by}" class="helper">{html.escape(helper)}</p>'
        "</div>"
    )


def field_helper(field_id: str) -> str:
    return {
        "plaintiff_name": "填写本案起诉状中的原告全称。",
        "original_creditor": "如存在债权转让，请核对转让前债权人。",
        "balance_date": "格式：YYYY-MM-DD。金额计算应与该日期一致。",
        "contract_sign_date": "格式：YYYY-MM-DD，请与合同和放款材料核对。",
        "contract_name": "按合同首页的完整名称填写。",
        "calculation_method": "例如：利息年化3.6%，罚息按合同约定计算。",
        "compound_interest": "仅填写数字；没有时填0.00。",
        "penalty_interest": "仅填写数字；没有时填0.00。",
        "co_party": "没有共同签约人时可留空。",
        "defendant_ethnicity": "材料未载明时可留空，由律师决定是否补充。",
        "defendant_employer": "材料未载明时可留空。",
        "defendant_title": "可沿用第一步提取的职业信息并人工核对。",
        "loan_lender": "核对合同签约主体及债权转让前后的身份关系。",
        "loan_borrower": "核对合同中的借款人名称。",
        "agreed_amount": "合同约定的借款金额，仅填写数字。",
        "disbursed_amount": "实际发放金额，仅填写数字。",
    }.get(field_id, "第一步未取得可靠值，请根据原始材料补充。")


def render_success(case: CaseRecord, token: str) -> str:
    download_url = f"document?{urlencode_token(token)}"
    body = f"""
    <main class="shell compact-shell">
      <section class="card success-card">
        <div class="success-mark" aria-hidden="true"><svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg></div>
        <p class="eyebrow">AI 诉状专员 · 已完成</p>
        <h1>Word 草稿已经生成</h1>
        <p>案件 {html.escape(case.id)} 的文书已发送到飞书群。也可以在当前页面下载一份。</p>
        <a class="primary-button link-button" href="{html.escape(download_url, quote=True)}">下载 Word 草稿</a>
        <p class="footnote">请继续核对主体、金额、日期、债权转让关系与诉讼请求。</p>
      </section>
    </main>
    """
    return page("Word草稿已生成", body)


def render_message(title: str, message: str) -> str:
    body = f"""
    <main class="shell compact-shell">
      <section class="card message-card">
        <p class="eyebrow">AI 诉状专员</p>
        <h1>{html.escape(title)}</h1>
        <p>{html.escape(message)}</p>
      </section>
    </main>
    """
    return page(title, body)


def urlencode_token(token: str) -> str:
    from urllib.parse import urlencode

    return urlencode({"token": token})


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="light">
  <title>{html.escape(title)} · AI诉状专员</title>
  <style>
    :root {{ --primary:#1e3a8a; --primary-hover:#172f72; --accent:#b45309; --background:#f8fafc; --surface:#fff; --text:#0f172a; --muted-text:#475569; --muted:#e9eef5; --border:#cbd5e1; --danger:#b91c1c; --danger-bg:#fef2f2; --ring:#1e3a8a; --success:#166534; }}
    * {{ box-sizing:border-box; }}
    html {{ background:var(--background); color:var(--text); font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif; line-height:1.6; }}
    body {{ margin:0; min-height:100dvh; background:linear-gradient(180deg,#edf3fb 0,#f8fafc 240px); }}
    a {{ color:var(--primary); }}
    .skip-link {{ position:absolute; left:16px; top:-80px; z-index:10; background:var(--surface); padding:12px 16px; border:2px solid var(--ring); border-radius:8px; }}
    .skip-link:focus {{ top:16px; }}
    .shell {{ width:min(calc(100% - 32px), 920px); margin:0 auto; padding:40px 0 64px; }}
    .compact-shell {{ width:min(calc(100% - 32px), 640px); padding-top:12vh; }}
    .page-header {{ margin-bottom:24px; }}
    .eyebrow {{ margin:0 0 6px; color:var(--primary); font-size:14px; font-weight:700; letter-spacing:.05em; }}
    h1,h2 {{ font-family:Georgia,"Songti SC","SimSun",serif; line-height:1.25; }}
    h1 {{ margin:0 0 12px; font-size:clamp(28px,6vw,42px); }}
    h2 {{ margin:0; font-size:21px; }}
    p {{ color:var(--muted-text); }}
    .case-id {{ font-variant-numeric:tabular-nums; font-weight:700; color:var(--text); }}
    .card {{ margin-bottom:20px; padding:24px; border:1px solid var(--border); border-radius:16px; background:var(--surface); box-shadow:0 8px 28px rgba(30,58,138,.08); }}
    .summary-grid {{ display:grid; grid-template-columns:1fr; gap:0; margin:16px 0 0; }}
    .summary-item {{ padding:12px 0; border-bottom:1px solid var(--muted); }}
    .summary-item:last-child {{ border-bottom:0; }}
    dt {{ color:var(--muted-text); font-size:13px; }}
    dd {{ margin:2px 0 0; overflow-wrap:anywhere; font-weight:600; font-variant-numeric:tabular-nums; }}
    .section-heading {{ display:flex; gap:16px; align-items:flex-start; justify-content:space-between; margin-bottom:20px; }}
    .section-heading p {{ margin:4px 0 0; }}
    .required-note {{ flex:none; color:var(--danger); font-size:14px; font-weight:700; }}
    .field-grid {{ display:grid; grid-template-columns:1fr; gap:18px; }}
    .field label {{ display:block; margin-bottom:6px; font-weight:700; }}
    .field label span {{ color:var(--danger); }}
    input,textarea {{ width:100%; min-height:48px; padding:11px 12px; border:1px solid #94a3b8; border-radius:9px; background:#fff; color:var(--text); font:inherit; font-size:16px; transition:border-color .18s ease,box-shadow .18s ease; }}
    textarea {{ resize:vertical; }}
    input:focus,textarea:focus,.primary-button:focus-visible {{ outline:3px solid rgba(30,58,138,.24); outline-offset:2px; border-color:var(--ring); }}
    .field-error input,.field-error textarea {{ border:2px solid var(--danger); }}
    .helper {{ min-height:20px; margin:5px 0 0; font-size:13px; color:var(--muted-text); }}
    .alert {{ margin-bottom:20px; padding:16px 18px; border:1px solid #fecaca; border-left:5px solid var(--danger); border-radius:10px; background:var(--danger-bg); color:var(--danger); }}
    .alert p {{ margin:4px 0 0; color:#7f1d1d; }}
    .notice {{ margin:24px 0 16px; padding:15px 16px; border-radius:10px; background:#fff7ed; border:1px solid #fed7aa; }}
    .notice p {{ margin:3px 0 0; color:#7c2d12; }}
    .primary-button {{ display:block; width:100%; min-height:50px; border:0; border-radius:10px; padding:12px 18px; background:var(--primary); color:white; font:inherit; font-weight:700; text-align:center; cursor:pointer; transition:background .18s ease,box-shadow .18s ease; touch-action:manipulation; }}
    .primary-button:hover {{ background:var(--primary-hover); }}
    .primary-button:disabled {{ opacity:.55; cursor:wait; }}
    .link-button {{ text-decoration:none; margin-top:22px; }}
    .success-card,.message-card {{ text-align:center; padding:36px 24px; }}
    .success-mark {{ width:56px; height:56px; margin:0 auto 16px; display:grid; place-items:center; border-radius:50%; background:#dcfce7; color:var(--success); font-size:30px; font-weight:700; }}
    .footnote {{ margin:18px 0 0; font-size:13px; }}
    .sr-only {{ position:absolute; width:1px; height:1px; padding:0; margin:-1px; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }}
    @media (min-width:700px) {{ .shell {{ padding-top:56px; }} .summary-grid,.field-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); column-gap:22px; }} .field-wide {{ grid-column:1 / -1; }} }}
    @media (prefers-reduced-motion:reduce) {{ *,*::before,*::after {{ scroll-behavior:auto!important; transition:none!important; }} }}
  </style>
</head>
<body>{body}</body>
</html>"""
