#!/usr/bin/env python3
"""QAOA hyperparameter warm-start library.

Stores optimal QAOA parameters indexed by regime/size/objective.
Provides warm-start initialization for new runs.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class QAOAHyperparameterLibrary:
    """Database of warm-start QAOA parameters."""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or Path("reports/research/qaoa_params.json")
        self._entries: List[Dict[str, Any]] = []
        self._load()

    def _load(self):
        if self.storage_path.exists():
            try:
                data = json.loads(self.storage_path.read_text(encoding="utf-8"))
                self._entries = data.get("entries", [])
            except Exception:
                self._entries = []

    def _save(self):
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": "qaoa_hyperparameter_library.v1",
            "entry_count": len(self._entries),
            "entries": self._entries,
        }
        self.storage_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def store_result(
        self,
        regime_state: str,
        portfolio_size: int,
        objective_type: str,
        optimal_params: Dict[str, Any],
        objective_value: float,
        converged: bool = True,
    ):
        entry = {
            "regime_state": regime_state,
            "portfolio_size": portfolio_size,
            "objective_type": objective_type,
            "optimal_params": optimal_params,
            "objective_value": objective_value,
            "converged": converged,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._entries.append(entry)
        self._save()

    def get_warm_start(
        self,
        regime_state: str,
        portfolio_size: int,
        objective_type: str,
    ) -> Optional[Dict[str, Any]]:
        """Find closest matching warm-start parameters."""
        best = None
        best_score = -1

        for entry in self._entries:
            if not entry.get("converged", True):
                continue

            score = 0
            if entry["regime_state"] == regime_state:
                score += 3
            if entry["objective_type"] == objective_type:
                score += 3
            size_diff = abs(entry["portfolio_size"] - portfolio_size)
            if size_diff == 0:
                score += 2
            elif size_diff <= 2:
                score += 1

            if score > best_score:
                best_score = score
                best = entry

        if best and best_score >= 3:
            return {
                "warm_start_params": best["optimal_params"],
                "source_regime": best["regime_state"],
                "source_size": best["portfolio_size"],
                "source_objective": best["objective_type"],
                "match_score": best_score,
            }
        return None

    @property
    def entry_count(self) -> int:
        return len(self._entries)
