from __future__ import annotations

import json
from pathlib import Path

from src.execution.adaptive_feedback_loop import AdaptiveFeedbackLoop
from src.execution.strategy_learning import (
    FEEDBACK_STATE_SCHEMA_VERSION,
    load_feedback_state,
)


def test_load_feedback_state_migrates_legacy_payload(tmp_path: Path) -> None:
    feedback_path = tmp_path / "logs" / "execution" / "feedback_state.json"
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.write_text(
        json.dumps(
            {
                "signal_adjustments": {"gss": 0.02},
                "cumulative_pnl": 123.45,
                "strategy_adjustments": {
                    "shipping_rate_explosion": {
                        "profit_target_mult": 1.1,
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    state = load_feedback_state(tmp_path)

    assert state["schema_version"] == FEEDBACK_STATE_SCHEMA_VERSION
    assert state["signal_adjustments"] == {"gss": 0.02}
    assert state["cumulative_pnl"] == 123.45
    assert state["strategy_adjustments"]["shipping_rate_explosion"]["profit_target_mult"] == 1.1
    assert state["strategy_adjustments"]["day_trade"]["profit_target_mult"] == 1.0
    assert state["strategy_adjustments"]["medium_long"]["profit_target_mult"] == 1.0


def test_adaptive_feedback_loop_migrates_loaded_state_and_saves_versioned_payload(tmp_path: Path) -> None:
    feedback_path = tmp_path / "logs" / "execution" / "feedback_state.json"
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    feedback_path.write_text(
        json.dumps(
            {
                "signal_adjustments": {"narrative": -0.01},
                "strategy_confidence_adjustments": {"airline_short": -0.04},
                "daily_pnl_history": [{"date": "2026-04-08", "pnl": 25.0, "target_met": False}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    loop = AdaptiveFeedbackLoop(tmp_path)

    assert loop.state["schema_version"] == FEEDBACK_STATE_SCHEMA_VERSION
    assert loop.state["signal_adjustments"] == {"narrative": -0.01}
    assert loop.state["strategy_confidence_adjustments"] == {"airline_short": -0.04}
    assert "day_trade" in loop.state["strategy_adjustments"]
    assert "medium_long" in loop.state["strategy_adjustments"]

    loop._save_state()

    saved = json.loads(feedback_path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == FEEDBACK_STATE_SCHEMA_VERSION
    assert saved["signal_adjustments"] == {"narrative": -0.01}
    assert saved["strategy_confidence_adjustments"] == {"airline_short": -0.04}
    assert saved["strategy_adjustments"]["day_trade"]["stop_loss_tightness"] == 1.0
    assert saved["strategy_adjustments"]["medium_long"]["profit_target_mult"] == 1.0
