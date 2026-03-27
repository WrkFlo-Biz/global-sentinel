#!/usr/bin/env python3
"""
Session Memory Layer — Persistent daily trading session snapshots on Azure Blob Storage.

Saves end-of-day snapshots and loads rolling 30-day history so the quantum
continuous learner can incorporate historical context into future decisions.

Blob container: gs-memory
Blob path:     gs-memory/YYYY-MM-DD/session_snapshot.json

Usage:
    python -m src.research.session_memory save          # Save today snapshot
    python -m src.research.session_memory load          # Load last 30 days
    python -m src.research.session_memory summary       # Print daily summary
    python -m src.research.session_memory seed          # Seed initial snapshot
"""

import json, os, sys, datetime, logging
from pathlib import Path
from typing import Optional

try:
    from azure.storage.blob import BlobServiceClient, ContainerClient
except ImportError:
    print("azure-storage-blob not installed. Run: pip install azure-storage-blob")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SessionMemory] %(message)s")
log = logging.getLogger("session_memory")

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QUANTUM_FEED = REPO_ROOT / "data" / "quantum_feed"
CONTAINER_NAME = "gs-memory"
LOCAL_MEMORY_DIR = REPO_ROOT / "data" / "session_memory"
SUMMARY_OUTPUT = QUANTUM_FEED / "session_memory_summary.json"

# ---------------------------------------------------------------------------
# Azure Blob helpers
# ---------------------------------------------------------------------------

def _get_blob_service() -> BlobServiceClient:
    """Get Azure Blob service client from .env connection string."""
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    if not conn_str:
        raise RuntimeError("AZURE_STORAGE_CONNECTION_STRING not set in .env")
    return BlobServiceClient.from_connection_string(conn_str)


def _ensure_container(svc: BlobServiceClient) -> ContainerClient:
    """Create the gs-memory container if it does not exist."""
    container = svc.get_container_client(CONTAINER_NAME)
    try:
        container.get_container_properties()
    except Exception:
        log.info("Creating container %s", CONTAINER_NAME)
        container.create_container()
    return container


def _today_et() -> str:
    """Return today date string in ET (UTC-4)."""
    utc = datetime.datetime.now(datetime.timezone.utc)
    et = utc - datetime.timedelta(hours=4)
    return et.strftime("%Y-%m-%d")


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Optional[dict]:
    """Safely read a JSON file."""
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _read_jsonl(path: Path) -> list:
    """Read a JSONL file into a list of dicts."""
    results = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        results.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    return results


def collect_quantum_signals() -> dict:
    """Collect all quantum_feed JSON files into a single dict."""
    signals = {}
    for fp in sorted(QUANTUM_FEED.glob("*.json")):
        if fp.name == "session_memory_summary.json":
            continue
        data = _read_json(fp)
        if data is not None:
            signals[fp.stem] = data
    return signals


def collect_trade_outcomes(date_str: str) -> dict:
    """Collect trade outcomes for the given date from feedback dataset."""
    all_trades = _read_jsonl(QUANTUM_FEED / "trade_feedback_dataset.jsonl")
    day_trades = [t for t in all_trades if t.get("date") == date_str]
    winners = [t for t in day_trades if t.get("profitable")]
    losers = [t for t in day_trades if not t.get("profitable")]
    total_pnl = sum(t.get("pnl", 0) for t in day_trades)
    return {
        "date": date_str,
        "total_trades": len(day_trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(len(winners) / max(len(day_trades), 1), 3),
        "total_pnl": round(total_pnl, 4),
        "trades": day_trades,
    }


def collect_system_state() -> dict:
    """Collect current system state: regime, uncertainty, TAPO, AMD."""
    regime = _read_json(QUANTUM_FEED / "hmm_regime.json") or {}
    uncertainty = _read_json(QUANTUM_FEED / "uncertainty_premium.json") or {}
    tapo = _read_json(QUANTUM_FEED / "tapo_pattern.json") or {}
    amd = _read_json(QUANTUM_FEED / "amd_phase.json") or {}
    learner = _read_json(QUANTUM_FEED / "learner_state.json") or {}
    strategy = _read_json(QUANTUM_FEED / "strategy_recommendations.json") or {}

    return {
        "regime": regime.get("current_regime"),
        "regime_probabilities": regime.get("regime_probabilities"),
        "uncertainty_premium": uncertainty.get("uncertainty_premium"),
        "premium_interpretation": uncertainty.get("premium_interpretation"),
        "real_risk_score": uncertainty.get("real_risk_score"),
        "market_implied_risk": uncertainty.get("market_implied_risk"),
        "tapo_phase": tapo.get("tapo_phase"),
        "tapo_signal": tapo.get("trade_signal"),
        "amd_phases": [
            {"symbol": p.get("symbol"), "phase": p.get("phase"), "direction": p.get("direction")}
            for p in (amd.get("phases") or [])[:10]
        ],
        "quantum_weight": learner.get("quantum_weight"),
        "cycle_count": learner.get("cycle_count"),
        "best_strategy": strategy.get("best_overall_strategy"),
        "strategy_rankings": strategy.get("strategy_rankings"),
    }


def collect_recommendations() -> list:
    """Collect what was recommended today."""
    strategy = _read_json(QUANTUM_FEED / "strategy_recommendations.json") or {}
    return strategy.get("top_5_recommendations", [])


# ---------------------------------------------------------------------------
# Snapshot save / load
# ---------------------------------------------------------------------------

def build_snapshot(date_str: str, manual_entries: Optional[dict] = None) -> dict:
    """Build a complete session snapshot for the given date."""
    snapshot = {
        "date": date_str,
        "saved_at": _now_iso(),
        "quantum_signals": collect_quantum_signals(),
        "trade_outcomes": collect_trade_outcomes(date_str),
        "system_state": collect_system_state(),
        "recommendations": collect_recommendations(),
        "manual_entries": manual_entries or {},
    }
    return snapshot


def save_snapshot(date_str: Optional[str] = None, manual_entries: Optional[dict] = None):
    """Save today session snapshot to Azure Blob Storage."""
    date_str = date_str or _today_et()
    snapshot = build_snapshot(date_str, manual_entries)

    # Save to Azure Blob
    svc = _get_blob_service()
    container = _ensure_container(svc)
    blob_name = f"{date_str}/session_snapshot.json"
    blob_client = container.get_blob_client(blob_name)
    data = json.dumps(snapshot, indent=2, default=str)
    blob_client.upload_blob(data, overwrite=True)
    log.info("Saved snapshot to blob: %s/%s (%d bytes)", CONTAINER_NAME, blob_name, len(data))

    # Also save locally for quick access
    LOCAL_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    local_path = LOCAL_MEMORY_DIR / f"{date_str}.json"
    with open(local_path, "w") as f:
        f.write(data)
    log.info("Local copy: %s", local_path)

    # Generate summary for continuous learner
    generate_rolling_summary()
    return snapshot


def load_snapshots(days: int = 30) -> list:
    """Load the last N days of snapshots from Azure Blob Storage."""
    svc = _get_blob_service()
    container = _ensure_container(svc)

    snapshots = []
    blobs = list(container.list_blobs())
    # Filter to session_snapshot.json blobs, sorted by name (date)
    snapshot_blobs = sorted(
        [b for b in blobs if b.name.endswith("/session_snapshot.json")],
        key=lambda b: b.name,
        reverse=True,
    )[:days]

    for blob in snapshot_blobs:
        try:
            data = container.get_blob_client(blob.name).download_blob().readall()
            snapshots.append(json.loads(data))
        except Exception as e:
            log.warning("Failed to load %s: %s", blob.name, e)

    log.info("Loaded %d snapshots from Azure Blob", len(snapshots))
    return snapshots


# ---------------------------------------------------------------------------
# Rolling analysis & summary
# ---------------------------------------------------------------------------

def generate_rolling_summary(days: int = 30):
    """Analyze the last N days and write a summary the continuous learner reads."""
    snapshots = load_snapshots(days)
    if not snapshots:
        log.warning("No snapshots found, skipping summary generation")
        return

    # Rolling win rate
    all_trades = []
    strategy_wins = {}
    daily_pnl = []
    lessons = []

    for snap in snapshots:
        outcomes = snap.get("trade_outcomes", {})
        trades = outcomes.get("trades", [])
        all_trades.extend(trades)
        daily_pnl.append({
            "date": snap.get("date"),
            "pnl": outcomes.get("total_pnl", 0),
            "win_rate": outcomes.get("win_rate", 0),
            "total_trades": outcomes.get("total_trades", 0),
        })

        # Collect manual lessons
        manual = snap.get("manual_entries", {})
        if manual.get("lessons"):
            if isinstance(manual["lessons"], list):
                lessons.extend(manual["lessons"])
            else:
                lessons.append(manual["lessons"])

        # Strategy tracking
        best = snap.get("system_state", {}).get("best_strategy", "unknown")
        if best not in strategy_wins:
            strategy_wins[best] = {"total": 0, "wins": 0}
        strategy_wins[best]["total"] += outcomes.get("total_trades", 0)
        strategy_wins[best]["wins"] += outcomes.get("winners", 0)

    # Compute rolling stats
    total_wins = sum(1 for t in all_trades if t.get("profitable"))
    total_count = len(all_trades)
    rolling_win_rate = round(total_wins / max(total_count, 1), 3)
    total_pnl = round(sum(d["pnl"] for d in daily_pnl), 4)
    best_day = max(daily_pnl, key=lambda d: d["pnl"]) if daily_pnl else None
    worst_day = min(daily_pnl, key=lambda d: d["pnl"]) if daily_pnl else None

    # Strategy win rates
    strategy_performance = {}
    for strat, data in strategy_wins.items():
        strategy_performance[strat] = {
            "total_trades": data["total"],
            "wins": data["wins"],
            "win_rate": round(data["wins"] / max(data["total"], 1), 3),
        }

    # Signal predictiveness: check which signals correlated with wins
    signal_scores = {}
    for snap in snapshots:
        regime = snap.get("system_state", {}).get("regime", "unknown")
        outcomes = snap.get("trade_outcomes", {})
        wr = outcomes.get("win_rate", 0)
        if regime not in signal_scores:
            signal_scores[regime] = {"count": 0, "total_wr": 0}
        signal_scores[regime]["count"] += 1
        signal_scores[regime]["total_wr"] += wr

    regime_performance = {}
    for regime, data in signal_scores.items():
        regime_performance[regime] = round(data["total_wr"] / max(data["count"], 1), 3)

    summary = {
        "generated_at": _now_iso(),
        "period_days": len(snapshots),
        "rolling_stats": {
            "total_trades": total_count,
            "total_wins": total_wins,
            "rolling_win_rate": rolling_win_rate,
            "total_pnl": total_pnl,
            "avg_daily_pnl": round(total_pnl / max(len(daily_pnl), 1), 4),
            "best_day": best_day,
            "worst_day": worst_day,
        },
        "strategy_performance": strategy_performance,
        "regime_performance": regime_performance,
        "recent_lessons": lessons[-20:],
        "daily_pnl_history": daily_pnl,
        "actionable_insights": _derive_insights(
            rolling_win_rate, strategy_performance, regime_performance, lessons
        ),
    }

    # Write summary where continuous learner can find it
    SUMMARY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(SUMMARY_OUTPUT, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    log.info("Summary written to %s", SUMMARY_OUTPUT)

    # Also upload summary to blob
    try:
        svc = _get_blob_service()
        container = _ensure_container(svc)
        blob_client = container.get_blob_client("rolling_summary.json")
        blob_client.upload_blob(json.dumps(summary, indent=2, default=str), overwrite=True)
        log.info("Summary uploaded to blob: %s/rolling_summary.json", CONTAINER_NAME)
    except Exception as e:
        log.warning("Failed to upload summary to blob: %s", e)

    return summary


def _derive_insights(win_rate, strategy_perf, regime_perf, lessons) -> list:
    """Derive actionable insights from historical data."""
    insights = []

    if win_rate < 0.4:
        insights.append("WARNING: Rolling win rate below 40%. Consider reducing position sizes.")
    elif win_rate > 0.6:
        insights.append("Strong win rate above 60%. Current strategy mix is working.")

    # Find best/worst strategy
    if strategy_perf:
        best_strat = max(strategy_perf.items(), key=lambda x: x[1].get("win_rate", 0))
        worst_strat = min(strategy_perf.items(), key=lambda x: x[1].get("win_rate", 0))
        if best_strat[1]["total_trades"] >= 3:
            insights.append(
                f"Best strategy: {best_strat[0]} ({best_strat[1]['win_rate']:.0%} win rate, "
                f"{best_strat[1]['total_trades']} trades)"
            )
        if worst_strat[0] != best_strat[0] and worst_strat[1]["total_trades"] >= 3:
            insights.append(
                f"Worst strategy: {worst_strat[0]} ({worst_strat[1]['win_rate']:.0%} win rate). "
                f"Consider reducing allocation."
            )

    # Find best regime
    if regime_perf:
        best_regime = max(regime_perf.items(), key=lambda x: x[1])
        insights.append(f"Best performing regime: {best_regime[0]} (avg win rate {best_regime[1]:.0%})")

    # Extract patterns from lessons
    hold_too_long = sum(1 for l in lessons if "held too long" in str(l).lower())
    sold_too_early = sum(1 for l in lessons if "should have held" in str(l).lower())
    if hold_too_long > sold_too_early and hold_too_long >= 2:
        insights.append(f"Pattern: holding too long ({hold_too_long} occurrences). Tighten exit rules.")
    elif sold_too_early > hold_too_long and sold_too_early >= 2:
        insights.append(f"Pattern: selling too early ({sold_too_early} occurrences). Consider trailing stops.")

    return insights


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def seed_initial_snapshot():
    """Seed the first snapshot with today manual trading data."""
    manual = {
        "notable_trades": [
            {
                "symbol": "NVDA 170P (2026-03-28)",
                "type": "put_option",
                "entry_price": 1.01,
                "peak_price": 2.56,
                "peak_gain_pct": 153.5,
                "status": "held_through_peak",
                "outcome": "gains_eroded_by_theta_and_lunch_lull",
            }
        ],
        "top_movers": [
            {"symbol": "WVE", "change_pct": 10.0},
            {"symbol": "GSAT", "change_pct": 9.0},
            {"symbol": "NAVN", "change_pct": 12.0},
            {"symbol": "KOD", "change_pct": 9.0},
        ],
        "lessons": [
            "NVDA 170P: Should have sold at 100%+ during morning session. Lunch lull + theta eroded gains from +154% peak.",
            "System improvement needed: Auto-sell at 100%+ gain unless user explicitly overrides with reason.",
            "Morning (9:30-11:00 ET) is the best window for option exits. Do not hold through lunch expecting continuation.",
            "Theta decay accelerates significantly for weekly options after midday. Take profits early.",
        ],
        "system_improvements": [
            "Add auto-profit-take at 100% gain for options with <3 DTE",
            "Add lunch lull warning (11:30-13:00 ET) for short-dated options",
            "Track time-of-day for all exits to build optimal exit time model",
        ],
    }
    snapshot = save_snapshot(_today_et(), manual)
    log.info("Seeded initial snapshot for %s with %d manual entries",
             snapshot["date"], len(manual))
    return snapshot


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "save"
    if cmd == "save":
        save_snapshot()
    elif cmd == "load":
        snaps = load_snapshots()
        print(json.dumps([s["date"] for s in snaps], indent=2))
    elif cmd == "summary":
        s = generate_rolling_summary()
        if s:
            print(json.dumps(s, indent=2, default=str))
    elif cmd == "seed":
        seed_initial_snapshot()
    else:
        print(f"Unknown command: {cmd}. Use: save, load, summary, seed")
        sys.exit(1)
