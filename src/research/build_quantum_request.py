#!/usr/bin/env python3
"""CLI entrypoint for building quantum optimization requests.

Wraps QuantumRequestBuilder with command-line args for:
- Loading candidate universe from JSON
- Loading market microstructure from JSON
- Setting objective type
- Writing output request JSON

Usage:
    python -m src.research.build_quantum_request \
        --package-id cycle_42 \
        --candidate-json artifacts/candidates.json \
        --market-micro-json artifacts/microstructure.json \
        --output-json artifacts/quantum/request_42.json \
        --objective-type hedge_basket_optimization
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from src.packets.quantum_optimization_request import QuantumOptimizationRequest


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class QuantumRequestBuilder:
    """Build QuantumOptimizationRequest from structured inputs."""

    def build(
        self,
        *,
        package_id: str,
        objective: Dict[str, Any],
        constraints: Dict[str, Any],
        candidate_universe: List[Dict[str, Any]],
        market_microstructure: Dict[str, Any],
        runtime_flags: Dict[str, Any],
        time_window_state: Dict[str, Any],
        regime_state: Dict[str, Any],
        provenance: Dict[str, Any],
    ) -> QuantumOptimizationRequest:
        return QuantumOptimizationRequest(
            request_id=f"qreq-{package_id}-{int(datetime.now(timezone.utc).timestamp())}",
            package_id=package_id,
            timestamp_utc=utc_now_iso(),
            runtime_flags=runtime_flags,
            time_window_state=time_window_state,
            regime_state=regime_state,
            objective=objective,
            constraints=constraints,
            candidate_universe=candidate_universe,
            market_microstructure=market_microstructure,
            provenance=provenance,
        )


def parse_args():
    p = argparse.ArgumentParser(description="Build quantum optimization request JSON")
    p.add_argument("--package-id", required=True, help="Package/cycle identifier")
    p.add_argument("--candidate-json", required=True, help="Path to candidate universe JSON")
    p.add_argument("--market-micro-json", required=True, help="Path to market microstructure JSON")
    p.add_argument("--output-json", required=True, help="Output path for request JSON")
    p.add_argument("--objective-type", default="hedge_basket_optimization",
                   choices=[
                       "hedge_basket_optimization",
                       "portfolio_optimization",
                       "derivative_pricing_research",
                       "risk_management_research",
                       "anomaly_detection_research",
                       "constrained_subset_selection",
                       "scenario_allocation",
                       "robust_portfolio_design",
                   ])
    return p.parse_args()


def main():
    args = parse_args()

    candidates = json.loads(Path(args.candidate_json).read_text(encoding="utf-8"))
    market_micro = json.loads(Path(args.market_micro_json).read_text(encoding="utf-8"))

    builder = QuantumRequestBuilder()
    req = builder.build(
        package_id=args.package_id,
        objective={"type": args.objective_type, "target": "maximize_risk_adjusted_protection"},
        constraints={
            "max_names": 8,
            "max_sector_weight": 0.30,
            "max_participation_rate": 0.02,
            "impact_budget_bps": 15.0,
        },
        candidate_universe=candidates,
        market_microstructure=market_micro,
        runtime_flags={"shadow_mode_only": True, "incident_mode": False},
        time_window_state={"window": "overnight", "impact_multiplier": 1.0, "confidence_multiplier": 1.0},
        regime_state={"regime_shift_probability": 0.5, "macro_state": "mixed", "geopolitical_state": "monitoring"},
        provenance={"builder": "QuantumRequestBuilder", "source_snapshot_id": args.package_id},
    )

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(req.to_dict(), indent=2), encoding="utf-8")
    print(f"Request written: {out}")


if __name__ == "__main__":
    main()
