from __future__ import annotations

from dashboard.api import server


def _router_event(payload: dict) -> dict:
    return {
        "event_type": "route_package_complete",
        "payload": payload,
    }


def _alpaca_account(label: str) -> dict:
    return {
        "label": label,
        "api_key": f"{label}-key",
        "api_secret": f"{label}-secret",
        "base_url": "https://paper-api.alpaca.markets/v2",
    }


def test_execution_summary_separates_routing_and_fill_state(monkeypatch):
    router_rows = [
        _router_event(
            {
                "candidate_count_in_package": 5,
                "submit_attempt_count": 3,
                "submitted_open_or_ack_count": 2,
                "broker_rejected_count": 1,
                "skipped_candidates": [
                    {
                        "reason": "risk_gate_blocked",
                        "risk_gate": {
                            "gates": [
                                {"gate": "impact_budget", "pass": False},
                            ]
                        },
                    },
                    {"reason": "max_orders_reached"},
                ],
                "errors": [{"error": 'asset "IYT" cannot be sold short'}],
            }
        ),
        _router_event(
            {
                "candidate_count_in_package": 4,
                "submit_attempt_count": 2,
                "submitted_open_or_ack_count": 2,
                "broker_rejected_count": 0,
                "skipped_candidates": [{"reason": "below_min_confidence"}],
                "errors": [],
            }
        ),
    ]

    accounts = [_alpaca_account("day_trade"), _alpaca_account("medium_long")]

    def fake_fetch_orders(acct: dict, limit: int = 100, status: str = "all") -> list[dict]:
        if acct["label"] == "day_trade":
            return [
                {"status": "filled", "submitted_at": "2026-03-06T17:00:00Z"},
                {"status": "new", "submitted_at": "2026-03-06T17:10:00Z"},
            ]
        return [
            {"status": "partially_filled", "submitted_at": "2026-03-06T17:20:00Z"},
            {"status": "rejected", "submitted_at": "2026-03-06T17:30:00Z"},
            {"status": "canceled", "submitted_at": "2026-03-06T17:40:00Z"},
        ]

    monkeypatch.setattr(server, "load_jsonl", lambda path, limit=500: router_rows)
    monkeypatch.setattr(server, "_get_alpaca_accounts", lambda: accounts)
    monkeypatch.setattr(server, "_fetch_alpaca_orders", fake_fetch_orders)

    summary = server.execution_summary(router_limit=20, broker_limit=20, lookback_hours=24)

    assert summary["schema_version"] == "dashboard.execution_summary.v1"

    routing = summary["routing"]
    assert routing["processed_candidate_count"] == 9
    assert routing["submit_attempt_count"] == 5
    assert routing["submit_success_count"] == 4
    assert routing["broker_rejected_count"] == 1
    assert routing["skipped_count"] == 3
    assert routing["error_count"] == 1
    assert routing["candidate_conversion_rate"] == 0.4444
    assert routing["broker_accept_rate"] == 0.8
    assert routing["skip_or_block_rate"] == 0.4444
    assert routing["block_reason_category_counts"] == {
        "Risk Gate": 1,
        "Capacity": 1,
        "Shortability": 1,
        "Confidence": 1,
    }
    assert routing["raw_block_reason_counts"] == {
        "risk_gate:impact_budget": 1,
        "max_orders_reached": 1,
        'asset "IYT" cannot be sold short': 1,
        "below_min_confidence": 1,
    }

    live_orders = summary["live_orders"]
    assert live_orders["status"] == "ok"
    assert live_orders["account_count"] == 2
    assert live_orders["order_count_total"] == 5
    assert live_orders["filled_count"] == 1
    assert live_orders["partially_filled_count"] == 1
    assert live_orders["open_count"] == 1
    assert live_orders["rejected_count"] == 1
    assert live_orders["canceled_count"] == 1
    assert live_orders["fill_rate_any"] == 0.4
    assert live_orders["fill_rate_full"] == 0.2
    assert live_orders["open_rate"] == 0.2
    assert live_orders["by_account"]["day_trade"]["fill_rate_full"] == 0.5
    assert live_orders["by_account"]["medium_long"]["fill_rate_any"] == 0.3333
