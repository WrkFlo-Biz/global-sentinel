"""Tests for OilShockRegime — classification, promotion/suppression, risk overlay."""

import pytest
from src.alpha.oil_shock_regime import OilShockRegime


@pytest.fixture
def regime():
    return OilShockRegime()


# ------------------------------------------------------------------
# Classification tests
# ------------------------------------------------------------------

class TestClassify:
    def test_normal_low_price(self, regime):
        assert regime.classify(oil_price=80.0) == "NORMAL"

    def test_normal_at_boundary(self, regime):
        assert regime.classify(oil_price=94.99) == "NORMAL"

    def test_elevated_at_threshold(self, regime):
        # 95.0 is the ceiling for NORMAL, so exactly 95.0 is still NORMAL
        assert regime.classify(oil_price=95.0) == "NORMAL"
        assert regime.classify(oil_price=95.01) == "ELEVATED"

    def test_elevated_mid(self, regime):
        assert regime.classify(oil_price=97.5) == "ELEVATED"

    def test_shock_at_threshold(self, regime):
        assert regime.classify(oil_price=100.0) == "ELEVATED"
        assert regime.classify(oil_price=100.01) == "SHOCK"

    def test_shock_mid(self, regime):
        assert regime.classify(oil_price=103.0) == "SHOCK"

    def test_dislocation_at_threshold(self, regime):
        assert regime.classify(oil_price=105.0) == "SHOCK"
        assert regime.classify(oil_price=105.01) == "DISLOCATION"

    def test_dislocation_extreme(self, regime):
        assert regime.classify(oil_price=150.0) == "DISLOCATION"

    def test_no_price_returns_normal(self, regime):
        assert regime.classify() == "NORMAL"

    def test_velocity_escalation_to_elevated(self, regime):
        """5%+ move in 24h should floor at ELEVATED even with low price."""
        result = regime.classify(oil_price=80.0, oil_change_24h=6.0)
        assert result == "ELEVATED"

    def test_velocity_escalation_to_shock(self, regime):
        """10%+ move should floor at SHOCK."""
        result = regime.classify(oil_price=85.0, oil_change_24h=11.0)
        assert result == "SHOCK"

    def test_velocity_negative_also_triggers(self, regime):
        """Large drops also trigger (absolute value)."""
        result = regime.classify(oil_price=80.0, oil_change_24h=-5.5)
        assert result == "ELEVATED"

    def test_chokepoint_hormuz_shock(self, regime):
        """Hormuz score > 0.5 should floor at SHOCK."""
        result = regime.classify(
            oil_price=85.0,
            chokepoint_status={"hormuz": 0.6},
        )
        assert result == "SHOCK"

    def test_chokepoint_hormuz_dislocation(self, regime):
        """Hormuz score > 0.8 should force DISLOCATION."""
        result = regime.classify(
            oil_price=85.0,
            chokepoint_status={"hormuz": 0.85},
        )
        assert result == "DISLOCATION"

    def test_chokepoint_bab_el_mandeb_elevated(self, regime):
        result = regime.classify(
            oil_price=80.0,
            chokepoint_status={"bab_el_mandeb": 0.5},
        )
        assert result == "ELEVATED"

    def test_commodity_shock_proxy_elevated(self, regime):
        result = regime.classify(commodity_shock=0.65)
        assert result == "ELEVATED"

    def test_commodity_shock_proxy_shock(self, regime):
        result = regime.classify(commodity_shock=0.85)
        assert result == "SHOCK"

    def test_max_regime_wins(self, regime):
        """Multiple signals: highest regime should win."""
        result = regime.classify(
            oil_price=96.0,        # ELEVATED from price
            oil_change_24h=12.0,   # SHOCK from velocity
        )
        assert result == "SHOCK"

    def test_persistence_tracking(self, regime):
        """Same regime across multiple cycles should increment count."""
        regime.classify(oil_price=80.0)
        regime.classify(oil_price=82.0)
        regime.classify(oil_price=81.0)
        assert regime._regime_cycle_count == 3

    def test_persistence_resets_on_change(self, regime):
        regime.classify(oil_price=80.0)
        regime.classify(oil_price=80.0)
        regime.classify(oil_price=96.0)  # switch to ELEVATED
        assert regime._regime_cycle_count == 1


# ------------------------------------------------------------------
# Strategy modifier tests
# ------------------------------------------------------------------

class TestStrategyModifiers:
    def test_normal_no_modifiers(self, regime):
        mods = regime.get_strategy_modifiers("NORMAL")
        assert mods["promote"] == []
        assert mods["suppress"] == []
        assert mods["size_multiplier"] == 1.0

    def test_elevated_promotes_energy(self, regime):
        mods = regime.get_strategy_modifiers("ELEVATED")
        assert "shipping_grind" in mods["promote"]
        assert "oil_momentum_intraday" in mods["promote"]
        assert mods["size_multiplier"] == 1.2

    def test_elevated_suppresses_airlines(self, regime):
        mods = regime.get_strategy_modifiers("ELEVATED")
        assert "airline_short" in mods["suppress"]

    def test_shock_no_suppression(self, regime):
        mods = regime.get_strategy_modifiers("SHOCK")
        assert "oil_mean_reversion" in mods["suppress"]
        assert mods["size_multiplier"] == 1.5

    def test_dislocation_corr_cap(self, regime):
        mods = regime.get_strategy_modifiers("DISLOCATION")
        assert mods["max_correlated_exposure"] == 0.60

    def test_dislocation_suppresses_mean_reversion(self, regime):
        mods = regime.get_strategy_modifiers("DISLOCATION")
        assert "oil_mean_reversion" in mods["suppress"]

    def test_invalid_regime_returns_normal(self, regime):
        mods = regime.get_strategy_modifiers("INVALID")
        assert mods == regime.STRATEGY_MODIFIERS["NORMAL"]


# ------------------------------------------------------------------
# Apply to ideas tests
# ------------------------------------------------------------------

class TestApplyToIdeas:
    def _make_idea(self, strategy="oil_momentum_intraday", symbol="USO",
                   confidence=0.6, notional=15000, side="long"):
        return {
            "strategy": strategy,
            "symbol": symbol,
            "confidence": confidence,
            "notional_usd": notional,
            "side": side,
        }

    def test_normal_no_changes(self, regime):
        ideas = [self._make_idea()]
        result = regime.apply_to_ideas(ideas, "NORMAL")
        assert len(result) == 1
        assert result[0]["confidence"] == 0.6
        assert result[0]["notional_usd"] == 15000

    def test_elevated_boosts_promoted(self, regime):
        ideas = [self._make_idea(strategy="oil_momentum_intraday")]
        result = regime.apply_to_ideas(ideas, "ELEVATED")
        assert result[0]["confidence"] == 0.7  # +0.10 boost
        assert result[0]["notional_usd"] == 18000  # 1.2x
        assert result[0]["oil_regime_promoted"] is True

    def test_elevated_suppresses_airline_short(self, regime):
        ideas = [
            self._make_idea(strategy="oil_momentum_intraday"),
            self._make_idea(strategy="airline_short", symbol="JETS"),
        ]
        result = regime.apply_to_ideas(ideas, "ELEVATED")
        assert len(result) == 1
        assert result[0]["strategy"] == "oil_momentum_intraday"

    def test_shock_bigger_boost(self, regime):
        ideas = [self._make_idea(strategy="shipping_rate_explosion", confidence=0.5)]
        result = regime.apply_to_ideas(ideas, "SHOCK")
        assert result[0]["confidence"] == 0.65  # +0.15
        assert result[0]["notional_usd"] == 22500  # 1.5x

    def test_confidence_capped_at_095(self, regime):
        ideas = [self._make_idea(strategy="oil_momentum_intraday", confidence=0.90)]
        result = regime.apply_to_ideas(ideas, "DISLOCATION")
        assert result[0]["confidence"] == 0.95

    def test_dislocation_caps_correlated(self, regime):
        """In DISLOCATION, oil-correlated ideas should be capped at 60% of total."""
        ideas = [
            self._make_idea(strategy="oil_momentum_intraday", notional=40000),
            self._make_idea(strategy="shipping_rate_explosion", notional=40000),
            self._make_idea(strategy="gold_safe_haven", symbol="GLD", notional=20000),
        ]
        result = regime.apply_to_ideas(ideas, "DISLOCATION")
        # Total = 100K, oil-corr = 80K (80%) → should be scaled to 60K (60%)
        oil_ideas = [i for i in result if i.get("oil_regime_capped")]
        assert len(oil_ideas) == 2
        oil_total = sum(i["notional_usd"] for i in oil_ideas)
        assert oil_total <= 60001  # 60% of 100K

    def test_non_promoted_unchanged(self, regime):
        """Strategies not in promote list get size multiplier but no confidence boost."""
        ideas = [self._make_idea(strategy="some_other_strategy", confidence=0.5)]
        result = regime.apply_to_ideas(ideas, "SHOCK")
        assert result[0]["confidence"] == 0.5  # not promoted
        assert result[0]["notional_usd"] == 22500  # still gets 1.5x size


# ------------------------------------------------------------------
# Risk overlay tests
# ------------------------------------------------------------------

class TestRiskOverlay:
    def test_no_warnings_in_normal(self, regime):
        exposure = {"positions": [], "oil_delta": 0, "gross_exposure_pct": 0}
        warnings = regime.risk_overlay(exposure, "NORMAL")
        assert warnings == []

    def test_oil_corr_exceeded_warning(self, regime):
        positions = [
            {"strategy": "oil_momentum_intraday", "notional_usd": 80000},
            {"strategy": "gold_safe_haven", "notional_usd": 20000},
        ]
        exposure = {"positions": positions}
        warnings = regime.risk_overlay(exposure, "DISLOCATION")
        assert any("OIL_CORR_EXCEEDED" in w for w in warnings)

    def test_oil_delta_warning_in_shock(self, regime):
        exposure = {"positions": [], "oil_delta": 8000}
        warnings = regime.risk_overlay(exposure, "SHOCK")
        assert any("OIL_DELTA_HIGH" in w for w in warnings)

    def test_gross_exposure_warning_dislocation(self, regime):
        exposure = {"positions": [], "gross_exposure_pct": 0.65}
        warnings = regime.risk_overlay(exposure, "DISLOCATION")
        assert any("GROSS_EXPOSURE_HIGH" in w for w in warnings)

    def test_airline_long_warning(self, regime):
        positions = [
            {"strategy": "other", "symbol": "UAL", "side": "long", "notional_usd": 10000},
        ]
        exposure = {"positions": positions}
        warnings = regime.risk_overlay(exposure, "SHOCK")
        assert any("AIRLINE_LONGS" in w for w in warnings)


# ------------------------------------------------------------------
# Data extraction tests
# ------------------------------------------------------------------

class TestExtractOilData:
    def test_from_cross_asset(self):
        bridge = {
            "v6_cross_asset_signals": {
                "commodities": {"wti_price": 98.5, "wti_change_pct": 2.3},
            },
        }
        result = OilShockRegime.extract_oil_data(bridge, {})
        assert result["price"] == 98.5
        assert result["change_24h"] == 2.3
        assert result["source"] == "cross_asset_signals"

    def test_from_eia_bridge(self):
        bridge = {
            "eia": [
                {"series_key": "crude_oil_stocks", "value": 97.2},
            ],
        }
        result = OilShockRegime.extract_oil_data(bridge, {})
        assert result["price"] == 97.2
        assert result["source"] == "eia_bridge"

    def test_fallback_commodity_shock(self):
        result = OilShockRegime.extract_oil_data(
            {},
            {"component_scores": {"commodity_shock": 0.7}},
        )
        assert result["commodity_shock"] == 0.7
        assert result["source"] == "scorecard_proxy"

    def test_empty_data(self):
        result = OilShockRegime.extract_oil_data({}, {})
        assert result["price"] is None
        assert result["source"] is None


# ------------------------------------------------------------------
# Telegram format tests
# ------------------------------------------------------------------

class TestFormatTelegram:
    def test_normal_format(self, regime):
        line = regime.format_telegram("NORMAL", oil_price=85.0)
        assert "NORMAL" in line
        assert "$85.00" in line

    def test_shock_format(self, regime):
        line = regime.format_telegram("SHOCK", oil_price=102.5, oil_change=3.5)
        assert "SHOCK" in line
        assert "$102.50" in line
        assert "+3.5%" in line

    def test_dislocation_format(self, regime):
        line = regime.format_telegram("DISLOCATION", oil_price=115.0)
        assert "DISLOCATION" in line


# ------------------------------------------------------------------
# Full cycle test
# ------------------------------------------------------------------

class TestRunCycle:
    def test_full_cycle(self, regime):
        bridge = {}
        scorecard = {
            "component_scores": {"commodity_shock": 0.7},
            "chokepoint_risk": {"hormuz": 0.2},
        }
        result = regime.run_cycle(bridge, scorecard)
        assert result["regime"] == "SHOCK"  # commodity_shock 0.7 + chokepoint activity escalates
        assert "modifiers" in result
        assert "telegram_line" in result
        assert isinstance(result["risk_warnings"], list)
