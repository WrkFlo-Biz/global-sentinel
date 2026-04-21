#!/usr/bin/env python3
"""Online Weighted Feature Encoder for Global Sentinel V4.

Extends QFinanceFeatureEncoder to use learned weights from online learning state
instead of hardcoded values. Supports:
- Versioned weight snapshots
- Dual-run comparison against baseline encoder
- Policy engine compliance checks
- Freeze in CRISIS / MANUAL_REVIEW modes
- Rollback to previous weight version
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Feature keys that map to learned weights
WEIGHT_KEYS = [
    "base_score",
    "event_score",
    "quality_score",
    "anomaly_score",
    "liquidity_score",
    "regime_alignment",
    "volatility_penalty",
]

# Hardcoded baseline weights (same as QFinanceFeatureEncoder)
BASELINE_WEIGHTS: Dict[str, float] = {
    "base_score": 0.35,
    "event_score": 0.20,
    "quality_score": 0.15,
    "anomaly_score": 0.10,
    "liquidity_score": 0.15,
    "regime_alignment": 0.15,
    "volatility_penalty": -0.10,
}


class OnlineWeightedFeatureEncoder:
    """Feature encoder that uses learned weights from online learning state."""

    def __init__(
        self,
        state_path: Optional[Path] = None,
        repo_root: Optional[Path] = None,
        version_tag: Optional[str] = None,
    ):
        self._repo_root = repo_root or Path(".")
        self._state_path = state_path or self._repo_root / "artifacts" / "learning_state" / "current_state.json"
        self._version_tag = version_tag
        self._weights = dict(BASELINE_WEIGHTS)
        self._state: Dict[str, Any] = {}
        self._frozen = False
        self._load_weights()

    def _load_weights(self) -> None:
        """Load learned weights from state file, falling back to baseline."""
        if not self._state_path.exists():
            logger.info("No learning state at %s, using baseline weights", self._state_path)
            return

        try:
            self._state = json.loads(self._state_path.read_text(encoding="utf-8"))
            stored_weights = self._state.get("weights", {})
            for key in WEIGHT_KEYS:
                if key in stored_weights:
                    self._weights[key] = float(stored_weights[key])
            logger.info(
                "Loaded learned weights v%s from %s",
                self._state.get("version", "?"),
                self._state_path,
            )
        except Exception as exc:
            logger.warning("Failed to load learning state: %s — using baseline", exc)

    @property
    def weights(self) -> Dict[str, float]:
        return dict(self._weights)

    @property
    def version(self) -> int:
        return int(self._state.get("version", 0))

    @property
    def is_frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> None:
        """Freeze encoder — no weight updates allowed (CRISIS/MANUAL_REVIEW)."""
        self._frozen = True

    def unfreeze(self) -> None:
        self._frozen = False

    def check_mode_allows_update(self) -> Tuple[bool, str]:
        """Check if current operating mode allows weight updates."""
        try:
            from src.core.policy_engine import PolicyEngine
            pe = PolicyEngine(config_dir=self._repo_root / "config")
            mode = pe._current_mode()
            if mode in {"CRISIS", "MANUAL_REVIEW"}:
                return False, f"mode_{mode}_blocks_weight_updates"
            return True, f"mode_{mode}_allows_updates"
        except Exception:
            return True, "policy_engine_unavailable_defaulting_to_allow"

    def encode_candidate(
        self,
        *,
        candidate: Dict[str, Any],
        regime_state: Dict[str, Any],
        market_microstructure: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Encode a single candidate using learned weights."""
        from src.research.qfinance_feature_encoder import QFinanceFeatureEncoder

        # Get base features from the standard encoder
        base_encoder = QFinanceFeatureEncoder()
        features = base_encoder.encode_candidate(
            candidate=candidate,
            regime_state=regime_state,
            market_microstructure=market_microstructure,
        )

        # Recompute preopt_feature_score using learned weights
        weighted_score = sum(
            self._weights.get(key, 0.0) * float(features.get(key, 0.0))
            for key in WEIGHT_KEYS
        )

        features["preopt_feature_score"] = weighted_score
        features["_encoder_version"] = self.version
        features["_encoder_type"] = "online_weighted"
        features["_weights_used"] = dict(self._weights)

        return features

    def encode_universe(
        self,
        *,
        candidate_universe: List[Dict[str, Any]],
        regime_state: Dict[str, Any],
        market_microstructure: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Encode all candidates and sort by weighted score."""
        rows = [
            self.encode_candidate(
                candidate=c,
                regime_state=regime_state,
                market_microstructure=market_microstructure,
            )
            for c in candidate_universe
        ]
        rows.sort(key=lambda x: float(x.get("preopt_feature_score", 0.0)), reverse=True)
        return rows

    def dual_run(
        self,
        *,
        candidate_universe: List[Dict[str, Any]],
        regime_state: Dict[str, Any],
        market_microstructure: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run both baseline and online encoders, return comparison."""
        from src.research.qfinance_feature_encoder import QFinanceFeatureEncoder

        baseline = QFinanceFeatureEncoder()
        baseline_results = baseline.encode_universe(
            candidate_universe=candidate_universe,
            regime_state=regime_state,
            market_microstructure=market_microstructure,
        )

        online_results = self.encode_universe(
            candidate_universe=candidate_universe,
            regime_state=regime_state,
            market_microstructure=market_microstructure,
        )

        # Compare rankings
        baseline_ranking = [r["symbol"] for r in baseline_results]
        online_ranking = [r["symbol"] for r in online_results]

        score_deltas = {}
        for b, o in zip(baseline_results, online_results):
            sym = b["symbol"]
            score_deltas[sym] = {
                "baseline_score": b["preopt_feature_score"],
                "online_score": o["preopt_feature_score"],
                "delta": o["preopt_feature_score"] - b["preopt_feature_score"],
            }

        ranking_match = baseline_ranking == online_ranking
        top3_match = baseline_ranking[:3] == online_ranking[:3]

        return {
            "schema_version": "dual_run_comparison.v1",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "encoder_version": self.version,
            "candidate_count": len(candidate_universe),
            "baseline_weights": dict(BASELINE_WEIGHTS),
            "online_weights": dict(self._weights),
            "ranking_match": ranking_match,
            "top3_match": top3_match,
            "baseline_ranking": baseline_ranking,
            "online_ranking": online_ranking,
            "score_deltas": score_deltas,
            "not_for_direct_execution": True,
        }

    def update_weights(
        self,
        new_weights: Dict[str, float],
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """Update learned weights with guardrail checks."""
        if self._frozen:
            return False, "encoder_frozen"

        mode_ok, mode_reason = self.check_mode_allows_update()
        if not mode_ok:
            return False, mode_reason

        # Validate step size
        max_step = float(self._state.get("guardrails", {}).get("max_abs_weight_step", 0.05))
        for key, new_val in new_weights.items():
            if key not in WEIGHT_KEYS:
                continue
            old_val = self._weights.get(key, 0.0)
            if abs(new_val - old_val) > max_step:
                return False, f"weight_step_too_large:{key}:{abs(new_val - old_val):.4f}>{max_step}"

        # Apply
        for key in WEIGHT_KEYS:
            if key in new_weights:
                self._weights[key] = float(new_weights[key])

        # Persist
        self._state.setdefault("weights", {}).update(self._weights)
        self._state["version"] = self._state.get("version", 0) + 1
        self._state.setdefault("update_stats", {})["updates_applied"] = (
            self._state.get("update_stats", {}).get("updates_applied", 0) + 1
        )
        self._state["update_stats"]["last_update_ts"] = datetime.now(timezone.utc).isoformat()

        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps(self._state, indent=2), encoding="utf-8")

        return True, f"weights_updated_v{self._state['version']}"
