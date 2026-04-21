#!/usr/bin/env python3
"""Agricultural Spread Signal — Corn/Soybean Spread Cascade Strategy.

Implements the second/third-order geopolitical trade chain:

Phase 1 (ETHANOL RALLY):
  Oil > $90/bbl → ethanol attractive → corn demand rises → corn/soy ratio rips
  Signal: LONG corn, SHORT soybeans (via ETFs or IBKR futures)

Phase 2 (FERTILIZER SQUEEZE → SPREAD REVERSAL):
  Strait of Hormuz chokes nitrogen fertilizer → fertilizer $480→$700+/ton
  Corn needs lots of nitrogen → too expensive to grow
  Farmers rotate to soybeans (self-fertilizing)
  Signal: SHORT corn, LONG soybeans (spread reversal)

The module monitors:
- Oil regime (from OilShockRegime)
- Fertilizer prices (from FertilizerBridge)
- Corn/soybean ratio (CORN ETF / SOYB ETF, or ZC/ZS futures)
- Chokepoint status (Hormuz closure = fertilizer supply disruption)

Trade instruments:
- ETFs: CORN (Teucrium Corn), SOYB (Teucrium Soybean)
- Futures via IBKR: ZC (corn), ZS (soybeans)
- Fertilizer equities: MOS, CF, NTR (benefit from high fert prices)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# Spread phase classification
class SpreadPhase:
    NEUTRAL = "NEUTRAL"
    ETHANOL_RALLY = "ETHANOL_RALLY"          # Phase 1: corn outperforms
    FERTILIZER_SQUEEZE = "FERTILIZER_SQUEEZE"  # Transition: fert costs spiking
    SPREAD_REVERSAL = "SPREAD_REVERSAL"        # Phase 2: soybeans outperform


# Thresholds
OIL_ETHANOL_THRESHOLD = 90.0      # Oil above $90 → ethanol becomes attractive
FERTILIZER_SQUEEZE_THRESHOLD = 500.0  # Urea > $500/ton → corn planting uneconomical
FERTILIZER_CRISIS_THRESHOLD = 650.0   # Urea > $650/ton → hard reversal signal
HORMUZ_DISRUPTION_THRESHOLD = 0.3     # Hormuz score > 0.3 → fertilizer supply risk
CORN_SOY_RATIO_BREAKOUT = 0.05       # 5% move from baseline = significant


class AgSpreadSignal:
    """Monitors and generates signals for corn/soybean spread cascade trades."""

    def __init__(self) -> None:
        self._phase: str = SpreadPhase.NEUTRAL
        self._phase_history: List[Dict[str, Any]] = []
        self._corn_soy_ratio_baseline: Optional[float] = None

    def classify_phase(
        self,
        oil_price: Optional[float] = None,
        oil_regime: str = "NORMAL",
        fertilizer_state: Optional[Dict[str, Any]] = None,
        hormuz_score: float = 0.0,
        corn_soy_ratio: Optional[float] = None,
        commodity_shock: float = 0.0,
    ) -> Dict[str, Any]:
        """Classify the current phase of the corn/soybean spread cascade.

        Returns:
            Dict with phase, confidence, signals, and trade recommendations.
        """
        fert = fertilizer_state or {}
        urea_price = fert.get("urea_price_estimated")
        fert_regime = fert.get("fertilizer_regime", "UNKNOWN")
        fert_disruption = fert.get("disruption_score", 0.3)

        signals: List[str] = []
        phase = SpreadPhase.NEUTRAL
        confidence = 0.0

        # --- Phase 1: Ethanol Rally (corn outperforms) ---
        oil_above_threshold = (
            (oil_price is not None and oil_price >= OIL_ETHANOL_THRESHOLD)
            or oil_regime in ("ELEVATED", "SHOCK", "DISLOCATION")
        )

        fertilizer_normal = (
            urea_price is None
            or urea_price < FERTILIZER_SQUEEZE_THRESHOLD
        )

        if oil_above_threshold and fertilizer_normal:
            phase = SpreadPhase.ETHANOL_RALLY
            signals.append(f"Oil regime {oil_regime} → ethanol demand lifting corn")
            if oil_price:
                signals.append(f"WTI ${oil_price:.2f} > ${OIL_ETHANOL_THRESHOLD}")
            if urea_price:
                signals.append(f"Fertilizer ${urea_price:.0f}/ton still manageable")
            confidence = self._ethanol_rally_confidence(
                oil_price, oil_regime, commodity_shock, corn_soy_ratio
            )

        # --- Phase 2: Fertilizer Squeeze (transition) ---
        fertilizer_squeeze = (
            (urea_price is not None and urea_price >= FERTILIZER_SQUEEZE_THRESHOLD)
            or fert_regime in ("SHOCK", "CRISIS")
            or (hormuz_score >= HORMUZ_DISRUPTION_THRESHOLD and oil_above_threshold)
        )

        if oil_above_threshold and fertilizer_squeeze:
            # Check if we're in full reversal or still transitioning
            if (
                (urea_price is not None and urea_price >= FERTILIZER_CRISIS_THRESHOLD)
                or (hormuz_score >= 0.5 and fert_disruption >= 0.7)
            ):
                phase = SpreadPhase.SPREAD_REVERSAL
                signals.append("FERTILIZER CRISIS → corn uneconomical to grow")
                signals.append("Farmers rotating to soybeans (self-fertilizing)")
                if urea_price:
                    signals.append(f"Urea ${urea_price:.0f}/ton > ${FERTILIZER_CRISIS_THRESHOLD} crisis threshold")
                if hormuz_score >= 0.5:
                    signals.append(f"Hormuz score {hormuz_score:.2f} choking fertilizer supply")
                confidence = self._reversal_confidence(
                    urea_price, hormuz_score, fert_disruption, corn_soy_ratio
                )
            else:
                phase = SpreadPhase.FERTILIZER_SQUEEZE
                signals.append("Fertilizer costs rising — spread under pressure")
                if urea_price:
                    signals.append(f"Urea ${urea_price:.0f}/ton approaching squeeze zone")
                if hormuz_score >= HORMUZ_DISRUPTION_THRESHOLD:
                    signals.append(f"Hormuz disruption {hormuz_score:.2f} threatens fertilizer supply")
                confidence = self._squeeze_confidence(
                    urea_price, hormuz_score, fert_disruption
                )

        # Update ratio baseline tracking
        if corn_soy_ratio is not None:
            if self._corn_soy_ratio_baseline is None:
                self._corn_soy_ratio_baseline = corn_soy_ratio
            ratio_change = (corn_soy_ratio - self._corn_soy_ratio_baseline) / self._corn_soy_ratio_baseline
            if abs(ratio_change) > CORN_SOY_RATIO_BREAKOUT:
                signals.append(
                    f"Corn/Soy ratio moved {ratio_change:+.1%} from baseline "
                    f"({self._corn_soy_ratio_baseline:.4f} → {corn_soy_ratio:.4f})"
                )

        self._phase = phase
        self._phase_history.append({"phase": phase, "confidence": confidence})
        if len(self._phase_history) > 50:
            self._phase_history = self._phase_history[-50:]

        return {
            "phase": phase,
            "confidence": round(confidence, 3),
            "signals": signals,
            "fertilizer_regime": fert_regime,
            "oil_regime": oil_regime,
            "urea_price": urea_price,
            "hormuz_score": hormuz_score,
            "corn_soy_ratio": corn_soy_ratio,
            "trade_recommendations": self._get_trade_recommendations(phase, confidence),
        }

    def _get_trade_recommendations(
        self, phase: str, confidence: float
    ) -> List[Dict[str, Any]]:
        """Generate trade recommendations based on spread phase."""
        if confidence < 0.35:
            return []

        recs: List[Dict[str, Any]] = []

        if phase == SpreadPhase.ETHANOL_RALLY:
            # Phase 1: Long corn / short soy spread
            recs.append({
                "spread": "CORN_LONG_SOY_SHORT",
                "description": "Oil spike → ethanol demand → corn outperforms soybeans",
                "legs": [
                    {"symbol": "CORN", "direction": "long", "weight": 0.5},
                    {"symbol": "SOYB", "direction": "short", "weight": 0.5},
                ],
                "ibkr_futures": [
                    {"symbol": "ZC", "direction": "long"},
                    {"symbol": "ZS", "direction": "short"},
                ],
                "fertilizer_beneficiaries": [
                    {"symbol": "MOS", "direction": "long", "thesis": "Fertilizer demand rising with corn"},
                    {"symbol": "CF", "direction": "long", "thesis": "Nitrogen fertilizer producer benefits"},
                ],
                "exit_trigger": "Fertilizer > $500/ton OR Hormuz disruption > 0.3",
            })

        elif phase == SpreadPhase.FERTILIZER_SQUEEZE:
            # Transition: take profits on corn leg, prepare to reverse
            recs.append({
                "spread": "REDUCE_CORN_EXPOSURE",
                "description": "Fertilizer costs rising — corn rally losing steam",
                "legs": [
                    {"symbol": "CORN", "direction": "reduce_long", "weight": 0.3},
                    {"symbol": "MOS", "direction": "long", "weight": 0.4,
                     "thesis": "Fertilizer producers benefit from price spike"},
                    {"symbol": "CF", "direction": "long", "weight": 0.3,
                     "thesis": "Nitrogen fertilizer pure play"},
                ],
                "exit_trigger": "Urea > $650/ton (flip to full reversal)",
            })

        elif phase == SpreadPhase.SPREAD_REVERSAL:
            # Phase 2: Short corn / long soy spread
            recs.append({
                "spread": "CORN_SHORT_SOY_LONG",
                "description": "Fertilizer crisis → farmers abandon corn → soybeans win",
                "legs": [
                    {"symbol": "SOYB", "direction": "long", "weight": 0.5},
                    {"symbol": "CORN", "direction": "short", "weight": 0.5},
                ],
                "ibkr_futures": [
                    {"symbol": "ZS", "direction": "long"},
                    {"symbol": "ZC", "direction": "short"},
                ],
                "fertilizer_beneficiaries": [
                    {"symbol": "MOS", "direction": "long", "thesis": "Fertilizer scarcity premium"},
                    {"symbol": "NTR", "direction": "long", "thesis": "Potash/nitrogen diversified producer"},
                ],
                "exit_trigger": "Hormuz reopens OR fertilizer < $400/ton OR ceasefire",
            })

        return recs

    # ------------------------------------------------------------------
    # Confidence scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _ethanol_rally_confidence(
        oil_price: Optional[float],
        oil_regime: str,
        commodity_shock: float,
        corn_soy_ratio: Optional[float],
    ) -> float:
        """Score confidence for ethanol rally phase."""
        conf = 0.30  # base

        # Oil price contribution
        if oil_price is not None:
            if oil_price >= 100:
                conf += 0.25
            elif oil_price >= 95:
                conf += 0.20
            elif oil_price >= 90:
                conf += 0.15

        # Oil regime contribution
        regime_boost = {
            "ELEVATED": 0.10, "SHOCK": 0.20, "DISLOCATION": 0.25
        }
        conf += regime_boost.get(oil_regime, 0.0)

        # Commodity shock
        if commodity_shock > 0.6:
            conf += 0.10

        return min(conf, 0.90)

    @staticmethod
    def _squeeze_confidence(
        urea_price: Optional[float],
        hormuz_score: float,
        fert_disruption: float,
    ) -> float:
        """Score confidence for fertilizer squeeze transition."""
        conf = 0.30

        if urea_price is not None:
            if urea_price >= 600:
                conf += 0.25
            elif urea_price >= 500:
                conf += 0.15

        if hormuz_score >= 0.5:
            conf += 0.20
        elif hormuz_score >= 0.3:
            conf += 0.10

        if fert_disruption >= 0.7:
            conf += 0.10

        return min(conf, 0.85)

    @staticmethod
    def _reversal_confidence(
        urea_price: Optional[float],
        hormuz_score: float,
        fert_disruption: float,
        corn_soy_ratio: Optional[float],
    ) -> float:
        """Score confidence for full spread reversal."""
        conf = 0.35  # higher base — this is a strong signal

        if urea_price is not None:
            if urea_price >= 700:
                conf += 0.25
            elif urea_price >= 650:
                conf += 0.20

        if hormuz_score >= 0.7:
            conf += 0.20
        elif hormuz_score >= 0.5:
            conf += 0.15

        if fert_disruption >= 0.8:
            conf += 0.10

        return min(conf, 0.92)

    # ------------------------------------------------------------------
    # Telegram formatting
    # ------------------------------------------------------------------

    def format_telegram(self, phase_result: Dict[str, Any]) -> str:
        """Format spread signal for Telegram digest."""
        phase = phase_result.get("phase", SpreadPhase.NEUTRAL)
        conf = phase_result.get("confidence", 0.0)
        signals = phase_result.get("signals", [])

        icons = {
            SpreadPhase.NEUTRAL: "\u26aa",          # white circle
            SpreadPhase.ETHANOL_RALLY: "\U0001f33d",   # corn
            SpreadPhase.FERTILIZER_SQUEEZE: "\u26a0\ufe0f",  # warning
            SpreadPhase.SPREAD_REVERSAL: "\U0001f504",  # reverse arrows
        }
        icon = icons.get(phase, "\u2753")

        parts = [f"{icon} Ag Spread: {phase} ({conf:.0%})"]
        if phase_result.get("urea_price"):
            parts.append(f"Urea ${phase_result['urea_price']:.0f}/ton")
        for sig in signals[:2]:  # max 2 signal lines
            parts.append(sig)

        recs = phase_result.get("trade_recommendations", [])
        if recs:
            spread_name = recs[0].get("spread", "")
            parts.append(f"Trade: {spread_name}")

        return " | ".join(parts)
