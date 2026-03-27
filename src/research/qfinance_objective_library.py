"""Reusable optimization objective library for QPanda / classical research.

Each objective returns metadata describing the scoring function,
constraint hints, and purpose. These are consumed by the request
builder and regime-conditioned optimizer.
"""
from __future__ import annotations

from typing import Any, Dict


class QFinanceObjectiveLibrary:

    def hedge_basket_optimization(self) -> Dict[str, Any]:
        return {
            "type": "hedge_basket_optimization",
            "target": "maximize_risk_adjusted_protection",
            "score_components": [
                "regime_alignment_score",
                "liquidity_score",
                "correlation_hedge_score",
                "impact_penalty",
            ],
            "description": "Maximize downside protection under liquidity and impact constraints.",
        }

    def portfolio_optimization(self) -> Dict[str, Any]:
        return {
            "type": "portfolio_optimization",
            "target": "maximize_expected_utility_under_constraints",
            "score_components": [
                "expected_return_score",
                "risk_penalty",
                "diversification_score",
                "impact_penalty",
            ],
            "description": "Optimize multi-name allocation under sector, risk, and execution constraints.",
        }

    def derivative_pricing_research(self) -> Dict[str, Any]:
        return {
            "type": "derivative_pricing_research",
            "target": "scenario_payoff_efficiency",
            "score_components": [
                "stress_payoff_score",
                "premium_efficiency_score",
                "theta_penalty",
            ],
            "description": "Rank option or structured hedge setups under scenario-based payoff analysis.",
        }

    def risk_management_research(self) -> Dict[str, Any]:
        return {
            "type": "risk_management_research",
            "target": "minimize_drawdown_and_tail_risk",
            "score_components": [
                "tail_risk_reduction_score",
                "drawdown_reduction_score",
                "liquidity_score",
                "impact_penalty",
            ],
            "description": "Find baskets that reduce drawdown and tail sensitivity during stress regimes.",
        }

    def anomaly_detection_research(self) -> Dict[str, Any]:
        return {
            "type": "anomaly_detection_research",
            "target": "maximize_signal_novelty_with_plausibility",
            "score_components": [
                "anomaly_score",
                "macro_consistency_score",
                "event_confirmation_score",
            ],
            "description": "Identify unusual but plausible candidate dislocations for research review.",
        }

    def get(self, objective_type: str) -> Dict[str, Any]:
        mapping = {
            "hedge_basket_optimization": self.hedge_basket_optimization,
            "portfolio_optimization": self.portfolio_optimization,
            "derivative_pricing_research": self.derivative_pricing_research,
            "risk_management_research": self.risk_management_research,
            "anomaly_detection_research": self.anomaly_detection_research,
        }
        fn = mapping.get(objective_type)
        if fn is None:
            return self.hedge_basket_optimization()
        return fn()
