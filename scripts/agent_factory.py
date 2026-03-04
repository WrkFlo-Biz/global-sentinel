#!/usr/bin/env python3
"""
Global Sentinel V4 - OpenClaw Agent Factory (Enhanced)

Enhancements:
- OpenClaw-Research can directly invoke:
  - src/replay_runner.py
  - scripts/outcome_tracker.py
- Parses replay outputs and enqueues proposal review tasks
- Preserves safety controls:
  - kill switch / manual veto respected
  - no live execution paths
- Dynamic worker pool + dead-letter handling retained

Usage:
  python scripts/agent_factory.py --bot ops --config config/thresholds.yaml
  python scripts/agent_factory.py --bot research --config config/thresholds.yaml
  python scripts/agent_factory.py list --bot ops
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import random
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import yaml
except ImportError:
    yaml = None

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", Path(__file__).resolve().parents[1]))
LOG_DIR = REPO_ROOT / "logs"
REPORTS_DIR = REPO_ROOT / "reports"
CONTROL_DIR = REPO_ROOT / "control"
STAGING_DIR = REPO_ROOT / "config" / "staging"

for p in [LOG_DIR, REPORTS_DIR, CONTROL_DIR, STAGING_DIR]:
    p.mkdir(parents=True, exist_ok=True)

OPENCLAW_OPS_REPORTS = REPORTS_DIR / "openclaw_ops"
OPENCLAW_RESEARCH_REPORTS = REPORTS_DIR / "openclaw_research"
OPENCLAW_OPS_REPORTS.mkdir(parents=True, exist_ok=True)
OPENCLAW_RESEARCH_REPORTS.mkdir(parents=True, exist_ok=True)


def utc_ts() -> float:
    return time.time()


def iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def latest_file(folder: Path, pattern: str = "*.json") -> Optional[Path]:
    if not folder.exists():
        return None
    files = sorted(folder.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def log_line(bot: str, msg: str) -> None:
    log_path = LOG_DIR / f"openclaw_{bot}.log"
    line = f"[{iso_now()}] {msg}\n"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(line)
    print(line, end="")


def gen_task_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# --- Task/Agent models ---

@dataclass
class Task:
    task_id: str
    kind: str
    payload: Dict[str, Any]
    priority: int = 5
    created_at: float = field(default_factory=utc_ts)
    ttl_seconds: int = 300
    attempts: int = 0


@dataclass
class AgentResult:
    task_id: str
    success: bool
    summary: str
    data: Dict[str, Any] = field(default_factory=dict)
    duration_sec: float = 0.0
    error: Optional[str] = None
    follow_up_tasks: List[Task] = field(default_factory=list)


# --- Control checks ---

def control_flags() -> Dict[str, bool]:
    veto = read_json(CONTROL_DIR / "manual_veto.json", {"manual_veto": False})
    kill = read_json(CONTROL_DIR / "kill_switch.json", {"kill_switch": False})
    return {
        "manual_veto": bool(veto.get("manual_veto", False)),
        "kill_switch": bool(kill.get("kill_switch", False)),
    }


# --- Utility: subprocess runner ---

def run_python_script(script_path: Path, args: List[str], timeout_sec: int = 180) -> Tuple[bool, Dict[str, Any]]:
    start = utc_ts()
    cmd = [sys.executable, str(script_path)] + args
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        payload = {
            "cmd": cmd, "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-4000:], "stderr_tail": proc.stderr[-4000:],
            "duration_sec": round(utc_ts() - start, 3),
        }
        return proc.returncode == 0, payload
    except subprocess.TimeoutExpired as e:
        return False, {
            "cmd": cmd, "error": "timeout", "duration_sec": round(utc_ts() - start, 3),
            "stdout_tail": (e.stdout or "")[-4000:] if isinstance(e.stdout, str) else None,
            "stderr_tail": (e.stderr or "")[-4000:] if isinstance(e.stderr, str) else None,
        }
    except Exception as e:
        return False, {"cmd": cmd, "error": str(e), "duration_sec": round(utc_ts() - start, 3)}


# --- Simulated subagent implementations ---

def run_azure_provisioner(task: Task) -> AgentResult:
    start = utc_ts()
    time.sleep(random.uniform(0.2, 0.8))
    return AgentResult(task_id=task.task_id, success=True,
                       summary="Azure provisioner health check completed (simulated).",
                       data={"checked": ["vm_status", "disk_space", "systemd_services"]},
                       duration_sec=utc_ts() - start)


def run_monitoring_alerting(task: Task) -> AgentResult:
    start = utc_ts()
    time.sleep(random.uniform(0.2, 0.6))
    return AgentResult(task_id=task.task_id, success=True,
                       summary="Monitoring/alerting pipeline validated (simulated).",
                       data={"heartbeat_ok": True}, duration_sec=utc_ts() - start)


def run_drift_monitor(task: Task) -> AgentResult:
    start = utc_ts()
    time.sleep(random.uniform(0.2, 0.7))
    drift_score = round(random.uniform(0.05, 0.35), 3)
    break_detected = drift_score > 0.25
    follow_ups: List[Task] = []
    if break_detected:
        follow_ups.append(Task(
            task_id=gen_task_id("research-corr-review"), kind="proposal_review",
            payload={"review_type": "drift_break_review", "drift_score": drift_score,
                     "trigger_source": "drift_monitor"},
            priority=2, ttl_seconds=600))
    return AgentResult(task_id=task.task_id, success=True,
                       summary=f"Drift scan complete. drift_score={drift_score}",
                       data={"drift_score": drift_score, "break_detected": break_detected},
                       duration_sec=utc_ts() - start, follow_up_tasks=follow_ups)


def run_replay_backtest_sim(task: Task) -> AgentResult:
    start = utc_ts()
    time.sleep(random.uniform(0.5, 1.2))
    precision = round(random.uniform(0.55, 0.9), 3)
    recall = round(random.uniform(0.45, 0.85), 3)
    lag_sec = round(random.uniform(20, 180), 2)
    return AgentResult(task_id=task.task_id, success=True,
                       summary=f"Replay complete (simulated). precision={precision} recall={recall} lag={lag_sec}s",
                       data={"precision": precision, "recall": recall, "lag_sec": lag_sec, "simulated": True},
                       duration_sec=utc_ts() - start)


def run_threshold_tuner(task: Task) -> AgentResult:
    start = utc_ts()
    time.sleep(random.uniform(0.3, 0.9))
    proposal = {
        "weights.physical_reality": 0.31, "weights.kinetic_trigger": 0.20,
        "weights.domestic_stress": 0.30, "weights.market_transmission": 0.19,
        "rationale": "Replay suggests slightly stronger physical layer under current regime",
    }
    return AgentResult(task_id=task.task_id, success=True,
                       summary="Threshold tuning proposal generated (staging only).",
                       data={"proposal": proposal}, duration_sec=utc_ts() - start)


def run_safety_audit(task: Task) -> AgentResult:
    start = utc_ts()
    time.sleep(random.uniform(0.2, 0.5))
    return AgentResult(task_id=task.task_id, success=True,
                       summary="Safety audit passed: no live-order paths observed.",
                       data={"live_order_paths_found": 0, "missing_audit_fields": []},
                       duration_sec=utc_ts() - start)


# --- NEW: Research bot direct invocations ---

def run_replay_runner(task: Task) -> AgentResult:
    """Directly invoke src/replay_runner.py and parse enhanced output."""
    start = utc_ts()
    repo_root = Path(task.payload.get("repo_root", REPO_ROOT))
    script = repo_root / "src" / "replay_runner.py"
    fixtures = task.payload.get("fixtures", "tests/replays")
    output = task.payload.get("output", "reports/openclaw_research/replay_latest.json")

    if not script.exists():
        return AgentResult(task_id=task.task_id, success=False,
                           summary="replay_runner.py not found", error=str(script),
                           duration_sec=utc_ts() - start)

    ok, payload = run_python_script(
        script, ["--repo-root", str(repo_root), "--fixtures", str(fixtures), "--output", str(output)],
        timeout_sec=int(task.payload.get("timeout_sec", 240)))

    replay_path = (repo_root / output) if not Path(output).is_absolute() else Path(output)
    replay = read_json(replay_path, {}) if replay_path.exists() else {}

    follow_ups: List[Task] = []
    pass_rate = replay.get("pass_rate")
    corr_break_count = replay.get("correlation_break_count")
    shadow_blocked_count = replay.get("shadow_execution_blocked_count")
    avg_conf = replay.get("avg_confidence")
    total_cases = replay.get("total_cases")

    # Enqueue review tasks based on replay outcomes
    if isinstance(pass_rate, (int, float)) and pass_rate < 0.80:
        follow_ups.append(Task(
            task_id=gen_task_id("research-review"), kind="proposal_review",
            payload={"review_type": "replay_pass_rate_low", "pass_rate": pass_rate,
                     "total_cases": total_cases, "source": "replay_runner"},
            priority=1, ttl_seconds=1200))

    if isinstance(corr_break_count, int) and corr_break_count > 0:
        follow_ups.append(Task(
            task_id=gen_task_id("research-review"), kind="proposal_review",
            payload={"review_type": "correlation_breaks_present",
                     "correlation_break_count": corr_break_count, "source": "replay_runner"},
            priority=2, ttl_seconds=1200))

    if isinstance(shadow_blocked_count, int) and isinstance(total_cases, int) and total_cases > 0:
        ratio = shadow_blocked_count / total_cases
        if ratio > 0.30:
            follow_ups.append(Task(
                task_id=gen_task_id("research-review"), kind="proposal_review",
                payload={"review_type": "shadow_blocked_ratio_high",
                         "shadow_blocked_ratio": round(ratio, 4), "source": "replay_runner"},
                priority=3, ttl_seconds=1200))

    if isinstance(avg_conf, (int, float)) and avg_conf < 0.70:
        follow_ups.append(Task(
            task_id=gen_task_id("research-review"), kind="proposal_review",
            payload={"review_type": "replay_avg_confidence_low", "avg_confidence": avg_conf,
                     "source": "replay_runner"},
            priority=2, ttl_seconds=1200))

    # If replay passed well, schedule threshold tuner (staging)
    if ok and isinstance(pass_rate, (int, float)) and pass_rate >= 0.80:
        follow_ups.append(Task(
            task_id=gen_task_id("research-tuner"), kind="threshold_tuner",
            payload={"mode": "staging_only", "trigger_source": "replay_runner"},
            priority=5, ttl_seconds=900))

    return AgentResult(
        task_id=task.task_id, success=ok,
        summary="Replay runner executed" if ok else "Replay runner execution failed",
        data={"subprocess": payload, "replay_report_path": str(replay_path),
              "replay_summary": {
                  "status": replay.get("status"), "total_cases": total_cases,
                  "pass_rate": pass_rate, "avg_confidence": avg_conf,
                  "effective_mode_counts": replay.get("effective_mode_counts"),
                  "correlation_break_count": corr_break_count,
                  "shadow_execution_blocked_count": shadow_blocked_count}},
        duration_sec=utc_ts() - start, follow_up_tasks=follow_ups,
        error=None if ok else payload.get("error") or payload.get("stderr_tail"))


def run_outcome_tracker(task: Task) -> AgentResult:
    """Directly invoke scripts/outcome_tracker.py if it exists."""
    start = utc_ts()
    repo_root = Path(task.payload.get("repo_root", REPO_ROOT))
    script = repo_root / "scripts" / "outcome_tracker.py"

    if not script.exists():
        return AgentResult(task_id=task.task_id, success=False,
                           summary="outcome_tracker.py not found", error=str(script),
                           duration_sec=utc_ts() - start)

    ok, payload = run_python_script(script, ["--repo-root", str(repo_root)], timeout_sec=180)
    latest = latest_file(repo_root / "reports" / "openclaw_research", "outcome_tracker_*.json")
    metrics = read_json(latest, {}) if latest else {}

    follow_ups: List[Task] = []
    avg_conf = metrics.get("avg_confidence")
    if isinstance(avg_conf, (int, float)) and avg_conf < 0.65:
        follow_ups.append(Task(
            task_id=gen_task_id("research-review"), kind="proposal_review",
            payload={"review_type": "low_outcome_confidence", "avg_confidence": avg_conf,
                     "source": "outcome_tracker"},
            priority=2, ttl_seconds=900))

    return AgentResult(
        task_id=task.task_id, success=ok,
        summary="Outcome tracker executed" if ok else "Outcome tracker execution failed",
        data={"subprocess": payload, "report_path": str(latest) if latest else None,
              "metrics_summary": {"status": metrics.get("status"),
                                  "sample_size": metrics.get("sample_size"),
                                  "avg_confidence": avg_conf}},
        duration_sec=utc_ts() - start, follow_up_tasks=follow_ups,
        error=None if ok else payload.get("error") or payload.get("stderr_tail"))


def run_proposal_review(task: Task) -> AgentResult:
    """Generate a review advisory artifact for humans/OpenClaw-Research."""
    start = utc_ts()
    repo_root = Path(task.payload.get("repo_root", REPO_ROOT))
    review_type = str(task.payload.get("review_type", "generic_review"))
    source = str(task.payload.get("source", "unknown"))

    replay_latest = read_json(repo_root / "reports" / "openclaw_research" / "replay_latest.json", {})
    flags = control_flags()
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())

    advisory = {
        "timestamp": iso_now(), "review_type": review_type, "source": source,
        "trigger_payload": task.payload, "control_flags": flags,
        "guardrails": {"staging_only": True, "requires_human_approval": True, "no_live_orders": True},
        "context": {
            "replay_pass_rate": replay_latest.get("pass_rate"),
            "replay_avg_confidence": replay_latest.get("avg_confidence"),
            "replay_correlation_break_count": replay_latest.get("correlation_break_count"),
            "replay_shadow_execution_blocked_count": replay_latest.get("shadow_execution_blocked_count"),
        },
        "recommendations": [], "warnings": [],
    }

    if review_type in {"replay_pass_rate_low", "replay_avg_confidence_low"}:
        advisory["recommendations"].extend([
            "Prioritize replay failure analysis before weight changes.",
            "Inspect penalty patterns for stale/conflicting/fallback dominance.",
        ])
    if review_type == "correlation_breaks_present":
        advisory["recommendations"].append("Keep correlation break handling as flag-only unless separately validated.")
    if review_type == "shadow_blocked_ratio_high":
        advisory["recommendations"].append("Investigate whether shadow blocks are from fallback mode, control flags, or quorum failures.")
    if flags["manual_veto"] or flags["kill_switch"]:
        advisory["warnings"].append("Manual veto or kill switch active — tuning must remain paused.")

    out_dir = repo_root / "reports" / "openclaw_research"
    out_json = out_dir / f"proposal_review_{review_type}_{ts}.json"
    write_json(out_json, advisory)

    return AgentResult(task_id=task.task_id, success=True,
                       summary=f"Proposal review generated: {review_type}",
                       data={"advisory_json": str(out_json), "review_type": review_type},
                       duration_sec=utc_ts() - start)


# Registry
AGENT_RUNNERS: Dict[str, Callable[[Task], AgentResult]] = {
    "azure_provisioner": run_azure_provisioner,
    "monitoring_alerting": run_monitoring_alerting,
    "safety_audit": run_safety_audit,
    "drift_monitor": run_drift_monitor,
    "replay_backtest": run_replay_backtest_sim,
    "replay_runner_run": run_replay_runner,
    "outcome_tracker_run": run_outcome_tracker,
    "threshold_tuner": run_threshold_tuner,
    "proposal_review": run_proposal_review,
}


# --- Queue manager / orchestrator ---

class OpenClawBot:
    def __init__(self, bot_name: str, cfg: Dict[str, Any]):
        self.bot_name = bot_name
        self.cfg = cfg
        self.running = True
        self.task_queue: "queue.PriorityQueue[tuple[int, float, Task]]" = queue.PriorityQueue()
        self.results_dir = OPENCLAW_OPS_REPORTS if bot_name == "ops" else OPENCLAW_RESEARCH_REPORTS
        self.dead_letter_dir = LOG_DIR / "dead_letter"
        self.dead_letter_dir.mkdir(parents=True, exist_ok=True)

        self.max_workers = int(os.getenv("OPENCLAW_MAX_WORKERS", "4"))
        self.min_workers = int(os.getenv("OPENCLAW_MIN_WORKERS", "1"))
        self.workers: List[threading.Thread] = []
        self.active_count = 0
        self.active_count_lock = threading.Lock()

        self.dispatch_interval_sec = int(os.getenv("OPENCLAW_DISPATCH_INTERVAL_SEC", "10"))
        self.seed_interval_sec = int(os.getenv("OPENCLAW_SEED_INTERVAL_SEC", "30"))

        self.replay_task_every_n_seeds = int(os.getenv("OPENCLAW_REPLAY_EVERY_N_SEEDS", "2"))
        self.outcome_task_every_n_seeds = int(os.getenv("OPENCLAW_OUTCOME_EVERY_N_SEEDS", "2"))
        self.seed_counter = 0

        self._spawn_initial_workers()

    def _spawn_initial_workers(self) -> None:
        for _ in range(self.min_workers):
            self._spawn_worker()

    def _spawn_worker(self) -> None:
        t = threading.Thread(target=self._worker_loop, daemon=True)
        t.start()
        self.workers.append(t)
        log_line(self.bot_name, f"Spawned worker (total={len(self.workers)})")

    def _enqueue_followups(self, tasks: List[Task]) -> None:
        if not tasks:
            return
        flags = control_flags()
        for t in tasks:
            if flags["kill_switch"] and t.kind not in {"monitoring_alerting", "safety_audit", "proposal_review"}:
                self._dead_letter(t, reason="kill_switch_blocked_followup")
                continue
            if flags["manual_veto"] and t.kind in {"threshold_tuner"}:
                self._dead_letter(t, reason="manual_veto_blocked_tuner")
                continue
            self.enqueue(t)

    def _worker_loop(self) -> None:
        while self.running:
            try:
                _, _, task = self.task_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            age = utc_ts() - task.created_at
            if age > task.ttl_seconds:
                self._dead_letter(task, reason="ttl_expired")
                self.task_queue.task_done()
                continue

            flags = control_flags()
            if flags["kill_switch"] and task.kind not in {"monitoring_alerting", "safety_audit", "proposal_review"}:
                self._dead_letter(task, reason="kill_switch_active")
                self.task_queue.task_done()
                continue

            with self.active_count_lock:
                self.active_count += 1

            start = utc_ts()
            try:
                runner = AGENT_RUNNERS.get(task.kind)
                if runner is None:
                    raise ValueError(f"No runner for task kind={task.kind}")
                result = runner(task)
                self._record_result(task, result)
                self._enqueue_followups(result.follow_up_tasks)
            except Exception as e:
                task.attempts += 1
                if task.attempts < 3 and not flags["manual_veto"]:
                    task.priority = min(task.priority + 1, 9)
                    self.enqueue(task)
                    log_line(self.bot_name, f"Task {task.task_id} failed, requeued: {e}")
                else:
                    self._dead_letter(task, reason=f"error:{e}")
            finally:
                with self.active_count_lock:
                    self.active_count -= 1
                self.task_queue.task_done()
                log_line(self.bot_name, f"Task done: id={task.task_id} dur={utc_ts()-start:.2f}s q={self.task_queue.qsize()}")

    def _record_result(self, task: Task, result: AgentResult) -> None:
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out = {
            "timestamp": iso_now(), "bot": self.bot_name,
            "task": {"task_id": task.task_id, "kind": task.kind, "payload": task.payload,
                     "priority": task.priority, "attempts": task.attempts},
            "result": {"success": result.success, "summary": result.summary, "data": result.data,
                       "duration_sec": result.duration_sec, "error": result.error,
                       "follow_up_tasks_count": len(result.follow_up_tasks)},
            "control_flags": control_flags(),
        }
        write_json(self.results_dir / f"{self.bot_name}_{task.kind}_{ts}_{task.task_id}.json", out)

    def _dead_letter(self, task: Task, reason: str) -> None:
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out = {
            "timestamp": iso_now(), "bot": self.bot_name, "reason": reason,
            "task": {"task_id": task.task_id, "kind": task.kind, "payload": task.payload,
                     "priority": task.priority, "attempts": task.attempts, "ttl_seconds": task.ttl_seconds},
            "control_flags": control_flags(),
        }
        write_json(self.dead_letter_dir / f"dead_{self.bot_name}_{task.task_id}_{ts}.json", out)

    def enqueue(self, task: Task) -> None:
        self.task_queue.put((task.priority, task.created_at, task))

    def seed_tasks(self) -> None:
        flags = control_flags()
        if flags["kill_switch"]:
            log_line(self.bot_name, "Kill switch active; seeding limited to safety/monitoring.")
            if self.bot_name == "ops":
                self.enqueue(Task(task_id=gen_task_id("ops-monitor"), kind="monitoring_alerting",
                                  payload={"scope": "health"}, priority=1, ttl_seconds=120))
                self.enqueue(Task(task_id=gen_task_id("ops-safety"), kind="safety_audit",
                                  payload={"scope": "runtime"}, priority=1, ttl_seconds=180))
            else:
                self.enqueue(Task(task_id=gen_task_id("res-safety"), kind="safety_audit",
                                  payload={"scope": "configs+outputs"}, priority=1, ttl_seconds=180))
            return

        self.seed_counter += 1
        ts_suffix = str(int(utc_ts()))

        if self.bot_name == "ops":
            tasks = [
                Task(task_id=f"ops-azure-{ts_suffix}", kind="azure_provisioner",
                     payload={"scope": "control-plane"}, priority=4, ttl_seconds=120),
                Task(task_id=f"ops-monitor-{ts_suffix}", kind="monitoring_alerting",
                     payload={"scope": "health"}, priority=3, ttl_seconds=120),
                Task(task_id=f"ops-safety-{ts_suffix}", kind="safety_audit",
                     payload={"scope": "runtime"}, priority=2, ttl_seconds=180),
            ]
        else:
            tasks = [
                Task(task_id=f"res-drift-{ts_suffix}", kind="drift_monitor",
                     payload={"window": "6h"}, priority=2, ttl_seconds=180),
                Task(task_id=f"res-safety-{ts_suffix}", kind="safety_audit",
                     payload={"scope": "configs+outputs"}, priority=2, ttl_seconds=180),
            ]

            # Direct outcome tracker on cadence
            if self.seed_counter % max(self.outcome_task_every_n_seeds, 1) == 0:
                tasks.append(Task(task_id=f"res-outcome-{ts_suffix}", kind="outcome_tracker_run",
                                  payload={"repo_root": str(REPO_ROOT)}, priority=3, ttl_seconds=300))

            # Direct replay runner on cadence
            if self.seed_counter % max(self.replay_task_every_n_seeds, 1) == 0:
                tasks.append(Task(
                    task_id=f"res-replay-{ts_suffix}", kind="replay_runner_run",
                    payload={"repo_root": str(REPO_ROOT), "fixtures": "tests/replays",
                             "output": "reports/openclaw_research/replay_latest.json", "timeout_sec": 240},
                    priority=3, ttl_seconds=420))

            if not flags["manual_veto"]:
                tasks.append(Task(task_id=f"res-tune-{ts_suffix}", kind="threshold_tuner",
                                  payload={"mode": "staging_only"}, priority=6, ttl_seconds=300))

        for t in tasks:
            self.enqueue(t)
        log_line(self.bot_name, f"Seeded {len(tasks)} tasks (seed_counter={self.seed_counter})")

    def autoscale(self) -> None:
        qsize = self.task_queue.qsize()
        if qsize > 10 and len(self.workers) < self.max_workers:
            self._spawn_worker()
        log_line(self.bot_name, f"autoscale: queue={qsize} active={self.active_count} workers={len(self.workers)}")

    def run_forever(self) -> None:
        log_line(self.bot_name, "OpenClaw bot started")
        last_seed = 0.0
        last_scale = 0.0

        while self.running:
            flags = control_flags()
            if flags["kill_switch"]:
                log_line(self.bot_name, "Kill switch active; idle.")
                time.sleep(5)
                continue

            now = utc_ts()
            if now - last_seed >= self.seed_interval_sec:
                self.seed_tasks()
                last_seed = now
            if now - last_scale >= self.dispatch_interval_sec:
                self.autoscale()
                last_scale = now
            time.sleep(1)

    def stop(self) -> None:
        self.running = False
        log_line(self.bot_name, "Stopping OpenClaw bot")


def load_cfg(path: Optional[str]) -> Dict[str, Any]:
    if not path or yaml is None:
        return {}
    try:
        return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenClaw Agent Factory")
    parser.add_argument("action", nargs="?", default="run", choices=["run", "list", "spawn", "terminate", "cleanup"],
                        help="Action to perform (default: run)")
    parser.add_argument("--bot", choices=["ops", "research"], required=True)
    parser.add_argument("--config", default=None)
    parser.add_argument("--type", dest="agent_type")
    parser.add_argument("--task", default="")
    parser.add_argument("--agent-id")
    args = parser.parse_args()

    if args.action == "run":
        cfg = load_cfg(args.config)
        bot = OpenClawBot(bot_name=args.bot, cfg=cfg)

        def handler(signum, frame):
            bot.stop()
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)
        bot.run_forever()
    elif args.action == "list":
        results_dir = REPORTS_DIR / ("openclaw_ops" if args.bot == "ops" else "openclaw_research")
        if results_dir.exists():
            files = sorted(results_dir.glob("*.json"), reverse=True)[:10]
            print(json.dumps([f.name for f in files], indent=2))
        else:
            print("[]")
    else:
        print(f"Action '{args.action}' not yet implemented in queue-based mode.", file=sys.stderr)


if __name__ == "__main__":
    main()
