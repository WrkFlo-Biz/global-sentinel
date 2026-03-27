#!/usr/bin/env python3
"""Side-by-side comparison of current vs candidate encoder outputs.

Runs paired evaluation on the same input data and produces a structured
promotion recommendation based on statistical metrics.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# Default thresholds for promotion decisions
_DEFAULT_THRESHOLDS = {
    "min_correlation": 0.85,
    "max_regression_delta": -0.02,  # reject if candidate worse by > 2%
    "min_improvement_delta": 0.005,  # promote requires > 0.5% improvement
    "min_sample_count": 30,
}


class CanaryEncoderComparator:
    """Compare current and candidate encoder outputs for canary promotion."""

    def __init__(self, thresholds: Dict[str, float] | None = None):
        self.thresholds = {**_DEFAULT_THRESHOLDS, **(thresholds or {})}

    def compare(
        self,
        current_outputs: List[Dict[str, Any]],
        candidate_outputs: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Compare paired encoder outputs and return a structured comparison report.

        Each output dict must contain at least: {"score": float}.
        Optional fields: "win" (bool), "return" (float).
        """
        if len(current_outputs) != len(candidate_outputs):
            raise ValueError(
                f"Mismatched output counts: {len(current_outputs)} vs {len(candidate_outputs)}"
            )

        n = len(current_outputs)
        if n == 0:
            raise ValueError("Cannot compare empty output lists")

        current_scores = [o["score"] for o in current_outputs]
        candidate_scores = [o["score"] for o in candidate_outputs]

        # --- Compute metrics ---
        deltas = [c - b for b, c in zip(current_scores, candidate_scores)]
        mean_delta = sum(deltas) / n
        max_delta = max(deltas, key=abs)

        correlation = self._pearson_correlation(current_scores, candidate_scores)

        # Win rate delta (if win field present)
        win_rate_delta = self._compute_win_rate_delta(current_outputs, candidate_outputs)

        # Sharpe delta (if return field present)
        sharpe_delta = self._compute_sharpe_delta(current_outputs, candidate_outputs)

        metrics = {
            "mean_score_delta": round(mean_delta, 6),
            "max_score_delta": round(max_delta, 6),
            "correlation": round(correlation, 6),
            "win_rate_delta": round(win_rate_delta, 6),
            "sharpe_delta": round(sharpe_delta, 6),
        }

        recommendation, reason = self._decide(metrics, n)

        report = {
            "schema_version": "canary_comparison.v1",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "sample_count": n,
            "metrics": metrics,
            "promotion_recommendation": recommendation,
            "reason": reason,
        }

        logger.info(
            "Canary comparison: n=%d recommendation=%s reason=%s",
            n, recommendation, reason,
        )
        return report

    def is_safe_to_promote(self, comparison: Dict[str, Any]) -> bool:
        """Return True only if the comparison recommends promotion."""
        return comparison.get("promotion_recommendation") == "promote"

    # ---- Private helpers ----

    def _decide(self, metrics: Dict[str, float], sample_count: int) -> tuple[str, str]:
        """Determine promotion recommendation from computed metrics."""
        t = self.thresholds

        # Insufficient data -> hold
        if sample_count < t["min_sample_count"]:
            return "hold", f"insufficient samples ({sample_count} < {t['min_sample_count']})"

        # Correlation too low -> reject (candidate behaves very differently)
        if metrics["correlation"] < t["min_correlation"]:
            return "reject", f"correlation too low ({metrics['correlation']:.4f} < {t['min_correlation']})"

        # Check for regression on any key metric
        regression_limit = t["max_regression_delta"]
        regressions = []
        if metrics["mean_score_delta"] < regression_limit:
            regressions.append(f"mean_score_delta={metrics['mean_score_delta']:.4f}")
        if metrics["win_rate_delta"] < regression_limit:
            regressions.append(f"win_rate_delta={metrics['win_rate_delta']:.4f}")
        if metrics["sharpe_delta"] < regression_limit:
            regressions.append(f"sharpe_delta={metrics['sharpe_delta']:.4f}")

        if regressions:
            return "reject", f"regression detected: {', '.join(regressions)}"

        # Promote if candidate is better on all key metrics
        min_improve = t["min_improvement_delta"]
        if (
            metrics["mean_score_delta"] >= min_improve
            and metrics["win_rate_delta"] >= 0
            and metrics["sharpe_delta"] >= 0
        ):
            return "promote", "candidate improves on all key metrics"

        return "hold", "mixed results; needs more data"

    @staticmethod
    def _pearson_correlation(xs: List[float], ys: List[float]) -> float:
        n = len(xs)
        if n < 2:
            return 1.0
        mx = sum(xs) / n
        my = sum(ys) / n
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        sy = math.sqrt(sum((y - my) ** 2 for y in ys))
        if sx == 0 or sy == 0:
            # Both constant or one constant: no divergence signal, treat as safe
            return 1.0
        return cov / (sx * sy)

    @staticmethod
    def _compute_win_rate_delta(
        current: List[Dict[str, Any]], candidate: List[Dict[str, Any]]
    ) -> float:
        cur_wins = [o.get("win") for o in current if "win" in o]
        cand_wins = [o.get("win") for o in candidate if "win" in o]
        if not cur_wins or not cand_wins:
            return 0.0
        cur_rate = sum(1 for w in cur_wins if w) / len(cur_wins)
        cand_rate = sum(1 for w in cand_wins if w) / len(cand_wins)
        return cand_rate - cur_rate

    @staticmethod
    def _compute_sharpe_delta(
        current: List[Dict[str, Any]], candidate: List[Dict[str, Any]]
    ) -> float:
        cur_returns = [o["return"] for o in current if "return" in o]
        cand_returns = [o["return"] for o in candidate if "return" in o]
        if len(cur_returns) < 2 or len(cand_returns) < 2:
            return 0.0

        def _sharpe(rets: List[float]) -> float:
            n = len(rets)
            mu = sum(rets) / n
            std = math.sqrt(sum((r - mu) ** 2 for r in rets) / (n - 1))
            return mu / std if std > 0 else 0.0

        return _sharpe(cand_returns) - _sharpe(cur_returns)
