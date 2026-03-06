from __future__ import annotations

from dashboard.api import server


def _account(label: str) -> dict:
    return {
        "label": label,
        "api_key": f"{label}-key",
        "api_secret": f"{label}-secret",
        "base_url": "https://paper-api.alpaca.markets/v2",
    }


def test_portfolio_history_merges_dual_account_curves(monkeypatch):
    server._ALPACA_RESPONSE_CACHE.clear()
    accounts = [_account("day_trade"), _account("medium_long")]

    def fake_fetch(acct: dict, period: str, timeframe: str) -> dict:
        assert period == "1M"
        assert timeframe == "1D"
        if acct["label"] == "day_trade":
            return {
                "timestamp": [100, 200],
                "equity": [10000.0, 10100.0],
                "profit_loss": [0.0, 100.0],
                "profit_loss_pct": [0.0, 0.01],
                "base_value": 10000.0,
                "timeframe": "1D",
            }
        return {
            "timestamp": [100, 300],
            "equity": [25000.0, 25250.0],
            "profit_loss": [0.0, 250.0],
            "profit_loss_pct": [0.0, 0.01],
            "base_value": 25000.0,
            "timeframe": "1D",
        }

    monkeypatch.setattr(server, "_get_alpaca_accounts", lambda: accounts)
    monkeypatch.setattr(server, "_fetch_alpaca_history", fake_fetch)

    payload = server.portfolio_history(period="1M", timeframe="1D", account="all")

    assert payload["schema_version"] == "dashboard.portfolio_history.v1"
    assert payload["timestamp"] == [100, 200, 300]
    assert payload["equity"] == [35000.0, 35100.0, 35350.0]
    assert payload["profit_loss"] == [0.0, 100.0, 350.0]
    assert payload["profit_loss_pct"] == [0.0, 100.0 / 35000.0, 350.0 / 35000.0]
    assert payload["base_value"] == 35000.0
    assert payload["timeframe"] == "1D"
    assert payload["source_timestamp_utc"] is not None
    assert payload["latest_source_timestamp_utc"] is not None
    assert payload["fetched_at_utc"] is not None
    assert payload["cache_status"] in {"hit", "miss", "mixed"}
    assert set(payload["accounts"].keys()) == {"day_trade", "medium_long"}


def test_portfolio_history_single_account_returns_raw_history(monkeypatch):
    server._ALPACA_RESPONSE_CACHE.clear()
    account = _account("day_trade")
    history = {
        "timestamp": [100, 200],
        "equity": [10000.0, 10050.0],
        "profit_loss": [0.0, 50.0],
        "profit_loss_pct": [0.0, 0.005],
        "base_value": 10000.0,
        "timeframe": "1D",
    }

    monkeypatch.setattr(server, "_get_alpaca_accounts", lambda: [account])
    monkeypatch.setattr(server, "_fetch_alpaca_history", lambda acct, period, timeframe: history)

    payload = server.portfolio_history(period="1M", timeframe="1D", account="day_trade")

    assert payload["timestamp"] == history["timestamp"]
    assert payload["equity"] == history["equity"]
    assert payload["profit_loss"] == history["profit_loss"]
    assert payload["profit_loss_pct"] == history["profit_loss_pct"]
    assert payload["base_value"] == history["base_value"]
    assert payload["schema_version"] == "dashboard.portfolio_history.v1"
    assert payload["account"] == "day_trade"
    assert payload["requested_period"] == "1M"
    assert payload["requested_timeframe"] == "1D"
    assert payload["timestamp_utc"] is not None
    assert payload["source_timestamp_utc"] is not None
