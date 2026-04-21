"""Tests for execution hardening modules."""
import pytest
from src.execution.circuit_breaker import CircuitBreaker, CircuitOpenError
from src.execution.order_state_machine import OrderStateMachine, OrderState, InvalidTransitionError
from src.execution.pre_trade_controls import PreTradeControls
from src.execution.microstructure_regime_classifier import MicrostructureRegimeClassifier, NORMAL_LIQUIDITY, SPREAD_WIDENING, PARTIAL_FILL_HEAVY


def test_circuit_breaker_closed():
    cb = CircuitBreaker(name="test", failure_threshold=3)
    result = cb.call(lambda: 42)
    assert result == 42
    assert cb.state == "CLOSED"


def test_circuit_breaker_trips_after_failures():
    cb = CircuitBreaker(name="test", failure_threshold=2)
    for _ in range(2):
        try:
            cb.call(lambda: 1/0)
        except ZeroDivisionError:
            pass
    assert cb.state == "OPEN"


def test_circuit_breaker_open_rejects():
    cb = CircuitBreaker(name="test", failure_threshold=1)
    try:
        cb.call(lambda: 1/0)
    except ZeroDivisionError:
        pass
    with pytest.raises(CircuitOpenError):
        cb.call(lambda: 42)


def test_circuit_breaker_reset():
    cb = CircuitBreaker(name="test", failure_threshold=1)
    try:
        cb.call(lambda: 1/0)
    except ZeroDivisionError:
        pass
    cb.reset()
    assert cb.state == "CLOSED"
    assert cb.call(lambda: 42) == 42


def test_order_state_machine_valid_transitions():
    sm = OrderStateMachine()
    sm.transition("ord1", OrderState.DRAFT, OrderState.VALIDATED)
    sm.transition("ord1", OrderState.VALIDATED, OrderState.SUBMITTED)
    sm.transition("ord1", OrderState.SUBMITTED, OrderState.ACKNOWLEDGED)
    sm.transition("ord1", OrderState.ACKNOWLEDGED, OrderState.FILLED)
    assert len(sm.order_history("ord1")) == 4


def test_order_state_machine_invalid_transition():
    sm = OrderStateMachine()
    with pytest.raises(InvalidTransitionError):
        sm.transition("ord1", OrderState.DRAFT, OrderState.FILLED)


def test_order_state_machine_terminal():
    sm = OrderStateMachine()
    assert sm.is_terminal(OrderState.FILLED)
    assert sm.is_terminal(OrderState.CANCELLED)
    assert not sm.is_terminal(OrderState.SUBMITTED)


def test_pre_trade_controls_pass():
    ptc = PreTradeControls(max_single_order_notional_pct=0.12)
    result = ptc.check(
        trade_idea={"symbol": "SPY", "notional": 10000},
        portfolio_state={"equity": 100000, "gross_exposure": 20000, "positions": {}, "orders_last_minute": 0},
    )
    assert result.passed


def test_pre_trade_controls_fat_finger():
    ptc = PreTradeControls(max_single_order_notional_pct=0.12)
    result = ptc.check(
        trade_idea={"symbol": "SPY", "notional": 50000},
        portfolio_state={"equity": 100000, "gross_exposure": 0, "positions": {}, "orders_last_minute": 0},
    )
    assert not result.passed
    assert any(c["name"] == "max_single_order_notional" and not c["passed"] for c in result.checks)


def test_pre_trade_controls_spread():
    ptc = PreTradeControls(max_spread_pct=0.02)
    result = ptc.check(
        trade_idea={"symbol": "SPY", "notional": 5000},
        portfolio_state={"equity": 100000, "gross_exposure": 0, "positions": {}, "orders_last_minute": 0},
        market_data={"bid": 100, "ask": 105},  # 5% spread
    )
    assert not result.passed


def test_microstructure_normal():
    mc = MicrostructureRegimeClassifier()
    regime = mc.classify(
        recent_orders=[{"fill_rate": 1.0}],
        market_data={"spread_ratio_vs_normal": 1.0},
        broker_state={"avg_ack_latency_ms": 50},
    )
    assert regime.state == NORMAL_LIQUIDITY


def test_microstructure_spread_widening():
    mc = MicrostructureRegimeClassifier()
    regime = mc.classify(
        recent_orders=[],
        market_data={"spread_ratio_vs_normal": 3.0},
        broker_state={},
    )
    assert regime.state == SPREAD_WIDENING


def test_microstructure_partial_fill():
    mc = MicrostructureRegimeClassifier()
    regime = mc.classify(
        recent_orders=[{"fill_rate": 0.5}, {"fill_rate": 0.3}, {"fill_rate": 0.4}],
        market_data={"spread_ratio_vs_normal": 1.0},
        broker_state={},
    )
    assert regime.state == PARTIAL_FILL_HEAVY


def test_microstructure_execution_params():
    mc = MicrostructureRegimeClassifier()
    from src.execution.microstructure_regime_classifier import MicrostructureRegime, STALE_BROKER_STATE
    regime = MicrostructureRegime(state=STALE_BROKER_STATE, confidence=0.9, contributing_factors=["recon_drift"])
    params = mc.get_execution_parameters(regime)
    assert params["should_pause_new_orders"] is True
