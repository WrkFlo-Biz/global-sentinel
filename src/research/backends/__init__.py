"""Research-only backend package for Global Sentinel quantum finance experiments.

All backend results are artifact-only and must carry
``not_for_direct_execution=True`` style guardrails.
"""

from importlib import import_module

__all__ = [
    "AnomalyScreeningPipeline",
    "ClassicalStrongBaseline",
    "PennyLaneAnomalyDetector",
    "QPandaQAEOptionPricer",
    "QiskitPortfolioOptimizer",
]


def __getattr__(name):
    if name == "AnomalyScreeningPipeline":
        return import_module(
            "src.research.backends.anomaly_screening_pipeline"
        ).AnomalyScreeningPipeline
    if name == "ClassicalStrongBaseline":
        return import_module("src.research.backends.classical_strong_baseline").ClassicalStrongBaseline
    if name == "PennyLaneAnomalyDetector":
        return import_module("src.research.backends.pennylane_anomaly_detector").PennyLaneAnomalyDetector
    if name == "QPandaQAEOptionPricer":
        return import_module("src.research.backends.qpanda_qae_pricer").QPandaQAEOptionPricer
    if name == "QiskitPortfolioOptimizer":
        return import_module("src.research.backends.qiskit_portfolio_optimizer").QiskitPortfolioOptimizer
    raise AttributeError(name)
