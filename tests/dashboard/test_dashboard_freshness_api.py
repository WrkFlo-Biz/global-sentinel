from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dashboard.api import server


def _account(label: str) -> dict:
    return {
        "label": label,
        "api_key": f"{label}-key",
        "api_secret": f"{label}-secret",
        "base_url": "https://paper-api.alpaca.markets/v2",
    }


def _account_snapshot(label: str, equity: float, positions: list[dict]) -> dict:
    return {
        "label": label,
        "account_number": f"{label}-acct",
        "equity": equity,
        "cash": equity / 2,
        "buying_power": equity * 2,
        "portfolio_value": equity,
        "positions": positions,
        "position_count": len(positions),
        "status": "ok",
        "timestamp_utc": "2026-03-08T00:00:00+00:00",
        "source_timestamp_utc": "2026-03-08T00:00:00+00:00",
    }


def test_bridge_cache_snapshot_ignores_housekeeping_files(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)
    bridge_dir = tmp_path / "logs" / "bridge_cache" / "fred_bridge"
    bridge_dir.mkdir(parents=True)

    payload = bridge_dir / "fred_snapshot.json"
    payload.write_text('{"ok": true}', encoding="utf-8")
    seen_hashes = bridge_dir / "seen_hashes.json"
    seen_hashes.write_text("{}", encoding="utf-8")

    newer = datetime.now(timezone.utc).timestamp()
    older = (datetime.now(timezone.utc) - timedelta(minutes=10)).timestamp()
    seen_hashes.touch()
    payload.touch()
    Path(seen_hashes).stat()
    Path(payload).stat()

    # Force housekeeping file to be newer than the real payload.
    import os

    os.utime(payload, (older, older))
    os.utime(seen_hashes, (newer, newer))

    snapshot = server._bridge_cache_snapshot("fred_bridge")

    assert snapshot["file_count"] == 2
    assert snapshot["json_file_count"] == 2
    assert snapshot["latest_file"] == "fred_snapshot.json"
    assert snapshot["latest_age_min"] is not None


def test_portfolio_payload_includes_pricing_summary(monkeypatch):
    server._ALPACA_RESPONSE_CACHE.clear()
    monkeypatch.setattr(server, "_get_alpaca_accounts", lambda: [_account("day_trade")])
    stale_price_timestamp = (datetime.now(timezone.utc) - timedelta(hours=6)).isoformat()

    def fake_fetch(acct: dict) -> dict:
        return _account_snapshot(
            "day_trade",
            10000.0,
            [
                {
                    "symbol": "AAPL",
                    "qty": 2.0,
                    "side": "long",
                    "avg_entry_price": 100.0,
                    "current_price": 102.0,
                    "unrealized_pl": 4.0,
                    "unrealized_plpc": 0.02,
                    "market_value": 204.0,
                    "pricing_timestamp_utc": stale_price_timestamp,
                }
            ],
        )

    monkeypatch.setattr(server, "_fetch_alpaca_account", fake_fetch)
    monkeypatch.setattr(server, "dashboard_live_state_manager", None)

    payload = server.portfolio(account="all")

    assert payload["pricing_summary"]["position_count"] == 1
    assert payload["pricing_summary"]["stale_position_count"] == 1
    assert payload["pricing_summary"]["market_data_health"] == "stale"


def test_dashboard_live_state_manager_start_is_non_blocking(monkeypatch):
    monkeypatch.setattr(server, "_get_alpaca_accounts", lambda: [])
    manager = server.DashboardLiveStateManager()
    started = asyncio.Event()
    gate = asyncio.Event()

    async def slow_refresh(force: bool, reason: str):
        started.set()
        await gate.wait()

    monkeypatch.setattr(manager, "refresh_and_broadcast", slow_refresh)

    async def run() -> None:
        await asyncio.wait_for(manager.start(), timeout=1.0)
        await asyncio.wait_for(started.wait(), timeout=1.0)
        gate.set()
        await asyncio.wait_for(manager.stop(), timeout=1.0)

    asyncio.run(run())


def test_stock_market_data_symbol_filter_excludes_crypto_pairs():
    assert server._is_stock_market_data_symbol("AAPL") is True
    assert server._is_stock_market_data_symbol("SPY") is True
    assert server._is_stock_market_data_symbol("BTCUSD") is False
    assert server._is_stock_market_data_symbol("ETH/USD") is False


def test_v6_warroom_exposes_source_freshness_and_bridge_status(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)
    scorecard_dir = tmp_path / "logs" / "scorecards"
    scorecard_dir.mkdir(parents=True)

    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    scorecard = {
        "timestamp_utc": old_ts,
        "regime_shift_probability": 0.42,
        "mode": "ELEVATED",
        "confidence": 0.77,
        "data_freshness_status": {"options_greeks": False},
        "bridge_summary": {"put_call_ratio": 0.66},
    }
    (scorecard_dir / "scorecard_20260308_000000.json").write_text(json.dumps(scorecard), encoding="utf-8")

    payload = server.v6_warroom()

    assert payload["source_timestamp_utc"] == old_ts
    assert payload["source_freshness"] == "stale"
    assert payload["bridge_health"]["options_greeks"]["status"] == "stale"


def test_bridge_status_prefers_recent_snapshot_over_stale_scorecard(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)
    scorecard_dir = tmp_path / "logs" / "scorecards"
    cache_dir = tmp_path / "logs" / "bridge_cache" / "fed_board_bridge"
    scorecard_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)

    scorecard = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "data_freshness_status": {"fed_board": False},
        "bridge_summary": {},
    }
    (scorecard_dir / "scorecard_20260308_000000.json").write_text(json.dumps(scorecard), encoding="utf-8")
    (cache_dir / "fed_board_20260308_000000.json").write_text('{"ok": true}', encoding="utf-8")

    payload = server.bridge_status()

    fed = payload["bridges"]["fed_board"]
    assert fed["status"] == "source_live"
    assert fed["display_status"] == "SOURCE LIVE"
    assert fed["snapshot_recent"] is True


def test_bridge_status_marks_recent_empty_payload_as_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)
    scorecard_dir = tmp_path / "logs" / "scorecards"
    cache_dir = tmp_path / "logs" / "bridge_cache" / "gdelt"
    scorecard_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)

    scorecard = {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "data_freshness_status": {"gdelt": False},
        "bridge_summary": {"gdelt_event_count": 0},
    }
    (scorecard_dir / "scorecard_20260308_000000.json").write_text(json.dumps(scorecard), encoding="utf-8")
    (cache_dir / "gdelt_20260308_000000.json").write_text('{"items": []}', encoding="utf-8")

    payload = server.bridge_status()

    gdelt = payload["bridges"]["gdelt"]
    assert gdelt["status"] == "empty"
    assert gdelt["display_status"] == "EMPTY"
    assert gdelt["snapshot_recent"] is True


def test_dashboard_layout_accepts_quantum_widget(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "LAYOUT_PATH", tmp_path / "dashboard_layout.json")
    monkeypatch.setattr(server, "LAYOUT_BACKUP_DIR", tmp_path / "dashboard_layout_backups")

    class DummyRequest:
        async def json(self):
            return {
                "updated_by": "test",
                "rows": [
                    {
                        "id": "row_quantum",
                        "widgets": [
                            {
                                "id": "quantum_comparison",
                                "cols": 12,
                                "title": "Quantum",
                                "visible": True,
                            }
                        ],
                    }
                ],
            }

    response = asyncio.run(server.set_dashboard_layout(DummyRequest()))

    assert response["ok"] is True


def test_get_dashboard_layout_upgrades_missing_quantum_widget(tmp_path, monkeypatch):
    layout_path = tmp_path / "dashboard_layout.json"
    layout_path.write_text(
        json.dumps(
            {
                "version": 1,
                "updated_at": "2026-03-08T00:00:00+00:00",
                "updated_by": "test",
                "rows": [
                    {
                        "id": "row_existing",
                        "widgets": [
                            {
                                "id": "portfolio",
                                "cols": 12,
                                "title": "Portfolio",
                                "visible": True,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "LAYOUT_PATH", layout_path)

    payload = server.get_dashboard_layout()

    widget_ids = [widget["id"] for row in payload["rows"] for widget in row["widgets"]]
    assert "quantum_comparison" in widget_ids
    assert payload["upgraded_widgets"] == ["quantum_comparison"]
