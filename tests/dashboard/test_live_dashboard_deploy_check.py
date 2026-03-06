from __future__ import annotations

from pathlib import Path

from scripts.verify import live_dashboard_deploy_check as deploy_check


def test_validate_portfolio_payload_accepts_latest_schema():
    payload = {
        "schema_version": "dashboard.portfolio.v1",
        "status": "ok",
        "positions": [{"symbol": "AAPL", "account": "day_trade"}],
        "accounts": [{"label": "day_trade", "status": "ok", "positions": [], "position_count": 1}],
        "account_errors": [],
        "position_count_total": 1,
        "position_count_by_account": {"day_trade": 1},
        "account_count": 1,
        "consistency": {
            "account_count_requested": 1,
            "account_count_success": 1,
            "account_count_error": 0,
            "position_count_total": 1,
            "position_count_total_from_accounts": 1,
            "position_count_by_account": {"day_trade": 1},
            "requested_accounts": ["day_trade"],
            "accounts_match_requested": True,
            "positions_match_total": True,
            "has_account_errors": False,
        },
        "timestamp_utc": "2026-03-06T00:00:00+00:00",
    }

    assert deploy_check._validate_portfolio_payload(payload) == []


def test_validate_portfolio_payload_flags_missing_new_consistency_keys():
    payload = {
        "schema_version": "dashboard.portfolio.v1",
        "status": "ok",
        "positions": [{"symbol": "AAPL", "account": "day_trade"}],
        "accounts": [{"label": "day_trade", "status": "ok", "positions": [], "position_count": 1}],
        "account_errors": [],
        "position_count_total": 1,
        "position_count_by_account": {"day_trade": 1},
        "account_count": 1,
        "consistency": {
            "account_count_requested": 1,
            "account_count_success": 1,
            "account_count_error": 0,
            "position_count_total": 1,
            "position_count_by_account": {"day_trade": 1},
        },
        "timestamp_utc": "2026-03-06T00:00:00+00:00",
    }

    errors = deploy_check._validate_portfolio_payload(payload)

    assert "missing consistency key: position_count_total_from_accounts" in errors
    assert "missing consistency key: requested_accounts" in errors
    assert "missing consistency key: accounts_match_requested" in errors
    assert "missing consistency key: positions_match_total" in errors
    assert "missing consistency key: has_account_errors" in errors


def test_validate_execution_summary_payload_accepts_latest_schema():
    payload = {
        "schema_version": "dashboard.execution_summary.v1",
        "timestamp_utc": "2026-03-06T00:00:00+00:00",
        "routing": {
            "event_count": 2,
            "processed_candidate_count": 9,
            "submit_attempt_count": 5,
            "submit_success_count": 4,
            "broker_rejected_count": 1,
            "skipped_count": 3,
            "error_count": 1,
            "candidate_conversion_rate": 0.4444,
            "broker_accept_rate": 0.8,
            "skip_or_block_rate": 0.4444,
            "block_reason_category_counts": {"Risk Gate": 1},
            "raw_block_reason_counts": {"risk_gate:impact_budget": 1},
        },
        "live_orders": {
            "status": "ok",
            "lookback_hours": 24,
            "sample_window_start_utc": "2026-03-05T00:00:00+00:00",
            "account_count": 2,
            "order_count_total": 5,
            "filled_count": 1,
            "partially_filled_count": 1,
            "open_count": 1,
            "rejected_count": 1,
            "canceled_count": 1,
            "expired_count": 0,
            "other_count": 0,
            "fill_rate_any": 0.4,
            "fill_rate_full": 0.2,
            "open_rate": 0.2,
            "by_account": {"day_trade": {"order_count_total": 2}},
            "raw_status_counts": {"filled": 1},
            "account_errors": [],
        },
    }

    assert deploy_check._validate_execution_summary_payload(payload) == []


def test_validate_execution_summary_payload_flags_missing_keys():
    payload = {
        "schema_version": "dashboard.execution_summary.v1",
        "timestamp_utc": "2026-03-06T00:00:00+00:00",
        "routing": {
            "event_count": 2,
            "processed_candidate_count": 9,
        },
        "live_orders": {
            "status": "broken",
            "account_errors": {},
        },
    }

    errors = deploy_check._validate_execution_summary_payload(payload)

    assert "missing routing key: submit_attempt_count" in errors
    assert "missing live_orders key: order_count_total" in errors
    assert "invalid live_orders status: 'broken'" in errors
    assert "live_orders.account_errors must be a list" in errors


def test_check_frontend_detects_stale_chunk_and_missing_markers(tmp_path, monkeypatch):
    out_dir = tmp_path / "out" / "_next" / "static" / "chunks" / "app"
    out_dir.mkdir(parents=True)

    local_index = tmp_path / "out" / "index.html"
    local_index.write_text(
        '<html><body><script src="/_next/static/chunks/app/page-local123.js"></script></body></html>',
        encoding="utf-8",
    )
    (out_dir / "page-local123.js").write_text(
        (
            'console.log("Partial account failure"); '
            'console.log("Portfolio unavailable. All requested accounts failed."); '
            'console.log("Routing Funnel"); '
            'console.log("True Fill Rate"); '
            'console.log("Skip / Block Categories"); '
            'console.log("Top Raw Reasons");'
        ),
        encoding="utf-8",
    )

    def fake_fetch_text(url: str, headers: dict[str, str], timeout: float):
        if url == "http://example.com/":
            return 200, '<html><body><script src="/_next/static/chunks/app/page-live999.js"></script></body></html>'
        if url == "http://example.com/_next/static/chunks/app/page-live999.js":
            return 200, 'console.log("old frontend");'
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(deploy_check, "_fetch_text", fake_fetch_text)

    result = deploy_check._check_frontend("http://example.com", local_index, timeout=1.0)

    assert result["local_page_chunk"] == "/_next/static/chunks/app/page-local123.js"
    assert result["live_page_chunk"] == "/_next/static/chunks/app/page-live999.js"
    assert "Partial account failure" in result["missing_ui_markers"]
    assert "Portfolio unavailable. All requested accounts failed." in result["missing_ui_markers"]
    assert "Routing Funnel" in result["missing_ui_markers"]
    assert "True Fill Rate" in result["missing_ui_markers"]
    assert "Skip / Block Categories" in result["missing_ui_markers"]
    assert "Top Raw Reasons" in result["missing_ui_markers"]
    assert any("does not match local built chunk" in error for error in result["errors"])
