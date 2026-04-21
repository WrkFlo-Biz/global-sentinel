"""Tests for the continuous paper-training orchestrator."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "ops" / "continuous_paper_training_orchestrator.py"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "continuous_paper_training_orchestrator_test",
        SCRIPT_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules["continuous_paper_training_orchestrator_test"] = module
    spec.loader.exec_module(module)
    return module


def _seed_repo(tmp_path: Path) -> Path:
    for rel in (
        "config/war_strategies.yaml",
        "config/freshness_policy.yaml",
        "config/paper_training_system.yaml",
    ):
        src = REPO_ROOT / rel
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    return tmp_path


def _base_context() -> dict:
    now = datetime.now(timezone.utc)
    return {
        "scorecard": {
            "regime_shift_probability": 0.64,
            "confidence": 0.73,
            "v6_oil_regime": "SHOCK",
            "component_scores": {
                "market_volatility": 0.58,
                "geopolitical_tension": 0.72,
                "commodity_shock": 0.81,
                "policy_signals": 0.35,
            },
            "chokepoint_risk": {
                "hormuz": 0.22,
                "bab_el_mandeb": 0.08,
            },
        },
        "bridge_results": {
            "market_microstructure": {
                "NVDA": {"last_price": 915.0, "sigma_daily_pct": 3.2},
                "GLD": {"last_price": 221.0, "sigma_daily_pct": 1.4},
            },
        },
        "market_data": {
            "oil": {"price": 111.0, "wti_change_pct": 2.6},
            "SPY": {"price": 500.0, "prior_close": 505.0, "relative_volume": 1.1},
            "VIX": {"price": 22.0},
            "NVDA": {
                "price": 915.0,
                "prior_close": 902.0,
                "change_pct": 1.45,
                "relative_volume": 2.2,
                "unusual_volume_score": 0.74,
                "unusual_volume_direction": "call",
                "dark_pool_score": 0.56,
                "dark_pool_direction": "long",
                "flow_imbalance_zscore": 2.9,
                "sweep_count": 4,
                "sweep_direction": "call",
                "hourly_gap_direction": "bullish",
                "liquidity_swept": True,
                "ema_13_break": "up",
            },
            "AAPL": {
                "price": 214.0,
                "prior_close": 211.5,
                "kronos_confidence": 0.71,
                "forecast_edge_pct": 0.9,
                "relative_volume": 1.5,
                "vol_forecast_pct": 1.4,
                "trend_alignment": "bullish",
            },
            "GLD": {"price": 221.0, "prior_close": 217.0, "relative_volume": 1.6},
            "SLV": {"price": 26.1, "prior_close": 25.6, "relative_volume": 1.4},
            "XLE": {"price": 93.2, "prior_close": 91.4, "relative_volume": 1.8},
            "USO": {"price": 84.5, "prior_close": 82.1, "relative_volume": 1.9},
            "DBA": {"price": 27.2, "prior_close": 26.7, "relative_volume": 1.3},
            "WEAT": {"price": 8.7, "prior_close": 8.5, "relative_volume": 1.2},
        },
        "source_timestamps": {
            "market_data": now.isoformat(),
            "options_greeks": (now - timedelta(minutes=50)).isoformat(),
            "gdelt": (now - timedelta(minutes=80)).isoformat(),
            "maritime": (now - timedelta(minutes=180)).isoformat(),
            "eia": now.isoformat(),
            "fed": now.isoformat(),
            "sentiment": now.isoformat(),
            "gpr_index": now.isoformat(),
            "cds_sovereign": now.isoformat(),
        },
    }


def test_orchestrator_routes_live_and_stale_candidates(tmp_path: Path) -> None:
    module = _load_module()
    repo_root = _seed_repo(tmp_path)
    orchestrator = module.ContinuousPaperTrainingOrchestrator(
        repo_root=repo_root,
        broker_name="mock",
    )

    summary = orchestrator.run_once(context=_base_context())

    assert summary["candidate_inventory_count"] > 0
    assert summary["stale_candidate_count"] > 0
    assert summary["route_summary"]["mock_simulated_fill_count"] > 0
    assert summary["open_position_count"] > 0

    inventory_rows = [
        json.loads(line)
        for line in (repo_root / "logs" / "execution" / "paper_training_candidate_inventory.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert inventory_rows
    candidates = inventory_rows[-1]["candidates"]
    assert any((row.get("metadata") or {}).get("freshness", {}).get("stale_sources") for row in candidates)

    intent_rows = [
        json.loads(line)
        for line in (repo_root / "logs" / "execution" / "order_intents.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert intent_rows
    assert (intent_rows[-1]["candidate_context"].get("metadata") or {}).get("strategy_name")


def test_orchestrator_auto_closes_mock_positions_and_records_metadata(tmp_path: Path) -> None:
    module = _load_module()
    repo_root = _seed_repo(tmp_path)
    orchestrator = module.ContinuousPaperTrainingOrchestrator(
        repo_root=repo_root,
        broker_name="mock",
    )

    broker_order = orchestrator.router.adapter.submit_order(
        {
            "symbol": "SPY",
            "side": "buy",
            "type": "market",
            "qty": 2,
            "shadow_mode": True,
            "client_order_id": "seed-open-spy",
        }
    )
    orchestrator.router.adapter.simulate_fill(broker_order["order_id"], 2, 100.0)
    opened_at = datetime.now(timezone.utc).isoformat()
    orchestrator.state["previous_positions"] = {
        "SPY": {
            "symbol": "SPY",
            "qty": 2.0,
            "avg_entry_price": 100.0,
            "current_price": 100.0,
            "strategy": "ict_candle_range_theory",
            "metadata": {
                "strategy_name": "ict_candle_range_theory",
                "signal_boost_detail": {"stale_market": -0.05},
                "stop_loss_pct": -1.0,
                "take_profit_pct": 1.0,
            },
            "order_metadata": {
                "signal_boost_detail": {"stale_market": -0.05},
            },
            "opened_at_utc": opened_at,
            "stop_loss_pct": -1.0,
            "take_profit_pct": 1.0,
        }
    }

    context = {
        "scorecard": {},
        "bridge_results": {},
        "market_data": {"SPY": {"price": 101.5, "prior_close": 100.5}},
        "source_timestamps": {"market_data": datetime.now(timezone.utc).isoformat()},
    }

    summary = orchestrator.run_once(context=context, route_orders=False)

    assert summary["auto_exit_count"] == 1
    assert summary["closure_count"] == 1

    history_rows = [
        json.loads(line)
        for line in (repo_root / "logs" / "execution" / "performance_history.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    closed = [row for row in history_rows if row.get("pnl") is not None]
    assert closed
    assert closed[-1]["metadata"]["strategy_name"] == "ict_candle_range_theory"
    assert closed[-1]["order_metadata"]["signal_boost_detail"]["stale_market"] == -0.05
