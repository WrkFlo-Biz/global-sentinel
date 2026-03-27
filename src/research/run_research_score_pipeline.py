#!/usr/bin/env python3
"""Research score pipeline orchestrator.

Single entrypoint that orchestrates:
1. Load request
2. Run classical + quantum optimizers
3. Evaluate against trade outcomes
4. Write bounded research score
5. Optionally attach score to snapshot
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Dict

from src.packets.quantum_optimization_request import QuantumOptimizationRequest
from src.research.classical_optimizer_baseline import ClassicalOptimizerBaseline
from src.research.evaluate_trade_outcomes import evaluate
from src.research.research_score_writer import build_research_score
from src.research.attach_research_score_to_snapshot import attach_research_score
from src.utils.storage_artifact_io import StorageArtifactIO

logger = logging.getLogger(__name__)


def load_request(path: Path) -> QuantumOptimizationRequest:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return QuantumOptimizationRequest(**raw)


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_args():
    p = argparse.ArgumentParser(description="Run full research score pipeline")
    p.add_argument("--request-json", required=True)
    p.add_argument("--trade-outcomes-json", required=True)
    p.add_argument("--snapshot-json", required=False)
    p.add_argument("--quantum-artifact-dir", default="artifacts/quantum")
    p.add_argument("--classical-artifact-dir", default="artifacts/classical")
    p.add_argument("--evaluation-out", default="reports/research/evaluation_latest.json")
    p.add_argument("--research-score-out", default="reports/research/research_score_latest.json")
    p.add_argument("--snapshot-out", default="reports/research/snapshot_with_research_score.json")
    return p.parse_args()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = parse_args()

    req = load_request(Path(args.request_json))
    logger.info("Loaded request %s (package=%s)", req.request_id, req.package_id)

    # Step 1: Classical baseline
    classical = ClassicalOptimizerBaseline().run(req)
    cdir = Path(args.classical_artifact_dir)
    cdir.mkdir(parents=True, exist_ok=True)
    cpath = cdir / f"{req.request_id}.json"
    cpath.write_text(json.dumps(classical.to_dict(), indent=2), encoding="utf-8")
    logger.info("Classical: objective=%.4f, %d solutions", classical.objective_value, len(classical.ranked_solutions))

    # Step 2: Quantum bridge (with fallback)
    try:
        from src.research.quantum_optimizer_bridge import QuantumOptimizerBridge
        quantum = QuantumOptimizerBridge(Path(args.quantum_artifact_dir)).run(req)
        logger.info("Quantum: objective=%.4f, %d solutions", quantum.objective_value, len(quantum.ranked_solutions))
    except Exception as exc:
        logger.warning("Quantum bridge failed, using classical fallback: %s", exc)
        quantum = classical

    # Step 3: Evaluate against trade outcomes
    trade_outcomes = load_json(Path(args.trade_outcomes_json))
    evaluation = evaluate(
        classical_result=classical.to_dict(),
        quantum_result=quantum.to_dict(),
        trade_outcomes=trade_outcomes,
    )

    eval_out = Path(args.evaluation_out)
    eval_out.parent.mkdir(parents=True, exist_ok=True)
    eval_out.write_text(json.dumps(evaluation, indent=2), encoding="utf-8")
    logger.info("Evaluation: winner=%s", evaluation.get("winner"))

    # Step 4: Write research score
    research_score = build_research_score(evaluation)
    rs_out = Path(args.research_score_out)
    rs_out.parent.mkdir(parents=True, exist_ok=True)
    rs_out.write_text(json.dumps(research_score, indent=2), encoding="utf-8")
    logger.info("Research score: %.4f (%s)", research_score["research_score"], research_score["recommended_influence"])

    # Step 5: Optionally attach to snapshot
    if args.snapshot_json:
        snapshot = load_json(Path(args.snapshot_json))
        merged = attach_research_score(snapshot, research_score)
        snap_out = Path(args.snapshot_out)
        snap_out.parent.mkdir(parents=True, exist_ok=True)
        snap_out.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        logger.info("Snapshot with research score: %s", snap_out)

    print(json.dumps({"evaluation": str(eval_out), "research_score": str(rs_out)}, indent=2))


if __name__ == "__main__":
    main()
