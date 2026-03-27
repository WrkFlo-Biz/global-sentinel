"""Regime-conditioned optimizer for research.

Chooses objectives and candidate filters based on the current
regime state before handing off to classical or quantum optimization.
"""
from __future__ import annotations

from typing import Dict, Any, List

from src.research.qfinance_objective_library import QFinanceObjectiveLibrary
from src.research.candidate_universe_ranker import CandidateUniverseRanker


class RegimeConditionedOptimizer:

    def __init__(self):
        self.objectives = QFinanceObjectiveLibrary()
        self.ranker = CandidateUniverseRanker()

    def prepare(
        self,
        *,
        candidate_universe: List[Dict[str, Any]],
        regime_state: Dict[str, Any],
        market_microstructure: Dict[str, Any],
    ) -> Dict[str, Any]:
        ranked = self.ranker.rank(
            candidate_universe=candidate_universe,
            regime_state=regime_state,
            market_microstructure=market_microstructure,
        )

        objective_type = self._select_objective(regime_state)
        objective = self.objectives.get(objective_type)

        filtered = self._filter_candidates(ranked, objective_type)

        return {
            "objective": objective,
            "candidate_universe": filtered,
            "objective_type": objective_type,
        }

    def _select_objective(self, regime_state: Dict[str, Any]) -> str:
        geo = str(regime_state.get("geopolitical_state", "monitoring")).lower()
        macro = str(regime_state.get("macro_state", "mixed")).lower()

        if geo in {"heightened", "crisis"}:
            return "hedge_basket_optimization"
        if macro in {"inflationary_stress", "banking_stress"}:
            return "risk_management_research"
        if macro in {"growth", "de_escalation"}:
            return "portfolio_optimization"
        return "anomaly_detection_research"

    def _filter_candidates(self, ranked: List[Dict[str, Any]], objective_type: str) -> List[Dict[str, Any]]:
        if objective_type == "hedge_basket_optimization":
            allow = {"energy", "defense", "gold", "rates", "utilities"}
            return [r for r in ranked if str(r.get("theme", "")).lower() in allow][:50]

        if objective_type == "portfolio_optimization":
            allow = {"ai", "tech", "semis", "industrials", "quality"}
            return [r for r in ranked if str(r.get("theme", "")).lower() in allow][:50]

        if objective_type == "risk_management_research":
            allow = {"gold", "rates", "energy", "defense", "quality"}
            return [r for r in ranked if str(r.get("theme", "")).lower() in allow][:50]

        return ranked[:50]
