from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from src.bridges.options_greeks_bridge import OptionsGreeksBridge


def _write_repo_config(repo_root: Path) -> None:
    config_dir = repo_root / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "assets_watchlist.yaml").write_text("symbols: []\n", encoding="utf-8")
    (config_dir / "data_trust_hierarchy.yaml").write_text(
        """
tiers:
  tier_3_research:
    weight: 0.5
    sources:
      - options_greeks
      - options_greeks_bridge
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (config_dir / "freshness_policy.yaml").write_text(
        """
sources:
  options_greeks_bridge:
    freshness_ttl_minutes: 15
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _cached_poll_payload() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "timestamp_utc": now,
        "source_priority": "alpaca_options_snapshot",
        "source_tier": "tier_3_research",
        "trust_weight": 0.5,
        "symbols": {
            "SPY": {
                "fresh": True,
                "source": "alpaca_options_snapshot",
                "put_call_ratio": 0.91,
                "gamma_squeeze_risk": "moderate",
                "avg_implied_volatility_pct": 21.0,
                "net_gamma_exposure": 12345.0,
                "total_open_interest": 100,
            }
        },
        "vix_term_structure": {"structure": "flat", "signal": "caution"},
        "implied_vol_rank": {"iv_rank": 54.0},
        "aggregate_signals": {
            "avg_put_call_ratio": 0.91,
            "max_gamma_squeeze_risk": "moderate",
            "options_risk_level": "elevated",
            "vix_signal": "caution",
            "iv_rank_value": 54.0,
        },
    }


def test_load_latest_cached_snapshot_returns_snapshot_section(tmp_path: Path):
    _write_repo_config(tmp_path)
    cache_dir = tmp_path / "logs" / "bridge_cache" / "options_greeks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = _cached_poll_payload()
    (cache_dir / "options_greeks_20260308_000000.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    bridge = OptionsGreeksBridge(tmp_path)
    snapshot = bridge.load_latest_cached_snapshot()

    assert snapshot is not None
    assert snapshot["fresh"] is True
    assert snapshot["source"] == "alpaca_options_snapshot"
    assert snapshot["put_call_ratio"] == 0.91
    assert snapshot["gamma_squeeze_risk"] == "moderate"


def test_fetch_uses_fresh_cached_snapshot_when_live_result_is_stale(tmp_path: Path, monkeypatch):
    _write_repo_config(tmp_path)
    cache_dir = tmp_path / "logs" / "bridge_cache" / "options_greeks"
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = _cached_poll_payload()
    (cache_dir / "options_greeks_20260308_000000.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    bridge = OptionsGreeksBridge(tmp_path)

    stale_poll = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_priority": "fallback_heuristic",
        "source_tier": "tier_3_research",
        "trust_weight": 0.5,
        "symbols": {
            "SPY": {
                "fresh": False,
                "source": "fallback_heuristic",
                "put_call_ratio": 0.0,
                "gamma_squeeze_risk": "unknown",
                "avg_implied_volatility_pct": 0.0,
                "net_gamma_exposure": 0.0,
                "total_open_interest": 0,
            }
        },
        "vix_term_structure": {"available": False},
        "implied_vol_rank": {"available": False},
        "aggregate_signals": {
            "avg_put_call_ratio": 0.0,
            "max_gamma_squeeze_risk": "unknown",
            "options_risk_level": "normal",
            "vix_signal": "normal",
            "iv_rank_value": 50.0,
        },
    }
    monkeypatch.setattr(bridge, "poll", lambda symbols=None: stale_poll)

    snapshot = bridge.fetch()

    assert snapshot["fresh"] is True
    assert snapshot["source"] == "alpaca_options_snapshot"
    assert snapshot["source_detail"] == "cached_snapshot_fallback"
    assert snapshot["put_call_ratio"] == 0.91


def test_empty_alpaca_snapshot_falls_back_to_vix_heuristic(tmp_path: Path, monkeypatch):
    _write_repo_config(tmp_path)
    bridge = OptionsGreeksBridge(tmp_path)

    monkeypatch.setattr(bridge, "_fetch_underlying_price", lambda symbol: 600.0)
    monkeypatch.setattr(bridge, "_fetch_vix_price", lambda: 28.0)
    monkeypatch.setattr(bridge, "_fetch_vix_history", lambda days=252: [14.0, 18.0, 22.0, 30.0, 35.0])
    monkeypatch.setattr(
        bridge,
        "_fetch_options_snapshot",
        lambda symbol: {
            "snapshots": {
                f"{symbol}C00000000": {
                    "greeks": {"delta": 0, "gamma": 0, "impliedVolatility": 0},
                    "latestQuote": {},
                    "openInterest": 0,
                }
            }
        },
    )

    result = bridge._analyze_symbol_options("SPY")

    assert result["fresh"] is True
    assert result["source"] == "vix_heuristic"
    assert result["source_detail"] == "alpaca_snapshot_empty"
    assert result["put_call_ratio"] > 0.0
    assert result["avg_implied_volatility_pct"] == 28.0
