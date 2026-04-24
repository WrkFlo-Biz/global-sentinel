from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Optional

TARGET_SCHEMA_VERSION = 3

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

AUDIT_LOG_COLUMNS = (
    "audit_id",
    "event_type",
    "timestamp",
    "agent_id",
    "decision",
    "reason",
    "entry_json",
)


def default_state_db_path(repo_root: Path) -> Path:
    override = os.getenv("OPENCLAW_STATE_DB_PATH")
    if override:
        return Path(override).expanduser()
    return repo_root / "state.db"


def ensure_audit_log_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            agent_id TEXT,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL,
            entry_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_event_type
        ON audit_log(event_type)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_timestamp
        ON audit_log(timestamp)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_agent_id
        ON audit_log(agent_id)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_audit_log_decision
        ON audit_log(decision)
        """
    )


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table' AND name = ?
        LIMIT 1
        """,
        (table_name,),
    ).fetchone()
    return row is not None


def _load_json_text(raw: Any, *, fallback: Any) -> Any:
    if raw in (None, ""):
        return fallback
    try:
        return json.loads(str(raw))
    except (TypeError, ValueError):
        return fallback


def migrate_legacy_trade_approval_audit(conn: sqlite3.Connection) -> None:
    if not _table_exists(conn, "trade_approval_audit"):
        return

    rows = conn.execute(
        """
        SELECT
            approval_id,
            event_type,
            timestamp,
            requesting_agent,
            decision,
            reason,
            fail_closed_trigger,
            approved,
            trade_details_json,
            metadata_json
        FROM trade_approval_audit
        ORDER BY event_id
        """
    ).fetchall()

    for row in rows:
        trade_details = _load_json_text(row["trade_details_json"], fallback={})
        if not isinstance(trade_details, dict):
            trade_details = {"raw_trade_details": trade_details}

        metadata = _load_json_text(row["metadata_json"], fallback=None)
        if metadata is not None and not isinstance(metadata, dict):
            metadata = {"raw_metadata": metadata}

        entry = {
            "schema_version": "trade_approval_audit.v1",
            "approval_id": str(row["approval_id"] or ""),
            "event_type": str(row["event_type"] or ""),
            "timestamp": str(row["timestamp"] or ""),
            "agent_id": str(row["requesting_agent"] or ""),
            "requesting_agent": str(row["requesting_agent"] or ""),
            "trade_details": trade_details,
            "order": trade_details,
            "decision": str(row["decision"] or ""),
            "reason": str(row["reason"] or ""),
            "approved": None if row["approved"] is None else bool(row["approved"]),
            "fail_closed_trigger": row["fail_closed_trigger"],
            "metadata": metadata,
        }
        entry_json = json.dumps(entry, sort_keys=True, default=str)

        existing = conn.execute(
            "SELECT 1 FROM audit_log WHERE entry_json = ? LIMIT 1",
            (entry_json,),
        ).fetchone()
        if existing is not None:
            continue

        conn.execute(
            """
            INSERT INTO audit_log (
                event_type,
                timestamp,
                agent_id,
                decision,
                reason,
                entry_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                str(row["event_type"] or ""),
                str(row["timestamp"] or ""),
                str(row["requesting_agent"] or ""),
                str(row["decision"] or ""),
                str(row["reason"] or ""),
                entry_json,
            ),
        )


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
            ensure_audit_log_schema(conn)
            migrate_legacy_trade_approval_audit(conn)
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
                    "audit_log": self._table_columns(conn, "audit_log"),
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

    def append_audit_log(
        self,
        *,
        event_type: str,
        timestamp: str,
        agent_id: Optional[str],
        decision: str,
        reason: str,
        entry: Dict[str, Any],
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_log (
                    event_type,
                    timestamp,
                    agent_id,
                    decision,
                    reason,
                    entry_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    timestamp,
                    agent_id,
                    decision,
                    reason,
                    json.dumps(entry, sort_keys=True, default=str),
                ),
            )
            conn.commit()
