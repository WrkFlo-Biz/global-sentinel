"""
Global Sentinel V5 — War Strategy Engine

Loads config/war_strategies.yaml and evaluates entry/exit signals
across 15 geopolitical-event-driven strategies.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class StrategyEngine:
    """Evaluates entry/exit signals against war_strategies.yaml configuration."""

    def __init__(
        self,
        config_path: str = "config/war_strategies.yaml",
        repo_root: str | None = None,
    ) -> None:
        if repo_root is None:
            repo_root = str(Path(__file__).resolve().parents[2])
        self._repo_root = Path(repo_root)
        self._config_path = self._repo_root / config_path
        self._config: dict[str, Any] = {}
        self._strategies: dict[str, Any] = {}
        self._accounts: dict[str, Any] = {}
        self._risk_controls: dict[str, Any] = {}
        self._load_config()

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        logger.info("Loading war strategies from %s", self._config_path)
        with open(self._config_path, "r") as fh:
            self._config = yaml.safe_load(fh)
        self._strategies = self._config.get("strategies", {})
        self._accounts = self._config.get("accounts", {})
        self._risk_controls = self._config.get("risk_controls", {})
        logger.info(
            "Loaded %d strategies across %d accounts",
            len(self._strategies),
            len(self._accounts),
        )

    @property
    def strategies(self) -> dict[str, Any]:
        """Public read-only access to loaded strategies."""
        return dict(self._strategies)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_get(data: dict | None, *keys: str, default: Any = None) -> Any:
        """Nested dict access that never raises."""
        if data is None:
            return default
        current = data
        for k in keys:
            if not isinstance(current, dict):
                return default
            current = current.get(k, default)
            if current is default:
                return default
        return current

    def _regime_probability(self, scorecard: dict | None) -> float | None:
        value = self._safe_get(scorecard, "regime_shift_probability")
        if value is None:
            value = self._safe_get(scorecard, "regime_probability")
        try:
            return float(value) if value is not None else None
        except Exception:
            return None

    def _component_score(self, scorecard: dict | None, name: str, default: float = 0.0) -> float:
        value = self._safe_get(scorecard, "component_scores", name, default=default)
        try:
            return float(value)
        except Exception:
            return default

    def _chokepoint_score(self, scorecard: dict | None, name: str, default: float = 0.0) -> float:
        value = self._safe_get(scorecard, "chokepoint_risk", name, default=default)
        if value == default:
            value = self._safe_get(scorecard, "chokepoint", name, default=default)
        try:
            return float(value)
        except Exception:
            return default

    def _scanner_categories(self, scorecard: dict | None) -> set[str]:
        discoveries = (scorecard or {}).get("v6_scanner_discoveries") or []
        categories = set()
        for discovery in discoveries:
            if not isinstance(discovery, dict):
                continue
            category = str(discovery.get("category") or "").strip()
            if category:
                categories.add(category)
        return categories

    def _bridge_text(self, bridge_results: dict | None, scorecard: dict | None = None) -> str:
        payload = bridge_results if bridge_results else scorecard
        try:
            return json.dumps(payload or {}).lower()
        except Exception:
            return str(payload or "").lower()

    def _build_idea(
        self,
        strategy_name: str,
        strat_cfg: dict,
        symbol: str,
        direction: str,
        notional_usd: float,
        entry_signal: str,
        confidence: float,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
    ) -> dict[str, Any]:
        account = strat_cfg.get("account", "medium_long")
        # Defaults from risk_controls
        default_stop = self._risk_controls.get("per_position_stop_pct", 3.0)
        return {
            "strategy": strategy_name,
            "symbol": symbol,
            "direction": direction,
            "notional_usd": notional_usd,
            "entry_signal": entry_signal,
            "confidence": round(confidence, 3),
            "account": account,
            "stop_loss_pct": stop_loss_pct if stop_loss_pct is not None else default_stop,
            "take_profit_pct": take_profit_pct if take_profit_pct is not None else default_stop * 2,
        }

    def _ideas_from_positions(
        self,
        strategy_name: str,
        strat_cfg: dict,
        entry_signal: str,
        confidence: float,
    ) -> list[dict[str, Any]]:
        """Generate trade ideas for every position defined in a strategy."""
        ideas: list[dict[str, Any]] = []
        for pos in strat_cfg.get("positions", []):
            ideas.append(
                self._build_idea(
                    strategy_name=strategy_name,
                    strat_cfg=strat_cfg,
                    symbol=pos["symbol"],
                    direction=pos.get("side", "long"),
                    notional_usd=pos.get("size_usd", 10000),
                    entry_signal=entry_signal,
                    confidence=confidence,
                    stop_loss_pct=abs(pos["stop_loss_pct"]) if "stop_loss_pct" in pos else None,
                    take_profit_pct=pos.get("take_profit_pct"),
                )
            )
        return ideas

    # ------------------------------------------------------------------
    # Entry evaluation — one method per strategy condition
    # ------------------------------------------------------------------

    def _eval_oil_momentum_intraday(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        oil_chg = self._safe_get(market_data, "oil", "wti_change_pct")
        if oil_chg is not None:
            if oil_chg > 1.5:
                return self._ideas_from_positions(
                    "oil_momentum_intraday", strat,
                    entry_signal=f"WTI up {oil_chg:.2f}% from prior close",
                    confidence=min(0.5 + (oil_chg - 1.5) * 0.1, 0.95),
                )
            if oil_chg < -2.0:
                # Reverse — short energy
                ideas = self._ideas_from_positions(
                    "oil_momentum_intraday", strat,
                    entry_signal=f"WTI gapped down {oil_chg:.2f}% — reversal short",
                    confidence=min(0.4 + abs(oil_chg + 2.0) * 0.08, 0.85),
                )
                for idea in ideas:
                    idea["direction"] = "short"
                return ideas
            return []
        # Scorecard fallback: commodity shock as proxy for oil momentum
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        if commodity_shock > 0.60:
            return self._ideas_from_positions(
                "oil_momentum_intraday", strat,
                entry_signal=f"Commodity shock proxy {commodity_shock:.2f} > 0.60 (no live WTI data)",
                confidence=min(0.40 + commodity_shock * 0.3, 0.75),
            )
        return []

    def _eval_shipping_rate_explosion(
        self, strat: dict, scorecard: dict | None, **_: Any
    ) -> list[dict]:
        disruption = self._safe_get(scorecard, "maritime", "disruption_score")
        hormuz = self._chokepoint_score(scorecard, "hormuz")
        bab_el_mandeb = self._chokepoint_score(scorecard, "bab_el_mandeb")
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        if disruption is not None and disruption > 0.4:
            return self._ideas_from_positions(
                "shipping_rate_explosion", strat,
                entry_signal=f"Maritime disruption_score {disruption:.2f} > 0.4",
                confidence=min(0.5 + disruption, 0.95),
            )
        if hormuz > 0.15:
            return self._ideas_from_positions(
                "shipping_rate_explosion", strat,
                entry_signal=f"Hormuz chokepoint score {hormuz:.2f} > 0.15",
                confidence=min(0.45 + hormuz, 0.90),
            )
        if bab_el_mandeb > 0.15 or commodity_shock > 0.55:
            return self._ideas_from_positions(
                "shipping_rate_explosion", strat,
                entry_signal=f"Red Sea / commodity shock regime (Bab={bab_el_mandeb:.2f}, commodity={commodity_shock:.2f})",
                confidence=min(0.45 + max(bab_el_mandeb, commodity_shock) * 0.5, 0.88),
            )
        return []

    def _eval_defense_accumulation(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        spy_cur = self._safe_get(market_data, "SPY", "price")
        spy_prev = self._safe_get(market_data, "SPY", "prior_close")
        if spy_cur is None or spy_prev is None:
            regime_prob = self._regime_probability(scorecard) or 0.0
            policy_signals = self._component_score(scorecard, "policy_signals")
            geopolitical_tension = self._component_score(scorecard, "geopolitical_tension")
            if regime_prob >= 0.30 and (policy_signals >= 0.70 or geopolitical_tension >= 0.05):
                return self._ideas_from_positions(
                    "defense_accumulation", strat,
                    entry_signal=f"War-policy regime active (regime={regime_prob:.2f}, policy={policy_signals:.2f})",
                    confidence=min(0.45 + max(policy_signals, geopolitical_tension) * 0.4, 0.90),
                )
            return []
        if spy_cur < spy_prev:
            pct_down = (spy_prev - spy_cur) / spy_prev * 100
            return self._ideas_from_positions(
                "defense_accumulation", strat,
                entry_signal=f"SPY pullback {pct_down:.2f}% — buying defense dip",
                confidence=min(0.4 + pct_down * 0.05, 0.85),
            )
        return []

    def _eval_gold_safe_haven(
        self, strat: dict, scorecard: dict | None, **_: Any
    ) -> list[dict]:
        regime_prob = self._regime_probability(scorecard)
        currency_stress = self._component_score(scorecard, "currency_stress")
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        if regime_prob is None and currency_stress <= 0 and commodity_shock <= 0:
            return []
        if (regime_prob or 0.0) > 0.30 or currency_stress > 0.55 or commodity_shock > 0.55:
            return self._ideas_from_positions(
                "gold_safe_haven", strat,
                entry_signal=f"Safe-haven regime active (regime={regime_prob or 0.0:.2f}, currency={currency_stress:.2f}, commodity={commodity_shock:.2f})",
                confidence=min(0.45 + max(regime_prob or 0.0, currency_stress, commodity_shock) * 0.4, 0.92),
            )
        return []

    def _eval_airline_short(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        oil_new_high = self._safe_get(market_data, "oil", "new_high", default=False)
        airspace_closure = self._safe_get(market_data, "airspace_closure", default=False)
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        bab_el_mandeb = self._chokepoint_score(scorecard, "bab_el_mandeb")
        if oil_new_high or airspace_closure:
            signals = []
            if oil_new_high:
                signals.append("oil at new high")
            if airspace_closure:
                signals.append("new airspace closure")
            return self._ideas_from_positions(
                "airline_short", strat,
                entry_signal=f"Airline short: {', '.join(signals)}",
                confidence=0.65 if (oil_new_high and airspace_closure) else 0.50,
            )
        if commodity_shock > 0.60 or bab_el_mandeb > 0.15:
            return self._ideas_from_positions(
                "airline_short", strat,
                entry_signal=f"Fuel/travel shock regime (commodity={commodity_shock:.2f}, bab={bab_el_mandeb:.2f})",
                confidence=min(0.45 + max(commodity_shock, bab_el_mandeb) * 0.4, 0.88),
            )
        return []

    def _eval_europe_energy_crisis(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        eu_gas_spike = self._safe_get(market_data, "europe_gas", "change_pct")
        bab_el_mandeb = self._chokepoint_score(scorecard, "bab_el_mandeb")
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        if eu_gas_spike is not None and eu_gas_spike > 10:
            return self._ideas_from_positions(
                "europe_energy_crisis", strat,
                entry_signal=f"European gas spike {eu_gas_spike:.1f}% > 10%",
                confidence=min(0.5 + (eu_gas_spike - 10) * 0.02, 0.90),
            )
        if bab_el_mandeb > 0.15 or commodity_shock > 0.60:
            return self._ideas_from_positions(
                "europe_energy_crisis", strat,
                entry_signal=f"Europe energy shock regime (Bab={bab_el_mandeb:.2f}, commodity={commodity_shock:.2f})",
                confidence=min(0.45 + max(bab_el_mandeb, commodity_shock) * 0.45, 0.90),
            )
        return []

    def _eval_fertilizer_food_chain(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        nat_gas = self._safe_get(market_data, "nat_gas", "price")
        if nat_gas is not None and nat_gas > 5.0:
            return self._ideas_from_positions(
                "fertilizer_food_chain", strat,
                entry_signal=f"Natural gas ${nat_gas:.2f} above $5",
                confidence=min(0.45 + (nat_gas - 5.0) * 0.05, 0.85),
            )
        # Scorecard fallback: commodity shock + chokepoint disruption as proxy
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        bab_el_mandeb = self._chokepoint_score(scorecard, "bab_el_mandeb")
        if commodity_shock > 0.60 and bab_el_mandeb > 0.10:
            return self._ideas_from_positions(
                "fertilizer_food_chain", strat,
                entry_signal=f"Commodity/supply chain shock proxy (commodity={commodity_shock:.2f}, bab={bab_el_mandeb:.2f})",
                confidence=min(0.40 + commodity_shock * 0.25, 0.72),
            )
        return []

    def _eval_nuclear_renaissance(
        self, strat: dict, bridge_results: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        scanner_categories = self._scanner_categories(scorecard)
        if "satellite_isr" in scanner_categories or "post_war_copper" in scanner_categories:
            return self._ideas_from_positions(
                "nuclear_renaissance", strat,
                entry_signal="Scanner discovery overlap with long-duration infrastructure themes",
                confidence=0.50,
            )
        if bridge_results is None and scorecard is None:
            return []
        # Check for nuclear-related announcements in bridge results
        nuclear_keywords = ["nuclear", "uranium", "reactor", "fission", "atomic energy"]
        br_str = self._bridge_text(bridge_results, scorecard)
        if any(kw in br_str for kw in nuclear_keywords):
            return self._ideas_from_positions(
                "nuclear_renaissance", strat,
                entry_signal="Nuclear announcement detected in bridge results",
                confidence=0.55,
            )
        return []

    def _eval_cyber_retaliation(
        self, strat: dict, bridge_results: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        if bridge_results is None and scorecard is None:
            return []
        # Check GDELT or bridge output for cyberattack events
        cyber_keywords = ["cyberattack", "cyber attack", "ransomware", "cyber warfare", "hacking"]
        br_str = self._bridge_text(bridge_results, scorecard)
        gdelt_events = self._safe_get(bridge_results, "gdelt", "events", default=[])
        gdelt_hit = any(
            "cyber" in str(evt).lower() for evt in gdelt_events
        ) if gdelt_events else False
        keyword_hit = any(kw in br_str for kw in cyber_keywords)
        if gdelt_hit or keyword_hit:
            return self._ideas_from_positions(
                "cyber_retaliation", strat,
                entry_signal="Cyberattack event detected in GDELT/bridge data",
                confidence=0.60 if gdelt_hit else 0.45,
            )
        return []

    def _eval_vix_spike_scalp(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        vix = self._safe_get(market_data, "VIX", "price")
        if vix is not None:
            ideas: list[dict] = []
            if vix > 35:
                # Buy SVXY (short vol) when VIX spikes
                for pos in strat.get("positions", []):
                    if pos.get("condition") == "vix_above_35":
                        ideas.append(self._build_idea(
                            "vix_spike_scalp", strat,
                            symbol=pos["symbol"],
                            direction=pos.get("side", "long"),
                            notional_usd=pos.get("size_usd", 5000),
                            entry_signal=f"VIX {vix:.1f} > 35 — buy SVXY",
                            confidence=min(0.5 + (vix - 35) * 0.02, 0.85),
                        ))
            if vix < 22:
                for pos in strat.get("positions", []):
                    if pos.get("condition") == "vix_below_22":
                        ideas.append(self._build_idea(
                            "vix_spike_scalp", strat,
                            symbol=pos["symbol"],
                            direction=pos.get("side", "long"),
                            notional_usd=pos.get("size_usd", 5000),
                            entry_signal=f"VIX {vix:.1f} < 22 — buy UVXY for next spike",
                            confidence=0.40,
                        ))
            return ideas
        # Scorecard fallback: market volatility component as VIX proxy
        market_vol = self._component_score(scorecard, "market_volatility")
        if market_vol > 0.55:
            # High volatility regime — prepare for mean reversion (SVXY-like)
            for pos in strat.get("positions", []):
                if pos.get("condition") == "vix_above_35":
                    return [self._build_idea(
                        "vix_spike_scalp", strat,
                        symbol=pos["symbol"],
                        direction=pos.get("side", "long"),
                        notional_usd=pos.get("size_usd", 5000),
                        entry_signal=f"Market volatility proxy {market_vol:.2f} > 0.55 (no live VIX)",
                        confidence=min(0.38 + market_vol * 0.3, 0.68),
                    )]
        return []

    def _eval_em_capital_flight(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        dxy = self._safe_get(market_data, "DXY", "price")
        if dxy is not None and dxy > 106:
            return self._ideas_from_positions(
                "em_capital_flight", strat,
                entry_signal=f"DXY {dxy:.2f} > 106 — EM capital flight",
                confidence=min(0.45 + (dxy - 106) * 0.03, 0.85),
            )
        currency_stress = self._component_score(scorecard, "currency_stress")
        if currency_stress > 0.55:
            return self._ideas_from_positions(
                "em_capital_flight", strat,
                entry_signal=f"Currency stress {currency_stress:.2f} > 0.55",
                confidence=min(0.45 + currency_stress * 0.35, 0.82),
            )
        return []

    def _eval_inflation_hedge(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        breakeven_rising = self._safe_get(market_data, "breakeven_inflation", "rising", default=False)
        oil_price = self._safe_get(market_data, "oil", "price")
        if breakeven_rising and oil_price is not None and oil_price > 100:
            return self._ideas_from_positions(
                "inflation_hedge", strat,
                entry_signal=f"Breakeven inflation rising + oil ${oil_price:.0f} > $100",
                confidence=0.60,
            )
        # Scorecard fallback: commodity shock + currency stress as inflation proxy
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        currency_stress = self._component_score(scorecard, "currency_stress")
        if commodity_shock > 0.60 and currency_stress > 0.50:
            return self._ideas_from_positions(
                "inflation_hedge", strat,
                entry_signal=f"Inflation proxy (commodity={commodity_shock:.2f}, currency={currency_stress:.2f})",
                confidence=min(0.40 + (commodity_shock + currency_stress) * 0.2, 0.72),
            )
        return []

    def _eval_canadian_oil_premium(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        spread_narrowing = self._safe_get(market_data, "wcs_wti_spread", "narrowing", default=False)
        if spread_narrowing:
            spread = self._safe_get(market_data, "wcs_wti_spread", "value", default=0)
            return self._ideas_from_positions(
                "canadian_oil_premium", strat,
                entry_signal=f"WCS-WTI spread narrowing (${spread:.2f})",
                confidence=0.55,
            )
        # Scorecard fallback: commodity shock + chokepoint disruption narrows Canadian discount
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        hormuz = self._chokepoint_score(scorecard, "hormuz")
        if commodity_shock > 0.60 and hormuz > 0.10:
            return self._ideas_from_positions(
                "canadian_oil_premium", strat,
                entry_signal=f"Canadian oil premium proxy (commodity={commodity_shock:.2f}, hormuz={hormuz:.2f})",
                confidence=min(0.38 + commodity_shock * 0.25, 0.65),
            )
        return []

    def _eval_wall_street_vol(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        vix = self._safe_get(market_data, "VIX", "price")
        if vix is not None and vix > 25:
            return self._ideas_from_positions(
                "wall_street_vol", strat,
                entry_signal=f"VIX sustained at {vix:.1f} > 25 — vol benefits GS/MS",
                confidence=min(0.45 + (vix - 25) * 0.02, 0.80),
            )
        market_volatility = self._component_score(scorecard, "market_volatility")
        if market_volatility > 0.45:
            return self._ideas_from_positions(
                "wall_street_vol", strat,
                entry_signal=f"Market volatility component {market_volatility:.2f} > 0.45",
                confidence=min(0.40 + market_volatility * 0.35, 0.78),
            )
        return []

    def _eval_refining_crack_spread(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        crack_widening = self._safe_get(market_data, "crack_spread", "widening", default=False)
        if crack_widening:
            spread = self._safe_get(market_data, "crack_spread", "value", default=0)
            return self._ideas_from_positions(
                "refining_crack_spread", strat,
                entry_signal=f"Crack spread widening (${spread:.2f}/bbl)",
                confidence=0.55,
            )
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        if commodity_shock > 0.60:
            return self._ideas_from_positions(
                "refining_crack_spread", strat,
                entry_signal=f"Commodity shock {commodity_shock:.2f} > 0.60",
                confidence=min(0.42 + commodity_shock * 0.35, 0.82),
            )
        return []

    # ------------------------------------------------------------------
    # Oil-Shock Regime Strategies (16-20)
    # ------------------------------------------------------------------

    def _eval_oil_gap_persistence(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        """Strategy 16: Oil gaps up >2% at open — momentum continues."""
        oil_chg = self._safe_get(market_data, "oil", "wti_change_pct")
        oil_regime = self._safe_get(scorecard, "v6_oil_regime")
        if oil_chg is not None and oil_chg > 2.0:
            # Only fire in ELEVATED+ regime
            if oil_regime in ("ELEVATED", "SHOCK", "DISLOCATION"):
                return self._ideas_from_positions(
                    "oil_gap_persistence", strat,
                    entry_signal=f"Oil gap +{oil_chg:.1f}% in {oil_regime} regime",
                    confidence=min(0.50 + (oil_chg - 2.0) * 0.08, 0.90),
                )
            # Even without regime data, large gaps are tradeable
            if oil_chg > 3.0:
                return self._ideas_from_positions(
                    "oil_gap_persistence", strat,
                    entry_signal=f"Oil gap +{oil_chg:.1f}% (large gap, no regime)",
                    confidence=min(0.40 + (oil_chg - 3.0) * 0.06, 0.75),
                )
        return []

    def _eval_oil_mean_reversion(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        """Strategy 17: Oil spikes >5% intraday — short-term mean reversion."""
        oil_chg = self._safe_get(market_data, "oil", "wti_change_pct")
        oil_regime = self._safe_get(scorecard, "v6_oil_regime")
        # NEVER in DISLOCATION (suppressed)
        if oil_regime == "DISLOCATION":
            return []
        if oil_chg is not None and oil_chg > 5.0:
            return self._ideas_from_positions(
                "oil_mean_reversion", strat,
                entry_signal=f"Oil spike +{oil_chg:.1f}% — mean reversion fade",
                confidence=min(0.35 + (oil_chg - 5.0) * 0.05, 0.70),
            )
        return []

    def _eval_jet_fuel_squeeze(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        """Strategy 18: Airlines hammered when oil >$100."""
        oil_price = self._safe_get(market_data, "oil", "price")
        oil_regime = self._safe_get(scorecard, "v6_oil_regime")
        if oil_regime in ("SHOCK", "DISLOCATION"):
            return self._ideas_from_positions(
                "jet_fuel_squeeze", strat,
                entry_signal=f"Oil regime {oil_regime} — airline margin destruction",
                confidence=0.65 if oil_regime == "SHOCK" else 0.75,
            )
        if oil_price is not None and oil_price > 100:
            return self._ideas_from_positions(
                "jet_fuel_squeeze", strat,
                entry_signal=f"WTI ${oil_price:.0f} > $100 — jet fuel squeeze",
                confidence=min(0.50 + (oil_price - 100) * 0.02, 0.80),
            )
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        if commodity_shock > 0.75:
            return self._ideas_from_positions(
                "jet_fuel_squeeze", strat,
                entry_signal=f"Commodity shock {commodity_shock:.2f} proxy for oil >$100",
                confidence=min(0.40 + commodity_shock * 0.3, 0.70),
            )
        return []

    def _eval_supply_shock_pairs(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        """Strategy 19: Long energy / short airlines spread."""
        oil_regime = self._safe_get(scorecard, "v6_oil_regime")
        oil_price = self._safe_get(market_data, "oil", "price")
        oil_chg = self._safe_get(market_data, "oil", "wti_change_pct")
        if oil_regime in ("ELEVATED", "SHOCK", "DISLOCATION"):
            return self._ideas_from_positions(
                "supply_shock_pairs", strat,
                entry_signal=f"Oil regime {oil_regime} — energy/airline pairs trade",
                confidence=0.55 if oil_regime == "ELEVATED" else 0.70,
            )
        if oil_price is not None and oil_price > 95:
            return self._ideas_from_positions(
                "supply_shock_pairs", strat,
                entry_signal=f"WTI ${oil_price:.0f} > $95 — supply shock pairs",
                confidence=min(0.45 + (oil_price - 95) * 0.015, 0.70),
            )
        if oil_chg is not None and oil_chg > 3.0:
            return self._ideas_from_positions(
                "supply_shock_pairs", strat,
                entry_signal=f"Oil momentum +{oil_chg:.1f}% — pairs entry",
                confidence=min(0.40 + oil_chg * 0.05, 0.65),
            )
        return []

    def _eval_petro_inflation(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        """Strategy 20: Long TIPS + gold when oil >$95 feeds inflation."""
        oil_regime = self._safe_get(scorecard, "v6_oil_regime")
        oil_price = self._safe_get(market_data, "oil", "price")
        breakeven = self._safe_get(market_data, "breakeven", "t5yie", default=None)
        if oil_regime in ("ELEVATED", "SHOCK", "DISLOCATION"):
            # Extra conviction if breakeven is low (room to rise)
            conf = 0.50
            signal_parts = [f"Oil regime {oil_regime}"]
            if breakeven is not None and breakeven < 2.8:
                conf += 0.15
                signal_parts.append(f"5y breakeven {breakeven:.2f}% < 2.8%")
            return self._ideas_from_positions(
                "petro_inflation", strat,
                entry_signal=" + ".join(signal_parts),
                confidence=min(conf, 0.80),
            )
        if oil_price is not None and oil_price > 95:
            return self._ideas_from_positions(
                "petro_inflation", strat,
                entry_signal=f"WTI ${oil_price:.0f} > $95 — inflation hedge",
                confidence=min(0.40 + (oil_price - 95) * 0.01, 0.60),
            )
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        if commodity_shock > 0.65:
            return self._ideas_from_positions(
                "petro_inflation", strat,
                entry_signal=f"Commodity shock {commodity_shock:.2f} > 0.65 — inflation proxy",
                confidence=min(0.35 + commodity_shock * 0.25, 0.60),
            )
        return []

    # ------------------------------------------------------------------
    # Oil Cascade Strategies (21-25)
    # ------------------------------------------------------------------

    def _eval_china_oil_import_shock(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        """Strategy 21: China imports 75% of oil from Gulf. $100 oil = massive import bill increase."""
        oil_regime = self._safe_get(scorecard, "v6_oil_regime")
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        if oil_regime in ("ELEVATED", "SHOCK", "DISLOCATION"):
            conf = 0.50 if oil_regime == "ELEVATED" else 0.65 if oil_regime == "SHOCK" else 0.75
            return self._ideas_from_positions(
                "china_oil_import_shock", strat,
                entry_signal=f"Oil regime {oil_regime} — China import bill shock ($1B+/day increase)",
                confidence=conf,
            )
        if commodity_shock > 0.60:
            return self._ideas_from_positions(
                "china_oil_import_shock", strat,
                entry_signal=f"Commodity shock {commodity_shock:.2f} > 0.60 — China oil import pain",
                confidence=min(0.40 + commodity_shock * 0.25, 0.65),
            )
        return []

    def _eval_asia_energy_cascade(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        """Strategy 22: Japan/Korea oil importers — short their ETFs, long yen safe haven."""
        oil_regime = self._safe_get(scorecard, "v6_oil_regime")
        if oil_regime in ("SHOCK", "DISLOCATION"):
            return self._ideas_from_positions(
                "asia_energy_cascade", strat,
                entry_signal=f"Oil regime {oil_regime} — Japan/Korea energy import pain, yen safe haven",
                confidence=0.60 if oil_regime == "SHOCK" else 0.72,
            )
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        hormuz = self._chokepoint_score(scorecard, "hormuz")
        if commodity_shock > 0.75 and hormuz > 0.10:
            return self._ideas_from_positions(
                "asia_energy_cascade", strat,
                entry_signal=f"Asia energy cascade proxy (commodity={commodity_shock:.2f}, hormuz={hormuz:.2f})",
                confidence=min(0.40 + commodity_shock * 0.25, 0.65),
            )
        return []

    def _eval_europe_pre_open(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        """Strategy 23: Short Europe before 3 AM ET open in oil shock."""
        from datetime import datetime, timezone, timedelta
        oil_regime = self._safe_get(scorecard, "v6_oil_regime")
        if oil_regime not in ("ELEVATED", "SHOCK", "DISLOCATION"):
            return []
        # Check if we're in the 2:00-4:00 AM ET window
        now_utc = datetime.now(timezone.utc)
        et_offset = timedelta(hours=-5)  # EST (close enough for pre-market)
        now_et = now_utc + et_offset
        hour_et = now_et.hour
        if 2 <= hour_et < 4:
            return self._ideas_from_positions(
                "europe_pre_open", strat,
                entry_signal=f"Europe pre-open {now_et.strftime('%H:%M')} ET — oil regime {oil_regime}",
                confidence=0.55 if oil_regime == "ELEVATED" else 0.68,
            )
        return []

    def _eval_us_premarket_gap(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        """Strategy 24: Long USO/XLE, short JETS before 9:30 AM."""
        from datetime import datetime, timezone, timedelta
        oil_regime = self._safe_get(scorecard, "v6_oil_regime")
        if oil_regime not in ("ELEVATED", "SHOCK", "DISLOCATION"):
            return []
        now_utc = datetime.now(timezone.utc)
        et_offset = timedelta(hours=-5)
        now_et = now_utc + et_offset
        hour_et = now_et.hour
        minute_et = now_et.minute
        # 7:00 AM - 9:30 AM ET window
        if (hour_et == 7 or hour_et == 8 or (hour_et == 9 and minute_et < 30)):
            return self._ideas_from_positions(
                "us_premarket_gap", strat,
                entry_signal=f"US pre-market {now_et.strftime('%H:%M')} ET — oil regime {oil_regime}, long energy/short airlines",
                confidence=0.55 if oil_regime == "ELEVATED" else 0.68,
            )
        return []

    def _eval_commodity_currency_divergence(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None, **_: Any
    ) -> list[dict]:
        """Strategy 25: Long commodity currencies (AUD, CAD) + yen safe haven on oil spike."""
        oil_regime = self._safe_get(scorecard, "v6_oil_regime")
        if oil_regime in ("SHOCK", "DISLOCATION"):
            return self._ideas_from_positions(
                "commodity_currency_divergence", strat,
                entry_signal=f"Oil regime {oil_regime} — commodity currency divergence + yen safe haven",
                confidence=0.58 if oil_regime == "SHOCK" else 0.70,
            )
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        currency_stress = self._component_score(scorecard, "currency_stress")
        if commodity_shock > 0.70 and currency_stress > 0.50:
            return self._ideas_from_positions(
                "commodity_currency_divergence", strat,
                entry_signal=f"Commodity/currency divergence (commodity={commodity_shock:.2f}, currency={currency_stress:.2f})",
                confidence=min(0.40 + commodity_shock * 0.2, 0.62),
            )
        return []

    # ------------------------------------------------------------------
    # Shock regime overrides
    # ------------------------------------------------------------------

    def _apply_shock_overrides(self, idea: dict, regime: str) -> dict:
        """Apply shock_overrides from war_strategies.yaml when oil regime is SHOCK or DISLOCATION.

        Widens stops/targets and scales position sizes for high-volatility regimes.
        """
        if regime not in ("SHOCK", "DISLOCATION"):
            return idea
        strategy_name = idea.get("strategy", "")
        config = self._strategies.get(strategy_name, {})
        overrides = config.get("shock_overrides", {})
        if not overrides:
            return idea
        idea = dict(idea)  # shallow copy to avoid mutating original
        if "stop_loss_pct" in overrides:
            idea["stop_loss_pct"] = overrides["stop_loss_pct"]
        if "take_profit_pct" in overrides:
            idea["take_profit_pct"] = overrides["take_profit_pct"]
        if "size_multiplier" in overrides:
            idea["notional_usd"] = int(
                idea.get("notional_usd", 10000) * overrides["size_multiplier"]
            )
        if "max_positions" in overrides:
            idea["max_positions"] = overrides["max_positions"]
        idea["shock_override_applied"] = True
        logger.info(
            "SHOCK override applied: %s:%s — SL=%.1f%% TP=%.1f%% size_mult=%.1fx",
            strategy_name,
            idea.get("symbol", "?"),
            overrides.get("stop_loss_pct", 0),
            overrides.get("take_profit_pct", 0),
            overrides.get("size_multiplier", 1.0),
        )
        return idea

    # ------------------------------------------------------------------
    # AG SPREAD CASCADE (Corn/Soybean)
    # ------------------------------------------------------------------

    def _eval_ag_spread_cascade(
        self, strat: dict, market_data: dict | None, scorecard: dict | None = None,
        bridge_results: dict | None = None, **_: Any
    ) -> list[dict]:
        """Evaluate corn/soybean spread cascade strategy.

        Phase 1 (ETHANOL_RALLY): Oil elevated + fertilizer normal → long corn/short soy
        Phase 2 (SPREAD_REVERSAL): Fertilizer crisis → short corn/long soy
        """
        from src.alpha.ag_spread_signal import AgSpreadSignal, SpreadPhase

        oil_regime = self._safe_get(scorecard, "v6_oil_regime") or "NORMAL"
        commodity_shock = self._component_score(scorecard, "commodity_shock")
        hormuz = self._chokepoint_score(scorecard, "hormuz")

        # Get oil price
        oil_price = self._safe_get(market_data, "oil", "wti_price")
        if oil_price is None:
            oil_price = self._safe_get(market_data, "oil", "price")

        # Get fertilizer state from bridge results
        fert_state = self._safe_get(bridge_results, "fertilizer_state")
        if not isinstance(fert_state, dict):
            fert_state = None

        # Get corn/soy ratio from market data
        corn_price = self._safe_get(market_data, "CORN", "price")
        soy_price = self._safe_get(market_data, "SOYB", "price")
        corn_soy_ratio = None
        if corn_price and soy_price and soy_price > 0:
            corn_soy_ratio = float(corn_price) / float(soy_price)

        # Initialize signal classifier (stateless per call — state lives in crisis_monitor)
        signal = AgSpreadSignal()
        result = signal.classify_phase(
            oil_price=float(oil_price) if oil_price else None,
            oil_regime=oil_regime,
            fertilizer_state=fert_state,
            hormuz_score=hormuz,
            corn_soy_ratio=corn_soy_ratio,
            commodity_shock=commodity_shock,
        )

        phase = result.get("phase", SpreadPhase.NEUTRAL)
        confidence = result.get("confidence", 0.0)

        if phase == SpreadPhase.NEUTRAL or confidence < 0.35:
            return []

        # Generate ideas based on phase — filter positions by phase tag
        ideas: list[dict] = []
        positions = strat.get("positions", [])

        if phase == SpreadPhase.ETHANOL_RALLY:
            # Phase 1 positions + non-phase-tagged (fertilizer beneficiaries)
            target_positions = [
                p for p in positions
                if p.get("phase") == "ethanol_rally" or "phase" not in p
            ]
            signal_desc = " | ".join(result.get("signals", [])[:3])
            for pos in target_positions:
                ideas.append(self._build_idea(
                    strategy_name="ag_spread_cascade",
                    strat_cfg=strat,
                    symbol=pos["symbol"],
                    direction=pos.get("side", "long"),
                    notional_usd=pos.get("size_usd", 15000),
                    entry_signal=f"AG_SPREAD Phase 1 (ETHANOL_RALLY): {signal_desc}",
                    confidence=confidence,
                    stop_loss_pct=abs(pos["stop_loss_pct"]) if "stop_loss_pct" in pos else None,
                    take_profit_pct=pos.get("take_profit_pct"),
                ))

        elif phase == SpreadPhase.FERTILIZER_SQUEEZE:
            # Transition: only fertilizer beneficiaries, reduce corn exposure
            target_positions = [p for p in positions if "phase" not in p]
            signal_desc = " | ".join(result.get("signals", [])[:3])
            for pos in target_positions:
                ideas.append(self._build_idea(
                    strategy_name="ag_spread_cascade",
                    strat_cfg=strat,
                    symbol=pos["symbol"],
                    direction=pos.get("side", "long"),
                    notional_usd=pos.get("size_usd", 15000),
                    entry_signal=f"AG_SPREAD Transition (FERT_SQUEEZE): {signal_desc}",
                    confidence=confidence * 0.85,  # lower confidence in transition
                    stop_loss_pct=abs(pos["stop_loss_pct"]) if "stop_loss_pct" in pos else None,
                    take_profit_pct=pos.get("take_profit_pct"),
                ))

        elif phase == SpreadPhase.SPREAD_REVERSAL:
            # Phase 2 positions + non-phase-tagged (fertilizer beneficiaries)
            target_positions = [
                p for p in positions
                if p.get("phase") == "spread_reversal" or "phase" not in p
            ]
            signal_desc = " | ".join(result.get("signals", [])[:3])
            for pos in target_positions:
                ideas.append(self._build_idea(
                    strategy_name="ag_spread_cascade",
                    strat_cfg=strat,
                    symbol=pos["symbol"],
                    direction=pos.get("side", "long"),
                    notional_usd=pos.get("size_usd", 15000),
                    entry_signal=f"AG_SPREAD Phase 2 (REVERSAL): {signal_desc}",
                    confidence=confidence,
                    stop_loss_pct=abs(pos["stop_loss_pct"]) if "stop_loss_pct" in pos else None,
                    take_profit_pct=pos.get("take_profit_pct"),
                ))

        return ideas

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    # Map strategy names to their eval methods
    _EVAL_MAP: dict[str, str] = {
        "oil_momentum_intraday": "_eval_oil_momentum_intraday",
        "shipping_rate_explosion": "_eval_shipping_rate_explosion",
        "defense_accumulation": "_eval_defense_accumulation",
        "gold_safe_haven": "_eval_gold_safe_haven",
        "airline_short": "_eval_airline_short",
        "europe_energy_crisis": "_eval_europe_energy_crisis",
        "fertilizer_food_chain": "_eval_fertilizer_food_chain",
        "nuclear_renaissance": "_eval_nuclear_renaissance",
        "cyber_retaliation": "_eval_cyber_retaliation",
        "vix_spike_scalp": "_eval_vix_spike_scalp",
        "em_capital_flight": "_eval_em_capital_flight",
        "inflation_hedge": "_eval_inflation_hedge",
        "canadian_oil_premium": "_eval_canadian_oil_premium",
        "wall_street_vol": "_eval_wall_street_vol",
        "refining_crack_spread": "_eval_refining_crack_spread",
        "oil_gap_persistence": "_eval_oil_gap_persistence",
        "oil_mean_reversion": "_eval_oil_mean_reversion",
        "jet_fuel_squeeze": "_eval_jet_fuel_squeeze",
        "supply_shock_pairs": "_eval_supply_shock_pairs",
        "petro_inflation": "_eval_petro_inflation",
        "china_oil_import_shock": "_eval_china_oil_import_shock",
        "asia_energy_cascade": "_eval_asia_energy_cascade",
        "europe_pre_open": "_eval_europe_pre_open",
        "us_premarket_gap": "_eval_us_premarket_gap",
        "commodity_currency_divergence": "_eval_commodity_currency_divergence",
        "ag_spread_cascade": "_eval_ag_spread_cascade",
    }

    def evaluate_entries(
        self,
        scorecard: dict | None = None,
        bridge_results: dict | None = None,
        market_data: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate all strategy entry conditions and return triggered trade ideas.

        Args:
            scorecard: Current geopolitical scorecard (disruption scores, regime probability, etc.)
            bridge_results: Outputs from ingestion bridges (GDELT, news, etc.)
            market_data: Current market data (prices, changes, indicators)

        Returns:
            List of trade idea dicts with strategy, symbol, direction, notional, confidence, etc.
        """
        all_ideas: list[dict[str, Any]] = []

        for name, strat_cfg in self._strategies.items():
            method_name = self._EVAL_MAP.get(name)
            if method_name is None:
                logger.warning("No evaluator for strategy '%s', skipping", name)
                continue
            method = getattr(self, method_name, None)
            if method is None:
                logger.warning("Evaluator method %s not found", method_name)
                continue
            try:
                ideas = method(
                    strat=strat_cfg,
                    scorecard=scorecard,
                    bridge_results=bridge_results,
                    market_data=market_data,
                )
                if ideas:
                    logger.info(
                        "Strategy '%s' triggered %d idea(s)", name, len(ideas)
                    )
                    all_ideas.extend(ideas)
            except Exception:
                logger.exception("Error evaluating strategy '%s'", name)

        # Apply shock overrides if oil regime is SHOCK or DISLOCATION
        oil_regime = self._safe_get(scorecard, "v6_oil_regime") or "NORMAL"
        if oil_regime in ("SHOCK", "DISLOCATION"):
            logger.info(
                "Oil regime %s — applying shock_overrides to %d ideas",
                oil_regime, len(all_ideas),
            )
            all_ideas = [
                self._apply_shock_overrides(idea, oil_regime)
                for idea in all_ideas
            ]

        # Apply risk controls — check daily loss halt, max gross exposure
        kill_vix = self._safe_get(self._risk_controls, "kill_triggers", "vix_above")
        if kill_vix is not None and market_data is not None:
            vix = self._safe_get(market_data, "VIX", "price")
            if vix is not None and vix > kill_vix:
                logger.warning(
                    "VIX %.1f exceeds kill trigger %.1f — suppressing ALL entries",
                    vix,
                    kill_vix,
                )
                return []

        return all_ideas

    def evaluate_exits(
        self,
        active_positions: list[dict[str, Any]] | None = None,
        market_data: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Evaluate exit signals for currently held positions.

        Args:
            active_positions: List of active position dicts, each containing at
                minimum: strategy, symbol, side, entry_price, qty, unrealized_pnl_pct
            market_data: Current market data

        Returns:
            List of exit signal dicts with strategy, symbol, action, reason, urgency.
        """
        if not active_positions:
            return []

        exits: list[dict[str, Any]] = []

        for pos in active_positions:
            strategy_name = pos.get("strategy", "")
            symbol = pos.get("symbol", "")
            side = pos.get("side", "long")
            unrealized_pct = pos.get("unrealized_pnl_pct", 0.0)
            strat_cfg = self._strategies.get(strategy_name, {})

            # Global stop loss check
            stop_pct = self._risk_controls.get("per_position_stop_pct", 3.0)
            if unrealized_pct < -stop_pct:
                exits.append({
                    "strategy": strategy_name,
                    "symbol": symbol,
                    "action": "exit",
                    "reason": f"Stop loss hit: {unrealized_pct:.2f}% exceeds -{stop_pct}%",
                    "urgency": "high",
                })
                continue

            # Strategy-specific exits
            if strategy_name == "oil_momentum_intraday":
                # Ceasefire rumor — immediate exit
                ceasefire = self._safe_get(market_data, "ceasefire_rumor", default=False)
                if ceasefire:
                    exits.append({
                        "strategy": strategy_name,
                        "symbol": symbol,
                        "action": "exit",
                        "reason": "Ceasefire rumor detected — exit ALL oil positions",
                        "urgency": "critical",
                    })
                    continue
                if unrealized_pct >= 2.0:
                    exits.append({
                        "strategy": strategy_name,
                        "symbol": symbol,
                        "action": "trim",
                        "reason": f"Take 50% profit at +{unrealized_pct:.1f}%",
                        "urgency": "medium",
                    })

            elif strategy_name == "shipping_rate_explosion":
                hormuz_open = self._safe_get(market_data, "hormuz_reopened", default=False)
                if hormuz_open:
                    exits.append({
                        "strategy": strategy_name,
                        "symbol": symbol,
                        "action": "exit",
                        "reason": "Hormuz reopened to commercial traffic — exit ALL shipping",
                        "urgency": "critical",
                    })
                    continue
                if unrealized_pct >= 15.0:
                    exits.append({
                        "strategy": strategy_name,
                        "symbol": symbol,
                        "action": "trim",
                        "reason": f"Trim 25% — shipping position up {unrealized_pct:.1f}%",
                        "urgency": "low",
                    })

            elif strategy_name == "defense_accumulation":
                # Only exit on confirmed ceasefire + defense budget cuts
                ceasefire_confirmed = self._safe_get(market_data, "ceasefire_confirmed", default=False)
                budget_cuts = self._safe_get(market_data, "defense_budget_cuts", default=False)
                if ceasefire_confirmed and budget_cuts:
                    exits.append({
                        "strategy": strategy_name,
                        "symbol": symbol,
                        "action": "exit",
                        "reason": "Ceasefire confirmed + defense budget cuts",
                        "urgency": "medium",
                    })

            elif strategy_name == "gold_safe_haven":
                gold_drop_pct = self._safe_get(market_data, "gold", "drop_from_peak_pct")
                if gold_drop_pct is not None and gold_drop_pct > 3.0 and symbol == "GDX":
                    exits.append({
                        "strategy": strategy_name,
                        "symbol": symbol,
                        "action": "trim",
                        "reason": f"Gold dropped {gold_drop_pct:.1f}% from peak — trim GDX",
                        "urgency": "medium",
                    })

            elif strategy_name == "airline_short":
                oil_drop = self._safe_get(market_data, "oil", "wti_change_pct")
                if oil_drop is not None and oil_drop < -5.0:
                    exits.append({
                        "strategy": strategy_name,
                        "symbol": symbol,
                        "action": "exit",
                        "reason": f"Oil dropped {oil_drop:.1f}% — cover ALL airline shorts",
                        "urgency": "high",
                    })
                    continue
                if side == "short" and unrealized_pct >= 3.0:
                    exits.append({
                        "strategy": strategy_name,
                        "symbol": symbol,
                        "action": "trim",
                        "reason": f"Cover 50% on {unrealized_pct:.1f}% gain",
                        "urgency": "medium",
                    })

            elif strategy_name == "vix_spike_scalp":
                vix = self._safe_get(market_data, "VIX", "price")
                if symbol == "UVXY" and vix is not None:
                    exits.append({
                        "strategy": strategy_name,
                        "symbol": symbol,
                        "action": "exit",
                        "reason": "NEVER hold UVXY overnight — flat by close",
                        "urgency": "high",
                    })

            # VIX kill trigger — reduce all
            kill_vix = self._safe_get(self._risk_controls, "kill_triggers", "vix_above")
            vix = self._safe_get(market_data, "VIX", "price")
            if kill_vix and vix and vix > kill_vix:
                reduce_pct = self._safe_get(
                    self._risk_controls, "kill_triggers", "vix_reduce_pct", default=50
                )
                exits.append({
                    "strategy": strategy_name,
                    "symbol": symbol,
                    "action": "trim",
                    "reason": f"VIX {vix:.0f} > kill trigger {kill_vix} — reduce by {reduce_pct}%",
                    "urgency": "critical",
                })

        return exits

    def daily_performance(
        self,
        filled_orders: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Compute daily PnL from filled orders, grouped by strategy.

        Args:
            filled_orders: List of filled order dicts, each with at minimum:
                strategy, symbol, side, qty, fill_price, realized_pnl

        Returns:
            Dict with per-strategy PnL, total PnL, and target comparison.
        """
        if not filled_orders:
            return {
                "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                "total_pnl": 0.0,
                "by_strategy": {},
                "daily_target": self._config.get("daily_target_usd", 500),
                "target_met": False,
            }

        by_strategy: dict[str, float] = {}
        total_pnl = 0.0

        for order in filled_orders:
            strat = order.get("strategy", "unknown")
            pnl = order.get("realized_pnl", 0.0)
            by_strategy[strat] = by_strategy.get(strat, 0.0) + pnl
            total_pnl += pnl

        daily_target = self._config.get("daily_target_usd", 500)
        return {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "total_pnl": round(total_pnl, 2),
            "by_strategy": {k: round(v, 2) for k, v in by_strategy.items()},
            "daily_target": daily_target,
            "target_met": total_pnl >= daily_target,
        }

    def strategy_scorecard(
        self,
        filled_orders: list[dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Per-strategy performance scorecard: trades, win_rate, avg_pnl, max_dd.

        Args:
            filled_orders: List of filled order dicts with at minimum:
                strategy, realized_pnl

        Returns:
            Dict keyed by strategy name with trades, win_rate, avg_pnl, max_dd.
        """
        if not filled_orders:
            return {}

        # Group orders by strategy
        by_strat: dict[str, list[float]] = {}
        for order in filled_orders:
            strat = order.get("strategy", "unknown")
            pnl = order.get("realized_pnl", 0.0)
            by_strat.setdefault(strat, []).append(pnl)

        scorecard: dict[str, dict[str, Any]] = {}
        for strat, pnls in by_strat.items():
            trades = len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            total_pnl = sum(pnls)

            # Max drawdown: largest peak-to-trough in cumulative PnL
            cumulative = 0.0
            peak = 0.0
            max_dd = 0.0
            for p in pnls:
                cumulative += p
                if cumulative > peak:
                    peak = cumulative
                dd = peak - cumulative
                if dd > max_dd:
                    max_dd = dd

            scorecard[strat] = {
                "trades": trades,
                "win_rate": round(wins / trades, 3) if trades > 0 else 0.0,
                "avg_pnl": round(total_pnl / trades, 2) if trades > 0 else 0.0,
                "max_dd": round(max_dd, 2),
                "total_pnl": round(total_pnl, 2),
            }

        return scorecard
