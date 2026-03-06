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

    assert payload["timestamp"] == [100, 200, 300]
    assert payload["equity"] == [35000.0, 35100.0, 35350.0]
    assert payload["profit_loss"] == [0.0, 100.0, 350.0]
    assert payload["profit_loss_pct"] == [0.0, 100.0 / 35000.0, 350.0 / 35000.0]
    assert payload["base_value"] == 35000.0
    assert payload["timeframe"] == "1D"
    assert set(payload["accounts"].keys()) == {"day_trade", "medium_long"}


def test_portfolio_history_single_account_returns_raw_history(monkeypatch):
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

    assert payload == history
