from __future__ import annotations

import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Optional

TARGET_SCHEMA_VERSION = 2

TASK_HISTORY_COLUMNS = (
    "task_id",
    "worker",
    "status",
    "started_at",
    "completed_at",
    "output_summary",
)

WORKER_HEALTH_COLUMNS = (
    "worker_id",
    "last_seen",
    "status",
    "current_task",
)


def default_state_db_path(repo_root: Path) -> Path:
    override = os.getenv("OPENCLAW_STATE_DB_PATH")
    if override:
        return Path(override).expanduser()
    return repo_root / "state.db"


class OpenClawStateDB:
    """Small SQLite helper for task and worker runtime state."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def ensure_schema(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS task_history (
                    task_id TEXT PRIMARY KEY,
                    worker TEXT,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    output_summary TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_health (
                    worker_id TEXT PRIMARY KEY,
                    last_seen TEXT NOT NULL,
                    status TEXT NOT NULL,
                    current_task TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_history_worker ON task_history(worker)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_history_status ON task_history(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_worker_health_status ON worker_health(status)"
            )
            conn.execute(f"PRAGMA user_version = {TARGET_SCHEMA_VERSION}")
            conn.commit()
        return self.schema_snapshot()

    def schema_snapshot(self) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            user_version = conn.execute("PRAGMA user_version").fetchone()[0]
            return {
                "db_path": str(self.db_path),
                "user_version": user_version,
                "target_schema_version": TARGET_SCHEMA_VERSION,
                "tables": {
                    "task_history": self._table_columns(conn, "task_history"),
                    "worker_health": self._table_columns(conn, "worker_health"),
                },
            }

    def _table_columns(self, conn: sqlite3.Connection, table: str) -> list[str]:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return [str(row["name"]) for row in rows]

    def record_task_start(
        self,
        *,
        task_id: str,
        worker: Optional[str],
        started_at: str,
        status: str = "running",
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_history (
                    task_id, worker, status, started_at, completed_at, output_summary
                ) VALUES (?, ?, ?, ?, NULL, NULL)
                ON CONFLICT(task_id) DO UPDATE SET
                    worker = excluded.worker,
                    status = excluded.status,
                    started_at = excluded.started_at,
                    completed_at = NULL,
                    output_summary = NULL
                """,
                (task_id, worker, status, started_at),
            )
            conn.commit()

    def record_task_status(
        self,
        *,
        task_id: str,
        worker: Optional[str],
        status: str,
        started_at: Optional[str] = None,
        completed_at: Optional[str] = None,
        output_summary: Optional[str] = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO task_history (
                    task_id, worker, status, started_at, completed_at, output_summary
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    worker = COALESCE(excluded.worker, task_history.worker),
                    status = excluded.status,
                    started_at = COALESCE(excluded.started_at, task_history.started_at),
                    completed_at = excluded.completed_at,
                    output_summary = excluded.output_summary
                """,
                (task_id, worker, status, started_at, completed_at, output_summary),
            )
            conn.commit()

    def update_worker_health(
        self,
        *,
        worker_id: str,
        last_seen: str,
        status: str,
        current_task: Optional[str],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO worker_health (
                    worker_id, last_seen, status, current_task
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    status = excluded.status,
                    current_task = excluded.current_task
                """,
                (worker_id, last_seen, status, current_task),
            )
            conn.commit()
