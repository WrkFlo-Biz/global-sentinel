"""
Global Sentinel V5.1 - Fault Semantic Conformance Tests

Advanced conformance tests validating fault-injection semantic behavior:
- Timeout retry idempotency
- Delayed ack normalization
- Out-of-order transition handling
- Cancel/replace failure normalization
- Sweeper TTL preference (intent-stored vs recomputed)
"""

from __future__ import annotations

import os
import uuid
import pytest
from pathlib import Path


VALID_ORDER_STATES = {
    "new", "accepted", "pending", "partially_filled", "filled", "canceled",
    "rejected", "expired", "replaced", "done_for_day",
}


@pytest.fixture
def mock_adapter():
    from tests.broker_conformance.fixtures.mock_broker_adapter import MockBrokerAdapter
    return MockBrokerAdapter()


def _build_shadow_order(symbol="AAPL", side="buy", qty=2, limit_price=150.0):
    return {
        "symbol": symbol,
        "side": side,
        "type": "limit",
        "time_in_force": "day",
        "qty": qty,
        "limit_price": limit_price,
        "client_order_id": f"gs-test-{uuid.uuid4().hex[:12]}",
        "shadow_mode": True,
    }


class TestTimeoutRetryIdempotency:
    """Submit, timeout, retry same client_order_id — verify no duplicate order."""

    def test_timeout_retry_idempotency(self, mock_adapter):
        mock_adapter.set_fault_profile({
            "timeout_on_submit_symbols": ["AAPL"],
            "submit_timeout_once_per_symbol": True,
        })

        order = _build_shadow_order(symbol="AAPL")
        cid = order["client_order_id"]

        # First submit should timeout
        with pytest.raises(TimeoutError):
            mock_adapter.submit_order(order)

        # Retry with same client_order_id should succeed (timeout_once means second attempt goes through)
        result = mock_adapter.submit_order(order)
        assert isinstance(result, dict)
        assert result["client_order_id"] == cid
        assert result["status"] in VALID_ORDER_STATES
        assert result["status"] != "rejected"

        # Third submit with same cid should be idempotent (return same order)
        result2 = mock_adapter.submit_order(order)
        assert result2["order_id"] == result["order_id"], "Expected idempotent return for duplicate client_order_id"
        assert result2["client_order_id"] == cid


class TestDelayedAckNormalization:
    """Verify new -> accepted transition after N get_order calls."""

    def test_delayed_ack_normalization(self, mock_adapter):
        n_calls = 3
        mock_adapter.set_fault_profile({
            "delayed_ack_symbols": ["XOM"],
            "ack_after_n_get_order_calls": n_calls,
        })

        order = _build_shadow_order(symbol="XOM")
        submitted = mock_adapter.submit_order(order)
        order_id = submitted["order_id"]

        # Initial status should be "new" (not yet acknowledged)
        assert submitted["status"] == "new", f"Expected initial status 'new', got '{submitted['status']}'"
        assert submitted["acknowledged_at_utc"] is None, "Expected no ack timestamp initially"

        # Get order N-1 times — should still be "new"
        for i in range(n_calls - 1):
            state = mock_adapter.get_order(order_id)
            assert state["status"] == "new", f"Expected 'new' after {i+1} get_order calls"

        # Nth get_order call should transition to "accepted"
        state = mock_adapter.get_order(order_id)
        assert state["status"] == "accepted", f"Expected 'accepted' after {n_calls} get_order calls, got '{state['status']}'"
        assert state["acknowledged_at_utc"] is not None, "Expected ack timestamp after transition"


class TestOutOfOrderTransition:
    """Verify partial fill is captured despite status inconsistency (ooo broker state)."""

    def test_out_of_order_transition(self, mock_adapter):
        mock_adapter.set_fault_profile({
            "out_of_order_transition_symbols": ["XOM"],
        })

        order = _build_shadow_order(symbol="XOM", qty=10)
        submitted = mock_adapter.submit_order(order)
        order_id = submitted["order_id"]
        assert submitted["status"] == "accepted"

        # Simulate partial fill — OOO keeps status as-is despite fill
        # First, change status to "new" to trigger OOO behavior
        mock_adapter.orders[order_id]["status"] = "new"
        mock_adapter.orders[order_id]["broker_raw_status"] = "new"

        result = mock_adapter.simulate_fill(order_id, fill_qty=3, fill_price=107.50)

        # The key OOO behavior: filled_qty should reflect the fill even if status stayed "new"
        assert float(result["filled_qty"]) == 3.0, f"Expected filled_qty=3.0, got {result['filled_qty']}"
        # Status may stay "new" due to OOO fault
        assert result["status"] in {"new", "partially_filled"}, f"Unexpected status '{result['status']}'"

        # Verify trades are recorded
        trades = mock_adapter.get_trades(order_id)
        assert len(trades) >= 1, "Expected at least one trade despite OOO status"
        assert float(trades[0]["fill_qty"]) == 3.0


class TestCancelReplaceFailureNormalization:
    """Cancel unknown order, replace canceled order — verify error handling."""

    def test_cancel_unknown_order(self, mock_adapter):
        fake_id = f"mock-{uuid.uuid4().hex[:12]}"
        with pytest.raises(KeyError):
            mock_adapter.cancel_order(fake_id)

    def test_replace_canceled_order(self, mock_adapter):
        order = _build_shadow_order(symbol="MSFT")
        submitted = mock_adapter.submit_order(order)
        order_id = submitted["order_id"]

        # Cancel it
        canceled = mock_adapter.cancel_order(order_id)
        assert canceled["status"] == "canceled"

        # Replace a canceled order should return the order as-is (already terminal)
        replaced = mock_adapter.replace_order(order_id, {"limit_price": 200.0})
        assert replaced["status"] == "canceled", "Replace on canceled order should return terminal state"

    def test_cancel_fail_injection(self, mock_adapter):
        mock_adapter.set_fault_profile({"cancel_fail_symbols": ["TSLA"]})

        order = _build_shadow_order(symbol="TSLA")
        submitted = mock_adapter.submit_order(order)
        order_id = submitted["order_id"]

        result = mock_adapter.cancel_order(order_id)
        assert result["status"] == "rejected", "Expected cancel to be rejected due to fault injection"
        assert result.get("reject_reason_code") == "mock_cancel_fail"

    def test_replace_fail_injection(self, mock_adapter):
        mock_adapter.set_fault_profile({"replace_fail_symbols": ["TSLA"]})

        order = _build_shadow_order(symbol="TSLA")
        submitted = mock_adapter.submit_order(order)
        order_id = submitted["order_id"]

        result = mock_adapter.replace_order(order_id, {"limit_price": 999.0})
        assert result["status"] == "rejected", "Expected replace to be rejected due to fault injection"
        assert result.get("reject_reason_code") == "mock_replace_fail"


class TestSweeperPrefersIntentStoredTTL:
    """Verify sweeper uses intent-stored resolved_ttl_minutes before recomputation."""

    def test_sweeper_prefers_intent_stored_ttl(self, tmp_path):
        import json as _json
        import sys
        repo_root = tmp_path
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

        from src.execution.order_intent_registry import OrderIntentRegistry
        from src.execution.stale_intent_sweeper import StaleIntentSweeper

        registry = OrderIntentRegistry(repo_root)

        # Create intent with a stored TTL of 5 minutes
        package = {
            "package_id": "pkg-test-ttl",
            "package_type": "test",
            "timestamp_utc": "2020-01-01T00:00:00+00:00",
            "effective_mode": "NORMAL",
            "window_context": {"time_window_name": "us_pre_market"},
        }
        candidate = {
            "symbol": "AAPL",
            "strategy_style": "momentum",
            "direction": "bullish",
            "confidence_score": 0.9,
        }
        order_request = {
            "symbol": "AAPL",
            "side": "buy",
            "type": "limit",
            "qty": 1,
            "limit_price": 150.0,
        }

        intent = registry.create_intent_from_candidate(
            package=package,
            candidate=candidate,
            order_request=order_request,
            shadow_mode=True,
            extra_context={
                "order_lifecycle_policy": {
                    "resolved_ttl_minutes": 5.0,
                    "ttl_explanation": {
                        "resolved_ttl_minutes": 5.0,
                        "time_window_name": "us_pre_market",
                        "reasons": [{"layer": "intent_stored_policy", "ttl_minutes": 5.0}],
                    },
                },
            },
        )

        # Update status to "submitted" so sweeper picks it up, then backdate timestamps
        # so age exceeds the 5 min stored TTL
        updated = registry.update_status(intent["intent_id"], "submitted", event="test_submitted")

        # Backdate: append a version with old timestamps so sweeper sees age > 5 min
        old_ts = "2020-01-01T00:00:00+00:00"
        backdated = dict(updated)
        backdated["timestamp_utc"] = old_ts
        backdated["audit"] = dict(backdated.get("audit") or {})
        backdated["audit"]["created_at_utc"] = old_ts
        backdated["audit"]["updated_at_utc"] = old_ts
        registry._append_jsonl(registry.intents_path, backdated)

        # Sweep with a very high default TTL (9999 min) — the intent-stored 5 min should win
        sweeper = StaleIntentSweeper(repo_root)
        report = sweeper.sweep(stale_after_minutes=9999.0)

        # Intent was created with a very old timestamp, so age >> 5 min
        # The stored TTL of 5 min should cause it to be flagged as stale
        stale_intents = report.get("stale_intents") or []
        stale_ids = {s["intent_id"] for s in stale_intents}
        assert intent["intent_id"] in stale_ids, (
            f"Expected intent {intent['intent_id']} to be stale (stored TTL=5 min, "
            f"default TTL=9999 min) — sweeper should prefer stored TTL"
        )

        # Verify the resolved TTL used was 5 min, not 9999
        stale_entry = next(s for s in stale_intents if s["intent_id"] == intent["intent_id"])
        assert stale_entry["resolved_ttl_minutes"] == 5.0, (
            f"Expected resolved_ttl_minutes=5.0, got {stale_entry['resolved_ttl_minutes']}"
        )
