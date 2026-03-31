#!/usr/bin/env python3
"""Multi-Persona Consensus & Disagreement Detector.

Runs market data through 5 isolated "strategy personas" modeled after different
investment philosophies. Each persona evaluates independently on the same data.
When personas DISAGREE strongly, that flags a potential trade opportunity.

Personas:
1. MACRO_SOVEREIGN (Dalio-style) — Global macro, risk parity, debt cycles
2. VALUE_ACTIVIST (Buffett/Munger-style) — Deep value, moats, margin of safety
3. EVENT_CATALYST (Ackman-style) — Activist catalysts, asymmetric bets
4. QUANT_MOMENTUM (Cohen-style) — Short-term momentum, information edge, flow
5. GEOPOLITICAL_ALPHA (GS-native) — War trades, chokepoint arbitrage, cascades

Each persona produces:
- Direction: BULLISH / BEARISH / NEUTRAL
- Conviction: 0.0-1.0
- Allocation suggestion: fraction of equity
- Reasoning: one-line thesis

Disagreement scoring:
- 5/5 agree = strong consensus (trade with high confidence)
- 4/5 agree = moderate consensus (trade with normal confidence)
- 3/2 split = interesting divergence (investigate, potential contrarian)
- Strong disagreement (high conviction on both sides) = HIGHEST SIGNAL

Integrates with StrategyEngine as a meta-layer over existing trade ideas.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PersonaVerdict:
    """A single persona's evaluation of a trade opportunity."""
    def __init__(
        self,
        persona: str,
        direction: str,
        conviction: float,
        allocation_pct: float,
        reasoning: str,
    ):
        self.persona = persona
        self.direction = direction  # BULLISH, BEARISH, NEUTRAL
        self.conviction = min(max(conviction, 0.0), 1.0)
        self.allocation_pct = allocation_pct
        self.reasoning = reasoning


class MultiPersonaConsensus:
    """Evaluates trade opportunities through 5 isolated investment personas."""

    PERSONAS = [
        "MACRO_SOVEREIGN",
        "VALUE_ACTIVIST",
        "EVENT_CATALYST",
        "QUANT_MOMENTUM",
        "GEOPOLITICAL_ALPHA",
    ]

    def __init__(self) -> None:
        self._history: List[Dict[str, Any]] = []

    def evaluate_opportunity(
        self,
        symbol: str,
        price: Optional[float],
        market_data: Optional[Dict[str, Any]] = None,
        scorecard: Optional[Dict[str, Any]] = None,
        strategy_ideas: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Run all 5 personas on a trade opportunity and score consensus.

        Returns:
            Dict with verdicts, consensus_score, disagreement_signal, etc.
        """
        market_data = market_data or {}
        scorecard = scorecard or {}
        strategy_ideas = strategy_ideas or []

        # Each persona evaluates independently
        verdicts = [
            self._eval_macro_sovereign(symbol, price, market_data, scorecard),
            self._eval_value_activist(symbol, price, market_data, scorecard),
            self._eval_event_catalyst(symbol, price, market_data, scorecard, strategy_ideas),
            self._eval_quant_momentum(symbol, price, market_data, scorecard),
            self._eval_geopolitical_alpha(symbol, price, market_data, scorecard, strategy_ideas),
        ]

        # Score consensus and disagreement
        result = self._score_consensus(symbol, verdicts)
        self._history.append(result)
        if len(self._history) > 100:
            self._history = self._history[-100:]

        return result

    def evaluate_batch(
        self,
        trade_ideas: List[Dict[str, Any]],
        market_data: Optional[Dict[str, Any]] = None,
        scorecard: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Evaluate multiple trade ideas through the persona consensus system.

        Returns ideas sorted by disagreement signal strength (strongest first).
        """
        results = []
        for idea in trade_ideas:
            symbol = idea.get("symbol", "")
            price = idea.get("price") or idea.get("notional_usd")
            result = self.evaluate_opportunity(
                symbol=symbol,
                price=price,
                market_data=market_data,
                scorecard=scorecard,
                strategy_ideas=[idea],
            )
            result["original_idea"] = idea
            results.append(result)

        # Sort by disagreement strength (strongest disagreements first — they're the best signals)
        results.sort(key=lambda r: r.get("disagreement_strength", 0), reverse=True)
        return results

    # ------------------------------------------------------------------
    # Persona evaluators (isolated — each sees the same data independently)
    # ------------------------------------------------------------------

    def _eval_macro_sovereign(
        self, symbol: str, price: Optional[float],
        market_data: Dict[str, Any], scorecard: Dict[str, Any],
    ) -> PersonaVerdict:
        """Dalio-style: Global macro, debt cycles, risk parity."""
        regime_prob = _safe_float(scorecard.get("regime_shift_probability"), 0.0)
        commodity_shock = _safe_float(
            (scorecard.get("component_scores") or {}).get("commodity_shock"), 0.0
        )
        currency_stress = _safe_float(
            (scorecard.get("component_scores") or {}).get("currency_stress"), 0.0
        )
        vix = _safe_float((market_data.get("VIX") or {}).get("price"), 20.0)

        # Macro sovereign focuses on regime and deleveraging risk
        if regime_prob > 0.5 and commodity_shock > 0.6:
            return PersonaVerdict(
                "MACRO_SOVEREIGN", "BEARISH", min(regime_prob, 0.9), 0.02,
                f"Regime shift {regime_prob:.0%} + commodity shock {commodity_shock:.0%} = deleveraging risk"
            )
        if vix > 35:
            return PersonaVerdict(
                "MACRO_SOVEREIGN", "BEARISH", 0.7, 0.01,
                f"VIX {vix:.0f} signals systemic stress — reduce risk"
            )
        if regime_prob < 0.2 and commodity_shock < 0.3:
            return PersonaVerdict(
                "MACRO_SOVEREIGN", "BULLISH", 0.5, 0.03,
                "Low regime risk, moderate commodity pressure — risk-on"
            )
        return PersonaVerdict(
            "MACRO_SOVEREIGN", "NEUTRAL", 0.3, 0.01,
            "Mixed macro signals — stay balanced"
        )

    def _eval_value_activist(
        self, symbol: str, price: Optional[float],
        market_data: Dict[str, Any], scorecard: Dict[str, Any],
    ) -> PersonaVerdict:
        """Buffett/Munger-style: Deep value, moats, margin of safety."""
        # Value investors are contrarian — they buy when others panic
        regime_prob = _safe_float(scorecard.get("regime_shift_probability"), 0.0)
        vix = _safe_float((market_data.get("VIX") or {}).get("price"), 20.0)

        # High VIX + panic = value buying opportunity
        if vix > 30 and regime_prob > 0.3:
            # But only for quality names, not war trades
            war_symbols = {"USO", "XOP", "STNG", "FRO", "JETS", "UVXY", "SVXY"}
            if symbol in war_symbols:
                return PersonaVerdict(
                    "VALUE_ACTIVIST", "NEUTRAL", 0.3, 0.0,
                    f"War trade {symbol} — no durable moat, pass"
                )
            return PersonaVerdict(
                "VALUE_ACTIVIST", "BULLISH", 0.6, 0.03,
                f"Fear elevated (VIX {vix:.0f}) — buy quality at discount"
            )

        # Fertilizer/defense companies have pricing power moats
        moat_symbols = {"MOS", "CF", "NTR", "LMT", "RTX", "NOC"}
        if symbol in moat_symbols:
            return PersonaVerdict(
                "VALUE_ACTIVIST", "BULLISH", 0.65, 0.03,
                f"{symbol} has pricing power moat in current environment"
            )

        return PersonaVerdict(
            "VALUE_ACTIVIST", "NEUTRAL", 0.4, 0.01,
            "Waiting for wider margin of safety"
        )

    def _eval_event_catalyst(
        self, symbol: str, price: Optional[float],
        market_data: Dict[str, Any], scorecard: Dict[str, Any],
        strategy_ideas: List[Dict[str, Any]],
    ) -> PersonaVerdict:
        """Ackman-style: Catalyst-driven, asymmetric risk/reward."""
        regime_prob = _safe_float(scorecard.get("regime_shift_probability"), 0.0)
        hormuz = _safe_float(
            (scorecard.get("chokepoint_risk") or {}).get("hormuz"), 0.0
        )

        # Catalyst: war escalation/de-escalation
        if hormuz > 0.5:
            # Hormuz closed = massive catalyst for energy, shipping, fertilizer
            catalyst_symbols = {
                "USO", "XOP", "STNG", "FRO", "MOS", "CF", "CORN",
                "SOYB", "LMT", "RTX", "GLD"
            }
            if symbol in catalyst_symbols:
                return PersonaVerdict(
                    "EVENT_CATALYST", "BULLISH", 0.8, 0.05,
                    f"Hormuz closure ({hormuz:.0%}) = clear catalyst for {symbol}"
                )
            anti_catalyst = {"JETS", "UAL", "AAL", "EWJ", "EWY", "FXI"}
            if symbol in anti_catalyst:
                return PersonaVerdict(
                    "EVENT_CATALYST", "BEARISH", 0.75, 0.04,
                    f"Hormuz closure devastates {symbol} — asymmetric short"
                )

        # Check for strategy-specific catalysts
        for idea in strategy_ideas:
            if idea.get("confidence", 0) > 0.7:
                direction = idea.get("direction", "long")
                return PersonaVerdict(
                    "EVENT_CATALYST",
                    "BULLISH" if direction == "long" else "BEARISH",
                    idea["confidence"] * 0.9,
                    0.03,
                    f"Strategy '{idea.get('strategy')}' catalyst: {idea.get('entry_signal', 'unknown')[:80]}"
                )

        return PersonaVerdict(
            "EVENT_CATALYST", "NEUTRAL", 0.3, 0.01,
            "No clear catalyst identified"
        )

    def _eval_quant_momentum(
        self, symbol: str, price: Optional[float],
        market_data: Dict[str, Any], scorecard: Dict[str, Any],
    ) -> PersonaVerdict:
        """Cohen-style: Short-term momentum, information edge, flow."""
        # Momentum follows price and volume
        sym_data = market_data.get(symbol, {})
        if isinstance(sym_data, dict):
            change_pct = _safe_float(sym_data.get("change_pct"), None)
            if change_pct is not None:
                if change_pct > 3.0:
                    return PersonaVerdict(
                        "QUANT_MOMENTUM", "BULLISH", min(0.5 + change_pct * 0.05, 0.85), 0.03,
                        f"{symbol} momentum +{change_pct:.1f}% — trend continuation"
                    )
                if change_pct < -3.0:
                    return PersonaVerdict(
                        "QUANT_MOMENTUM", "BEARISH", min(0.5 + abs(change_pct) * 0.05, 0.85), 0.03,
                        f"{symbol} momentum {change_pct:.1f}% — trend continuation short"
                    )

        # Sector momentum from scorecard
        commodity_shock = _safe_float(
            (scorecard.get("component_scores") or {}).get("commodity_shock"), 0.0
        )
        energy_symbols = {"USO", "XOP", "XLE", "OXY", "STNG", "FRO"}
        if symbol in energy_symbols and commodity_shock > 0.5:
            return PersonaVerdict(
                "QUANT_MOMENTUM", "BULLISH", 0.6, 0.03,
                f"Energy sector momentum strong (commodity shock {commodity_shock:.0%})"
            )

        return PersonaVerdict(
            "QUANT_MOMENTUM", "NEUTRAL", 0.3, 0.01,
            "Insufficient momentum signal"
        )

    def _eval_geopolitical_alpha(
        self, symbol: str, price: Optional[float],
        market_data: Dict[str, Any], scorecard: Dict[str, Any],
        strategy_ideas: List[Dict[str, Any]],
    ) -> PersonaVerdict:
        """GS-native: War trades, chokepoint arbitrage, second-order cascades."""
        regime_prob = _safe_float(scorecard.get("regime_shift_probability"), 0.0)
        commodity_shock = _safe_float(
            (scorecard.get("component_scores") or {}).get("commodity_shock"), 0.0
        )
        hormuz = _safe_float(
            (scorecard.get("chokepoint_risk") or {}).get("hormuz"), 0.0
        )
        policy = _safe_float(
            (scorecard.get("component_scores") or {}).get("policy_signals"), 0.0
        )

        # This persona is highly tuned to GS's strength — second-order effects
        if regime_prob > 0.4 and commodity_shock > 0.5:
            # Ag spread cascade is a pure GS play
            ag_symbols = {"CORN", "SOYB", "MOS", "CF", "NTR", "WEAT"}
            if symbol in ag_symbols:
                return PersonaVerdict(
                    "GEOPOLITICAL_ALPHA", "BULLISH", 0.75, 0.04,
                    f"2nd-order cascade: oil→fertilizer→{symbol} (regime {regime_prob:.0%}, commodity {commodity_shock:.0%})"
                )

            shipping = {"STNG", "FRO", "ZIM", "NAT"}
            if symbol in shipping and hormuz > 0.3:
                return PersonaVerdict(
                    "GEOPOLITICAL_ALPHA", "BULLISH", 0.8, 0.05,
                    f"Hormuz chokepoint {hormuz:.0%} → shipping rates explosion"
                )

        # Use existing strategy ideas as strong signal
        for idea in strategy_ideas:
            if idea.get("symbol") == symbol and idea.get("confidence", 0) > 0.5:
                direction = "BULLISH" if idea.get("direction") == "long" else "BEARISH"
                return PersonaVerdict(
                    "GEOPOLITICAL_ALPHA", direction,
                    idea["confidence"],
                    0.03,
                    f"GS strategy '{idea.get('strategy')}' active: {idea.get('entry_signal', '')[:80]}"
                )

        return PersonaVerdict(
            "GEOPOLITICAL_ALPHA", "NEUTRAL", 0.3, 0.01,
            "No geopolitical edge detected"
        )

    # ------------------------------------------------------------------
    # Consensus & disagreement scoring
    # ------------------------------------------------------------------

    def _score_consensus(
        self, symbol: str, verdicts: List[PersonaVerdict],
    ) -> Dict[str, Any]:
        """Score the degree of consensus and disagreement among personas."""
        bullish = [v for v in verdicts if v.direction == "BULLISH"]
        bearish = [v for v in verdicts if v.direction == "BEARISH"]
        neutral = [v for v in verdicts if v.direction == "NEUTRAL"]

        total = len(verdicts)
        bull_count = len(bullish)
        bear_count = len(bearish)

        # Consensus score: how much agreement (0 = max disagreement, 1 = unanimity)
        max_direction = max(bull_count, bear_count, len(neutral))
        consensus_score = max_direction / total

        # Disagreement strength: high conviction on BOTH sides = strongest signal
        bull_conviction = max((v.conviction for v in bullish), default=0.0)
        bear_conviction = max((v.conviction for v in bearish), default=0.0)
        disagreement_strength = 0.0

        if bull_count > 0 and bear_count > 0:
            # Both sides represented — disagreement exists
            min_side = min(bull_count, bear_count)
            # Strength = product of top convictions * balance of sides
            disagreement_strength = (
                bull_conviction * bear_conviction
                * (min_side / total)
                * 2.0  # amplify when sides are balanced
            )
            disagreement_strength = min(disagreement_strength, 1.0)

        # Net direction
        if bull_count > bear_count:
            net_direction = "BULLISH"
            net_conviction = sum(v.conviction for v in bullish) / bull_count
        elif bear_count > bull_count:
            net_direction = "BEARISH"
            net_conviction = sum(v.conviction for v in bearish) / bear_count
        else:
            net_direction = "SPLIT"
            net_conviction = 0.5

        # Suggested allocation (conviction-weighted average)
        total_alloc = sum(v.allocation_pct * v.conviction for v in verdicts)
        total_conv = sum(v.conviction for v in verdicts) or 1.0
        suggested_allocation = total_alloc / total_conv

        return {
            "symbol": symbol,
            "net_direction": net_direction,
            "net_conviction": round(net_conviction, 3),
            "consensus_score": round(consensus_score, 3),
            "disagreement_strength": round(disagreement_strength, 3),
            "is_disagreement_signal": disagreement_strength > 0.25,
            "bull_count": bull_count,
            "bear_count": bear_count,
            "neutral_count": len(neutral),
            "suggested_allocation_pct": round(suggested_allocation, 4),
            "verdicts": [
                {
                    "persona": v.persona,
                    "direction": v.direction,
                    "conviction": round(v.conviction, 3),
                    "allocation_pct": v.allocation_pct,
                    "reasoning": v.reasoning,
                }
                for v in verdicts
            ],
        }

    # ------------------------------------------------------------------
    # Telegram formatting
    # ------------------------------------------------------------------

    def format_telegram(self, result: Dict[str, Any]) -> str:
        """Format persona consensus result for Telegram."""
        symbol = result.get("symbol", "?")
        direction = result.get("net_direction", "SPLIT")
        conviction = result.get("net_conviction", 0.0)
        consensus = result.get("consensus_score", 0.0)
        disagreement = result.get("disagreement_strength", 0.0)

        icons = {"BULLISH": "\U0001f7e2", "BEARISH": "\U0001f534", "SPLIT": "\U0001f7e1", "NEUTRAL": "\u26aa"}
        icon = icons.get(direction, "\u2753")

        header = f"{icon} {symbol}: {direction} ({conviction:.0%} conviction)"

        if result.get("is_disagreement_signal"):
            header += f" \u26a1 DISAGREEMENT SIGNAL ({disagreement:.0%})"

        lines = [header]
        for v in result.get("verdicts", []):
            v_icon = icons.get(v["direction"], "\u26aa")
            lines.append(
                f"  {v_icon} {v['persona']}: {v['direction']} "
                f"({v['conviction']:.0%}) — {v['reasoning'][:60]}"
            )

        return "\n".join(lines)

    def format_batch_telegram(self, results: List[Dict[str, Any]]) -> str:
        """Format batch results, highlighting top disagreements."""
        disagreements = [r for r in results if r.get("is_disagreement_signal")]
        consensus = [r for r in results if not r.get("is_disagreement_signal")]

        lines = []
        if disagreements:
            lines.append("\U0001f4a1 **PERSONA DISAGREEMENTS** (investigate):")
            for r in disagreements[:3]:
                lines.append(self.format_telegram(r))
                lines.append("")

        if consensus:
            lines.append(f"\u2705 **CONSENSUS TRADES** ({len(consensus)} ideas agree):")
            for r in consensus[:3]:
                symbol = r.get("symbol", "?")
                direction = r.get("net_direction", "SPLIT")
                conviction = r.get("net_conviction", 0.0)
                count = r.get("bull_count", 0) if direction == "BULLISH" else r.get("bear_count", 0)
                lines.append(f"  {symbol}: {direction} {count}/5 ({conviction:.0%})")

        return "\n".join(lines)


def _safe_float(val: Any, default: float = 0.0) -> float:
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default
