#!/usr/bin/env python3
"""Experimental QNTK-UCB Lane for portfolio diversification research.

Global Sentinel V4 Pack 8 — RESEARCH ONLY.

Combines Quantum Neural Tangent Kernel (QNTK) inspired kernel similarity
with Upper Confidence Bound (UCB) exploration for candidate selection.

This module is NOT for direct execution in production pipelines.
It provides a research framework for exploring portfolio construction using:
1. QNTK-inspired RBF kernel similarity between asset feature vectors
2. UCB exploration-exploitation tradeoff for candidate selection
3. Diversification scoring based on kernel distance matrix
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False
    logger.warning("numpy not available; using pure-python fallback for QNTK-UCB lane")


class ExperimentalQNTKUCBLane:
    """QNTK-inspired kernel + UCB exploration for portfolio diversification.

    Research-only module. All outputs are tagged with
    ``not_for_direct_execution=True`` and ``research_only=True``.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        cfg = config or {}
        self.alpha: float = float(cfg.get("alpha", 1.0))
        self.kernel_bandwidth: float = float(cfg.get("kernel_bandwidth", 0.5))
        self.max_candidates: int = int(cfg.get("max_candidates", 20))

    # ------------------------------------------------------------------
    # Feature extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_features(candidate: Dict[str, Any]) -> List[float]:
        """Extract numeric feature vector from a candidate dict."""
        return [
            float(candidate.get("preopt_feature_score", 0.5)),
            float(candidate.get("volatility_penalty", 0.3)),
            float(candidate.get("event_score", 0.0)),
        ]

    # ------------------------------------------------------------------
    # Kernel matrix
    # ------------------------------------------------------------------

    def compute_kernel_matrix(self, candidates: List[Dict[str, Any]]) -> Any:
        """Compute RBF kernel similarity matrix between candidate feature vectors.

        Args:
            candidates: list of candidate dicts with score fields.

        Returns:
            n x n kernel matrix (numpy ndarray if available, else list-of-lists).
        """
        n = len(candidates)
        if n == 0:
            return np.zeros((0, 0)) if _HAS_NUMPY else []

        features = [self._extract_features(c) for c in candidates]
        gamma = 1.0 / (2.0 * self.kernel_bandwidth ** 2)

        if _HAS_NUMPY:
            feat_arr = np.array(features, dtype=float)
            # Pairwise squared distances via broadcasting
            diff = feat_arr[:, None, :] - feat_arr[None, :, :]
            sq_dist = np.sum(diff ** 2, axis=2)
            kernel = np.exp(-gamma * sq_dist)
            return kernel
        else:
            # Pure-python fallback
            kernel: List[List[float]] = []
            for i in range(n):
                row: List[float] = []
                for j in range(n):
                    sq_dist = sum(
                        (features[i][k] - features[j][k]) ** 2
                        for k in range(len(features[i]))
                    )
                    row.append(math.exp(-gamma * sq_dist))
                kernel.append(row)
            return kernel

    # ------------------------------------------------------------------
    # UCB selection
    # ------------------------------------------------------------------

    def ucb_select(
        self,
        candidates: List[Dict[str, Any]],
        kernel_matrix: Any,
        visit_counts: Dict[str, int],
    ) -> List[Dict[str, Any]]:
        """UCB selection balancing exploitation (score) with exploration (kernel diversity).

        Args:
            candidates: list of candidate dicts.
            kernel_matrix: precomputed kernel matrix from ``compute_kernel_matrix``.
            visit_counts: mapping of symbol -> past visit count.

        Returns:
            Ranked list of candidates (up to ``max_candidates``), each
            augmented with a ``ucb_score`` field.
        """
        n = len(candidates)
        if n == 0:
            return []

        total_visits = max(sum(visit_counts.values()), 1)

        scored: List[Dict[str, Any]] = []
        for i, cand in enumerate(candidates):
            symbol = cand.get("symbol", f"C{i}")
            base_score = float(cand.get("preopt_feature_score", cand.get("score", 0.5)))

            visits_i = visit_counts.get(symbol, 0)

            # Exploration bonus (UCB1-style)
            if visits_i == 0:
                exploration = self.alpha * math.sqrt(math.log(total_visits + 1))
            else:
                exploration = self.alpha * math.sqrt(math.log(total_visits + 1) / visits_i)

            # Diversity bonus: mean kernel distance to already-selected candidates
            if _HAS_NUMPY:
                kernel_row = kernel_matrix[i]
                mean_similarity = float(np.mean(kernel_row)) if n > 1 else 1.0
            else:
                kernel_row = kernel_matrix[i] if kernel_matrix else [1.0]
                mean_similarity = sum(kernel_row) / len(kernel_row) if kernel_row else 1.0

            diversity_bonus = 1.0 - mean_similarity

            ucb_score = base_score + exploration + diversity_bonus

            entry = dict(cand)
            entry["ucb_score"] = round(ucb_score, 6)
            entry["_ucb_components"] = {
                "base_score": round(base_score, 6),
                "exploration": round(exploration, 6),
                "diversity_bonus": round(diversity_bonus, 6),
            }
            scored.append(entry)

        scored.sort(key=lambda x: x["ucb_score"], reverse=True)
        return scored[: self.max_candidates]

    # ------------------------------------------------------------------
    # Diversification score
    # ------------------------------------------------------------------

    def diversification_score(
        self, selected: List[Dict[str, Any]], kernel_matrix: Any
    ) -> float:
        """Measure portfolio diversification as mean off-diagonal kernel distance.

        Returns a value in [0, 1] where 1 = maximally diversified.

        Args:
            selected: list of selected candidate dicts.
            kernel_matrix: full kernel matrix (original candidate ordering).

        Returns:
            Diversification score float.
        """
        n = len(selected)
        if n <= 1:
            return 0.0

        if _HAS_NUMPY:
            # Use the top-left n x n submatrix (assumes selected are first n)
            km = kernel_matrix[:n, :n] if hasattr(kernel_matrix, '__getitem__') and hasattr(kernel_matrix, 'shape') else kernel_matrix
            if hasattr(km, 'shape') and len(km.shape) == 2:
                mask = ~np.eye(n, dtype=bool)
                off_diag = km[mask]
                mean_similarity = float(np.mean(off_diag))
            else:
                mean_similarity = self._mean_off_diag_fallback(km, n)
        else:
            mean_similarity = self._mean_off_diag_fallback(kernel_matrix, n)

        # Diversification = 1 - mean similarity (clamped to [0, 1])
        div_score = max(0.0, min(1.0, 1.0 - mean_similarity))
        return round(div_score, 6)

    @staticmethod
    def _mean_off_diag_fallback(matrix: Any, n: int) -> float:
        """Pure-python mean of off-diagonal elements."""
        total = 0.0
        count = 0
        for i in range(n):
            for j in range(n):
                if i != j:
                    if hasattr(matrix, '__getitem__'):
                        total += float(matrix[i][j])
                    else:
                        total += 1.0
                    count += 1
        return total / max(count, 1)

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def run(
        self,
        candidates: List[Dict[str, Any]],
        visit_counts: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Any]:
        """Full QNTK-UCB pipeline: kernel -> UCB select -> diversification score.

        Args:
            candidates: list of candidate dicts with score fields.
            visit_counts: optional mapping of symbol -> past visit count.

        Returns:
            Research result dict with schema version, selected candidates,
            diversification score, and research-only flags.
        """
        visit_counts = visit_counts or {}

        if not candidates:
            return {
                "schema_version": "experimental_qntk_ucb.v1",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "selected_candidates": [],
                "diversification_score": 0.0,
                "candidate_count": 0,
                "not_for_direct_execution": True,
                "research_only": True,
            }

        # Step 1: Compute kernel matrix
        kernel_matrix = self.compute_kernel_matrix(candidates)

        # Step 2: UCB selection
        selected = self.ucb_select(candidates, kernel_matrix, visit_counts)

        # Step 3: Recompute kernel for selected subset and score diversification
        selected_kernel = self.compute_kernel_matrix(selected)
        div_score = self.diversification_score(selected, selected_kernel)

        return {
            "schema_version": "experimental_qntk_ucb.v1",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "selected_candidates": selected,
            "diversification_score": div_score,
            "candidate_count": len(candidates),
            "selected_count": len(selected),
            "config": {
                "alpha": self.alpha,
                "kernel_bandwidth": self.kernel_bandwidth,
                "max_candidates": self.max_candidates,
            },
            "not_for_direct_execution": True,
            "research_only": True,
        }
