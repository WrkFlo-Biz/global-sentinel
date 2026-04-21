from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.execution.adaptive_feedback_loop import AdaptiveFeedbackLoop


def _write_trade_history(repo_root: Path, *, drifty: bool) -> None:
    history_path = repo_root / "logs" / "execution" / "performance_history.jsonl"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    trades = []
    for idx in range(6):
        win = idx < 5
        if drifty:
            fill_rate = 0.32
            slippage_bps = 24.0
            time_to_edge_minutes = 320.0
            edge_decay_score = 0.88
            mfe_pct = 2.2
            mae_pct = -1.2
        else:
            fill_rate = 0.96
            slippage_bps = 3.0
            time_to_edge_minutes = 18.0
            edge_decay_score = 0.14
            mfe_pct = 1.1
            mae_pct = -0.2
        trades.append(
            {
                "timestamp_utc": now,
                "exit_time": now,
                "pnl": 55.0 if win else -20.0,
                "pnl_pct": 1.2 if win else -0.5,
                "win": win,
                "strategy": "regime_breakout",
                "metadata": {
                    "strategy_name": "regime_breakout",
                    "strategy_family": "day_trade",
                    "signal_boost_detail": {"macro_rebound": 0.04},
                },
                "order_metadata": {
                    "signal_boost_detail": {"macro_rebound": 0.04},
                },
                "fill_rate": fill_rate,
                "realized_slippage_bps": slippage_bps,
                "time_to_edge_minutes": time_to_edge_minutes,
                "edge_decay_score": edge_decay_score,
                "realized_return_bps": 18.0,
                "mfe_pct": mfe_pct,
                "mae_pct": mae_pct,
            }
        )

    history_path.write_text(
        "\n".join(json.dumps(row) for row in trades) + "\n",
        encoding="utf-8",
    )


def test_concept_drift_dampens_signal_adjustments_and_emits_threshold_payload(tmp_path: Path) -> None:
    control_root = tmp_path / "control"
    drift_root = tmp_path / "drift"
    _write_trade_history(control_root, drifty=False)
    _write_trade_history(drift_root, drifty=True)

    control_loop = AdaptiveFeedbackLoop(control_root)
    control_loop.CONCEPT_DRIFT_TRIGGER_SCORE = 1.1
    control_loop.CONCEPT_DRIFT_CRITICAL_SCORE = 1.2
    control = control_loop.analyze_and_adjust()

    drift_loop = AdaptiveFeedbackLoop(drift_root)
    drift = drift_loop.analyze_and_adjust()

    assert control["status"] == "active"
    assert drift["status"] == "active"
    assert control["concept_drift"]["triggered"] is False
    assert drift["concept_drift"]["triggered"] is True
    assert drift["concept_drift"]["application"]["applied"] is True
    assert drift["concept_drift"]["thresholds"]["concept_drift_trigger_score"] == drift_loop.CONCEPT_DRIFT_TRIGGER_SCORE

    control_abs = abs(control["adjustments"]["macro_rebound"])
    drift_abs = abs(drift["adjustments"]["macro_rebound"])
    assert control_abs > 0
    assert drift_abs < control_abs

    multiplier = drift["concept_drift"]["down_weighting_multiplier"]
    assert 1.0 - drift_loop.MAX_DRIFT_DAMPING <= multiplier <= 1.0

    signal_names = {row["name"] for row in drift["concept_drift"]["signals"]}
    assert "concept_drift_score" in signal_names
    assert "avg_edge_decay_score" in signal_names
