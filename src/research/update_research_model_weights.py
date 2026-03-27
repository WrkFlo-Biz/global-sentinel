"""Bounded weight updates from labeled training rows.

Simple online update rule: positively labeled rows with high feature
values nudge that feature weight up; negatively labeled rows nudge down.
All updates are bounded by max_abs_weight_step guardrail.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from src.research.qfinance_online_learning_state import load_state, save_state


FEATURE_KEYS = [
    "base_score",
    "event_score",
    "quality_score",
    "anomaly_score",
    "liquidity_score",
    "regime_alignment",
    "volatility_penalty",
]


LABEL_TO_TARGET = {
    "strong_positive": 1.0,
    "positive": 0.5,
    "neutral": 0.0,
    "negative": -0.5,
    "strong_negative": -1.0,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


class ResearchModelWeightUpdater:

    def update(
        self,
        *,
        state: Dict[str, Any],
        labeled_dataset: Dict[str, Any],
        learning_rate: float = 0.01,
    ) -> Dict[str, Any]:
        weights = dict((state.get("weights") or {}))
        guardrails = state.get("guardrails") or {}
        max_step = float(guardrails.get("max_abs_weight_step", 0.05))
        min_w = float(guardrails.get("min_weight", -1.0))
        max_w = float(guardrails.get("max_weight", 1.0))

        rows = labeled_dataset.get("rows") or []
        n = 0

        gradients = {k: 0.0 for k in FEATURE_KEYS}
        for row in rows:
            label = str(row.get("alpha_label", "neutral"))
            target = LABEL_TO_TARGET.get(label, 0.0)

            for k in FEATURE_KEYS:
                gradients[k] += target * safe_float(row.get(k), 0.0)
            n += 1

        if n > 0:
            for k in FEATURE_KEYS:
                gradients[k] /= n

        for k in FEATURE_KEYS:
            old = safe_float(weights.get(k), 0.0)
            step = max(-max_step, min(max_step, learning_rate * gradients[k]))
            new = old + step
            weights[k] = max(min_w, min(max_w, new))

        new_state = dict(state)
        new_state["weights"] = weights
        new_state.setdefault("update_stats", {})
        new_state["update_stats"]["updates_applied"] = int(new_state["update_stats"].get("updates_applied", 0)) + 1
        new_state["update_stats"]["last_update_ts"] = utc_now_iso()
        new_state["update_stats"]["last_training_rows"] = n
        return new_state


def parse_args():
    p = argparse.ArgumentParser(description="Update research model weights")
    p.add_argument("--state-json", required=True)
    p.add_argument("--labeled-dataset-json", required=True)
    p.add_argument("--output-json", required=True)
    p.add_argument("--learning-rate", type=float, default=0.01)
    return p.parse_args()


def main():
    args = parse_args()
    state = load_state(Path(args.state_json))
    labeled_dataset = json.loads(Path(args.labeled_dataset_json).read_text(encoding="utf-8"))

    updater = ResearchModelWeightUpdater()
    new_state = updater.update(
        state=state,
        labeled_dataset=labeled_dataset,
        learning_rate=args.learning_rate,
    )

    out = Path(args.output_json)
    save_state(out, new_state)
    print(out)


if __name__ == "__main__":
    main()
