import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from src.core.openclaw_state_db import (
    AUDIT_LOG_COLUMNS,
    OpenClawStateDB,
    TASK_HISTORY_COLUMNS,
    TARGET_SCHEMA_VERSION,
    WORKER_HEALTH_COLUMNS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_agent_factory():
    script_path = REPO_ROOT / "scripts" / "agent_factory.py"
    module_name = "test_agent_factory_module"
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_state_db_schema_contains_task_worker_and_audit_tables(tmp_path: Path):
    db_path = tmp_path / "state.db"
    state_db = OpenClawStateDB(db_path)

    snapshot = state_db.schema_snapshot()

    assert snapshot["db_path"] == str(db_path)
    assert snapshot["user_version"] == TARGET_SCHEMA_VERSION
    assert snapshot["tables"]["task_history"] == list(TASK_HISTORY_COLUMNS)
    assert snapshot["tables"]["worker_health"] == list(WORKER_HEALTH_COLUMNS)
    assert snapshot["tables"]["audit_log"] == list(AUDIT_LOG_COLUMNS)

    second_snapshot = state_db.ensure_schema()
    assert second_snapshot["tables"]["task_history"] == list(TASK_HISTORY_COLUMNS)
    assert second_snapshot["tables"]["worker_health"] == list(WORKER_HEALTH_COLUMNS)
    assert second_snapshot["tables"]["audit_log"] == list(AUDIT_LOG_COLUMNS)


def test_migrate_state_db_script_is_idempotent(tmp_path: Path):
    db_path = tmp_path / "state.db"
    script_path = REPO_ROOT / "scripts" / "ops" / "migrate_state_db.py"

    first = subprocess.run(
        [sys.executable, str(script_path), "--db-path", str(db_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    second = subprocess.run(
        [sys.executable, str(script_path), "--db-path", str(db_path)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr

    first_payload = json.loads(first.stdout)
    second_payload = json.loads(second.stdout)
    assert first_payload["user_version"] == TARGET_SCHEMA_VERSION
    assert second_payload["tables"]["task_history"] == list(TASK_HISTORY_COLUMNS)
    assert second_payload["tables"]["worker_health"] == list(WORKER_HEALTH_COLUMNS)
    assert second_payload["tables"]["audit_log"] == list(AUDIT_LOG_COLUMNS)


def test_state_db_appends_structured_audit_log_rows(tmp_path: Path):
    db_path = tmp_path / "state.db"
    state_db = OpenClawStateDB(db_path)
    entry = {
        "timestamp": "2026-04-23T21:00:00+00:00",
        "agent_id": "unit-test-agent",
        "trade_details": {"symbol": "NVDA", "side": "buy", "qty": 1},
        "decision": "approved",
        "reason": "User approved: 'inline_button_yes'",
    }

    state_db.append_audit_log(
        event_type="approval_decision",
        timestamp=entry["timestamp"],
        agent_id=entry["agent_id"],
        decision=entry["decision"],
        reason=entry["reason"],
        entry=entry,
    )

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT event_type, timestamp, agent_id, decision, reason, entry_json
            FROM audit_log
            ORDER BY audit_id
            """
        ).fetchone()

    assert row[0] == "approval_decision"
    assert row[1] == entry["timestamp"]
    assert row[2] == entry["agent_id"]
    assert row[3] == entry["decision"]
    assert row[4] == entry["reason"]
    assert json.loads(row[5]) == entry


def test_state_db_migrates_legacy_trade_approval_rows_into_audit_log(tmp_path: Path):
    db_path = tmp_path / "state.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA user_version = 3")
        conn.execute(
            """
            CREATE TABLE trade_approval_audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                approval_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                requesting_agent TEXT,
                decision TEXT,
                reason TEXT,
                fail_closed_trigger TEXT,
                approved INTEGER,
                trade_details_json TEXT NOT NULL,
                metadata_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO trade_approval_audit (
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy-approval-1",
                "approval_decision",
                "2026-04-24T00:00:00Z",
                "legacy-agent",
                "approved",
                "legacy reason",
                None,
                1,
                json.dumps({"symbol": "NVDA", "side": "buy"}),
                json.dumps({"source": "legacy-test"}),
            ),
        )
        conn.commit()

    state_db = OpenClawStateDB(db_path)
    state_db.ensure_schema()

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT event_type, timestamp, agent_id, decision, reason, entry_json
            FROM audit_log
            ORDER BY audit_id
            """
        ).fetchall()

    assert len(rows) == 1
    assert rows[0][0] == "approval_decision"
    assert rows[0][1] == "2026-04-24T00:00:00Z"
    assert rows[0][2] == "legacy-agent"
    assert rows[0][3] == "approved"
    assert rows[0][4] == "legacy reason"

    entry = json.loads(rows[0][5])
    assert entry["schema_version"] == "trade_approval_audit.v1"
    assert entry["approval_id"] == "legacy-approval-1"
    assert entry["requesting_agent"] == "legacy-agent"
    assert entry["trade_details"] == {"symbol": "NVDA", "side": "buy"}
    assert entry["metadata"] == {"source": "legacy-test"}


def test_agent_factory_control_flags_use_shared_control_snapshot(tmp_path: Path, monkeypatch):
    module = _load_agent_factory()
    calls: list[Path] = []

    def fake_snapshot(repo_root: Path) -> dict[str, bool]:
        calls.append(repo_root)
        return {
            "manual_veto": True,
            "kill_switch": False,
        }

    monkeypatch.setenv("OPENCLAW_ENABLE_STRATEGY_EXECUTOR", "1")
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "read_control_state_snapshot", fake_snapshot)

    assert module.control_flags() == {
        "manual_veto": True,
        "kill_switch": False,
        "strategy_executor_enabled": True,
    }
    assert calls == [tmp_path]


def test_agent_factory_proposal_reviews_are_advisory_only(tmp_path: Path, monkeypatch):
    module = _load_agent_factory()
    repo_root = tmp_path
    reports_dir = repo_root / "reports" / "openclaw_research"
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / "replay_latest.json").write_text(
        json.dumps(
            {
                "pass_rate": 0.67,
                "avg_confidence": 0.61,
                "correlation_break_count": 2,
                "shadow_execution_blocked_count": 3,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module,
        "control_flags",
        lambda: {
            "manual_veto": False,
            "kill_switch": False,
            "strategy_executor_enabled": False,
        },
    )

    task = module.Task(
        task_id="proposal-review-1",
        kind="proposal_review",
        payload={
            "repo_root": str(repo_root),
            "review_type": "replay_pass_rate_low",
            "source": "unit-test",
        },
    )

    result = module.run_proposal_review(task)

    assert result.success is True
    advisory_path = Path(result.data["advisory_json"])
    assert advisory_path.exists()

    advisory = json.loads(advisory_path.read_text(encoding="utf-8"))
    assert advisory["advisory_only"] is True
    assert advisory["not_for_direct_execution"] is True
    assert advisory["research_only"] is True
    assert advisory["guardrails"] == {
        "advisory_only": True,
        "staging_only": True,
        "requires_human_approval": True,
        "manual_approval_required": True,
        "paper_only": True,
        "live_execution_forbidden": True,
        "execution_enabled": False,
        "no_live_orders": True,
        "no_promotion_authority": True,
    }
    assert advisory["context"] == {
        "replay_pass_rate": 0.67,
        "replay_avg_confidence": 0.61,
        "replay_correlation_break_count": 2,
        "replay_shadow_execution_blocked_count": 3,
    }


def test_agent_factory_records_task_history_and_worker_health(tmp_path: Path, monkeypatch):
    module = _load_agent_factory()
    repo_root = tmp_path
    log_dir = repo_root / "logs"
    reports_dir = repo_root / "reports"
    control_dir = repo_root / "control"
    staging_dir = repo_root / "config" / "staging"
    ops_reports = reports_dir / "openclaw_ops"
    research_reports = reports_dir / "openclaw_research"

    for path in [log_dir, reports_dir, control_dir, staging_dir, ops_reports, research_reports]:
        path.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENCLAW_MIN_WORKERS", "0")
    monkeypatch.setenv("OPENCLAW_STATE_DB_PATH", str(repo_root / "state.db"))
    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(module, "LOG_DIR", log_dir)
    monkeypatch.setattr(module, "REPORTS_DIR", reports_dir)
    monkeypatch.setattr(module, "CONTROL_DIR", control_dir)
    monkeypatch.setattr(module, "STAGING_DIR", staging_dir)
    monkeypatch.setattr(module, "OPENCLAW_OPS_REPORTS", ops_reports)
    monkeypatch.setattr(module, "OPENCLAW_RESEARCH_REPORTS", research_reports)

    bot = module.OpenClawBot("ops", {})
    task = module.Task(task_id="task-1", kind="monitoring_alerting", payload={})
    started_at = "2026-04-23T21:00:00Z"
    bot._mark_task_started(task, "ops-worker-1", started_at)
    result = module.AgentResult(task_id=task.task_id, success=True, summary="health ok")
    bot._mark_task_result(task, result, "ops-worker-1", started_at)
    bot._dead_letter(
        module.Task(task_id="task-2", kind="safety_audit", payload={}),
        reason="ttl_expired",
    )

    with sqlite3.connect(repo_root / "state.db") as conn:
        task_row = conn.execute(
            """
            SELECT task_id, worker, status, started_at, completed_at, output_summary
            FROM task_history
            WHERE task_id = 'task-1'
            """
        ).fetchone()
        dead_row = conn.execute(
            """
            SELECT task_id, worker, status, output_summary
            FROM task_history
            WHERE task_id = 'task-2'
            """
        ).fetchone()
        worker_row = conn.execute(
            """
            SELECT worker_id, status, current_task
            FROM worker_health
            WHERE worker_id = 'ops-worker-1'
            """
        ).fetchone()

    assert task_row[0] == "task-1"
    assert task_row[1] == "ops-worker-1"
    assert task_row[2] == "completed"
    assert task_row[3] == started_at
    assert task_row[4] is not None
    assert task_row[5] == "health ok"

    assert dead_row[0] == "task-2"
    assert dead_row[1] is None
    assert dead_row[2] == "dead_letter"
    assert dead_row[3] == "ttl_expired"

    assert worker_row[0] == "ops-worker-1"
    assert worker_row[1] == "idle"
    assert worker_row[2] is None
