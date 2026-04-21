"""Feature encoder for QPanda / classical optimization research.

Encodes candidate + regime + microstructure inputs into normalized
research features. Output is intentionally simple and auditable.
"""
from __future__ import annotations

from typing import Any, Dict, List


class QFinanceFeatureEncoder:

    def encode_candidate(
        self,
        *,
        candidate: Dict[str, Any],
        regime_state: Dict[str, Any],
        market_microstructure: Dict[str, Any],
    ) -> Dict[str, Any]:
        symbol = str(candidate.get("symbol", ""))
        theme = str(candidate.get("theme", "unknown")).lower()
        sector = str(candidate.get("sector", "unknown"))

        base_score = float(candidate.get("score", 0.0))
        event_score = float(candidate.get("event_score", 0.0))
        quality_score = float(candidate.get("quality_score", 0.0))
        anomaly_score = float(candidate.get("anomaly_score", 0.0))

        micro = market_microstructure.get(symbol, {})
        adv = float(micro.get("adv_shares", 0.0) or 0.0)
        sigma = float(micro.get("sigma_daily", 0.0) or 0.0)

        regime_prob = float(regime_state.get("regime_shift_probability", 0.5))
        macro_state = str(regime_state.get("macro_state", "mixed")).lower()
        geo_state = str(regime_state.get("geopolitical_state", "monitoring")).lower()

        liquidity_score = min(adv / 20_000_000.0, 1.0)
        volatility_penalty = min(sigma / 0.05, 1.0)

        regime_alignment = 0.0
        if geo_state in {"heightened", "crisis"} and theme in {"energy", "defense", "gold", "rates"}:
            regime_alignment += 1.0
        if macro_state in {"inflationary_stress", "inflation"} and theme in {"energy", "gold", "rates"}:
            regime_alignment += 0.8
        if macro_state in {"growth", "de_escalation"} and theme in {"ai", "tech", "semis"}:
            regime_alignment += 0.8

        return {
            "symbol": symbol,
            "sector": sector,
            "theme": theme,
            "base_score": base_score,
            "event_score": event_score,
            "quality_score": quality_score,
            "anomaly_score": anomaly_score,
            "liquidity_score": liquidity_score,
            "volatility_penalty": volatility_penalty,
            "regime_alignment": regime_alignment,
            "regime_shift_probability": regime_prob,
            "preopt_feature_score": (
                0.35 * base_score
                + 0.20 * event_score
                + 0.15 * quality_score
                + 0.10 * anomaly_score
                + 0.15 * liquidity_score
                + 0.15 * regime_alignment
                - 0.10 * volatility_penalty
            ),
        }

    def encode_universe(
        self,
        *,
        candidate_universe: List[Dict[str, Any]],
        regime_state: Dict[str, Any],
        market_microstructure: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        rows = [
            self.encode_candidate(
                candidate=c,
                regime_state=regime_state,
                market_microstructure=market_microstructure,
            )
            for c in candidate_universe
        ]
        rows.sort(key=lambda x: float(x.get("preopt_feature_score", 0.0)), reverse=True)
        return rows
