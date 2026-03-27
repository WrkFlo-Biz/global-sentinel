"""Pre-optimization candidate universe ranker.

Combines base score, regime alignment, liquidity preference,
impact penalty, and event relevance into a preopt_score for
downstream optimization.
"""
from __future__ import annotations

from typing import Dict, Any, List


class CandidateUniverseRanker:

    def rank(
        self,
        *,
        candidate_universe: List[Dict[str, Any]],
        regime_state: Dict[str, Any],
        market_microstructure: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        ranked = []

        regime_prob = float(regime_state.get("regime_shift_probability", 0.5))
        macro_state = str(regime_state.get("macro_state", "mixed")).lower()
        geo_state = str(regime_state.get("geopolitical_state", "monitoring")).lower()

        for row in candidate_universe:
            symbol = str(row.get("symbol", ""))
            base_score = float(row.get("score", 0.0))
            event_score = float(row.get("event_score", 0.0))
            theme = str(row.get("theme", "")).lower()

            micro = market_microstructure.get(symbol, {})
            adv = float(micro.get("adv_shares", 0.0) or 0.0)
            sigma = float(micro.get("sigma_daily", 0.0) or 0.0)

            liquidity_bonus = min(adv / 20_000_000.0, 1.0) * 0.15
            impact_penalty = min(sigma / 0.05, 1.0) * 0.10

            regime_alignment = 0.0
            if geo_state in {"heightened", "crisis"} and theme in {"energy", "defense", "gold", "rates", "utilities"}:
                regime_alignment += 0.20
            if macro_state in {"inflationary_stress", "inflation"} and theme in {"energy", "gold", "rates"}:
                regime_alignment += 0.15
            if macro_state in {"growth", "de_escalation"} and theme in {"ai", "tech", "semis"}:
                regime_alignment += 0.15

            final_score = base_score + event_score + (regime_prob * 0.10) + liquidity_bonus + regime_alignment - impact_penalty

            new_row = dict(row)
            new_row["preopt_score"] = final_score
            new_row["liquidity_bonus"] = liquidity_bonus
            new_row["impact_penalty"] = impact_penalty
            new_row["regime_alignment"] = regime_alignment
            ranked.append(new_row)

        ranked.sort(key=lambda x: float(x.get("preopt_score", 0.0)), reverse=True)
        return ranked
