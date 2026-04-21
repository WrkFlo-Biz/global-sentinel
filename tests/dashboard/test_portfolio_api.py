from __future__ import annotations

import json

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


def _broker_account(label: str, broker: str, display_label: str, is_live: bool = False) -> dict:
    return {
        "label": label,
        "broker": broker,
        "display_label": display_label,
        "account_number": f"{label}-acct",
        "is_live": is_live,
        "base_url": f"https://{broker}.example.test",
    }


def _clear_cache() -> None:
    server._ALPACA_RESPONSE_CACHE.clear()


def test_portfolio_aggregates_multi_account_schema(monkeypatch):
    _clear_cache()
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
    assert payload["source_timestamp_utc"] is not None
    assert payload["latest_source_timestamp_utc"] is not None
    assert payload["fetched_at_utc"] is not None
    assert payload["cache_status"] in {"hit", "miss", "mixed"}
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


def test_tastytrade_account_discovery_uses_named_env_accounts(monkeypatch):
    monkeypatch.setattr(server, "_load_env", lambda: None)
    monkeypatch.setenv("TASTYTRADE_USERNAME", "tester")
    monkeypatch.setenv("TASTYTRADE_PASSWORD", "secret")
    monkeypatch.setenv("TASTYTRADE_CASH_ACCOUNT", "CASH123")
    monkeypatch.setenv("TASTYTRADE_MARGIN_ACCOUNT", "MARGIN456")
    monkeypatch.setattr(
        server,
        "_discover_tastytrade_account_numbers",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not discover tastytrade accounts")),
    )

    accounts = server._get_tastytrade_accounts()

    assert [acct["account_number"] for acct in accounts] == ["CASH123", "MARGIN456"]
    assert [acct["label"] for acct in accounts] == ["tastytrade_CASH123", "tastytrade_MARGIN456"]
    assert all(acct["username"] == "tester" for acct in accounts)
    assert all(acct["password"] == "secret" for acct in accounts)


def test_tastytrade_session_cache_is_used_before_login(monkeypatch, tmp_path):
    _clear_cache()
    cache_path = tmp_path / ".tastytrade_session.json"
    cache_path.write_text(
        json.dumps(
            {
                "session_token": "cached-session-token",
                "token_type": "session",
                "timestamp": "2026-04-09T13:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(server, "TASTYTRADE_SESSION_CACHE_FILE", cache_path)
    monkeypatch.setattr(
        server,
        "_json_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network should not be used when cache is present")),
    )

    token = server._get_tastytrade_session(base_url="https://api.tastyworks.com", username=None, password=None)

    assert token == "cached-session-token"


def test_ibkr_account_discovery_uses_sequential_env_accounts(monkeypatch):
    monkeypatch.setattr(server, "_load_env", lambda: None)
    monkeypatch.setenv("IBKR_ACCOUNT_1", "U25016589")
    monkeypatch.setenv("IBKR_ACCOUNT_2", "U25027523")
    monkeypatch.setattr(
        server,
        "_discover_ibkr_account_numbers",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not discover ibkr accounts")),
    )

    accounts = server._get_ibkr_accounts()

    assert [acct["account_number"] for acct in accounts] == ["U25016589", "U25027523"]
    assert [acct["label"] for acct in accounts] == ["ibkr_U25016589", "ibkr_U25027523"]


def test_alpaca_account_discovery_normalizes_base_urls(monkeypatch):
    monkeypatch.setattr(server, "_load_env", lambda: None)
    monkeypatch.setenv("ALPACA_API_KEY", "day")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "day-secret")
    monkeypatch.setenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
    monkeypatch.setenv("ALPACA_API_KEY_MEDLONG", "ml")
    monkeypatch.setenv("ALPACA_SECRET_KEY_MEDLONG", "ml-secret")
    monkeypatch.setenv("ALPACA_BASE_URL_MEDLONG", "https://paper-api.alpaca.markets/")
    monkeypatch.setenv("ALPACA_API_KEY_LIVE", "live")
    monkeypatch.setenv("ALPACA_SECRET_KEY_LIVE", "live-secret")
    monkeypatch.setenv("ALPACA_BASE_URL_LIVE", "https://api.alpaca.markets")

    accounts = server._get_alpaca_accounts()
    by_label = {acct["label"]: acct for acct in accounts}

    assert by_label["day_trade"]["base_url"] == "https://paper-api.alpaca.markets/v2"
    assert by_label["medium_long"]["base_url"] == "https://paper-api.alpaca.markets/v2"
    assert by_label["live"]["base_url"] == "https://api.alpaca.markets/v2"


def test_portfolio_partial_failure_keeps_error_account_consistent(monkeypatch):
    _clear_cache()
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
    assert payload["source_timestamp_utc"] is not None
    assert payload["fetched_at_utc"] is not None
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


def test_portfolio_uses_live_state_manager_cache(monkeypatch):
    _clear_cache()

    class DummyLiveStateManager:
        def get_latest_portfolio(self):
            return {
                "schema_version": "dashboard.portfolio.v1",
                "status": "ok",
                "equity": 12345.0,
                "cash": 5000.0,
                "buying_power": 10000.0,
                "portfolio_value": 12345.0,
                "positions": [
                    {
                        "symbol": "XLE",
                        "qty": 1.0,
                        "side": "long",
                        "account": "day_trade",
                        "current_price": 100.0,
                        "market_value": 100.0,
                        "pricing_timestamp_utc": "2026-03-08T00:00:00+00:00",
                    }
                ],
                "accounts": [
                    {
                        "label": "day_trade",
                        "status": "ok",
                        "equity": 12345.0,
                        "cash": 5000.0,
                        "buying_power": 10000.0,
                        "portfolio_value": 12345.0,
                        "positions": [
                            {
                                "symbol": "XLE",
                                "qty": 1.0,
                                "side": "long",
                                "current_price": 100.0,
                                "market_value": 100.0,
                                "pricing_timestamp_utc": "2026-03-08T00:00:00+00:00",
                            }
                        ],
                        "position_count": 1,
                    }
                ],
                "account_errors": [],
                "position_count_total": 1,
                "position_count_by_account": {"day_trade": 1},
                "account_count": 1,
                "consistency": {},
                "timestamp_utc": "2026-03-08T00:00:00+00:00",
                "stream_health": {},
            }

    monkeypatch.setattr(server, "dashboard_live_state_manager", DummyLiveStateManager())
    monkeypatch.setattr(server, "_fetch_alpaca_account", lambda acct: (_ for _ in ()).throw(AssertionError("should not fetch direct account data")))

    payload = server.portfolio(account="all")

    assert payload["equity"] == 12345.0
    assert payload["position_count_total"] == 1
    assert payload["positions"][0]["symbol"] == "XLE"


def test_portfolio_aggregates_mixed_broker_accounts(monkeypatch):
    _clear_cache()
    accounts = [
        _broker_account("day_trade", "alpaca", "Alpaca Day Trade"),
        _broker_account("tastytrade_5WI54194", "tastytrade", "TastyTrade 5WI54194", is_live=True),
        _broker_account("ibkr_U25016589", "ibkr", "IBKR U25016589", is_live=True),
    ]
    snapshots = {
        "day_trade": {
            **_account_snapshot(
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
            ),
            "broker": "alpaca",
            "display_label": "Alpaca Day Trade",
            "is_live": False,
        },
        "tastytrade_5WI54194": {
            **_account_snapshot(
                "tastytrade_5WI54194",
                650.0,
                [
                    {
                        "symbol": "SPY",
                        "qty": 1.0,
                        "side": "long",
                        "avg_entry_price": 520.0,
                        "current_price": 525.0,
                        "unrealized_pl": 5.0,
                        "unrealized_plpc": 0.0096,
                        "market_value": 525.0,
                    }
                ],
            ),
            "broker": "tastytrade",
            "display_label": "TastyTrade 5WI54194",
            "is_live": True,
        },
        "ibkr_U25016589": {
            **_account_snapshot(
                "ibkr_U25016589",
                54.0,
                [],
            ),
            "broker": "ibkr",
            "display_label": "IBKR U25016589",
            "is_live": True,
        },
    }

    monkeypatch.setattr(server, "_get_portfolio_accounts", lambda: accounts)
    monkeypatch.setattr(server, "_get_cached_portfolio_account", lambda acct: snapshots[acct["label"]])

    payload = server.portfolio(account="all")

    assert payload["status"] == "ok"
    assert payload["equity"] == 10704.0
    assert payload["account_count"] == 3
    assert payload["position_count_by_account"] == {
        "day_trade": 1,
        "tastytrade_5WI54194": 1,
        "ibkr_U25016589": 0,
    }
    assert {position["account_label"] for position in payload["positions"]} == {"day_trade", "tastytrade_5WI54194"}
    assert {account["broker"] for account in payload["accounts"]} == {"alpaca", "tastytrade", "ibkr"}


def test_tastytrade_snapshot_fallback_used_when_live_fetch_times_out(monkeypatch, tmp_path):
    _clear_cache()
    snapshot_path = tmp_path / "tastytrade.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-04-09T13:00:00+00:00",
                "source": "local_sync",
                "accounts": [
                    {
                        "label": "tastytrade_5WI54194",
                        "broker": "tastytrade",
                        "display_label": "TastyTrade 5WI54194",
                        "account_number": "5WI54194",
                        "equity": 1234.56,
                        "cash": 210.0,
                        "buying_power": 321.0,
                        "portfolio_value": 1234.56,
                        "positions": [
                            {
                                "symbol": "SPY",
                                "qty": 1.0,
                                "side": "long",
                                "avg_entry_price": 520.0,
                                "current_price": 525.0,
                                "unrealized_pl": 5.0,
                                "unrealized_plpc": 0.0096,
                                "market_value": 525.0,
                            }
                        ],
                        "position_count": 1,
                        "status": "ok",
                        "timestamp_utc": "2026-04-09T12:59:00+00:00",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(server, "TASTYTRADE_SNAPSHOT_FILE", snapshot_path)
    monkeypatch.setattr(
        server,
        "_tastytrade_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("connect timeout")),
    )

    acct = {
        "label": "tastytrade_5WI54194",
        "broker": "tastytrade",
        "display_label": "TastyTrade 5WI54194",
        "account_number": "5WI54194",
        "username": "tester",
        "password": "secret",
        "base_url": "https://api.tastyworks.com",
        "is_live": True,
    }

    payload = server._fetch_tastytrade_account(acct)

    assert payload["status"] == "ok"
    assert payload["data_source"] == "snapshot"
    assert payload["account_number"] == "5WI54194"
    assert payload["equity"] == 1234.56
    assert payload["cash"] == 210.0
    assert payload["buying_power"] == 321.0
    assert payload["position_count"] == 1
    assert payload["positions"][0]["symbol"] == "SPY"


def test_snapshot_fallback_works_for_all_brokers(monkeypatch, tmp_path):
    _clear_cache()
    snapshot_path = tmp_path / "portfolio.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "generated_at_utc": "2026-04-09T13:00:00+00:00",
                "source": "local_sync",
                "accounts": [
                    {
                        "label": "day_trade",
                        "broker": "alpaca",
                        "display_label": "Alpaca Day Trade",
                        "account_number": "alpaca-day-trade",
                        "equity": 1000.0,
                        "cash": 400.0,
                        "buying_power": 2000.0,
                        "portfolio_value": 1000.0,
                        "positions": [],
                        "position_count": 0,
                        "status": "ok",
                        "timestamp_utc": "2026-04-09T12:59:00+00:00",
                    },
                    {
                        "label": "tastytrade_5WI54194",
                        "broker": "tastytrade",
                        "display_label": "TastyTrade 5WI54194",
                        "account_number": "5WI54194",
                        "equity": 1234.56,
                        "cash": 210.0,
                        "buying_power": 321.0,
                        "portfolio_value": 1234.56,
                        "positions": [
                            {
                                "symbol": "SPY",
                                "qty": 1.0,
                                "side": "long",
                                "avg_entry_price": 520.0,
                                "current_price": 525.0,
                                "unrealized_pl": 5.0,
                                "unrealized_plpc": 0.0096,
                                "market_value": 525.0,
                            }
                        ],
                        "position_count": 1,
                        "status": "ok",
                        "timestamp_utc": "2026-04-09T12:59:00+00:00",
                    },
                    {
                        "label": "ibkr_U25016589",
                        "broker": "ibkr",
                        "display_label": "IBKR U25016589",
                        "account_number": "U25016589",
                        "equity": 456.0,
                        "cash": 123.0,
                        "buying_power": 789.0,
                        "portfolio_value": 456.0,
                        "positions": [],
                        "position_count": 0,
                        "status": "ok",
                        "timestamp_utc": "2026-04-09T12:59:00+00:00",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(server, "PORTFOLIO_SNAPSHOT_FILE", snapshot_path)
    monkeypatch.setattr(server, "TASTYTRADE_SNAPSHOT_FILE", snapshot_path)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("connect timeout")))
    monkeypatch.setattr(
        server,
        "_tastytrade_request",
        lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("connect timeout")),
    )

    alpaca_payload = server._fetch_alpaca_account(
        {
            "label": "day_trade",
            "broker": "alpaca",
            "display_label": "Alpaca Day Trade",
            "api_key": "day",
            "api_secret": "day-secret",
            "base_url": "https://paper-api.alpaca.markets/v2",
            "is_live": False,
        }
    )
    tastytrade_payload = server._fetch_tastytrade_account(
        {
            "label": "tastytrade_5WI54194",
            "broker": "tastytrade",
            "display_label": "TastyTrade 5WI54194",
            "account_number": "5WI54194",
            "username": "tester",
            "password": "secret",
            "base_url": "https://api.tastyworks.com",
            "is_live": True,
        }
    )
    ibkr_payload = server._fetch_ibkr_account(
        {
            "label": "ibkr_U25016589",
            "broker": "ibkr",
            "display_label": "IBKR U25016589",
            "account_number": "U25016589",
            "base_url": "https://localhost:5000/v1/api",
            "is_live": True,
        }
    )

    assert alpaca_payload["data_source"] == "snapshot"
    assert tastytrade_payload["data_source"] == "snapshot"
    assert ibkr_payload["data_source"] == "snapshot"
    assert {alpaca_payload["account_number"], tastytrade_payload["account_number"], ibkr_payload["account_number"]} == {
        "alpaca-day-trade",
        "5WI54194",
        "U25016589",
    }
