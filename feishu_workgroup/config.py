from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AppCredentials:
    app_id: str
    app_secret: str

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.app_secret)


@dataclass(frozen=True)
class Config:
    material_app: AppCredentials
    draft_app: AppCredentials
    data_dir: Path
    host: str
    port: int
    public_base_url: str
    extractor_api_url: str
    extractor_review_url: str
    generator_api_url: str
    allowed_chat_ids: frozenset[str]
    poll_seconds: float
    max_wait_seconds: int
    local_mode: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        allowed = frozenset(
            item.strip()
            for item in os.environ.get("FEISHU_ALLOWED_CHAT_IDS", "").split(",")
            if item.strip()
        )
        data_dir = Path(
            os.environ.get("FEISHU_WORKGROUP_DATA_DIR", BASE_DIR / "feishu_workgroup" / "data")
        ).resolve()
        return cls(
            material_app=AppCredentials(
                os.environ.get("FEISHU_MATERIAL_APP_ID", "").strip(),
                os.environ.get("FEISHU_MATERIAL_APP_SECRET", "").strip(),
            ),
            draft_app=AppCredentials(
                os.environ.get("FEISHU_DRAFT_APP_ID", "").strip(),
                os.environ.get("FEISHU_DRAFT_APP_SECRET", "").strip(),
            ),
            data_dir=data_dir,
            host=os.environ.get("FEISHU_WORKGROUP_HOST", "127.0.0.1"),
            port=int(os.environ.get("FEISHU_WORKGROUP_PORT", "8788")),
            public_base_url=os.environ.get(
                "FEISHU_WORKGROUP_PUBLIC_URL", "http://127.0.0.1:8788"
            ).rstrip("/"),
            extractor_api_url=os.environ.get(
                "FEISHU_EXTRACTOR_API_URL", "http://127.0.0.1:8777"
            ).rstrip("/"),
            extractor_review_url=os.environ.get(
                "FEISHU_EXTRACTOR_REVIEW_URL", "http://127.0.0.1:8777"
            ).rstrip("/"),
            generator_api_url=os.environ.get(
                "FEISHU_GENERATOR_API_URL", "http://127.0.0.1:8765"
            ).rstrip("/"),
            allowed_chat_ids=allowed,
            poll_seconds=float(os.environ.get("FEISHU_JOB_POLL_SECONDS", "2")),
            max_wait_seconds=int(os.environ.get("FEISHU_JOB_MAX_WAIT_SECONDS", "1800")),
            local_mode=os.environ.get("FEISHU_WORKGROUP_LOCAL_MODE", "").strip().lower()
            in {"1", "true", "yes", "on"},
        )

    def missing_live_settings(self) -> list[str]:
        missing: list[str] = []
        if not self.material_app.configured:
            missing.append("FEISHU_MATERIAL_APP_ID / FEISHU_MATERIAL_APP_SECRET")
        if not self.draft_app.configured:
            missing.append("FEISHU_DRAFT_APP_ID / FEISHU_DRAFT_APP_SECRET")
        if not self.local_mode and not self.public_base_url.startswith("https://"):
            missing.append("FEISHU_WORKGROUP_PUBLIC_URL（正式使用需为 HTTPS）")
        return missing
