"""
Oil-Shock Regime Layer — classifies oil market state and controls strategy promotion/suppression.

Integrates with:
- strategy_engine.py (evaluate_entries): modifies confidence/size of generated ideas
- crisis_monitor.py (V6.9 block): runs every cycle, enriches scorecard
- Telegram digest: shows current regime + oil price

Data sources:
- Alpaca MCP (USO price as WTI proxy)
- EIA bridge (weekly petroleum status)
- Cross-asset signals (commodity_shock component)
- Chokepoint risk (Hormuz, Bab el-Mandeb)
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class OilShockRegime:
    """
    4 regimes based on WTI price level + velocity + chokepoint status:

    - NORMAL:      WTI < $95  — standard strategy weights
    - ELEVATED:    $95-$100   — promote energy/shipping, suppress airlines
    - SHOCK:       $100-$105  — aggressive promotion, max position sizes
    - DISLOCATION: > $105     — war footing, cap correlated exposure at 60%

    Additional escalation triggers:
    - Rate of change: oil up 5%+ in 24h → instant ELEVATED minimum
    - Chokepoint:     Hormuz closed (score > 0.5) → instant SHOCK minimum
    - Regime persistence: must stay in regime 2+ cycles before full promotion
    """

    REGIMES = ("NORMAL", "ELEVATED", "SHOCK", "DISLOCATION")

    THRESHOLDS = {
        "normal_ceiling": 95.0,
        "elevated_ceiling": 100.0,
        "shock_ceiling": 105.0,
        # Above 105 = DISLOCATION
    }

    # Velocity triggers (24h % change)
    VELOCITY_TRIGGERS = {
        "elevated_min_pct": 5.0,   # +5% in 24h → ELEVATED floor
        "shock_min_pct": 10.0,     # +10% in 24h → SHOCK floor
    }

    # Chokepoint escalation thresholds
    CHOKEPOINT_TRIGGERS = {
        "hormuz_shock": 0.5,       # Hormuz score > 0.5 → SHOCK minimum
        "hormuz_dislocation": 0.8, # Hormuz score > 0.8 → DISLOCATION
        "bab_el_mandeb_elevated": 0.4,  # Red Sea disruption → ELEVATED
    }

    # Strategy promotion/suppression by regime
    STRATEGY_MODIFIERS: Dict[str, Dict[str, Any]] = {
        "NORMAL": {
            "promote": [],
            "suppress": [],
            "size_multiplier": 1.0,
            "max_correlated_exposure": 1.0,
        },
        "ELEVATED": {
            "promote": [
                "shipping_grind", "shipping_rate_explosion",
                "oil_momentum_intraday", "defense_accumulation",
                "oil_gap_persistence", "petro_inflation",
                "china_oil_import_shock", "europe_pre_open",
                "us_premarket_gap", "ag_spread_cascade",
            ],
            "suppress": ["airline_short"],  # wait for confirmation
            "size_multiplier": 1.2,
            "max_correlated_exposure": 0.80,
        },
        "SHOCK": {
            "promote": [
                "shipping_rate_explosion",
                "oil_momentum_intraday", "defense_accumulation",
                "gold_safe_haven", "airline_short", "europe_energy_crisis",
                "fertilizer_food_chain", "nuclear_renaissance",
                "em_capital_flight", "inflation_hedge",
                "canadian_oil_premium", "wall_street_vol",
                "refining_crack_spread", "vix_spike_scalp",
                "cyber_retaliation",
                "oil_gap_persistence",
                "jet_fuel_squeeze", "supply_shock_pairs",
                "petro_inflation",
                "china_oil_import_shock", "asia_energy_cascade",
                "europe_pre_open", "us_premarket_gap",
                "commodity_currency_divergence",
                "ag_spread_cascade",
            ],
            "suppress": [
                "oil_mean_reversion",  # don't fade $100 oil
            ],
            "size_multiplier": 1.5,
            "max_correlated_exposure": 0.50,  # 50% cap on oil-correlated
            "max_new_gross_per_cycle": 0.06,  # 6% — don't add too fast
        },
        "DISLOCATION": {
            "promote": [
                "shipping_grind", "shipping_rate_explosion",
                "oil_momentum_intraday", "defense_accumulation",
                "gold_safe_haven", "europe_energy_crisis",
                "fertilizer_food_chain", "inflation_hedge",
                "airline_short", "em_capital_flight",
                "nuclear_renaissance", "oil_gap_persistence",
                "jet_fuel_squeeze", "supply_shock_pairs",
                "petro_inflation",
                "china_oil_import_shock", "asia_energy_cascade",
                "europe_pre_open", "us_premarket_gap",
                "commodity_currency_divergence",
                "ag_spread_cascade",
            ],
            "suppress": [
                "oil_mean_reversion",  # don't fade dislocation
            ],
            "size_multiplier": 1.0,  # back to normal in dislocation (risk mgmt)
            "max_correlated_exposure": 0.60,  # cap oil-correlated at 60%
        },
    }

    # Oil-correlated strategy names (for exposure cap)
    OIL_CORRELATED_STRATEGIES = {
        "oil_momentum_intraday", "shipping_rate_explosion", "shipping_grind",
        "oil_gap_persistence", "oil_mean_reversion", "jet_fuel_squeeze",
        "supply_shock_pairs", "petro_inflation", "refining_crack_spread",
        "canadian_oil_premium", "europe_energy_crisis",
        "china_oil_import_shock", "asia_energy_cascade",
        "europe_pre_open", "us_premarket_gap",
        "commodity_currency_divergence",
    }

    def __init__(self) -> None:
        self._regime_history: List[Tuple[float, str]] = []  # (timestamp, regime)
        self._last_regime: str = "NORMAL"
        self._regime_cycle_count: int = 0  # how many cycles in current regime

    # ------------------------------------------------------------------
    # Core classification
    # ------------------------------------------------------------------

    def classify(
        self,
        oil_price: Optional[float] = None,
        oil_change_24h: Optional[float] = None,
        chokepoint_status: Optional[Dict[str, float]] = None,
        commodity_shock: Optional[float] = None,
    ) -> str:
        """
        Classify current oil market regime.

        Args:
            oil_price: Current WTI price (or USO-derived proxy)
            oil_change_24h: 24-hour % change in oil price
            chokepoint_status: Dict of chokepoint scores (hormuz, bab_el_mandeb)
            commodity_shock: Scorecard commodity_shock component (0.0-1.0)

        Returns:
            Regime string: NORMAL, ELEVATED, SHOCK, or DISLOCATION
        """
        regime_from_price = "NORMAL"
        regime_from_velocity = "NORMAL"
        regime_from_chokepoint = "NORMAL"
        regime_from_commodity = "NORMAL"

        # 1. Price-level classification
        if oil_price is not None:
            if oil_price > self.THRESHOLDS["shock_ceiling"]:
                regime_from_price = "DISLOCATION"
            elif oil_price > self.THRESHOLDS["elevated_ceiling"]:
                regime_from_price = "SHOCK"
            elif oil_price > self.THRESHOLDS["normal_ceiling"]:
                regime_from_price = "ELEVATED"

        # 2. Velocity classification (24h rate of change)
        if oil_change_24h is not None:
            abs_change = abs(oil_change_24h)
            if abs_change >= self.VELOCITY_TRIGGERS["shock_min_pct"]:
                regime_from_velocity = "SHOCK"
            elif abs_change >= self.VELOCITY_TRIGGERS["elevated_min_pct"]:
                regime_from_velocity = "ELEVATED"

        # 3. Chokepoint-driven classification
        if chokepoint_status:
            hormuz = chokepoint_status.get("hormuz", 0.0)
            bab = chokepoint_status.get("bab_el_mandeb", 0.0)
            if hormuz >= self.CHOKEPOINT_TRIGGERS["hormuz_dislocation"]:
                regime_from_chokepoint = "DISLOCATION"
            elif hormuz >= self.CHOKEPOINT_TRIGGERS["hormuz_shock"]:
                regime_from_chokepoint = "SHOCK"
            elif bab >= self.CHOKEPOINT_TRIGGERS["bab_el_mandeb_elevated"]:
                regime_from_chokepoint = "ELEVATED"

        # 4. Commodity shock proxy (when no direct oil price)
        if commodity_shock is not None:
            chokepoint_composite = max(
                (chokepoint_status or {}).get("hormuz", 0.0),
                (chokepoint_status or {}).get("bab_el_mandeb", 0.0),
            ) if chokepoint_status else 0.0
            if commodity_shock > 0.8:
                regime_from_commodity = "SHOCK"
            elif commodity_shock >= 0.65 and chokepoint_composite > 0:
                # High commodity shock + any chokepoint activity = SHOCK
                regime_from_commodity = "SHOCK"
            elif commodity_shock > 0.6:
                regime_from_commodity = "ELEVATED"

        # Take the maximum (most alarming) regime
        candidates = [
            regime_from_price, regime_from_velocity,
            regime_from_chokepoint, regime_from_commodity,
        ]
        regime = max(candidates, key=lambda r: self.REGIMES.index(r))

        # Environment variable override (for manual escalation)
        env_override = os.environ.get("GS_OIL_REGIME_OVERRIDE", "").strip().upper()
        if env_override in self.REGIMES:
            override_idx = self.REGIMES.index(env_override)
            current_idx = self.REGIMES.index(regime)
            if override_idx > current_idx:
                logger.warning(
                    "Oil regime override: %s -> %s (via GS_OIL_REGIME_OVERRIDE)",
                    regime, env_override,
                )
                regime = env_override

        # Track persistence
        now = time.time()
        if regime == self._last_regime:
            self._regime_cycle_count += 1
        else:
            self._regime_cycle_count = 1
            self._last_regime = regime

        self._regime_history.append((now, regime))
        # Keep last 100 entries
        if len(self._regime_history) > 100:
            self._regime_history = self._regime_history[-100:]

        logger.info(
            "Oil regime: %s (price=%s, vel=%s, choke=%s, commodity=%s) persistence=%d",
            regime, regime_from_price, regime_from_velocity,
            regime_from_chokepoint, regime_from_commodity,
            self._regime_cycle_count,
        )

        return regime

    # ------------------------------------------------------------------
    # Strategy modifiers
    # ------------------------------------------------------------------

    def get_strategy_modifiers(self, regime: str) -> Dict[str, Any]:
        """Get promotion/suppression config for a regime."""
        return dict(self.STRATEGY_MODIFIERS.get(regime, self.STRATEGY_MODIFIERS["NORMAL"]))

    # ------------------------------------------------------------------
    # Apply to trade ideas
    # ------------------------------------------------------------------

    def apply_to_ideas(
        self,
        ideas: List[Dict[str, Any]],
        regime: str,
    ) -> List[Dict[str, Any]]:
        """
        Modify trade ideas based on current oil regime.

        - Promoted strategies get confidence boost + size multiplier
        - Suppressed strategies get filtered out
        - DISLOCATION caps total oil-correlated ideas

        Args:
            ideas: List of trade idea dicts from strategy_engine
            regime: Current oil regime string

        Returns:
            Modified list of trade ideas
        """
        if regime == "NORMAL":
            return ideas

        mods = self.get_strategy_modifiers(regime)
        promoted = set(mods.get("promote", []))
        suppressed = set(mods.get("suppress", []))
        size_mult = mods.get("size_multiplier", 1.0)

        modified = []
        for idea in ideas:
            strategy = idea.get("strategy", "")

            # Suppress: skip this idea entirely
            if strategy in suppressed:
                logger.info(
                    "Oil regime %s: SUPPRESSED %s:%s",
                    regime, strategy, idea.get("symbol", "?"),
                )
                continue

            idea = dict(idea)  # shallow copy

            # Promote: boost confidence + size
            if strategy in promoted:
                old_conf = idea.get("confidence", 0.5)
                # Boost confidence by 10-20% depending on regime severity
                boost = 0.10 if regime == "ELEVATED" else 0.15 if regime == "SHOCK" else 0.20
                idea["confidence"] = min(old_conf + boost, 0.95)
                idea["oil_regime_promoted"] = True

            # Scale notional by regime multiplier
            if "notional_usd" in idea:
                idea["notional_usd"] = round(idea["notional_usd"] * size_mult, 2)

            idea["oil_regime"] = regime
            modified.append(idea)

        # In DISLOCATION, cap oil-correlated ideas to prevent over-concentration
        if regime == "DISLOCATION":
            max_corr = mods.get("max_correlated_exposure", 0.60)
            oil_ideas = [i for i in modified if i.get("strategy", "") in self.OIL_CORRELATED_STRATEGIES]
            other_ideas = [i for i in modified if i.get("strategy", "") not in self.OIL_CORRELATED_STRATEGIES]

            total_notional = sum(i.get("notional_usd", 0) for i in modified)
            oil_notional = sum(i.get("notional_usd", 0) for i in oil_ideas)

            if total_notional > 0 and oil_notional / total_notional > max_corr:
                # Scale down oil-correlated ideas proportionally
                target_oil = total_notional * max_corr
                scale = target_oil / oil_notional if oil_notional > 0 else 1.0
                for idea in oil_ideas:
                    if "notional_usd" in idea:
                        idea["notional_usd"] = round(idea["notional_usd"] * scale, 2)
                    idea["oil_regime_capped"] = True
                logger.warning(
                    "DISLOCATION: Capped oil-correlated exposure from %.0f%% to %.0f%%",
                    (oil_notional / total_notional) * 100,
                    max_corr * 100,
                )

            modified = other_ideas + oil_ideas

        return modified

    # ------------------------------------------------------------------
    # Risk overlay
    # ------------------------------------------------------------------

    def risk_overlay(
        self,
        exposure_snapshot: Dict[str, Any],
        regime: str,
    ) -> List[str]:
        """
        Check if current exposure is appropriate for the regime.

        Args:
            exposure_snapshot: Dict with keys like gross_exposure_pct, oil_delta,
                              positions (list of {strategy, symbol, notional_usd})
            regime: Current regime string

        Returns:
            List of warning strings (empty = all clear)
        """
        warnings = []
        mods = self.get_strategy_modifiers(regime)
        max_corr = mods.get("max_correlated_exposure", 1.0)

        # Check oil-correlated exposure
        positions = exposure_snapshot.get("positions", [])
        total_notional = sum(abs(p.get("notional_usd", 0)) for p in positions)
        oil_corr_notional = sum(
            abs(p.get("notional_usd", 0)) for p in positions
            if p.get("strategy", "") in self.OIL_CORRELATED_STRATEGIES
        )

        if total_notional > 0:
            oil_pct = oil_corr_notional / total_notional
            if oil_pct > max_corr:
                warnings.append(
                    f"OIL_CORR_EXCEEDED: {oil_pct:.0%} oil-correlated "
                    f"(max {max_corr:.0%} in {regime})"
                )

        # Check oil delta exposure in SHOCK/DISLOCATION
        oil_delta = abs(exposure_snapshot.get("oil_delta", 0))
        if regime in ("SHOCK", "DISLOCATION") and oil_delta > 5000:
            warnings.append(
                f"OIL_DELTA_HIGH: ${oil_delta:,.0f}/pt exposure "
                f"in {regime} regime — consider hedging"
            )

        # Check gross exposure in DISLOCATION
        gross_pct = exposure_snapshot.get("gross_exposure_pct", 0)
        if regime == "DISLOCATION" and gross_pct > 0.5:
            warnings.append(
                f"GROSS_EXPOSURE_HIGH: {gross_pct:.0%} in DISLOCATION "
                f"— reduce to <50%"
            )

        # Check for unhedged airline longs in SHOCK/DISLOCATION
        airline_symbols = {"UAL", "DAL", "AAL", "LUV", "JBLU", "ALK", "SAVE", "JETS"}
        airline_longs = [
            p for p in positions
            if p.get("symbol", "") in airline_symbols
            and p.get("side", "long") == "long"
        ]
        if airline_longs and regime in ("SHOCK", "DISLOCATION"):
            symbols = ", ".join(p["symbol"] for p in airline_longs)
            warnings.append(
                f"AIRLINE_LONGS_IN_{regime}: {symbols} — "
                f"consider closing or hedging with JETS puts"
            )

        return warnings

    # ------------------------------------------------------------------
    # Oil price extraction from bridge results
    # ------------------------------------------------------------------

    @staticmethod
    def extract_oil_data(
        bridge_results: Dict[str, Any],
        scorecard: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Extract oil price and change data from available sources.

        Priority:
        1. Cross-asset signals (if they have commodity data)
        2. EIA bridge results
        3. Scorecard component_scores commodity_shock proxy

        Returns:
            Dict with keys: price, change_24h, source
        """
        result: Dict[str, Any] = {
            "price": None,
            "change_24h": None,
            "source": None,
        }

        # Try cross-asset signals first
        cas = bridge_results.get("v6_cross_asset_signals") or scorecard.get("v6_cross_asset_signals") or {}
        if isinstance(cas, dict):
            commodity_data = cas.get("commodities", {})
            if commodity_data.get("wti_price"):
                result["price"] = float(commodity_data["wti_price"])
                result["change_24h"] = commodity_data.get("wti_change_pct")
                result["source"] = "cross_asset_signals"
                return result

        # Try EIA bridge
        eia_data = bridge_results.get("eia", [])
        if isinstance(eia_data, list):
            for item in eia_data:
                if isinstance(item, dict):
                    series = item.get("series_key", "") or item.get("series_name", "")
                    if "crude" in series.lower() or "petroleum" in series.lower():
                        val = item.get("value") or item.get("latest_value")
                        if val is not None:
                            try:
                                result["price"] = float(val)
                                result["source"] = "eia_bridge"
                            except (ValueError, TypeError):
                                pass

        # Try market_data passed through bridge_results
        market = bridge_results.get("market_data", {})
        if isinstance(market, dict):
            oil = market.get("oil", {})
            if isinstance(oil, dict):
                if oil.get("price"):
                    result["price"] = float(oil["price"])
                    result["source"] = "market_data"
                if oil.get("wti_change_pct"):
                    result["change_24h"] = float(oil["wti_change_pct"])

        # Fallback: use commodity_shock as proxy
        comp = scorecard.get("component_scores", {})
        if isinstance(comp, dict) and result["price"] is None:
            cs = comp.get("commodity_shock")
            if cs is not None:
                result["commodity_shock"] = float(cs)
                result["source"] = "scorecard_proxy"

        return result

    @staticmethod
    def extract_chokepoint_status(scorecard: Dict[str, Any]) -> Dict[str, float]:
        """Extract chokepoint risk scores from scorecard."""
        chokepoint = scorecard.get("chokepoint_risk", {})
        if not isinstance(chokepoint, dict):
            chokepoint = {}

        # Also check component_scores for chokepoint data
        comp = scorecard.get("component_scores", {})
        if isinstance(comp, dict):
            for key in ("hormuz", "bab_el_mandeb", "suez", "panama"):
                if key not in chokepoint and key in comp:
                    try:
                        chokepoint[key] = float(comp[key])
                    except (ValueError, TypeError):
                        pass

        return chokepoint

    # ------------------------------------------------------------------
    # Telegram formatting
    # ------------------------------------------------------------------

    def format_telegram(
        self,
        regime: str,
        oil_price: Optional[float] = None,
        oil_change: Optional[float] = None,
    ) -> str:
        """Format a single digest line for Telegram."""
        icons = {
            "NORMAL": "\u2705",      # green check
            "ELEVATED": "\u26a0\ufe0f",  # warning
            "SHOCK": "\U0001f6a8",     # rotating light
            "DISLOCATION": "\U0001f4a5",  # explosion
        }
        icon = icons.get(regime, "\u2753")

        parts = [f"{icon} Oil Regime: {regime}"]

        if oil_price is not None:
            parts.append(f"WTI ${oil_price:.2f}")

        if oil_change is not None:
            parts.append(f"24h {oil_change:+.1f}%")

        mods = self.get_strategy_modifiers(regime)
        promoted = mods.get("promote", [])
        suppressed = mods.get("suppress", [])
        size_mult = mods.get("size_multiplier", 1.0)

        if promoted:
            parts.append(f"Promote: {len(promoted)} strats")
        if suppressed:
            parts.append(f"Suppress: {len(suppressed)} strats")
        if size_mult != 1.0:
            parts.append(f"Size: {size_mult:.1f}x")

        persistence = self._regime_cycle_count
        if persistence > 1:
            parts.append(f"({persistence} cycles)")

        return " | ".join(parts)

    # ------------------------------------------------------------------
    # Full cycle run (convenience for crisis_monitor integration)
    # ------------------------------------------------------------------

    def run_cycle(
        self,
        bridge_results: Dict[str, Any],
        scorecard: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Full classification cycle. Extracts data, classifies, returns enrichment dict.

        Returns dict suitable for adding to scorecard:
            {
                "regime": str,
                "oil_price": float|None,
                "oil_change_24h": float|None,
                "modifiers": dict,
                "risk_warnings": list,
                "persistence_cycles": int,
                "telegram_line": str,
            }
        """
        oil_data = self.extract_oil_data(bridge_results, scorecard)
        chokepoint = self.extract_chokepoint_status(scorecard)

        regime = self.classify(
            oil_price=oil_data.get("price"),
            oil_change_24h=oil_data.get("change_24h"),
            chokepoint_status=chokepoint,
            commodity_shock=oil_data.get("commodity_shock"),
        )

        modifiers = self.get_strategy_modifiers(regime)

        # Build exposure snapshot from scorecard for risk overlay
        exposure = scorecard.get("v6_exposure_summary", {}) or {}
        risk_warnings = self.risk_overlay(exposure, regime)

        telegram_line = self.format_telegram(
            regime,
            oil_price=oil_data.get("price"),
            oil_change=oil_data.get("change_24h"),
        )

        return {
            "regime": regime,
            "oil_price": oil_data.get("price"),
            "oil_change_24h": oil_data.get("change_24h"),
            "oil_data_source": oil_data.get("source"),
            "modifiers": modifiers,
            "risk_warnings": risk_warnings,
            "persistence_cycles": self._regime_cycle_count,
            "telegram_line": telegram_line,
        }
