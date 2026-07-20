from __future__ import annotations

import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


ACTIVE_STATUSES = {
    "collecting",
    "extracting",
    "review_step1",
    "handoff",
    "supplementing",
    "generating",
}


@dataclass(frozen=True)
class CaseRecord:
    id: str
    chat_id: str
    created_by: str
    status: str
    access_token: str
    case_dir: str
    archive_path: str
    workbook_path: str
    extractor_job_id: str
    extractor_result: dict[str, Any]
    generator_fields: dict[str, Any]
    docx_path: str
    warnings: list[str]
    error: str
    created_at: float
    updated_at: float

    @property
    def directory(self) -> Path:
        return Path(self.case_dir)


class CaseStore:
    _UPDATABLE = {
        "status",
        "archive_path",
        "workbook_path",
        "extractor_job_id",
        "extractor_result_json",
        "generator_fields_json",
        "docx_path",
        "warnings_json",
        "error",
    }

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.case_root = data_dir / "cases"
        self.database_path = data_dir / "workgroup.sqlite3"
        self.case_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    status TEXT NOT NULL,
                    access_token TEXT NOT NULL,
                    case_dir TEXT NOT NULL,
                    archive_path TEXT NOT NULL DEFAULT '',
                    workbook_path TEXT NOT NULL DEFAULT '',
                    extractor_job_id TEXT NOT NULL DEFAULT '',
                    extractor_result_json TEXT NOT NULL DEFAULT '{}',
                    generator_fields_json TEXT NOT NULL DEFAULT '{}',
                    docx_path TEXT NOT NULL DEFAULT '',
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    error TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cases_chat_updated
                    ON cases(chat_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    processed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    FOREIGN KEY(case_id) REFERENCES cases(id)
                );
                """
            )

    def create_case(self, chat_id: str, created_by: str) -> CaseRecord:
        now = time.time()
        case_id = f"FS-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
        case_dir = self.case_root / case_id
        case_dir.mkdir(parents=True, exist_ok=False)
        token = secrets.token_urlsafe(24)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO cases (
                    id, chat_id, created_by, status, access_token, case_dir,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'collecting', ?, ?, ?, ?)
                """,
                (case_id, chat_id, created_by, token, str(case_dir), now, now),
            )
            self._append_audit(connection, case_id, created_by, "case_created", {})
        return self.get_case(case_id)

    def get_case(self, case_id: str) -> CaseRecord:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            raise KeyError(f"案件不存在：{case_id}")
        return self._to_record(row)

    def get_active_case(self, chat_id: str) -> CaseRecord | None:
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        params = [chat_id, *sorted(ACTIVE_STATUSES)]
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT * FROM cases
                WHERE chat_id = ? AND status IN ({placeholders})
                ORDER BY updated_at DESC LIMIT 1
                """,
                params,
            ).fetchone()
        return self._to_record(row) if row is not None else None

    def update_case(self, case_id: str, actor_id: str = "system", action: str = "case_updated", **values: Any) -> CaseRecord:
        unknown = set(values) - self._UPDATABLE
        if unknown:
            raise ValueError(f"不允许更新字段：{', '.join(sorted(unknown))}")
        if not values:
            return self.get_case(case_id)
        values["updated_at"] = time.time()
        assignments = ", ".join(f"{name} = ?" for name in values)
        with self._connect() as connection:
            cursor = connection.execute(
                f"UPDATE cases SET {assignments} WHERE id = ?",
                [*values.values(), case_id],
            )
            if cursor.rowcount != 1:
                raise KeyError(f"案件不存在：{case_id}")
            detail = {
                key: value
                for key, value in values.items()
                if key not in {"extractor_result_json", "generator_fields_json", "updated_at"}
            }
            self._append_audit(connection, case_id, actor_id, action, detail)
        return self.get_case(case_id)

    def save_extractor_result(self, case_id: str, result: dict[str, Any]) -> CaseRecord:
        return self.update_case(
            case_id,
            action="extraction_completed",
            extractor_result_json=json.dumps(result, ensure_ascii=False),
            status="review_step1",
            error="",
        )

    def rotate_access_token(self, case_id: str, actor_id: str = "system") -> CaseRecord:
        token = secrets.token_urlsafe(24)
        updated_at = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE cases SET access_token = ?, updated_at = ? WHERE id = ?",
                (token, updated_at, case_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"案件不存在：{case_id}")
            self._append_audit(
                connection,
                case_id,
                actor_id,
                "access_token_rotated",
                {},
            )
        return self.get_case(case_id)

    def save_generator_fields(
        self, case_id: str, fields: dict[str, Any], actor_id: str
    ) -> CaseRecord:
        return self.update_case(
            case_id,
            actor_id=actor_id,
            action="step1_approved",
            generator_fields_json=json.dumps(fields, ensure_ascii=False),
            status="supplementing",
            error="",
        )

    def mark_event(self, event_id: str) -> bool:
        if not event_id:
            return True
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO processed_events(event_id, processed_at) VALUES (?, ?)",
                    (event_id, time.time()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def append_audit(
        self, case_id: str, actor_id: str, action: str, detail: dict[str, Any] | None = None
    ) -> None:
        with self._connect() as connection:
            self._append_audit(connection, case_id, actor_id, action, detail or {})

    @staticmethod
    def _append_audit(
        connection: sqlite3.Connection,
        case_id: str,
        actor_id: str,
        action: str,
        detail: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log(case_id, actor_id, action, detail_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (case_id, actor_id, action, json.dumps(detail, ensure_ascii=False), time.time()),
        )

    @staticmethod
    def _to_record(row: sqlite3.Row) -> CaseRecord:
        return CaseRecord(
            id=row["id"],
            chat_id=row["chat_id"],
            created_by=row["created_by"],
            status=row["status"],
            access_token=row["access_token"],
            case_dir=row["case_dir"],
            archive_path=row["archive_path"],
            workbook_path=row["workbook_path"],
            extractor_job_id=row["extractor_job_id"],
            extractor_result=json.loads(row["extractor_result_json"] or "{}"),
            generator_fields=json.loads(row["generator_fields_json"] or "{}"),
            docx_path=row["docx_path"],
            warnings=json.loads(row["warnings_json"] or "[]"),
            error=row["error"],
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
        )
