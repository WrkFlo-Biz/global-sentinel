"""Tests for quantum enhancement modules."""
import pytest
from src.research.classical_strong_baseline import ClassicalStrongBaseline
from src.research.quantum_utility_score import QuantumUtilityScorer
from src.research.quantum_formulation_validator import QuantumFormulationValidator
from src.research.qaoa_hyperparameter_library import QAOAHyperparameterLibrary
from src.research.higher_order_objectives import HigherOrderObjectives
from src.research.rqaoa_structured_optimizer import RQAOAStructuredOptimizer


def test_classical_strong_baseline_sharpe():
    bl = ClassicalStrongBaseline()
    candidates = [
        {"symbol": "XOM", "preopt_feature_score": 0.8, "volatility_penalty": 0.3},
        {"symbol": "AAPL", "preopt_feature_score": 0.6, "volatility_penalty": 0.4},
        {"symbol": "NVDA", "preopt_feature_score": 0.9, "volatility_penalty": 0.5},
    ]
    result = bl.optimize(candidates, objective_type="sharpe")
    assert result["candidate_count"] == 3
    assert result["not_for_direct_execution"] is True
    assert len(result["all_weights"]) == 3
    assert abs(sum(result["all_weights"]) - 1.0) < 0.01


def test_classical_strong_baseline_empty():
    bl = ClassicalStrongBaseline()
    result = bl.optimize([], objective_type="sharpe")
    assert result["candidate_count"] == 0


def test_classical_strong_baseline_min_variance():
    bl = ClassicalStrongBaseline()
    candidates = [
        {"symbol": "A", "preopt_feature_score": 0.5, "volatility_penalty": 0.1},
        {"symbol": "B", "preopt_feature_score": 0.5, "volatility_penalty": 0.8},
    ]
    result = bl.optimize(candidates, objective_type="min_variance", constraints={"max_single_weight": 0.95})
    try:
        import scipy  # noqa: F401
        assert result["all_weights"][0] > result["all_weights"][1]
    except ImportError:
        assert result["all_weights"][0] == result["all_weights"][1]


def test_quantum_utility_scorer():
    scorer = QuantumUtilityScorer()
    result = scorer.score(
        quantum_result={"sharpe_ratio": 1.2, "elapsed_seconds": 5.0},
        classical_result={"sharpe_ratio": 1.0, "elapsed_seconds": 1.0},
        metadata={"feasibility_rate": 0.9, "rerun_stability": 0.85},
    )
    assert "overall_utility" in result
    assert result["overall_utility"] > 0
    assert result["not_for_direct_execution"] is True


def test_quantum_utility_significance():
    scorer = QuantumUtilityScorer()
    q_scores = [1.1, 1.2, 1.3, 1.0, 1.4]
    c_scores = [0.9, 1.0, 1.1, 0.8, 1.2]
    result = scorer.statistical_significance(q_scores, c_scores)
    assert "sample_size" in result
    assert result["sample_size"] == 5


def test_quantum_utility_insufficient_samples():
    scorer = QuantumUtilityScorer()
    result = scorer.statistical_significance([1.0], [0.9])
    assert not result["significant"]


def test_formulation_validator_valid(tmp_path):
    import yaml
    registry = {
        "formulations": {
            "portfolio_optimization_qaoa": {
                "problem_family": "portfolio_optimization",
                "max_decision_variables": 10,
                "max_circuit_depth": 20,
                "encoding": "QUBO",
            }
        }
    }
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.dump(registry))
    validator = QuantumFormulationValidator(registry_path=path)
    result = validator.validate({
        "formulation_id": "portfolio_optimization_qaoa",
        "decision_variable_count": 5,
        "circuit_depth": 10,
        "encoding": "QUBO",
    })
    assert result["valid"]


def test_formulation_validator_too_many_vars(tmp_path):
    import yaml
    registry = {
        "formulations": {
            "test_qaoa": {
                "problem_family": "test",
                "max_decision_variables": 5,
                "max_circuit_depth": 10,
                "encoding": "QUBO",
            }
        }
    }
    path = tmp_path / "registry.yaml"
    path.write_text(yaml.dump(registry))
    validator = QuantumFormulationValidator(registry_path=path)
    result = validator.validate({
        "formulation_id": "test_qaoa",
        "decision_variable_count": 15,
    })
    assert not result["valid"]


def test_qaoa_library_store_and_retrieve(tmp_path):
    lib = QAOAHyperparameterLibrary(storage_path=tmp_path / "params.json")
    lib.store_result("crisis", 5, "sharpe", {"gamma": [0.1], "beta": [0.2]}, 1.5)
    ws = lib.get_warm_start("crisis", 5, "sharpe")
    assert ws is not None
    assert ws["warm_start_params"]["gamma"] == [0.1]


def test_qaoa_library_no_match(tmp_path):
    lib = QAOAHyperparameterLibrary(storage_path=tmp_path / "params.json")
    ws = lib.get_warm_start("growth", 3, "cvar")
    assert ws is None


def test_higher_order_cvar():
    ho = HigherOrderObjectives()
    returns = [-0.05, -0.03, -0.01, 0.02, 0.04, 0.06, 0.08, 0.10]
    cvar = ho.compute_cvar(returns, [1.0], alpha=0.25)
    assert cvar < 0  # Worst 25% should be negative


def test_higher_order_max_drawdown():
    ho = HigherOrderObjectives()
    cum_ret = [100, 105, 103, 98, 102, 110]
    dd = ho.compute_max_drawdown(cum_ret)
    assert dd > 0
    assert dd < 1.0


def test_higher_order_skewness():
    ho = HigherOrderObjectives()
    # Right-skewed distribution
    returns = [0.01, 0.02, 0.01, 0.03, 0.15]
    skew = ho.compute_skewness(returns)
    assert skew > 0


def test_higher_order_list():
    ho = HigherOrderObjectives()
    objectives = ho.list_objectives()
    assert len(objectives) == 3
    types = [o["type"] for o in objectives]
    assert "cvar" in types
    assert "max_drawdown" in types


def test_rqaoa_basic():
    rq = RQAOAStructuredOptimizer(max_iterations=3)
    candidates = [
        {"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"},
        {"symbol": "D"}, {"symbol": "E"}, {"symbol": "F"},
    ]
    scores = [0.9, 0.3, 0.7, 0.2, 0.8, 0.1]
    result = rq.optimize(candidates, scores)
    assert result["candidate_count"] == 6
    assert result["not_for_direct_execution"] is True
    # High-scoring candidates should get weight
    selected_symbols = [s["symbol"] for s in result["selected_candidates"]]
    assert "A" in selected_symbols  # Highest score


def test_rqaoa_empty():
    rq = RQAOAStructuredOptimizer()
    result = rq.optimize([], [])
    assert result["candidate_count"] == 0


def test_rqaoa_reduces_problem():
    rq = RQAOAStructuredOptimizer(max_iterations=5, fix_fraction=0.3)
    candidates = [{"symbol": f"S{i}"} for i in range(10)]
    scores = [0.1 * i for i in range(10)]
    result = rq.optimize(candidates, scores)
    assert result["final_free_count"] < 10
    assert result["iterations_used"] > 0
