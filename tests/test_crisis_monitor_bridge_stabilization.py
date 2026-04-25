from __future__ import annotations

import json
import sys
import types
from datetime import datetime, timezone
from pathlib import Path

import src.monitoring.crisis_monitor as crisis_monitor_module
from src.monitoring.crisis_monitor import CrisisMonitor


def _write_repo_config(repo_root: Path) -> None:
    config_dir = repo_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "assets_watchlist.yaml").write_text("symbols: []\n", encoding="utf-8")
    (config_dir / "data_trust_hierarchy.yaml").write_text(
        (
            "tiers:\n"
            "  tier_3_research:\n"
            "    weight: 0.5\n"
            "    sources:\n"
            "      - options_greeks\n"
            "      - options_greeks_bridge\n"
        ),
        encoding="utf-8",
    )
    (config_dir / "freshness_policy.yaml").write_text(
        "sources:\n  options_greeks_bridge:\n    freshness_ttl_minutes: 15\n",
        encoding="utf-8",
    )


def _write_feature_freshness_config(repo_root: Path) -> None:
    config_dir = repo_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "feature_registry.yaml").write_text(
        (
            "schema_version: feature_registry.v1\n"
            "features:\n"
            "  liquidity_score:\n"
            "    source: market_microstructure\n"
            "    version: v1\n"
            "    type: numeric\n"
            "    freshness_ttl_minutes: 5\n"
            "    description: core liquidity signal\n"
            "  volatility_penalty:\n"
            "    source: market_microstructure\n"
            "    version: v1\n"
            "    type: numeric\n"
            "    freshness_ttl_minutes: 5\n"
            "    description: core volatility signal\n"
            "  put_call_ratio:\n"
            "    source: options_greeks_bridge\n"
            "    version: v1\n"
            "    type: numeric\n"
            "    freshness_ttl_minutes: 15\n"
            "    description: advisory options signal\n"
        ),
        encoding="utf-8",
    )
    (config_dir / "feature_group_registry.yaml").write_text(
        (
            "schema_version: feature_group_registry.v1\n"
            "groups:\n"
            "  market_microstructure:\n"
            "    description: core execution features\n"
            "    features:\n"
            "      - liquidity_score\n"
            "      - volatility_penalty\n"
            "    freshness_policy: all_fresh\n"
            "    min_features_required: 2\n"
            "    operational_critical: true\n"
            "    consumers:\n"
            "      - pre_trade_controls\n"
            "  options_greeks:\n"
            "    description: advisory options features\n"
            "    features:\n"
            "      - put_call_ratio\n"
            "    freshness_policy: best_effort\n"
            "    min_features_required: 1\n"
            "    operational_critical: false\n"
            "    consumers:\n"
            "      - options_liquidity_filter\n"
            "freshness_policies:\n"
            "  all_fresh:\n"
            "    strategy: require_all\n"
            "  best_effort:\n"
            "    strategy: use_available\n"
            "    confidence_penalty_per_stale: 0.1\n"
            "  quorum:\n"
            "    strategy: minimum_count\n"
            "    min_fresh_ratio: 0.5\n"
        ),
        encoding="utf-8",
    )


def _cached_poll_payload() -> dict:
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_priority": "alpaca_options_snapshot",
        "source_tier": "tier_3_research",
        "trust_weight": 0.5,
        "symbols": {
            "SPY": {
                "fresh": True,
                "source": "alpaca_options_snapshot",
                "put_call_ratio": 0.93,
                "gamma_squeeze_risk": "moderate",
                "avg_implied_volatility_pct": 19.0,
                "net_gamma_exposure": 5000.0,
                "total_open_interest": 100,
            }
        },
        "vix_term_structure": {"structure": "flat", "signal": "caution"},
        "implied_vol_rank": {"iv_rank": 48.0},
        "aggregate_signals": {
            "avg_put_call_ratio": 0.93,
            "max_gamma_squeeze_risk": "moderate",
            "options_risk_level": "elevated",
            "vix_signal": "caution",
            "iv_rank_value": 48.0,
        },
    }


def test_stabilize_bridge_inputs_promotes_fresh_options_cache(tmp_path: Path):
    _write_repo_config(tmp_path)
    cache_dir = tmp_path / "logs" / "bridge_cache" / "options_greeks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "options_greeks_20260309_000000.json").write_text(
        json.dumps(_cached_poll_payload()),
        encoding="utf-8",
    )

    monitor = CrisisMonitor.__new__(CrisisMonitor)
    monitor.repo_root = tmp_path

    bridge_results = {
        "freshness": {"options_greeks": False},
        "summary": {"put_call_ratio": 0.0, "gamma_squeeze_risk": "unknown"},
        "bridge_errors": [],
        "options_greeks": {
            "fresh": False,
            "put_call_ratio": 0.0,
            "gamma_squeeze_risk": "unknown",
        },
    }

    stabilized = monitor._stabilize_bridge_inputs_for_scorecard(bridge_results)

    assert stabilized["freshness"]["options_greeks"] is True
    assert stabilized["summary"]["put_call_ratio"] == 0.93
    assert stabilized["summary"]["gamma_squeeze_risk"] == "moderate"
    assert stabilized["options_greeks"]["fresh"] is True
    assert any(
        "options_greeks_scorecard_stabilized" in item
        for item in stabilized["bridge_errors"]
    )


def test_feature_freshness_system_exit_degrades_without_crashing(
    tmp_path: Path,
    monkeypatch,
):
    class BrokenFeatureFreshnessEnforcer:
        def __init__(self, config_dir):
            self.is_loaded = True
            self._features = {"base_score": {"source": "fred"}}

        def summary(self, feature_timestamps, now):
            raise SystemExit("freshness enforcer exploded")

    fake_module = types.ModuleType("src.core.feature_freshness_enforcer")
    fake_module.FeatureFreshnessEnforcer = BrokenFeatureFreshnessEnforcer
    monkeypatch.setitem(sys.modules, "src.core.feature_freshness_enforcer", fake_module)

    monitor = CrisisMonitor.__new__(CrisisMonitor)
    monitor.repo_root = tmp_path
    monitor.events_dir = tmp_path / "missing" / "events"
    monitor.current_mode = "NORMAL"
    monitor.cycle_count = 7

    result = monitor._check_feature_freshness({"freshness": {"fred": True}})

    assert result["max_confidence_penalty"] == 0
    assert "freshness enforcer exploded" in result["error"]


def test_feature_freshness_ignores_advisory_staleness(tmp_path: Path):
    _write_feature_freshness_config(tmp_path)

    monitor = CrisisMonitor.__new__(CrisisMonitor)
    monitor.repo_root = tmp_path

    result = monitor._check_feature_freshness(
        {
            "freshness": {
                "market_microstructure": True,
                "options_greeks": False,
            }
        }
    )

    assert result["critical_degraded_groups"] == 0
    assert result["advisory_degraded_groups"] == 1
    assert result["active_degraded_groups"] == 0
    assert result["max_confidence_penalty"] == 0
    assert result["overall_max_confidence_penalty"] > 0


def test_position_alert_marks_manual_approval_required(tmp_path: Path):
    class DummyAlerter:
        def __init__(self):
            self.calls = []

        def _dispatch(self, title, body, level="info", extra=None):
            self.calls.append({
                "title": title,
                "body": body,
                "level": level,
                "extra": extra or {},
            })

    monitor = CrisisMonitor.__new__(CrisisMonitor)
    monitor.repo_root = tmp_path
    monitor.alerter = DummyAlerter()

    monitor._send_position_alert(
        {
            "actions_taken": 0,
            "proposed_close_count": 1,
            "manual_approval_required": True,
            "profits_taken": 1,
            "stops_hit": 0,
            "eod_flattened": 0,
            "close_details": [
                {
                    "symbol": "TEST",
                    "reason": "take_profit",
                    "unrealized_plpc": 0.05,
                    "unrealized_pl": 500.0,
                    "status": "pending_manual_approval",
                }
            ],
        }
    )

    assert len(monitor.alerter.calls) == 1
    call = monitor.alerter.calls[0]
    assert call["title"] == "Position Manager: 1 close(s) require approval"
    assert "Auto-close is blocked" in call["body"]
    assert call["extra"]["event"] == "position_management_review_required"
    assert call["extra"]["manual_approval_required"] is True


def test_run_cycle_uses_shared_control_snapshot_for_kill_switch(tmp_path: Path, monkeypatch) -> None:
    class DummyAlerter:
        def __init__(self) -> None:
            self.kill_switch_alerts = 0

        def send_kill_switch_alert(self) -> None:
            self.kill_switch_alerts += 1

    monitor = CrisisMonitor.__new__(CrisisMonitor)
    monitor.repo_root = tmp_path
    monitor.current_mode = "NORMAL"
    monitor.cycle_count = 0
    monitor.alerter = DummyAlerter()

    logged_events: list[tuple[str, dict[str, object]]] = []
    heartbeat_statuses: list[str] = []
    monitor._log_event = lambda event, payload: logged_events.append((event, payload))
    monitor._update_heartbeat = heartbeat_statuses.append
    monitor._poll_bridges = lambda: (_ for _ in ()).throw(
        AssertionError("kill switch should short-circuit before bridge polling")
    )

    monkeypatch.setattr(
        crisis_monitor_module,
        "read_control_state_snapshot",
        lambda repo_root: {"manual_veto": False, "kill_switch": True},
    )

    monitor._run_cycle()

    assert monitor.cycle_count == 1
    assert logged_events == [("kill_switch_active", {"cycle": 1})]
    assert heartbeat_statuses == ["kill_switch_active"]
    assert monitor.alerter.kill_switch_alerts == 1


def test_build_snapshot_uses_shared_control_snapshot_booleans() -> None:
    monitor = CrisisMonitor.__new__(CrisisMonitor)

    snapshot = monitor._build_snapshot(
        {
            "freshness": {"fred": True},
            "fallback_mode": True,
            "market_microstructure": {"spread": 0.01},
        },
        {"manual_veto": True, "kill_switch": False},
    )

    assert snapshot["market_microstructure"] == {"spread": 0.01}
    assert snapshot["data_freshness"] == {"fred": True}
    assert snapshot["fallback_mode"] is True
    assert snapshot["controls"] == {
        "manual_veto": True,
        "kill_switch": False,
    }
