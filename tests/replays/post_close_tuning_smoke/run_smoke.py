#!/usr/bin/env python3
"""
Post-close tuning smoke test (E2E)
Validates:
- recommendation generation from analytics
- queue enqueue
- threshold drift guard intraday block
- threshold drift guard post-close review path
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
import sys

import yaml

# Repo-root imports
# Run from repo root:
#   python tests/replays/post_close_tuning_smoke/run_smoke.py --repo-root .
def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))

def load_yaml(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8"))

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    smoke_dir = repo_root / "tests" / "replays" / "post_close_tuning_smoke"
    out_dir = smoke_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Import project modules
    sys.path.insert(0, str(repo_root))

    from src.self_improvement.recommendation_queue import RecommendationQueue, generate_recommendations_from_analytics
    from src.monitoring.threshold_drift_guard import ThresholdDriftGuard

    tca = load_json(smoke_dir / "tca_shadow_report.json")
    nt = load_json(smoke_dir / "no_trade_quality.json")
    recon = load_json(smoke_dir / "paper_trade_reconciliation.json")

    # 1) Generate recommendations
    generated = generate_recommendations_from_analytics(tca, nt, recon)
    assert len(generated) >= 1, "Expected at least one recommendation from analytics"

    # 2) Enqueue recommendations into a temp queue path under smoke dir
    queue = RecommendationQueue(repo_root, relpath="tests/replays/post_close_tuning_smoke/out/recommendation_queue.jsonl")
    enqueued = [queue.enqueue(r) for r in generated]
    assert len(enqueued) == len(generated)

    # 3) Threshold drift guard - intraday should block
    current_cfg = load_yaml(smoke_dir / "thresholds_current.yaml")
    proposed_cfg = load_yaml(smoke_dir / "thresholds_proposed.yaml")
    intraday_meta = load_json(smoke_dir / "metadata_intraday_block.json")
    postclose_meta = load_json(smoke_dir / "metadata_postclose_ready.json")

    guard = ThresholdDriftGuard()

    intraday_assessment = guard.assess(current_cfg, proposed_cfg, intraday_meta)
    assert "intraday_threshold_mutation_not_allowed" in intraday_assessment["summary"]["policy_violations"], \
        "Intraday drift mutation should be blocked"

    postclose_assessment = guard.assess(current_cfg, proposed_cfg, postclose_meta)
    # We don't force fully approved if changes are severe + missing more reviews,
    # but with provided approvals/evidence it should not include intraday block.
    assert "intraday_threshold_mutation_not_allowed" not in postclose_assessment["summary"]["policy_violations"]

    # 4) Persist smoke outputs
    (out_dir / "generated_recommendations.json").write_text(json.dumps({"recommendations": enqueued}, indent=2), encoding="utf-8")
    (out_dir / "intraday_assessment.json").write_text(json.dumps(intraday_assessment, indent=2), encoding="utf-8")
    (out_dir / "postclose_assessment.json").write_text(json.dumps(postclose_assessment, indent=2), encoding="utf-8")

    summary = {
        "generated_recommendation_count": len(enqueued),
        "intraday_policy_violations": intraday_assessment["summary"]["policy_violations"],
        "postclose_policy_violations": postclose_assessment["summary"]["policy_violations"],
        "postclose_approved_for_apply_post_close": postclose_assessment["summary"]["approved_for_apply_post_close"],
    }
    (out_dir / "smoke_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(json.dumps({
        "status": "ok",
        "summary": summary,
        "out_dir": str(out_dir)
    }, indent=2))


if __name__ == "__main__":
    main()
