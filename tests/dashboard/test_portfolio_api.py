from __future__ import annotations

from dashboard.api import server


def _account(label: str) -> dict:
    return {"label": label, "api_key": f"{label}-key", "api_secret": f"{label}-secret", "base_url": "https://paper-api.alpaca.markets/v2"}


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
        "timestamp_utc": "2026-03-06T00:00:00+00:00",
    }


def test_portfolio_aggregates_multi_account_schema(monkeypatch):
    accounts = [_account("day_trade"), _account("medium_long")]

    def fake_fetch(acct: dict) -> dict:
        if acct["label"] == "day_trade":
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
                    }
                ],
            )
        return _account_snapshot(
            "medium_long",
            25000.0,
            [
                {
                    "symbol": "GLD",
                    "qty": 3.0,
                    "side": "long",
                    "avg_entry_price": 50.0,
                    "current_price": 55.0,
                    "unrealized_pl": 15.0,
                    "unrealized_plpc": 0.10,
                    "market_value": 165.0,
                },
                {
                    "symbol": "TLT",
                    "qty": 1.0,
                    "side": "long",
                    "avg_entry_price": 90.0,
                    "current_price": 92.0,
                    "unrealized_pl": 2.0,
                    "unrealized_plpc": 0.0222,
                    "market_value": 92.0,
                },
            ],
        )

    monkeypatch.setattr(server, "_get_alpaca_accounts", lambda: accounts)
    monkeypatch.setattr(server, "_fetch_alpaca_account", fake_fetch)

    payload = server.portfolio(account="all")

    assert payload["schema_version"] == "dashboard.portfolio.v1"
    assert payload["status"] == "ok"
    assert payload["equity"] == 35000.0
    assert payload["position_count_total"] == 3
    assert payload["position_count_by_account"] == {"day_trade": 1, "medium_long": 2}
    assert payload["account_errors"] == []
    assert payload["consistency"]["account_count_requested"] == 2
    assert payload["consistency"]["account_count_success"] == 2
    assert payload["consistency"]["account_count_error"] == 0
    assert payload["consistency"]["position_count_total_from_accounts"] == 3
    assert payload["consistency"]["positions_match_total"] is True
    assert payload["consistency"]["accounts_match_requested"] is True
    assert payload["consistency"]["requested_accounts"] == ["day_trade", "medium_long"]
    assert {position["account"] for position in payload["positions"]} == {"day_trade", "medium_long"}


def test_portfolio_partial_failure_keeps_error_account_consistent(monkeypatch):
    accounts = [_account("day_trade"), _account("medium_long")]

    def fake_fetch(acct: dict) -> dict:
        if acct["label"] == "medium_long":
            raise RuntimeError("timeout from broker")
        return _account_snapshot(
            "day_trade",
            10000.0,
            [
                {
                    "symbol": "NVDA",
                    "qty": 1.0,
                    "side": "long",
                    "avg_entry_price": 120.0,
                    "current_price": 121.0,
                    "unrealized_pl": 1.0,
                    "unrealized_plpc": 0.0083,
                    "market_value": 121.0,
                }
            ],
        )

    monkeypatch.setattr(server, "_get_alpaca_accounts", lambda: accounts)
    monkeypatch.setattr(server, "_fetch_alpaca_account", fake_fetch)

    payload = server.portfolio(account="all")

    assert payload["status"] == "partial"
    assert payload["position_count_total"] == 1
    assert payload["position_count_by_account"] == {"day_trade": 1, "medium_long": 0}
    assert payload["account_errors"] == [{"label": "medium_long", "error": "timeout from broker"}]
    assert payload["consistency"]["account_count_requested"] == 2
    assert payload["consistency"]["account_count_success"] == 1
    assert payload["consistency"]["account_count_error"] == 1
    assert payload["consistency"]["position_count_total_from_accounts"] == 1
    assert payload["consistency"]["positions_match_total"] is True
    assert payload["consistency"]["has_account_errors"] is True

    failed_account = next(account for account in payload["accounts"] if account["label"] == "medium_long")
    assert failed_account["status"] == "error"
    assert failed_account["equity"] == 0.0
    assert failed_account["cash"] == 0.0
    assert failed_account["buying_power"] == 0.0
    assert failed_account["portfolio_value"] == 0.0
    assert failed_account["positions"] == []
    assert failed_account["position_count"] == 0
