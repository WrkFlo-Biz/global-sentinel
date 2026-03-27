#!/usr/bin/env python3
"""
Global Sentinel — RL Position Sizer Training Script

Designed to be invoked by the quantum continuous learner on weekends,
or manually for ad-hoc retraining.

Usage:
    python3 -m src.research.train_rl_position_sizer              # default 50k steps
    python3 -m src.research.train_rl_position_sizer --timesteps 100000
    python3 -m src.research.train_rl_position_sizer --recommend   # run inference only
    python3 -m src.research.train_rl_position_sizer --full        # train + recommend

Can also be imported:
    from src.research.train_rl_position_sizer import run_weekly_rl_training
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure repo root is on path
REPO_ROOT = Path("/opt/global-sentinel")
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.research.rl_position_sizer import (
    RLPositionSizer,
    train_rl_sizer,
    load_paper_trade_episodes,
    RECOMMENDATIONS_PATH,
    MODEL_PATH,
    TRAINING_LOG_PATH,
)

logger = logging.getLogger("global_sentinel.train_rl_sizer")


def run_weekly_rl_training(timesteps: int = 50000) -> dict:
    """
    Weekly retraining entry point for the quantum continuous learner.

    Returns a report dict with training metrics and status.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    logger.info("=== RL Position Sizer Weekly Retraining ===")
    logger.info(f"Timesteps: {timesteps}")

    # Train
    report = train_rl_sizer(total_timesteps=timesteps, save=True)
    logger.info(f"Training completed: status={report.get('status')}")
    logger.info(f"Mean reward (last 20 eps): {report.get('mean_reward', 'N/A')}")
    logger.info(f"Reward trend: {report.get('reward_trend', 'N/A')}")

    # Run inference on current strategy recommendations
    strat_path = REPO_ROOT / "data" / "quantum_feed" / "strategy_recommendations.json"
    if strat_path.exists():
        try:
            with open(strat_path) as f:
                recs = json.load(f)
            candidates = recs.get("top_5_recommendations", [])
            if candidates:
                sizer = RLPositionSizer()
                output = sizer.recommend_and_save(candidates)
                report["inference_run"] = True
                report["recommendations_count"] = len(output.get("recommendations", []))
                logger.info(f"Generated {report['recommendations_count']} sizing recommendations")
        except Exception as e:
            logger.warning(f"Could not run inference: {e}")
            report["inference_run"] = False
    else:
        report["inference_run"] = False
        logger.info("No strategy_recommendations.json found, skipping inference")

    # Save summary report
    report_path = REPO_ROOT / "reports" / "research" / "rl_training"
    report_path.mkdir(parents=True, exist_ok=True)
    report_file = report_path / f"rl_training_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_file, "w") as f:
        json.dump(report, f, indent=2)
    logger.info(f"Report saved to {report_file}")

    return report


def run_inference_only() -> dict:
    """Run inference on current candidates without retraining."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    if not MODEL_PATH.exists():
        logger.error("No trained model found. Run training first.")
        return {"error": "no_model", "status": "failed"}

    strat_path = REPO_ROOT / "data" / "quantum_feed" / "strategy_recommendations.json"
    if not strat_path.exists():
        logger.error("No strategy_recommendations.json found")
        return {"error": "no_candidates", "status": "failed"}

    with open(strat_path) as f:
        recs = json.load(f)
    candidates = recs.get("top_5_recommendations", [])

    sizer = RLPositionSizer()
    output = sizer.recommend_and_save(candidates)
    logger.info(f"Generated {len(output.get('recommendations', []))} recommendations")
    return output


def main():
    parser = argparse.ArgumentParser(description="RL Position Sizer Training")
    parser.add_argument("--timesteps", type=int, default=50000, help="Training timesteps")
    parser.add_argument("--recommend", action="store_true", help="Run inference only")
    parser.add_argument("--full", action="store_true", help="Train + recommend")
    args = parser.parse_args()

    if args.recommend:
        result = run_inference_only()
    elif args.full:
        result = run_weekly_rl_training(timesteps=args.timesteps)
    else:
        result = run_weekly_rl_training(timesteps=args.timesteps)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
