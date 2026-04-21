#!/usr/bin/env python3
"""Build a quantum optimization request using regime-conditioned preparation.

Uses RegimeConditionedOptimizer to select objectives and filter candidates,
PortfolioConstraintBuilder for constraints, and optionally includes
derivative candidates before constructing the request.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from src.research.regime_conditioned_optimizer import RegimeConditionedOptimizer
from src.research.portfolio_constraint_builder import PortfolioConstraintBuilder
from src.research.derivative_candidate_builder import DerivativeCandidateBuilder
from src.research.build_quantum_request import QuantumRequestBuilder


def parse_args():
    p = argparse.ArgumentParser(description="Build regime-conditioned quantum request")
    p.add_argument("--package-id", required=True)
    p.add_argument("--candidate-json", required=True)
    p.add_argument("--market-micro-json", required=True)
    p.add_argument("--regime-state-json", required=True)
    p.add_argument("--runtime-flags-json", required=False, default=None)
    p.add_argument("--time-window-json", required=False, default=None)
    p.add_argument("--include-derivatives", action="store_true")
    p.add_argument("--output-json", required=True)
    return p.parse_args()


def load_json(path: str, default: Dict[str, Any]) -> Dict[str, Any]:
    if not path:
        return default
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main():
    args = parse_args()

    candidate_universe = json.loads(Path(args.candidate_json).read_text(encoding="utf-8"))
    market_micro = json.loads(Path(args.market_micro_json).read_text(encoding="utf-8"))
    regime_state = json.loads(Path(args.regime_state_json).read_text(encoding="utf-8"))
    runtime_flags = load_json(args.runtime_flags_json, {"shadow_mode_only": True, "incident_mode": False})
    time_window_state = load_json(
        args.time_window_json,
        {"window": "overnight", "impact_multiplier": 1.0, "confidence_multiplier": 1.0},
    )

    prepared = RegimeConditionedOptimizer().prepare(
        candidate_universe=candidate_universe,
        regime_state=regime_state,
        market_microstructure=market_micro,
    )

    prepared_universe = prepared["candidate_universe"]

    if args.include_derivatives:
        derivs = DerivativeCandidateBuilder().build(
            underlyings=prepared_universe,
            regime_state=regime_state,
        )
        prepared_universe = prepared_universe + derivs

    objective = prepared["objective"]
    constraints = PortfolioConstraintBuilder().build(
        objective_type=prepared["objective_type"],
        runtime_flags=runtime_flags,
        time_window_state=time_window_state,
        regime_state=regime_state,
        session_context=time_window_state.get("session_context") if isinstance(time_window_state.get("session_context"), dict) else None,
    )

    req = QuantumRequestBuilder().build(
        package_id=args.package_id,
        objective=objective,
        constraints=constraints,
        candidate_universe=prepared_universe,
        market_microstructure=market_micro,
        runtime_flags=runtime_flags,
        time_window_state=time_window_state,
        regime_state=regime_state,
        provenance={
            "builder": "build_regime_conditioned_request",
            "objective_type": prepared["objective_type"],
            "include_derivatives": args.include_derivatives,
        },
    )

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(req.to_dict(), indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
