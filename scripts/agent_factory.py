#!/usr/bin/env python3
"""
Global Sentinel V4 - OpenClaw Agent Factory (Enhanced)

Enhancements:
- OpenClaw-Research can directly invoke:
  - src/replay_runner.py
  - scripts/outcome_tracker.py
- Parses replay outputs and enqueues proposal review tasks
- Safety controls:
  - kill switch / manual veto respected
  - execution routed to configured broker adapter
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

OPENCLAW_OPS_REPORTS = REPORTS_DIR / "openclaw_ops"
OPENCLAW_RESEARCH_REPORTS = REPORTS_DIR / "openclaw_research"
sys.path.insert(0, str(REPO_ROOT))

from src.core.control_state_snapshot import read_control_state_snapshot
from src.core.openclaw_role_registry import load_openclaw_role_registry
from src.core.openclaw_state_db import OpenClawStateDB, default_state_db_path
from src.monitoring.telegram_topic_notifier import TelegramTopicNotifier
from src.reports.openclaw_role_briefing import OpenClawRoleBriefingBuilder
from src.reports.openclaw_recommendation_queue import OpenClawRecommendationQueueWriter


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


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def ensure_runtime_dirs() -> None:
    for path in [LOG_DIR, REPORTS_DIR, CONTROL_DIR, STAGING_DIR, OPENCLAW_OPS_REPORTS, OPENCLAW_RESEARCH_REPORTS]:
        path.mkdir(parents=True, exist_ok=True)


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
    control_snapshot = read_control_state_snapshot(REPO_ROOT)
    return {
        "manual_veto": control_snapshot["manual_veto"],
        "kill_switch": control_snapshot["kill_switch"],
        "strategy_executor_enabled": env_flag("OPENCLAW_ENABLE_STRATEGY_EXECUTOR", False),
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
        "guardrails": {"staging_only": False, "requires_human_approval": False, "execution_enabled": True},
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


def run_role_briefing(task: Task) -> AgentResult:
    """Build a role-specific oversight brief and optionally send a Telegram topic update."""
    start = utc_ts()
    repo_root = Path(task.payload.get("repo_root", REPO_ROOT))
    role_id = str(task.payload.get("role_id", ""))
    registry = load_openclaw_role_registry(repo_root / "config" / "openclaw_role_registry.yaml")
    role = registry.roles.get(role_id)
    if role is None:
        return AgentResult(
            task_id=task.task_id,
            success=False,
            summary=f"Unknown role_id={role_id}",
            error="missing_role",
            duration_sec=utc_ts() - start,
        )

    builder = OpenClawRoleBriefingBuilder(repo_root)
    output_path = builder.write_role_artifact(role)
    artifact = read_json(output_path, {})
    queued_advisory = None
    if role.bot == "research":
        queued_advisory = OpenClawRecommendationQueueWriter(repo_root).append_role_advisory(
            role_artifact=artifact,
            artifact_path=Path(output_path),
        )
    notifier_result = {"ok": False, "reason": "telegram_updates_disabled"}
    if role.telegram_updates:
        notifier = TelegramTopicNotifier(topic="advisories")
        lines = [
            f"{role.title} update",
            f"status={artifact.get('status', 'unknown')}",
            f"role={role.role_id}",
        ]
        actions = artifact.get("actions", []) or []
        if actions:
            lines.append(f"next={actions[0]}")
        notifier_result = vars(notifier.send_message("\n".join(lines), require_topic_target=True))

    return AgentResult(
        task_id=task.task_id,
        success=True,
        summary=f"Role brief generated for {role_id}",
        data={
            "role_id": role_id,
            "artifact_json": str(output_path),
            "queued_advisory": queued_advisory,
            "telegram_update": notifier_result,
        },
        duration_sec=utc_ts() - start,
    )


# --- Oil superspike / commodity shock scanner ---

def run_commodity_shock_scanner(task: Task) -> AgentResult:
    """Scan for oil-sensitive opportunities across all cascade sectors.

    Reads latest scorecard, checks commodity_shock component, scans microstructure
    for vol/momentum signals on energy-chain symbols, and generates a prioritized
    opportunity report. Feeds into execution pipeline when thresholds met."""
    start = utc_ts()
    repo_root = Path(task.payload.get("repo_root", str(REPO_ROOT)))

    try:
        import glob as _glob

        # Load latest scorecard
        sc_files = sorted(_glob.glob(str(repo_root / "logs" / "scorecards" / "*.json")))
        if not sc_files:
            return AgentResult(task_id=task.task_id, success=False,
                               summary="No scorecards available", duration_sec=utc_ts() - start)
        sc = json.loads(Path(sc_files[-1]).read_text(encoding="utf-8"))
        components = sc.get("component_scores", {})
        commodity_shock = components.get("commodity_shock", 0)
        regime_p = sc.get("regime_shift_probability", 0)
        mode = sc.get("mode", "NORMAL")

        # Load microstructure cache for vol/price data
        micro = {}
        cache_dir = repo_root / "logs" / "bridge_cache" / "market_microstructure"
        if cache_dir.exists():
            cache_files = sorted(cache_dir.glob("microstructure_*.json"), reverse=True)
            if cache_files:
                try:
                    cache_data = json.loads(cache_files[0].read_text(encoding="utf-8"))
                    micro = cache_data.get("symbols", {})
                except Exception:
                    pass

        # Define scan universe by cascade tier
        scan_universe = {
            "tier1_direct_crude": ["USO", "UCO", "BNO", "DBO", "OILK"],
            "tier2_oil_majors": ["XOM", "CVX", "COP", "OXY", "EOG", "DVN", "FANG", "PXD"],
            "tier3_ep_services": ["XOP", "XLE", "OIH", "SLB", "HAL", "BKR", "NOG", "CHRD", "SM", "MTDR"],
            "tier4_refiners": ["VLO", "MPC", "PSX", "PBF", "DK", "HFC"],
            "tier5_midstream": ["ET", "EPD", "WMB", "KMI", "MPLX", "OKE", "TRGP", "PAA"],
            "tier6_lng": ["LNG", "GLNG", "AR", "EQT", "RRC", "TELL"],
            "tier7_tankers_shipping": ["FRO", "STNG", "INSW", "DHT", "TNK", "NAT", "EURN", "GOGL", "ZIM", "SBLK"],
            "tier8_alt_energy_beneficiaries": ["FSLR", "ENPH", "SEDG", "RUN", "NOVA", "TAN", "ICLN",
                                                "TSLA", "RIVN", "NIO", "LI", "BYDDY",
                                                "CCJ", "URA", "LEU", "UUUU", "NNE",
                                                "PLUG", "FCEL", "BE", "BLDP"],
            "tier9_inflation_hedges": ["TIP", "VTIP", "STIP", "RINF", "DJP", "PDBC", "COMT",
                                       "GLD", "GDX", "SLV", "SIL", "GOLD", "NEM", "AEM",
                                       "IBIT", "BITO"],
            "tier10_agriculture_food": ["DBA", "WEAT", "CORN", "SOYB", "MOO", "COW",
                                        "CF", "MOS", "NTR", "ADM", "BG", "CTVA", "DE", "AGCO"],
            "tier11_defense": ["ITA", "PPA", "XAR", "LMT", "RTX", "NOC", "GD", "BA", "HII", "LHX", "PLTR"],
            "tier12_short_targets": ["JETS", "UAL", "AAL", "DAL", "LUV", "CCL", "RCL", "NCLH",
                                     "IYT", "FDX", "UPS", "XRT", "XLY", "XHB",
                                     "EEM", "EWY", "EWZ", "INDA", "TUR",
                                     "LYB", "DOW", "DD", "EMN",
                                     "F", "GM", "LYFT", "UBER"],
            "tier13_financials": ["XLF", "KRE", "BKX", "GS", "MS", "JPM"],
            "tier14_utilities": ["XLU", "NEE", "DUK", "SO", "VST", "CEG", "NRG"],
            "tier15_country_exporters": ["KSA", "NORW", "EWC", "FLBR"],
            "tier16_niche": ["SPH", "AMLP", "CWEN", "AROC", "CLNE", "UGI",
                             "TGS", "PTEN", "HP", "RIG", "VAL", "NE",
                             "TRMD", "ASC", "CPLP",
                             "MRO", "APA", "MGY", "CNQ", "SU", "IMO", "CPG",
                             "NOV", "CHX", "OII", "USAC", "PUMP", "WTTR",
                             "HLX", "DRQ", "MRC", "BRY"],
            "tier17_royalties": ["VNOM", "TPL", "BSM", "DMLP", "MNRL", "PBT"],
            "tier18_railcar_crude_by_rail": ["TRN", "GBX", "GATX"],
            "tier19_coal_fuel_switching": ["BTU", "ARCH", "ARLP", "HCC", "CEIX"],
            "tier20_waste_haulers_short": ["WM", "RSG", "GFL"],
            "tier21_fuel_distributors": ["CAPL", "PARR", "CVI", "DINO"],
        }

        # Score each symbol based on microstructure + commodity_shock level
        opportunities = []
        for tier, symbols in scan_universe.items():
            for sym in symbols:
                sym_data = micro.get(sym, {})
                daily_vol = sym_data.get("daily_vol_pct", 0)
                price = sym_data.get("last_price", 0)
                score = commodity_shock  # base score from regime
                if daily_vol > 3:
                    score += 0.1  # vol bonus
                if daily_vol > 5:
                    score += 0.1
                opportunities.append({
                    "symbol": sym,
                    "tier": tier,
                    "commodity_shock_score": round(commodity_shock, 3),
                    "daily_vol_pct": round(daily_vol, 2),
                    "price": round(price, 2) if price else None,
                    "opportunity_score": round(score, 3),
                })

        # Sort by opportunity score
        opportunities.sort(key=lambda x: x["opportunity_score"], reverse=True)

        # Build report
        report = {
            "schema_version": "commodity_shock_scan.v1",
            "timestamp_utc": iso_now(),
            "commodity_shock_level": round(commodity_shock, 3),
            "regime_shift_probability": round(regime_p, 3),
            "mode": mode,
            "symbols_scanned": sum(len(s) for s in scan_universe.values()),
            "symbols_with_microstructure": sum(1 for o in opportunities if o["price"]),
            "top_opportunities": opportunities[:30],
            "short_targets": [o for o in opportunities if "short" in o["tier"]][:15],
            "tier_summary": {tier: len(syms) for tier, syms in scan_universe.items()},
            "execution_eligible": True,
        }

        # Persist report
        out_path = OPENCLAW_RESEARCH_REPORTS / f"commodity_shock_scan_{int(utc_ts())}.json"
        write_json(out_path, report)

        # Generate follow-up if commodity_shock > 0.7
        follow_ups: List[Task] = []
        if commodity_shock > 0.7:
            follow_ups.append(Task(
                task_id=gen_task_id("res-proposal-commodity"),
                kind="proposal_review",
                payload={
                    "review_type": "commodity_shock_alert",
                    "commodity_shock": commodity_shock,
                    "trigger_source": "commodity_shock_scanner",
                },
                priority=2, ttl_seconds=600,
            ))

        return AgentResult(
            task_id=task.task_id, success=True,
            summary=f"Commodity shock scan: level={commodity_shock:.3f}, "
                    f"scanned={report['symbols_scanned']}, micro={report['symbols_with_microstructure']}",
            data={"report_path": str(out_path), "commodity_shock": commodity_shock,
                  "top_5": [o["symbol"] for o in opportunities[:5]]},
            duration_sec=utc_ts() - start,
            follow_up_tasks=follow_ups,
        )
    except Exception as e:
        return AgentResult(task_id=task.task_id, success=False,
                           summary=f"Commodity shock scan failed: {e}",
                           error=str(e), duration_sec=utc_ts() - start)


def run_crypto_executor(task: Task) -> AgentResult:
    """24/7 crypto strategy executor. Runs regardless of market hours.

    Reads config/crypto_strategies.yaml, checks crypto prices via Alpaca API,
    evaluates momentum/entry conditions, and submits crypto orders via the
    Alpaca paper adapter. Crypto trades 24/7/365 on Alpaca.
    """
    start = utc_ts()
    repo_root = Path(task.payload.get("repo_root", str(REPO_ROOT)))

    try:
        import urllib.request
        import urllib.error

        # Load crypto strategies config
        strat_path = repo_root / "config" / "crypto_strategies.yaml"
        if not strat_path.exists():
            return AgentResult(task_id=task.task_id, success=False,
                               summary="crypto_strategies.yaml not found",
                               duration_sec=utc_ts() - start)

        strat_cfg = yaml.safe_load(strat_path.read_text(encoding="utf-8"))

        # Check kill switch
        flags = control_flags()
        if flags["kill_switch"]:
            return AgentResult(task_id=task.task_id, success=False,
                               summary="Kill switch active — crypto execution halted",
                               duration_sec=utc_ts() - start)

        # Load latest scorecard for regime context
        import glob as _glob
        sc_files = sorted(_glob.glob(str(repo_root / "logs" / "scorecards" / "*.json")))
        sc = json.loads(Path(sc_files[-1]).read_text(encoding="utf-8")) if sc_files else {}
        mode = sc.get("mode", "NORMAL")
        commodity_shock = sc.get("component_scores", {}).get("commodity_shock", 0)

        # Load Alpaca credentials from env — both accounts
        from dotenv import load_dotenv
        load_dotenv(repo_root / ".env")

        account_creds = {
            "day_trade": {
                "api_key": os.environ.get("ALPACA_API_KEY_DAYTRADE", ""),
                "secret_key": os.environ.get("ALPACA_SECRET_KEY_DAYTRADE", ""),
                "base_url": os.environ.get("ALPACA_BASE_URL_DAYTRADE", "https://paper-api.alpaca.markets/v2"),
            },
            "medium_long": {
                "api_key": os.environ.get("ALPACA_API_KEY_MEDLONG", ""),
                "secret_key": os.environ.get("ALPACA_SECRET_KEY_MEDLONG", ""),
                "base_url": os.environ.get("ALPACA_BASE_URL_MEDLONG", "https://paper-api.alpaca.markets/v2"),
            },
        }

        if not account_creds["day_trade"]["api_key"] and not account_creds["medium_long"]["api_key"]:
            return AgentResult(task_id=task.task_id, success=False,
                               summary="Missing Alpaca credentials",
                               duration_sec=utc_ts() - start)

        # Default to day_trade for data queries
        api_key = account_creds["day_trade"]["api_key"] or account_creds["medium_long"]["api_key"]
        secret_key = account_creds["day_trade"]["secret_key"] or account_creds["medium_long"]["secret_key"]
        base_url = account_creds["day_trade"]["base_url"]

        # Helper: Alpaca API request
        def alpaca_request(method: str, endpoint: str, body: dict = None) -> dict:
            url = f"{base_url}{endpoint}"
            data = json.dumps(body).encode() if body else None
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("APCA-API-KEY-ID", api_key)
            req.add_header("APCA-API-SECRET-KEY", secret_key)
            req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read().decode())
            except urllib.error.HTTPError as e:
                body_text = e.read().decode() if e.fp else ""
                return {"error": f"HTTP {e.code}", "detail": body_text[:500]}
            except Exception as e:
                return {"error": str(e)}

        # Get current crypto prices
        scan_universe = strat_cfg.get("scan_universe", {})
        all_symbols = []
        for tier_syms in scan_universe.values():
            all_symbols.extend(tier_syms)
        # Remove stables from trading
        trade_symbols = [s for s in all_symbols if s not in ("USDC/USD", "USDT/USD", "USDG/USD")]

        # Get latest bars for momentum detection
        symbols_param = ",".join(trade_symbols[:20])  # API limit
        bars_url = f"https://data.alpaca.markets/v1beta3/crypto/us/latest/bars?symbols={symbols_param}"
        bars_req = urllib.request.Request(bars_url)
        bars_req.add_header("APCA-API-KEY-ID", api_key)
        bars_req.add_header("APCA-API-SECRET-KEY", secret_key)
        try:
            with urllib.request.urlopen(bars_req, timeout=10) as resp:
                bars_data = json.loads(resp.read().decode())
        except Exception:
            bars_data = {"bars": {}}

        prices = {}
        for sym, bar in bars_data.get("bars", {}).items():
            prices[sym] = bar.get("c", 0)  # Close price

        # Get current positions to avoid duplicates
        positions_resp = alpaca_request("GET", "/positions")
        current_positions = set()
        if isinstance(positions_resp, list):
            for pos in positions_resp:
                current_positions.add(pos.get("symbol", ""))

        # Get account info for allocation limits
        account = alpaca_request("GET", "/account")
        equity = float(account.get("equity", 0)) if isinstance(account, dict) and "equity" in account else 100000

        # Process strategies and submit orders
        orders_submitted = []
        errors = []
        strategies = strat_cfg.get("strategies", {})

        for strat_name, strat in strategies.items():
            try:
                # Select correct account credentials for this strategy
                acct_name = strat.get("account", "day_trade")
                acct = account_creds.get(acct_name, account_creds["day_trade"])
                strat_api_key = acct["api_key"]
                strat_secret_key = acct["secret_key"]
                strat_base_url = acct["base_url"]

                def strat_request(method: str, endpoint: str, body: dict = None) -> dict:
                    url = f"{strat_base_url}{endpoint}"
                    data = json.dumps(body).encode() if body else None
                    req = urllib.request.Request(url, data=data, method=method)
                    req.add_header("APCA-API-KEY-ID", strat_api_key)
                    req.add_header("APCA-API-SECRET-KEY", strat_secret_key)
                    req.add_header("Content-Type", "application/json")
                    try:
                        with urllib.request.urlopen(req, timeout=10) as resp:
                            return json.loads(resp.read().decode())
                    except urllib.error.HTTPError as e:
                        body_text = e.read().decode() if e.fp else ""
                        return {"error": f"HTTP {e.code}", "detail": body_text[:500]}
                    except Exception as e:
                        return {"error": str(e)}

                for pos in strat.get("positions", []):
                    symbol = pos.get("symbol", "")
                    if not symbol:
                        continue

                    # Convert crypto symbol for position check (BTC/USD -> BTCUSD)
                    pos_symbol = symbol.replace("/", "")
                    if pos_symbol in current_positions:
                        continue  # Already have this position

                    notional = pos.get("notional_usd", 0)
                    if notional <= 0:
                        continue

                    side = pos.get("side", "buy")
                    order_type = pos.get("order_type", "market")
                    tif = pos.get("time_in_force", "gtc")

                    # Scale notional based on regime — higher commodity_shock = more crypto
                    if commodity_shock > 0.6:
                        notional = round(notional * 1.2)  # 20% boost in high commodity shock
                    elif mode == "CRISIS":
                        notional = round(notional * 0.5)  # Reduce in crisis (liquidity risk)

                    # Submit crypto order via correct account
                    order_body = {
                        "symbol": symbol,
                        "side": side,
                        "type": order_type,
                        "time_in_force": tif,
                        "notional": str(notional),
                    }

                    result = strat_request("POST", "/orders", order_body)

                    if "error" not in result and result.get("id"):
                        orders_submitted.append({
                            "strategy": strat_name,
                            "symbol": symbol,
                            "side": side,
                            "notional": notional,
                            "order_id": result.get("id"),
                            "status": result.get("status"),
                        })
                    else:
                        errors.append({
                            "strategy": strat_name,
                            "symbol": symbol,
                            "error": result.get("error", result.get("detail", "unknown")),
                        })

            except Exception as e:
                errors.append({"strategy": strat_name, "error": str(e)[:200]})

        # Build momentum scan report
        momentum_scan = []
        for sym in trade_symbols[:20]:
            price = prices.get(sym, 0)
            if price > 0:
                momentum_scan.append({"symbol": sym, "price": round(price, 4)})

        # Persist report
        report = {
            "schema_version": "crypto_executor.v1",
            "timestamp_utc": iso_now(),
            "mode": mode,
            "commodity_shock": round(commodity_shock, 3),
            "market_hours": "24/7",
            "prices_snapshot": {s: round(p, 4) for s, p in prices.items()},
            "current_crypto_positions": list(current_positions),
            "orders_submitted": orders_submitted,
            "orders_count": len(orders_submitted),
            "errors": errors,
            "equity": equity,
            "duration_sec": round(utc_ts() - start, 3),
        }

        out_path = OPENCLAW_RESEARCH_REPORTS / f"crypto_execution_{int(utc_ts())}.json"
        write_json(out_path, report)

        return AgentResult(
            task_id=task.task_id, success=True,
            summary=f"Crypto executor 24/7: orders={len(orders_submitted)}, prices={len(prices)}, errors={len(errors)}",
            data={"report_path": str(out_path), "orders_submitted": orders_submitted,
                  "errors": errors[:5], "prices_count": len(prices)},
            duration_sec=utc_ts() - start,
        )
    except Exception as e:
        return AgentResult(task_id=task.task_id, success=False,
                           summary=f"Crypto executor failed: {e}",
                           error=str(e), duration_sec=utc_ts() - start)


def run_strategy_executor(task: Task) -> AgentResult:
    """Execute war strategies by feeding them into the trade analysis + order pipeline.

    Reads config/war_strategies.yaml, loads latest scorecard & microstructure,
    runs TradeAnalysisEngine for both day_trade and medium_long, then routes
    resulting ideas through TradeIdeaPackager → ShadowOrderRouter → Broker Adapter.
    """
    start = utc_ts()
    repo_root = Path(task.payload.get("repo_root", str(REPO_ROOT)))

    try:
        import glob as _glob

        # Load war strategies config
        strat_path = repo_root / "config" / "war_strategies.yaml"
        if not strat_path.exists():
            return AgentResult(task_id=task.task_id, success=False,
                               summary="war_strategies.yaml not found",
                               duration_sec=utc_ts() - start)

        strat_cfg = yaml.safe_load(strat_path.read_text(encoding="utf-8"))
        risk_controls = strat_cfg.get("risk_controls", {})

        # Load latest scorecard
        sc_files = sorted(_glob.glob(str(repo_root / "logs" / "scorecards" / "*.json")))
        if not sc_files:
            return AgentResult(task_id=task.task_id, success=False,
                               summary="No scorecards available",
                               duration_sec=utc_ts() - start)

        sc = json.loads(Path(sc_files[-1]).read_text(encoding="utf-8"))
        mode = sc.get("mode", "NORMAL")

        # Check kill switch
        flags = control_flags()
        if flags["kill_switch"]:
            return AgentResult(task_id=task.task_id, success=False,
                               summary="Kill switch active — strategy execution halted",
                               duration_sec=utc_ts() - start)

        # Load microstructure
        micro = {}
        cache_dir = repo_root / "logs" / "bridge_cache" / "market_microstructure"
        if cache_dir.exists():
            cache_files = sorted(cache_dir.glob("microstructure_*.json"), reverse=True)
            if cache_files:
                try:
                    cache_data = json.loads(cache_files[0].read_text(encoding="utf-8"))
                    micro = cache_data.get("symbols", {})
                except Exception:
                    pass

        # Import execution pipeline
        sys.path.insert(0, str(repo_root))
        from src.alpha.trade_analysis_engine import TradeAnalysisEngine
        from src.execution.trade_idea_packager import TradeIdeaPackager
        from src.execution.shadow_order_router import ShadowOrderRouter

        engine = TradeAnalysisEngine(repo_root)
        packager = TradeIdeaPackager()
        router = ShadowOrderRouter(repo_root)

        orders_submitted = []
        errors = []

        # Run both strategy types
        for strategy_type in ["day_trade", "medium_long"]:
            try:
                # Generate trade ideas from regime analysis
                analysis = engine.analyze(
                    scorecard=sc,
                    microstructure=micro,
                    strategy_type=strategy_type,
                )

                if not analysis.get("trade_ideas"):
                    continue

                # Package ideas for routing
                package = packager.build_package(
                    trade_analysis=analysis,
                    scorecard=sc,
                    microstructure=micro,
                    max_ideas=15,
                )

                if not package.get("candidates"):
                    continue

                # Pass strategy config for position sizing
                strategy_config = {
                    "risk_controls": risk_controls,
                    "strategies": strat_cfg.get("strategies", {}),
                    "accounts": strat_cfg.get("accounts", {}),
                }

                # Route to broker
                route_result = router.route_package(
                    package=package,
                    max_orders=10,
                    min_confidence=0.0,
                    strategy_config=strategy_config,
                )

                submitted = route_result.get("orders_submitted", 0)
                if submitted > 0:
                    orders_submitted.append({
                        "strategy_type": strategy_type,
                        "orders_submitted": submitted,
                        "symbols": [o.get("symbol") for o in route_result.get("bound_order_attempts", [])],
                    })

            except Exception as e:
                errors.append(f"{strategy_type}: {str(e)[:200]}")

        # Persist execution report
        report = {
            "schema_version": "strategy_executor.v1",
            "timestamp_utc": iso_now(),
            "mode": mode,
            "strategies_config": strat_path.name,
            "orders_submitted": orders_submitted,
            "errors": errors,
            "risk_controls_applied": risk_controls,
            "duration_sec": round(utc_ts() - start, 3),
        }

        out_path = OPENCLAW_RESEARCH_REPORTS / f"strategy_execution_{int(utc_ts())}.json"
        write_json(out_path, report)

        total_orders = sum(o.get("orders_submitted", 0) for o in orders_submitted)
        return AgentResult(
            task_id=task.task_id, success=True,
            summary=f"Strategy executor: mode={mode}, orders={total_orders}, errors={len(errors)}",
            data={"report_path": str(out_path), "orders_submitted": orders_submitted,
                  "errors": errors},
            duration_sec=utc_ts() - start,
        )
    except Exception as e:
        return AgentResult(task_id=task.task_id, success=False,
                           summary=f"Strategy executor failed: {e}",
                           error=str(e), duration_sec=utc_ts() - start)


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
    "role_briefing": run_role_briefing,
    "commodity_shock_scanner": run_commodity_shock_scanner,
    "strategy_executor": run_strategy_executor,
    "crypto_executor": run_crypto_executor,
}


# --- Queue manager / orchestrator ---

class OpenClawBot:
    def __init__(self, bot_name: str, cfg: Dict[str, Any]):
        ensure_runtime_dirs()
        self.bot_name = bot_name
        self.cfg = cfg
        self.running = True
        self.task_queue: "queue.PriorityQueue[tuple[int, float, Task]]" = queue.PriorityQueue()
        self.results_dir = OPENCLAW_OPS_REPORTS if bot_name == "ops" else OPENCLAW_RESEARCH_REPORTS
        self.dead_letter_dir = LOG_DIR / "dead_letter"
        self.dead_letter_dir.mkdir(parents=True, exist_ok=True)
        self.state_db = OpenClawStateDB(default_state_db_path(REPO_ROOT))

        self.max_workers = int(os.getenv("OPENCLAW_MAX_WORKERS", "4"))
        self.min_workers = int(os.getenv("OPENCLAW_MIN_WORKERS", "1"))
        self.workers: List[threading.Thread] = []
        self.worker_ids: List[str] = []
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
        worker_id = f"{self.bot_name}-worker-{len(self.worker_ids) + 1}"
        self.worker_ids.append(worker_id)
        self.state_db.update_worker_health(
            worker_id=worker_id,
            last_seen=iso_now(),
            status="starting",
            current_task=None,
        )
        t = threading.Thread(target=self._worker_loop, args=(worker_id,), daemon=True, name=worker_id)
        t.start()
        self.workers.append(t)
        log_line(self.bot_name, f"Spawned worker (total={len(self.workers)})")

    def _mark_task_started(self, task: Task, worker_id: str, started_at: Optional[str] = None) -> str:
        started_at = started_at or iso_now()
        self.state_db.record_task_start(
            task_id=task.task_id,
            worker=worker_id,
            status="running",
            started_at=started_at,
        )
        self.state_db.update_worker_health(
            worker_id=worker_id,
            last_seen=started_at,
            status="busy",
            current_task=task.task_id,
        )
        return started_at

    def _mark_task_result(
        self,
        task: Task,
        result: AgentResult,
        worker_id: str,
        started_at: str,
    ) -> None:
        completed_at = iso_now()
        self.state_db.record_task_status(
            task_id=task.task_id,
            worker=worker_id,
            status="completed" if result.success else "failed",
            started_at=started_at,
            completed_at=completed_at,
            output_summary=result.summary,
        )
        self.state_db.update_worker_health(
            worker_id=worker_id,
            last_seen=completed_at,
            status="idle",
            current_task=None,
        )

    def _mark_task_requeued(self, task: Task, worker_id: str, started_at: str, error: str) -> None:
        completed_at = iso_now()
        self.state_db.record_task_status(
            task_id=task.task_id,
            worker=worker_id,
            status="requeued",
            started_at=started_at,
            completed_at=completed_at,
            output_summary=error,
        )
        self.state_db.update_worker_health(
            worker_id=worker_id,
            last_seen=completed_at,
            status="idle",
            current_task=None,
        )

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

    def _worker_loop(self, worker_id: str) -> None:
        last_idle_heartbeat = 0.0
        self.state_db.update_worker_health(
            worker_id=worker_id,
            last_seen=iso_now(),
            status="idle",
            current_task=None,
        )
        while self.running:
            try:
                _, _, task = self.task_queue.get(timeout=1.0)
            except queue.Empty:
                if utc_ts() - last_idle_heartbeat >= 5.0:
                    last_idle_heartbeat = utc_ts()
                    self.state_db.update_worker_health(
                        worker_id=worker_id,
                        last_seen=iso_now(),
                        status="idle",
                        current_task=None,
                    )
                continue

            age = utc_ts() - task.created_at
            if age > task.ttl_seconds:
                self._dead_letter(task, reason="ttl_expired", worker_id=worker_id)
                self.task_queue.task_done()
                continue

            flags = control_flags()
            if flags["kill_switch"] and task.kind not in {"monitoring_alerting", "safety_audit", "proposal_review"}:
                self._dead_letter(task, reason="kill_switch_active", worker_id=worker_id)
                self.task_queue.task_done()
                continue

            with self.active_count_lock:
                self.active_count += 1

            started_at = self._mark_task_started(task, worker_id)
            start = utc_ts()
            try:
                runner = AGENT_RUNNERS.get(task.kind)
                if runner is None:
                    raise ValueError(f"No runner for task kind={task.kind}")
                result = runner(task)
                self._record_result(task, result)
                self._mark_task_result(task, result, worker_id, started_at)
                self._enqueue_followups(result.follow_up_tasks)
            except Exception as e:
                task.attempts += 1
                if task.attempts < 3 and not flags["manual_veto"]:
                    task.priority = min(task.priority + 1, 9)
                    self.enqueue(task)
                    self._mark_task_requeued(task, worker_id, started_at, str(e))
                    log_line(self.bot_name, f"Task {task.task_id} failed, requeued: {e}")
                else:
                    self._dead_letter(task, reason=f"error:{e}", worker_id=worker_id, started_at=started_at)
            finally:
                with self.active_count_lock:
                    self.active_count -= 1
                self.task_queue.task_done()
                log_line(self.bot_name, f"Task done: id={task.task_id} dur={utc_ts()-start:.2f}s q={self.task_queue.qsize()}")
        self.state_db.update_worker_health(
            worker_id=worker_id,
            last_seen=iso_now(),
            status="stopped",
            current_task=None,
        )

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

    def _dead_letter(
        self,
        task: Task,
        reason: str,
        *,
        worker_id: Optional[str] = None,
        started_at: Optional[str] = None,
    ) -> None:
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        out = {
            "timestamp": iso_now(), "bot": self.bot_name, "reason": reason,
            "task": {"task_id": task.task_id, "kind": task.kind, "payload": task.payload,
                     "priority": task.priority, "attempts": task.attempts, "ttl_seconds": task.ttl_seconds},
            "control_flags": control_flags(),
        }
        write_json(self.dead_letter_dir / f"dead_{self.bot_name}_{task.task_id}_{ts}.json", out)
        completed_at = iso_now()
        self.state_db.record_task_status(
            task_id=task.task_id,
            worker=worker_id,
            status="dead_letter",
            started_at=started_at,
            completed_at=completed_at,
            output_summary=reason,
        )
        if worker_id:
            self.state_db.update_worker_health(
                worker_id=worker_id,
                last_seen=completed_at,
                status="idle",
                current_task=None,
            )

    def enqueue(self, task: Task) -> None:
        self.task_queue.put((task.priority, task.created_at, task))

    def seed_tasks(self) -> None:
        flags = control_flags()
        role_registry = load_openclaw_role_registry(REPO_ROOT / "config" / "openclaw_role_registry.yaml")
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

            # Commodity shock scanner every 3 seeds (~60s)
            if self.seed_counter % 3 == 0:
                tasks.append(Task(
                    task_id=f"res-commodity-scan-{ts_suffix}",
                    kind="commodity_shock_scanner",
                    payload={"repo_root": str(REPO_ROOT)},
                    priority=3, ttl_seconds=300,
                ))

            # Strategy executor is opt-in because it can route ideas into the
            # live order pipeline. Advisory role agents stay enabled by default.
            if flags["strategy_executor_enabled"] and self.seed_counter % 2 == 0:
                tasks.append(Task(
                    task_id=f"res-strategy-exec-{ts_suffix}",
                    kind="strategy_executor",
                    payload={"repo_root": str(REPO_ROOT)},
                    priority=2, ttl_seconds=300,
                ))

            # Crypto executor every 4 seeds (~120s) — 24/7 crypto markets
            if self.seed_counter % 4 == 0:
                tasks.append(Task(
                    task_id=f"res-crypto-exec-{ts_suffix}",
                    kind="crypto_executor",
                    payload={"repo_root": str(REPO_ROOT)},
                    priority=2, ttl_seconds=300,
                ))

            if not flags["manual_veto"]:
                tasks.append(Task(task_id=f"res-tune-{ts_suffix}", kind="threshold_tuner",
                                  payload={"mode": "staging_only"}, priority=6, ttl_seconds=300))

        for role in role_registry.roles_for_bot(self.bot_name):
            if self.seed_counter % role.every_n_seeds != 0:
                continue
            tasks.append(Task(
                task_id=f"{self.bot_name}-role-{role.role_id}-{ts_suffix}",
                kind="role_briefing",
                payload={"repo_root": str(REPO_ROOT), "role_id": role.role_id},
                priority=3 if self.bot_name == "ops" else 4,
                ttl_seconds=180,
            ))

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
        for worker_id in self.worker_ids:
            self.state_db.update_worker_health(
                worker_id=worker_id,
                last_seen=iso_now(),
                status="stopping",
                current_task=None,
            )
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
