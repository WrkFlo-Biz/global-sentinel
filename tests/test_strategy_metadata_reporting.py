from __future__ import annotations

import json
from pathlib import Path

from src.execution.tca_shadow_report import TCAShadowReport, render_markdown_summary
from src.reports.manual_review_queue_report import (
    ManualReviewQueueReport,
    render_markdown as render_manual_review_markdown,
)
from src.monitoring.reconciler_lag_sla_monitor import (
    ReconcilerLagSLAMonitor,
    render_markdown as render_reconciler_markdown,
)
from src.monitoring.telegram_notifier import TelegramNotifier


class _StubStrategyManager:
    def get_bot_for_strategy(self, strategy_name: str) -> str:
        return f"{strategy_name}_bot"

    def format_telegram_position_update(self, positions, strategy_name: str) -> str:
        return f"{strategy_name}: {len(positions)} positions"


def test_telegram_position_update_includes_exact_strategy_context(tmp_path: Path, monkeypatch):
    log_path = tmp_path / "logs" / "execution" / "shadow_order_router.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(
            {
                "payload": {
                    "time_window_name": "lunch_lull",
                    "selected_candidates": [
                        {
                            "symbol": "ZIM",
                            "strategy": "shipping_rate_explosion",
                            "strategy_style": "regime_playbook_medium_long",
                            "strategy_family": "medium_long",
                            "underlying_strategy": "oil_shock_regime",
                            "learning_adjusted": True,
                            "metadata": {
                                "strategy": "shipping_rate_explosion",
                                "strategy_family": "medium_long",
                                "underlying_strategy": "oil_shock_regime",
                                "learning_adjusted": True,
                            },
                        }
                    ],
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    notifier = TelegramNotifier(tmp_path)
    sent = []
    monkeypatch.setattr(
        notifier,
        "notify_position_update",
        lambda formatted_message, strategy_name, bot_name: sent.append(
            {
                "message": formatted_message,
                "strategy_name": strategy_name,
                "bot_name": bot_name,
            }
        ),
    )

    notifier._send_all_position_updates(
        lambda: [{"symbol": "ZIM", "qty": 10}],
        _StubStrategyManager(),
    )

    assert sent
    assert sent[0]["strategy_name"] == "medium_long"
    assert "Strategy Context:" in sent[0]["message"]
    assert "shipping_rate_explosion" in sent[0]["message"]
    assert "[medium_long]" in sent[0]["message"]
    assert "underlying=oil_shock_regime" in sent[0]["message"]
    assert "learning=on" in sent[0]["message"]


def test_reconciler_report_exposes_strategy_metadata(tmp_path: Path):
    log_path = tmp_path / "logs" / "execution" / "order_intents.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(
            {
                "intent_id": "intent-1",
                "status": "open",
                "timestamp_utc": "2026-04-09T13:00:00+00:00",
                "candidate_context": {
                    "symbol": "UAL",
                    "strategy_style": "regime_playbook_day_trade",
                    "metadata": {
                        "strategy": "airline_short",
                        "strategy_family": "day_trade",
                        "underlying_strategy": "jet_fuel_squeeze",
                        "learning_adjusted": True,
                        "learning_adjustment_detail": {"boost_bps": 12},
                    },
                },
                "broker_binding": {"broker_name": "alpaca", "broker_order_id": "abc"},
                "broker_state": {"status": "submitted"},
                "reconciliation": {"reconciler_status": "pending"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = ReconcilerLagSLAMonitor(tmp_path).evaluate(per_intent_lag_warn_minutes=0.01)
    top = report["top_lagging_intents"][0]

    assert top["strategy"] == "airline_short"
    assert top["strategy_family"] == "day_trade"
    assert top["underlying_strategy"] == "jet_fuel_squeeze"
    assert top["learning_adjusted"] is True
    assert report["summary"]["learning_adjusted_intent_count"] == 1
    assert report["summary"]["strategy_family_counts"]["day_trade"] == 1

    markdown = render_reconciler_markdown(report)
    assert "airline_short" in markdown
    assert "jet_fuel_squeeze" in markdown
    assert "| Intent ID | Symbol | Strategy | Family | Underlying | Learned |" in markdown


def test_tca_shadow_report_breakdown_uses_exact_strategy_metadata():
    packages = [
        {
            "timestamp_utc": "2026-04-09T14:00:00+00:00",
            "effective_mode": "NORMAL",
            "window_context": {"time_window_name": "power_hour", "watchlist_only_window": False},
            "macro_context": {"macro_event_quorum_pass": True},
            "candidates": [
                {
                    "symbol": "VLO",
                    "window_name": "power_hour",
                    "strategy": "shipping_rate_explosion",
                    "strategy_style": "regime_playbook_medium_long",
                    "strategy_family": "medium_long",
                    "underlying_strategy": "oil_shock_regime",
                    "learning_adjusted": True,
                    "learning_adjustment_detail": {"strategy": "shipping_rate_explosion", "delta": 0.08},
                    "confidence_score": 0.77,
                    "fill_sim_assessment": {
                        "expected_slippage_bps": 5.5,
                        "reject_risk_probability": 0.08,
                    },
                }
            ],
            "blocked_candidates": [
                {
                    "symbol": "JETS",
                    "window_name": "power_hour",
                    "metadata": {
                        "strategy": "airline_short",
                        "strategy_style": "regime_playbook_day_trade",
                        "strategy_family": "day_trade",
                        "underlying_strategy": "jet_fuel_squeeze",
                        "learning_adjusted": True,
                        "learning_adjustment_detail": {"strategy": "airline_short", "delta": 0.03},
                    },
                    "reason": "spread too wide",
                }
            ],
            "global_blocks": [],
        }
    ]

    report = TCAShadowReport().build_report(packages)

    shipping = report["strategy_breakdown"]["shipping_rate_explosion"]
    airline = report["strategy_breakdown"]["airline_short"]

    assert shipping["strategy_family_counts"]["medium_long"] == 1
    assert shipping["underlying_strategy_counts"]["oil_shock_regime"] == 1
    assert shipping["learning_adjusted_candidate_count"] == 1
    assert airline["strategy_family_counts"]["day_trade"] == 1
    assert airline["underlying_strategy_counts"]["jet_fuel_squeeze"] == 1
    assert airline["learning_adjusted_blocked_count"] == 1

    markdown = render_markdown_summary(report)
    assert "## Top Strategy Buckets" in markdown
    assert "shipping_rate_explosion" in markdown
    assert "airline_short" in markdown


def test_manual_review_queue_report_uses_exact_strategy_metadata(tmp_path: Path):
    log_path = tmp_path / "logs" / "execution" / "order_intents.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps(
            {
                "intent_id": "intent-1",
                "status": "manual_review",
                "timestamp_utc": "2026-04-09T12:00:00+00:00",
                "candidate_context": {
                    "symbol": "ZIM",
                    "strategy_style": "regime_playbook_medium_long",
                    "metadata": {
                        "strategy": "shipping_rate_explosion",
                        "strategy_family": "medium_long",
                        "underlying_strategy": "oil_shock_regime",
                        "learning_adjusted": True,
                        "learning_adjustment_detail": {"strategy": "shipping_rate_explosion", "delta": 0.08},
                    },
                },
                "broker_binding": {"broker_name": "alpaca", "broker_order_id": "abc"},
                "broker_state": {"status": "manual_review"},
                "audit": {
                    "history": [
                        {
                            "event": "manual_review_required",
                            "details": {"reason": "risk gate"},
                        }
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = ManualReviewQueueReport(tmp_path).build()

    assert report["summary"]["manual_review_count"] == 1
    assert report["summary"]["learning_adjusted_count"] == 1
    assert report["strategy_counts_top"]["shipping_rate_explosion"] == 1
    assert report["strategy_family_counts_top"]["medium_long"] == 1
    assert report["underlying_strategy_counts_top"]["oil_shock_regime"] == 1

    top = report["oldest_unresolved"][0]
    assert top["strategy"] == "shipping_rate_explosion"
    assert top["strategy_family"] == "medium_long"
    assert top["underlying_strategy"] == "oil_shock_regime"
    assert top["learning_adjusted"] is True

    markdown = render_manual_review_markdown(report)
    assert "shipping_rate_explosion" in markdown
    assert "medium_long" in markdown
    assert "oil_shock_regime" in markdown
    assert "| Intent ID | Symbol | Strategy | Family | Underlying | Learned | Broker | Broker Status | Reason | Age (min) |" in markdown
