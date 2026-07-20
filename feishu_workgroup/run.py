from __future__ import annotations

import argparse
import json
import os
import threading
from pathlib import Path

import requests

from .clients import ExtractorClient, FeishuRestClient, GeneratorClient
from .config import BASE_DIR, Config
from .orchestrator import WorkgroupOrchestrator
from .store import CaseStore
from .web import build_server


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def build_orchestrator(config: Config) -> WorkgroupOrchestrator:
    store = CaseStore(config.data_dir)
    return WorkgroupOrchestrator(
        config=config,
        store=store,
        material_bot=FeishuRestClient(config.material_app),
        draft_bot=FeishuRestClient(config.draft_app),
        extractor=ExtractorClient(config.extractor_api_url),
        generator=GeneratorClient(config.generator_api_url),
    )


def start_web_server(config: Config, orchestrator: WorkgroupOrchestrator):
    server = build_server(config.host, config.port, orchestrator)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="feishu-workgroup-web")
    thread.start()
    print(f"飞书工作群补充页已启动：http://{config.host}:{config.port}/health")
    return server


def check_services(config: Config) -> bool:
    ok = True
    for name, url in (
        ("材料提取服务", f"{config.extractor_api_url}/api/health"),
        ("律书生成服务", f"{config.generator_api_url}/api/health"),
    ):
        try:
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            print(f"[正常] {name}：{url}")
        except Exception as exc:
            ok = False
            print(f"[异常] {name}：{url}（{exc}）")
    missing = config.missing_live_settings()
    for item in missing:
        print(f"[待配置] {item}")
    return ok and not missing


def start_feishu_listener(config: Config, orchestrator: WorkgroupOrchestrator) -> None:
    try:
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTrigger,
            P2CardActionTriggerResponse,
        )
    except ImportError as exc:
        raise SystemExit(
            "缺少飞书官方 SDK。请先运行：python -m pip install -r requirements-feishu.txt"
        ) from exc

    def parse_sdk_payload(data) -> dict:
        raw = lark.JSON.marshal(data)
        return json.loads(raw) if isinstance(raw, str) else raw

    def on_message(data: P2ImMessageReceiveV1) -> None:
        orchestrator.submit_message(parse_sdk_payload(data))

    def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        response = orchestrator.submit_card_action(parse_sdk_payload(data))
        return P2CardActionTriggerResponse(response)

    event_handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .register_p2_card_action_trigger(on_card_action)
        .build()
    )
    client = lark.ws.Client(
        config.material_app.app_id,
        config.material_app.app_secret,
        event_handler=event_handler,
        log_level=lark.LogLevel.INFO,
    )
    print("正在连接飞书长连接；连接成功后即可在内部群使用 AI 材料专员。")
    client.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="飞书双 AI 员工第一版")
    parser.add_argument("--check", action="store_true", help="仅检查配置和两套现有服务")
    parser.add_argument("--web-only", action="store_true", help="只启动补充信息页面，不连接飞书")
    args = parser.parse_args()

    load_env_file(BASE_DIR / ".env.feishu")
    config = Config.from_env()
    if args.check:
        raise SystemExit(0 if check_services(config) else 1)

    orchestrator = build_orchestrator(config)
    server = start_web_server(config, orchestrator)
    try:
        if args.web_only:
            print("当前为 web-only 模式，按 Ctrl+C 停止。")
            threading.Event().wait()
        else:
            missing = config.missing_live_settings()
            if missing:
                raise SystemExit("飞书正式运行仍缺少配置：\n- " + "\n- ".join(missing))
            start_feishu_listener(config, orchestrator)
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
