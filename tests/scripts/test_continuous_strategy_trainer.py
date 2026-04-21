import json
from pathlib import Path

from scripts.ops import continuous_strategy_trainer as trainer_module


def _write_minimal_strategy_config(repo_root: Path) -> None:
    config_dir = repo_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "war_strategies.yaml").write_text(
        """
strategies:
  shipping_rate_explosion:
    account: medium_long
    positions:
      - symbol: STNG
        side: long
        size_usd: 1000
accounts:
  medium_long:
    broker: paper
risk_controls:
  per_position_stop_pct: 3.0
""".strip()
        + "\n",
        encoding="utf-8",
    )


def test_continuous_strategy_trainer_writes_research_artifacts(tmp_path, monkeypatch):
    repo_root = tmp_path
    _write_minimal_strategy_config(repo_root)

    trainer = trainer_module.ContinuousStrategyTrainer(
        repo_root=repo_root,
        interval_seconds=1,
        scenarios_per_cycle=1,
    )
    assert trainer._init_strategy_engine() is True

    scenario = {
        "scenario_id": "sim-oil-shock-000001",
        "archetype": "oil_shock_escalation",
        "description": "Test scenario",
        "scorecard": {
            "timestamp_utc": "2026-04-08T15:00:00Z",
            "mode": "CRISIS",
            "regime_shift_probability": 0.82,
            "confidence": 0.88,
            "v6_oil_regime": "SHOCK",
            "component_scores": {
                "geopolitical_tension": 0.91,
                "commodity_shock": 0.87,
                "market_volatility": 0.72,
                "currency_stress": 0.42,
                "policy_uncertainty": 0.33,
                "yield_curve": 0.22,
                "politician_alpha": 0.15,
                "credit_spread": 0.20,
                "liquidity_stress": 0.08,
                "consciousness_coherence": 0.02,
                "labor_disruption": 0.01,
                "policy_signals": 0.44,
            },
            "chokepoint_risk": {
                "hormuz": 0.77,
                "bab_el_mandeb": 0.18,
                "panama": 0.02,
            },
        },
        "bridge_results": {},
        "market_data": {
            "VIX": {"price": 36.0, "change_pct": 8.0},
            "STNG": {"price": 57.0, "change_pct": 4.6, "volume": 4_500_000},
            "GLD": {"price": 433.0, "change_pct": 1.2, "volume": 8_000_000},
            "SPY": {"price": 642.0, "change_pct": -1.9, "volume": 45_000_000},
        },
    }

    class FakeEngine:
        def evaluate_entries(self, **_kwargs):
            return [
                {
                    "strategy": "shipping_rate_explosion",
                    "symbol": "STNG",
                    "direction": "long",
                    "notional_usd": 1000,
                    "confidence": 0.81,
                    "stop_loss_pct": -3.0,
                    "take_profit_pct": 8.0,
                    "entry_signal": "Test chokepoint trigger",
                },
                {
                    "strategy": "gold_safe_haven",
                    "symbol": "GLD",
                    "direction": "long",
                    "notional_usd": 1000,
                    "confidence": 0.68,
                    "stop_loss_pct": -2.0,
                    "take_profit_pct": 5.0,
                    "entry_signal": "Test safe haven trigger",
                },
            ]

    trainer._strategy_engine = FakeEngine()
    monkeypatch.setattr(trainer_module, "generate_scenario", lambda archetype=None: scenario)

    trainer.run_cycle()

    today = "20260408"
    pnl_log = repo_root / "logs" / "training" / f"strategy_pnl_{today}.jsonl"
    cycle_log = repo_root / "logs" / "training" / f"scenario_cycles_{today}.jsonl"
    score_path = repo_root / "reports" / "research" / "research_score_latest.json"
    replay_path = repo_root / "reports" / "research" / "replay_quantum_research_backtest.json"
    dataset_path = repo_root / "reports" / "research" / "training_dataset.json"
    labels_path = repo_root / "reports" / "research" / "training_labels.json"
    learning_state_path = repo_root / "artifacts" / "learning_state" / "current_state.json"
    history_path = repo_root / "reports" / "research" / "history" / "sim-oil-shock-000001.json"

    assert pnl_log.exists()
    assert cycle_log.exists()
    assert score_path.exists()
    assert replay_path.exists()
    assert dataset_path.exists()
    assert labels_path.exists()
    assert learning_state_path.exists()
    assert history_path.exists()

    replay = json.loads(replay_path.read_text(encoding="utf-8"))
    assert replay["evaluation_count"] == 1

    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    assert labels["row_count"] >= 2

    history = json.loads(history_path.read_text(encoding="utf-8"))
    assert history["request_id"].endswith("-req")
    assert history["quantum_solver"] == "sipqc_mc_proxy"

