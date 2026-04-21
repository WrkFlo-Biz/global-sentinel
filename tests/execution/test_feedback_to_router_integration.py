from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from src.execution.adaptive_feedback_loop import AdaptiveFeedbackLoop
from src.execution.order_intent_registry import OrderIntentRegistry
from src.execution.shadow_order_router import ShadowOrderRouter
from src.execution.trade_idea_packager import TradeIdeaPackager


def _copy_runtime_config(repo_root: Path, tmp_repo: Path) -> None:
    for rel_path in (
        "config/execution_mode.yaml",
        "config/order_ttl_policy.yaml",
    ):
        src = repo_root / rel_path
        dst = tmp_repo / rel_path
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _write_seed_feedback_state(repo_root: Path) -> None:
    state_path = repo_root / "logs" / "execution" / "feedback_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "signal_adjustments": {},
                "signal_win_counts": {},
                "signal_loss_counts": {},
                "total_trades_analyzed": 0,
                "last_analysis_time": None,
                "cumulative_pnl": 0.0,
                "daily_pnl_history": [],
                "strategy_confidence_adjustments": {
                    "shipping_rate_explosion": 0.08,
                    "medium_long": 0.03,
                    "airline_short": -0.05,
                    "day_trade": -0.02,
                },
                "strategy_adjustments": {
                    "day_trade": {
                        "stop_loss_tightness": 1.02,
                        "profit_target_mult": 1.01,
                    },
                    "medium_long": {
                        "stop_loss_tightness": 1.05,
                        "profit_target_mult": 1.08,
                    },
                    "shipping_rate_explosion": {
                        "stop_loss_tightness": 1.12,
                        "profit_target_mult": 1.1,
                    },
                    "airline_short": {
                        "stop_loss_tightness": 1.04,
                        "profit_target_mult": 0.98,
                    },
                },
                "strategy_group_stats": {},
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_performance_history(repo_root: Path) -> None:
    history_path = repo_root / "logs" / "execution" / "performance_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    trades = [
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": 180.0,
            "pnl_pct": 4.5,
            "win": True,
            "strategy": "shipping_rate_explosion",
            "metadata": {
                "strategy_name": "shipping_rate_explosion",
                "strategy_family": "medium_long",
                "holding_period": "swing",
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": 95.0,
            "pnl_pct": 2.2,
            "win": True,
            "strategy": "shipping_rate_explosion",
            "metadata": {
                "strategy_name": "shipping_rate_explosion",
                "strategy_family": "medium_long",
                "holding_period": "swing",
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": 60.0,
            "pnl_pct": 1.3,
            "win": True,
            "strategy": "shipping_rate_explosion",
            "metadata": {
                "strategy_name": "shipping_rate_explosion",
                "strategy_family": "medium_long",
                "holding_period": "swing",
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": -40.0,
            "pnl_pct": -0.9,
            "win": False,
            "strategy": "shipping_rate_explosion",
            "metadata": {
                "strategy_name": "shipping_rate_explosion",
                "strategy_family": "medium_long",
                "holding_period": "swing",
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": 70.0,
            "pnl_pct": 1.6,
            "win": True,
            "strategy": "airline_short",
            "metadata": {
                "strategy_name": "airline_short",
                "strategy_family": "day_trade",
                "holding_period": "day",
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": -50.0,
            "pnl_pct": -1.1,
            "win": False,
            "strategy": "airline_short",
            "metadata": {
                "strategy_name": "airline_short",
                "strategy_family": "day_trade",
                "holding_period": "day",
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": -80.0,
            "pnl_pct": -1.8,
            "win": False,
            "strategy": "airline_short",
            "metadata": {
                "strategy_name": "airline_short",
                "strategy_family": "day_trade",
                "holding_period": "day",
            },
        },
        {
            "timestamp_utc": now,
            "exit_time": now,
            "pnl": -35.0,
            "pnl_pct": -0.8,
            "win": False,
            "strategy": "airline_short",
            "metadata": {
                "strategy_name": "airline_short",
                "strategy_family": "day_trade",
                "holding_period": "day",
            },
        },
    ]

    history_path.write_text(
        "\n".join(json.dumps(row) for row in trades) + "\n",
        encoding="utf-8",
    )


def _trade_analysis() -> dict:
    return {
        "trade_ideas": [
            {
                "symbol": "ZIM",
                "side": "long",
                "historical_win_rate": 0.55,
                "confidence_adjusted_score": 0.46,
                "strategy": "shipping_rate_explosion",
                "strategy_family": "medium_long",
                "strategy_style": "regime_playbook_medium_long",
                "holding_period": "swing",
                "reason": "shipping rates continue to climb",
            },
            {
                "symbol": "UAL",
                "side": "short",
                "historical_win_rate": 0.56,
                "confidence_adjusted_score": 0.5,
                "strategy": "airline_short",
                "strategy_family": "day_trade",
                "strategy_style": "regime_playbook_day_trade",
                "holding_period": "day",
                "reason": "fuel-cost pressure is hitting airlines",
            },
        ]
    }


def _scorecard_no_gate() -> dict:
    return {
        "mode": "NORMAL",
        "regime_shift_probability": 0.1,
        "confidence": 0.9,
        "shadow_execution_eligible": True,
        "time_window": {
            "current_window": "eu_close",
            "confidence_multiplier": 1.0,
            "size_multiplier": 1.0,
            "risk_budget": {"max_new_positions": 10},
            "strategy_eligibility": {},
            "preferred_setups": [],
            "restrictions": {},
            "thresholds": {
                "watchlist_min_confidence": 0.55,
                "apply_to_holding_periods": [
                    "day",
                    "intraday_scalp",
                    "intraday_momentum",
                ],
            },
            "shadow_execution_window_blocked": False,
        },
    }


def _microstructure() -> dict:
    return {
        "ZIM": {
            "last_price": 17.0,
            "adv_shares": 6_500_000.0,
            "sigma_daily_pct": 4.2,
        },
        "UAL": {
            "last_price": 45.0,
            "adv_shares": 12_000_000.0,
            "sigma_daily_pct": 2.8,
        },
    }


def _strategy_config() -> dict:
    return {
        "name": "integration_regression",
        "holding_period": "swing",
        "time_in_force": "gtc",
        "extended_hours": False,
        "position_sizing": {
            "method": "notional_pct",
            "base_pct_of_equity": 1.0,
            "high_confidence_pct": 1.5,
            "max_single_position_pct": 3.0,
            "min_notional": 1000.0,
            "max_qty_cap": 500,
        },
    }


def test_feedback_history_changes_packaged_and_routed_candidate(tmp_path: Path) -> None:
    source_repo = Path(__file__).resolve().parents[2]
    _copy_runtime_config(source_repo, tmp_path)

    trade_analysis = _trade_analysis()
    scorecard = _scorecard_no_gate()
    microstructure = _microstructure()

    baseline_package = TradeIdeaPackager(repo_root=tmp_path).build_package(
        trade_analysis=trade_analysis,
        scorecard=scorecard,
        microstructure=microstructure,
    )
    baseline_candidates = {
        candidate["symbol"]: candidate for candidate in baseline_package["candidates"]
    }
    assert baseline_candidates["UAL"]["confidence_score"] > baseline_candidates["ZIM"]["confidence_score"]

    baseline_router = ShadowOrderRouter(tmp_path, broker_name="mock")
    baseline_route = baseline_router.route_package(
        package=baseline_package,
        max_orders=1,
        strategy_config=_strategy_config(),
    )
    assert [row["symbol"] for row in baseline_route["selected_candidates"]] == ["UAL"]
    assert baseline_route["submitted_open_or_ack_count"] == 1
    assert baseline_route["selected_candidates"][0]["strategy"] == "airline_short"
    assert baseline_route["selected_candidates"][0]["strategy_family"] == "day_trade"

    _write_seed_feedback_state(tmp_path)
    _write_performance_history(tmp_path)

    result = AdaptiveFeedbackLoop(tmp_path).analyze_and_adjust()
    assert result["status"] == "active"
    assert result["strategy_confidence_adjustments"]["shipping_rate_explosion"] > 0
    assert result["strategy_confidence_adjustments"]["airline_short"] < 0

    learned_package = TradeIdeaPackager(repo_root=tmp_path).build_package(
        trade_analysis=trade_analysis,
        scorecard=scorecard,
        microstructure=microstructure,
    )
    learned_candidates = {
        candidate["symbol"]: candidate for candidate in learned_package["candidates"]
    }

    shipping_candidate = learned_candidates["ZIM"]
    airline_candidate = learned_candidates["UAL"]

    assert shipping_candidate["confidence_score"] > baseline_candidates["ZIM"]["confidence_score"]
    assert airline_candidate["confidence_score"] < baseline_candidates["UAL"]["confidence_score"]
    assert shipping_candidate["confidence_score"] > airline_candidate["confidence_score"]
    assert shipping_candidate["metadata"]["learning_adjustment_detail"]["strategy"] == "shipping_rate_explosion"
    assert shipping_candidate["metadata"]["learning_adjustment_detail"]["strategy_family"] == "medium_long"
    assert shipping_candidate["metadata"]["learning_adjustment_detail"]["confidence_delta"] > 0
    assert airline_candidate["metadata"]["learning_adjustment_detail"]["confidence_delta"] < 0

    learned_router = ShadowOrderRouter(tmp_path, broker_name="mock")
    learned_route = learned_router.route_package(
        package=learned_package,
        max_orders=1,
        strategy_config=_strategy_config(),
    )

    assert [row["symbol"] for row in learned_route["selected_candidates"]] == ["ZIM"]
    assert learned_route["submitted_open_or_ack_count"] == 1
    assert learned_route["broker_rejected_count"] == 0
    selected = learned_route["selected_candidates"][0]
    assert selected["strategy"] == "shipping_rate_explosion"
    assert selected["strategy_family"] == "medium_long"
    assert selected["learning_adjusted"] is True
    assert selected["learning_adjustment_detail"]["strategy"] == "shipping_rate_explosion"

    registry = OrderIntentRegistry(tmp_path)
    intents = registry.list_intents(package_ids=[learned_route["package_id"]])
    assert len(intents) == 1
    intent = intents[0]
    assert intent["shadow_mode"] is True
    assert intent["candidate_context"]["symbol"] == "ZIM"
    assert intent["candidate_context"]["strategy"] == "shipping_rate_explosion"
    assert intent["candidate_context"]["strategy_family"] == "medium_long"
    assert intent["candidate_context"]["learning_adjusted"] is True
    assert intent["candidate_context"]["learning_adjustment_detail"]["strategy"] == "shipping_rate_explosion"
    assert intent["candidate_context"]["metadata"]["learning_adjustment_detail"]["strategy"] == "shipping_rate_explosion"
    assert intent["candidate_context"]["metadata"]["learning_adjustment_detail"]["confidence_delta"] > 0
    assert intent["order_request"]["shadow_mode"] is True

    router_log = tmp_path / "logs" / "execution" / "shadow_order_router.jsonl"
    route_events = [
        json.loads(line)
        for line in router_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    completed = [
        row for row in route_events
        if row.get("event_type") == "route_package_complete"
        and (row.get("payload") or {}).get("package_id") == learned_route["package_id"]
    ]
    assert completed
    logged_selected = completed[-1]["payload"]["selected_candidates"][0]
    assert logged_selected["symbol"] == "ZIM"
    assert logged_selected["strategy"] == "shipping_rate_explosion"
    assert logged_selected["strategy_family"] == "medium_long"
    assert logged_selected["learning_adjusted"] is True
    assert logged_selected["metadata"]["learning_adjustment_detail"]["strategy"] == "shipping_rate_explosion"
