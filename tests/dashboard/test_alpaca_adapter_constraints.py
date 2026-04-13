from __future__ import annotations

import pytest

from src.execution.alpaca_paper_adapter import AlpacaPaperAdapter, BrokerAdapterError


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> AlpacaPaperAdapter:
    monkeypatch.setattr("src.utils.rate_limiter.get_limiter", lambda *args, **kwargs: None)
    return AlpacaPaperAdapter(api_key="test-key", api_secret="test-secret")


def _base_order(**overrides) -> dict:
    order = {
        "symbol": "AAPL",
        "side": "buy",
        "type": "limit",
        "time_in_force": "day",
        "qty": 1,
        "limit_price": 12.3456,
        "client_order_id": "gs-test-constraint-check",
        "shadow_mode": True,
    }
    order.update(overrides)
    return order


def test_normalize_limit_price_respects_alpaca_tick_sizes(adapter: AlpacaPaperAdapter):
    assert adapter._normalize_limit_price(12.3456) == 12.35
    assert adapter._normalize_limit_price(0.123456) == 0.1235
    assert adapter._normalize_limit_price(0) is None
    assert adapter._normalize_limit_price(-1) is None


def test_map_order_preserves_only_valid_extended_hours_payloads(adapter: AlpacaPaperAdapter):
    day_limit_payload = adapter._map_canonical_order_to_alpaca(
        _base_order(extended_hours=True, limit_price=12.3456)
    )
    assert day_limit_payload["extended_hours"] is True
    assert day_limit_payload["limit_price"] == "12.35"

    market_payload = adapter._map_canonical_order_to_alpaca(
        _base_order(type="market", limit_price=None, extended_hours=True)
    )
    assert market_payload["extended_hours"] is False
    assert "limit_price" not in market_payload

    ioc_limit_payload = adapter._map_canonical_order_to_alpaca(
        _base_order(time_in_force="ioc", limit_price=0.123456, extended_hours=True)
    )
    assert ioc_limit_payload["extended_hours"] is False
    assert ioc_limit_payload["limit_price"] == "0.1235"


def test_map_order_rejects_invalid_limit_price_after_normalization(adapter: AlpacaPaperAdapter):
    with pytest.raises(BrokerAdapterError) as exc_info:
        adapter._map_canonical_order_to_alpaca(_base_order(limit_price="bad-price"))

    assert exc_info.value.payload["error_code"] == "invalid_order"
