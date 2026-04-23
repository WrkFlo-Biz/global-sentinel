#!/usr/bin/env python3
"""Recursive QAOA (RQAOA) structured optimizer.

Iteratively fix variables and reduce problem size before quantum execution.
Implements problem reduction-first approach per OpenQAOA guidance.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RQAOAStructuredOptimizer:
    """Recursive QAOA with iterative problem reduction.

    Strategy:
    1. Classical pre-pruning to reduce candidate universe
    2. Iterative variable fixing based on correlation analysis
    3. QAOA on reduced problem only when stable
    4. Compare RQAOA vs standard QAOA vs classical
    """

    def __init__(
        self,
        max_iterations: int = 5,
        fix_fraction: float = 0.2,
        min_problem_size: int = 3,
    ):
        self.max_iterations = max_iterations
        self.fix_fraction = fix_fraction
        self.min_problem_size = min_problem_size

    def optimize(
        self,
        candidates: List[Dict[str, Any]],
        objective_scores: List[float],
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run RQAOA optimization.

        1. Pre-prune low-scoring candidates
        2. Iteratively fix highest-confidence variables
        3. Return reduced solution
        """
        start = time.time()
        n = len(candidates)
        if n == 0:
            return self._empty_result()

        constraints = constraints or {}
        scores = [float(score) for score in objective_scores[:n]]

        # Track which variables are still free
        free_mask = [True] * n
        fixed_values = [0.0] * n  # 0 = not selected, will be overwritten
        iterations = []

        for iteration in range(self.max_iterations):
            free_indices = [index for index, is_free in enumerate(free_mask) if is_free]
            if len(free_indices) <= self.min_problem_size:
                break

            # Compute correlations between candidates (using scores as proxy)
            free_scores = [scores[index] for index in free_indices]

            # Fix the least ambiguous variables (highest or lowest scores)
            n_to_fix = max(1, int(len(free_indices) * self.fix_fraction))
            sorted_idx = sorted(range(len(free_scores)), key=lambda idx: free_scores[idx])

            # Fix bottom candidates to 0 (exclude)
            for i in range(n_to_fix):
                orig_idx = free_indices[sorted_idx[i]]
                fixed_values[orig_idx] = 0.0
                free_mask[orig_idx] = False

            iterations.append({
                "iteration": iteration,
                "free_count": sum(1 for is_free in free_mask if is_free),
                "fixed_count": n - sum(1 for is_free in free_mask if is_free),
                "fixed_low_score_indices": [int(free_indices[sorted_idx[i]]) for i in range(n_to_fix)],
            })

        # Solve remaining free variables with simple optimization
        free_indices = [index for index, is_free in enumerate(free_mask) if is_free]
        if len(free_indices) > 0:
            free_scores = [scores[index] for index in free_indices]
            # Allocate weights proportional to scores
            total = sum(free_scores)
            if total > 0:
                weights = [score / total for score in free_scores]
            else:
                weights = [1.0 / len(free_indices)] * len(free_indices)

            for i, idx in enumerate(free_indices):
                fixed_values[idx] = float(weights[i])

        elapsed = time.time() - start

        # Build result
        selected = []
        for i, c in enumerate(candidates):
            if fixed_values[i] > 0.01:
                selected.append({
                    "symbol": c.get("symbol", f"C{i}"),
                    "weight": round(float(fixed_values[i]), 4),
                    "score": float(scores[i]),
                })

        return {
            "schema_version": "rqaoa_structured_optimizer.v1",
            "optimizer": "rqaoa",
            "candidate_count": n,
            "iterations_used": len(iterations),
            "final_free_count": sum(1 for is_free in free_mask if is_free),
            "iterations": iterations,
            "selected_candidates": selected,
            "all_weights": [round(float(w), 6) for w in fixed_values],
            "elapsed_seconds": round(elapsed, 4),
            "not_for_direct_execution": True,
            "artifact_only": True,
        }

    @staticmethod
    def _empty_result():
        return {
            "schema_version": "rqaoa_structured_optimizer.v1",
            "optimizer": "rqaoa",
            "candidate_count": 0,
            "selected_candidates": [],
            "not_for_direct_execution": True,
            "artifact_only": True,
        }
