#!/usr/bin/env python3
"""Tests for broker_order_audit.py (v2 classification schema)"""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import MagicMock

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.execution.broker_order_audit import (
    BrokerOrderAudit,
    BUCKET_PENDING_CLOSE,
    BUCKET_STALE_OPEN,
    BUCKET_DUPLICATE,
    BUCKET_CRYPTO_ORPHAN,
    BUCKET_PARTIAL_FILL,
    BUCKET_REJECTED,
    BUCKET_POSITION_LEAK,
    safe_float,
    parse_ts,
)


NOW = datetime(2026, 3, 9, 14, 0, 0, tzinfo=timezone.utc)


class FakeAdapter:
    """Lightweight mock that matches AlpacaPaperAdapter contract."""
    def __init__(self, account_state=None, positions=None, open_orders=None):
        self._account_state = account_state or {"equity": 100000, "cash": 90000, "buying_power": 120000}
        self._positions = positions or []
        self._open_orders = open_orders or []
        self.cancel_calls: list[str] = []

    def get_account_state(self):
        return dict(self._account_state)

    def list_positions(self):
        return [dict(p) for p in self._positions]

    def list_open_orders(self):
        return [dict(o) for o in self._open_orders]

    def cancel_order(self, order_id):
        self.cancel_calls.append(order_id)
        return {"order_id": order_id, "status": "canceled"}


def _order(order_id="ord-1", symbol="AAPL", side="sell", qty=10, filled_qty=0,
           status="accepted", submitted_hours_ago=1, limit_price=None, **extra):
    submitted = (NOW - timedelta(hours=submitted_hours_ago)).isoformat()
    base = {
        "order_id": order_id,
        "symbol": symbol,
        "side": side,
        "type": extra.pop("order_type", "market"),
        "status": status,
        "qty": qty,
        "filled_qty": filled_qty,
        "remaining_qty": qty - filled_qty,
        "limit_price": limit_price,
        "avg_fill_price": None,
        "submitted_at_utc": submitted,
        "updated_at_utc": submitted,
    }
    base.update(extra)
    return base


def _position(symbol="AAPL", qty=10, market_value=1500.0):
    return {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": 150.0,
        "market_value": market_value,
        "unrealized_pl": 0.0,
        "side": "short" if qty < 0 else "long",
    }


# ── Classification tests ───────────────────────────────────────────

def test_pending_close_sell_with_long_position():
    """Sell order with matching long position = pending_close"""
    audit = BrokerOrderAudit(
        accounts={"test": FakeAdapter(
            positions=[_position("AAPL", qty=10)],
            open_orders=[_order(symbol="AAPL", side="sell", qty=10)],
        )},
        dry_run=True,
    )
    report = audit.run_audit(now=NOW)
    classified = report["accounts"]["test"]["classified_orders"]
    assert len(classified) == 1
    assert classified[0]["bucket"] == BUCKET_PENDING_CLOSE


def test_pending_close_buy_with_short_position():
    """Buy order with matching short position = pending_close"""
    audit = BrokerOrderAudit(
        accounts={"test": FakeAdapter(
            positions=[_position("AAL", qty=-50)],
            open_orders=[_order(symbol="AAL", side="buy", qty=50)],
        )},
        dry_run=True,
    )
    report = audit.run_audit(now=NOW)
    classified = report["accounts"]["test"]["classified_orders"]
    assert classified[0]["bucket"] == BUCKET_PENDING_CLOSE


def test_stale_order_old():
    """Order older than 24h with no position = stale"""
    audit = BrokerOrderAudit(
        accounts={"test": FakeAdapter(
            open_orders=[_order(symbol="XYZ", side="buy", submitted_hours_ago=48)],
        )},
        dry_run=True,
    )
    report = audit.run_audit(now=NOW)
    classified = report["accounts"]["test"]["classified_orders"]
    assert classified[0]["bucket"] == BUCKET_STALE_OPEN
    assert classified[0]["age_hours"] == 48.0


def test_stale_buy_no_position():
    """Fresh buy order with no matching position is treated as a new entry."""
    audit = BrokerOrderAudit(
        accounts={"test": FakeAdapter(
            open_orders=[_order(symbol="XYZ", side="buy", submitted_hours_ago=2)],
        )},
        dry_run=True,
    )
    report = audit.run_audit(now=NOW)
    classified = report["accounts"]["test"]["classified_orders"]
    assert classified[0]["bucket"] == "new_entry"


def test_crypto_orphan():
    """Order with / in symbol = crypto orphan"""
    audit = BrokerOrderAudit(
        accounts={"test": FakeAdapter(
            open_orders=[_order(symbol="RENDER/USD", side="buy", qty=100)],
        )},
        dry_run=True,
    )
    report = audit.run_audit(now=NOW)
    classified = report["accounts"]["test"]["classified_orders"]
    assert classified[0]["bucket"] == BUCKET_CRYPTO_ORPHAN


def test_partial_fill():
    """Partially filled order"""
    audit = BrokerOrderAudit(
        accounts={"test": FakeAdapter(
            positions=[_position("AAPL", qty=10)],
            open_orders=[_order(symbol="AAPL", side="sell", qty=10, filled_qty=3)],
        )},
        dry_run=True,
    )
    report = audit.run_audit(now=NOW)
    classified = report["accounts"]["test"]["classified_orders"]
    assert classified[0]["bucket"] == BUCKET_PARTIAL_FILL


def test_rejected_order():
    """Rejected order"""
    audit = BrokerOrderAudit(
        accounts={"test": FakeAdapter(
            open_orders=[_order(symbol="AAPL", side="buy", status="rejected")],
        )},
        dry_run=True,
    )
    report = audit.run_audit(now=NOW)
    classified = report["accounts"]["test"]["classified_orders"]
    assert classified[0]["bucket"] == BUCKET_REJECTED


# ── Duplicate detection ─────────────────────────────────────────────

def test_duplicate_close_orders():
    """Multiple sell orders for same symbol, older kept, newer marked duplicate"""
    audit = BrokerOrderAudit(
        accounts={"test": FakeAdapter(
            positions=[_position("AAPL", qty=10)],
            open_orders=[
                _order(order_id="old", symbol="AAPL", side="sell", qty=10, submitted_hours_ago=3),
                _order(order_id="new", symbol="AAPL", side="sell", qty=10, submitted_hours_ago=1),
            ],
        )},
        dry_run=True,
    )
    report = audit.run_audit(now=NOW)
    classified = report["accounts"]["test"]["classified_orders"]
    buckets = {o["order_id"]: o["bucket"] for o in classified}
    assert buckets["old"] == BUCKET_PENDING_CLOSE
    assert buckets["new"] == BUCKET_DUPLICATE


# ── Position leak detection ─────────────────────────────────────────

def test_position_with_no_close_order():
    """Position with no corresponding close order = leak"""
    audit = BrokerOrderAudit(
        accounts={"test": FakeAdapter(
            positions=[_position("TSLA", qty=5)],
            open_orders=[],
        )},
        dry_run=True,
    )
    report = audit.run_audit(now=NOW)
    leaks = report["accounts"]["test"]["position_leaks"]
    assert len(leaks) == 1
    assert leaks[0]["symbol"] == "TSLA"
    assert leaks[0]["bucket"] == BUCKET_POSITION_LEAK


def test_no_leak_when_close_exists():
    """Position with a close order = no leak"""
    audit = BrokerOrderAudit(
        accounts={"test": FakeAdapter(
            positions=[_position("AAPL", qty=10)],
            open_orders=[_order(symbol="AAPL", side="sell", qty=10)],
        )},
        dry_run=True,
    )
    report = audit.run_audit(now=NOW)
    leaks = report["accounts"]["test"]["position_leaks"]
    assert len(leaks) == 0


# ── Execute mode ────────────────────────────────────────────────────

def test_dry_run_does_not_cancel():
    """In dry-run mode, cancel_order is never called"""
    adapter = FakeAdapter(open_orders=[_order(symbol="RENDER/USD", side="buy")])
    audit = BrokerOrderAudit(accounts={"test": adapter}, dry_run=True)
    audit.run_audit(now=NOW)
    assert len(adapter.cancel_calls) == 0


def test_execute_cancels_stale():
    """In execute mode, stale orders get cancelled"""
    adapter = FakeAdapter(open_orders=[
        _order(order_id="stale-1", symbol="XYZ", side="buy", submitted_hours_ago=48),
    ])
    audit = BrokerOrderAudit(accounts={"test": adapter}, dry_run=False)
    report = audit.run_audit(now=NOW)
    assert "stale-1" in adapter.cancel_calls
    assert report["accounts"]["test"]["cancelled_count"] == 1


def test_execute_cancels_crypto():
    """In execute mode, crypto orphans get cancelled"""
    adapter = FakeAdapter(open_orders=[
        _order(order_id="crypto-1", symbol="BTC/USD", side="buy"),
    ])
    audit = BrokerOrderAudit(accounts={"test": adapter}, dry_run=False)
    audit.run_audit(now=NOW)
    assert "crypto-1" in adapter.cancel_calls


def test_execute_cancels_duplicate():
    """In execute mode, duplicate close orders get cancelled"""
    adapter = FakeAdapter(
        positions=[_position("AAPL", qty=10)],
        open_orders=[
            _order(order_id="old", symbol="AAPL", side="sell", qty=10, submitted_hours_ago=3),
            _order(order_id="dup", symbol="AAPL", side="sell", qty=10, submitted_hours_ago=1),
        ],
    )
    audit = BrokerOrderAudit(accounts={"test": adapter}, dry_run=False)
    audit.run_audit(now=NOW)
    assert "dup" in adapter.cancel_calls
    assert "old" not in adapter.cancel_calls


# ── Verify flat ─────────────────────────────────────────────────────

def test_verify_flat_all_flat():
    adapter = FakeAdapter(positions=[], open_orders=[])
    audit = BrokerOrderAudit(accounts={"test": adapter}, dry_run=True)
    result = audit.verify_flat()
    assert result["all_flat"] is True


def test_verify_flat_not_flat():
    adapter = FakeAdapter(positions=[_position("AAPL", qty=10)])
    audit = BrokerOrderAudit(accounts={"test": adapter}, dry_run=True)
    result = audit.verify_flat()
    assert result["all_flat"] is False
    assert result["accounts"]["test"]["position_symbols"] == ["AAPL"]


# ── Multi-account ───────────────────────────────────────────────────

def test_two_accounts():
    """Audit runs across both accounts"""
    dt_adapter = FakeAdapter(
        positions=[_position("AAPL", qty=10)],
        open_orders=[_order(symbol="AAPL", side="sell")],
    )
    ml_adapter = FakeAdapter(
        positions=[],
        open_orders=[_order(symbol="RENDER/USD", side="buy")],
    )
    audit = BrokerOrderAudit(
        accounts={"day_trade": dt_adapter, "medium_long": ml_adapter},
        dry_run=True,
    )
    report = audit.run_audit(now=NOW)
    assert "day_trade" in report["accounts"]
    assert "medium_long" in report["accounts"]
    dt = report["accounts"]["day_trade"]
    ml = report["accounts"]["medium_long"]
    assert dt["bucket_counts"].get(BUCKET_PENDING_CLOSE, 0) == 1
    assert ml["bucket_counts"].get(BUCKET_CRYPTO_ORPHAN, 0) == 1


# ── Text report ─────────────────────────────────────────────────────

def test_text_report_generated():
    adapter = FakeAdapter(
        positions=[_position("AAPL", qty=10)],
        open_orders=[_order(symbol="AAPL", side="sell")],
    )
    audit = BrokerOrderAudit(accounts={"test": adapter}, dry_run=True)
    report = audit.run_audit(now=NOW)
    text = report.get("text_report", "")
    assert "BROKER ORDER AUDIT REPORT" in text
    assert "PENDING CLOSES: 1" in text


# ── Save report ─────────────────────────────────────────────────────

def test_save_creates_files(tmp_path):
    adapter = FakeAdapter()
    audit = BrokerOrderAudit(accounts={"test": adapter}, dry_run=True)
    report = audit.run_audit(now=NOW)
    paths = audit.save_report(report, output_dir=str(tmp_path))
    assert Path(paths["json_path"]).exists()
    assert Path(paths["txt_path"]).exists()


# ── Global summary ──────────────────────────────────────────────────

def test_global_summary_flat_ready_when_clean():
    """All pending_close, no actionable = flat_ready"""
    adapter = FakeAdapter(
        positions=[_position("AAPL", qty=10)],
        open_orders=[_order(symbol="AAPL", side="sell", qty=10)],
    )
    audit = BrokerOrderAudit(accounts={"test": adapter}, dry_run=True)
    report = audit.run_audit(now=NOW)
    assert report["global_summary"]["flat_ready"] is True


def test_global_summary_not_flat_when_actionable():
    """Stale orders make flat_ready = False"""
    adapter = FakeAdapter(
        open_orders=[_order(symbol="XYZ", side="buy", submitted_hours_ago=48)],
    )
    audit = BrokerOrderAudit(accounts={"test": adapter}, dry_run=True)
    report = audit.run_audit(now=NOW)
    assert report["global_summary"]["flat_ready"] is False
    assert report["global_summary"]["actionable_count"] > 0


# ── Helpers ─────────────────────────────────────────────────────────

def test_safe_float():
    assert safe_float(None) == 0.0
    assert safe_float("123.45") == 123.45
    assert safe_float("abc", 99.0) == 99.0


def test_parse_ts():
    assert parse_ts(None) is None
    dt = parse_ts("2026-03-08T03:25:00.870480888Z")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_ts_already_offset():
    dt = parse_ts("2026-03-08T03:25:00+00:00")
    assert dt is not None
