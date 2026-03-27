"""Tests for ``src.options.options_chain_normalizer``."""
from src.options.options_chain_normalizer import OptionsChainNormalizer


def test_normalize_vendor_alias_fields():
    normalizer = OptionsChainNormalizer()
    raw_chain = [
        {
            "bid_price": "1.20",
            "ask_price": "1.45",
            "implied_volatility": 25.0,
            "greeks": {"delta": 0.52, "gamma": 0.11, "theta": -0.03, "vega": 0.08},
            "open_interest": "250",
            "volume": 42,
            "expiration_date": "2026-04-17",
            "strike_price": "500",
            "option_type": "C",
            "symbol": "SPY260417C00500000",
            "underlying_symbol": "SPY",
        }
    ]

    normalized = normalizer.normalize(raw_chain)
    assert len(normalized) == 1
    contract = normalized[0]
    assert contract["bid"] == 1.2
    assert contract["ask"] == 1.45
    assert contract["IV"] == 0.25
    assert contract["delta"] == 0.52
    assert contract["OI"] == 250
    assert contract["volume"] == 42
    assert contract["strike"] == 500.0
    assert contract["contract_type"] == "call"
    assert contract["not_for_direct_execution"] is True


def test_normalize_nested_quote_fields():
    normalizer = OptionsChainNormalizer()
    raw_chain = [
        {
            "quote": {"bid": 2.1, "ask": 2.4},
            "iv": 0.31,
            "delta": -0.4,
            "gamma": 0.07,
            "theta": -0.05,
            "vega": 0.09,
            "OI": 120,
            "daily_volume": 18,
            "expiry": "2026-05-15",
            "strike": 470,
            "type": "put",
        }
    ]

    normalized = normalizer.normalize(raw_chain)
    assert normalized[0]["bid"] == 2.1
    assert normalized[0]["ask"] == 2.4
    assert normalized[0]["contract_type"] == "put"


def test_normalize_skips_incomplete_contracts():
    normalizer = OptionsChainNormalizer()
    raw_chain = [
        {"bid": 1.0, "ask": 1.2, "strike": 100},  # missing expiry/type
        {"bid": 0.8, "ask": 1.0, "expiry": "2026-06-19", "strike": 100, "contract_type": "call"},
    ]

    normalized = normalizer.normalize(raw_chain)
    assert len(normalized) == 1
    assert normalized[0]["expiry"] == "2026-06-19"


def test_normalize_unknown_type_rejected():
    normalizer = OptionsChainNormalizer()
    normalized = normalizer.normalize(
        [{"bid": 1.0, "ask": 1.2, "expiry": "2026-06-19", "strike": 100, "contract_type": "other"}]
    )
    assert normalized == []
