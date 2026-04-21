#!/usr/bin/env python3
"""Factory for building QuantumOptimizationRequest packets from feature store snapshots.

Extracts regime state, time window state, and market microstructure from
a canonical research snapshot and produces a request dict ready for the
quantum optimizer bridge.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
_DEFAULT_OBJECTIVE = "hedge_basket_optimization"
_DEFAULT_SHOTS = 1000
_DEFAULT_MAX_CANDIDATES = 5


def _load_quantum_policy(repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Load quantum_lane_policy.yaml for constraint defaults."""
    if repo_root is None:
        return {}
    policy_path = repo_root / "config" / "quantum_lane_policy.yaml"
    if not policy_path.exists():
        return {}
    try:
        import yaml  # type: ignore[import-untyped]
        return yaml.safe_load(policy_path.read_text(encoding="utf-8")) or {}
    except ImportError:
        return {}
    except Exception as exc:
        logger.warning("Could not load quantum_lane_policy.yaml: %s", exc)
        return {}


def _extract_regime_state(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Derive regime_state from macro aggregates in the snapshot."""
    aggregates = snapshot.get("aggregates", {})

    hd = aggregates.get("hawkish_dovish", {})
    gi = aggregates.get("growth_inflation", {})

    hd_mean = hd.get("mean", 0.0)
    gi_mean = gi.get("mean", 0.0)

    # Classify regime buckets
    if hd_mean > 0.3:
        monetary_stance = "hawkish"
    elif hd_mean < -0.3:
        monetary_stance = "dovish"
    else:
        monetary_stance = "neutral"

    if gi_mean > 0.3:
        growth_regime = "inflationary"
    elif gi_mean < -0.3:
        growth_regime = "deflationary"
    else:
        growth_regime = "stable"

    return {
        "monetary_stance": monetary_stance,
        "hawkish_dovish_score": hd_mean,
        "hawkish_dovish_sample_count": hd.get("count", 0),
        "growth_regime": growth_regime,
        "growth_inflation_score": gi_mean,
        "growth_inflation_sample_count": gi.get("count", 0),
    }


def _extract_time_window_state(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Build time_window_state from source freshness timestamps."""
    freshness = snapshot.get("source_freshness", {})
    now_iso = datetime.now(timezone.utc).isoformat()

    # Find most recent and oldest data points
    timestamps = list(freshness.values())
    latest = max(timestamps) if timestamps else None
    oldest = min(timestamps) if timestamps else None

    return {
        "snapshot_timestamp": snapshot.get("timestamp_utc", now_iso),
        "latest_data": latest,
        "oldest_data": oldest,
        "source_count": len(freshness),
        "per_source": dict(freshness),
    }


def _extract_microstructure(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Extract market microstructure and physical flow signals."""
    grouped = snapshot.get("grouped_packets", {})

    # Physical flow disruption scores
    flow_packets = grouped.get("physical_flow_event", [])
    disruption_scores = [p.get("disruption_score", 0) for p in flow_packets if "disruption_score" in p]

    # Market microstructure summaries
    micro_packets = grouped.get("market_microstructure_summary", [])
    micro_data = micro_packets[0].get("data", {}) if micro_packets else {}

    return {
        "physical_flow": {
            "event_count": len(flow_packets),
            "mean_disruption": sum(disruption_scores) / len(disruption_scores) if disruption_scores else 0.0,
            "max_disruption": max(disruption_scores) if disruption_scores else 0.0,
        },
        "market_microstructure": micro_data,
    }


def _build_constraints(
    user_constraints: Optional[Dict[str, Any]],
    policy: Dict[str, Any],
) -> Dict[str, Any]:
    """Merge user constraints with policy defaults."""
    origin = policy.get("origin_config", {})
    defaults: Dict[str, Any] = {
        "max_candidates": origin.get("max_candidates", _DEFAULT_MAX_CANDIDATES),
        "shots": origin.get("shots", _DEFAULT_SHOTS),
        "fallback": origin.get("fallback", "classical"),
        "artifact_only": policy.get("lane_rules", {}).get("artifact_only", True),
        "disable_execution_path": policy.get("lane_rules", {}).get("disable_execution_path", True),
        "shadow_mode_only": policy.get("lane_rules", {}).get("shadow_mode_only", True),
    }
    if user_constraints:
        defaults.update(user_constraints)
    return defaults


def _generate_request_id(snapshot: Dict[str, Any]) -> str:
    """Deterministic request id from snapshot timestamp."""
    ts = snapshot.get("timestamp_utc", datetime.now(timezone.utc).isoformat())
    digest = hashlib.sha256(ts.encode()).hexdigest()[:12]
    return f"qreq_{digest}"


def build_request(
    snapshot: Dict[str, Any],
    candidate_universe: List[Dict[str, Any]],
    objective_type: str = _DEFAULT_OBJECTIVE,
    constraints: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a quantum optimization request from a feature store snapshot.

    Parameters
    ----------
    snapshot:
        Canonical research snapshot from FeatureStoreBuilder.build_snapshot().
    candidate_universe:
        List of candidate dicts, each with at least ``symbol`` and ``score``.
    objective_type:
        One of the allowed objectives from quantum_lane_policy.yaml.
    constraints:
        Optional user-supplied constraints (merged with policy defaults).
    repo_root:
        Path to repo root for loading quantum_lane_policy.yaml. If None,
        policy defaults are used.

    Returns
    -------
    Dict suitable for passing to the quantum optimizer bridge.
    """
    policy = _load_quantum_policy(repo_root)

    # Validate objective type against policy
    allowed = policy.get("allowed_objectives", [
        "hedge_basket_optimization",
        "constrained_subset_selection",
        "scenario_allocation",
        "robust_portfolio_design",
    ])
    if objective_type not in allowed:
        logger.warning("Objective '%s' not in allowed list %s; using default", objective_type, allowed)
        objective_type = _DEFAULT_OBJECTIVE

    request_id = _generate_request_id(snapshot)
    regime_state = _extract_regime_state(snapshot)
    time_window = _extract_time_window_state(snapshot)
    microstructure = _extract_microstructure(snapshot)
    merged_constraints = _build_constraints(constraints, policy)

    request: Dict[str, Any] = {
        "request_id": request_id,
        "package_id": f"gs_feature_store_{request_id}",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "runtime_flags": {
            "shadow_mode_only": True,
            "artifact_only": True,
            "disable_execution_path": True,
        },
        "objective": {
            "type": objective_type,
            "regime_state": regime_state,
            "time_window_state": time_window,
            "microstructure": microstructure,
        },
        "constraints": merged_constraints,
        "candidate_universe": candidate_universe,
        "provenance": {
            "builder": "quantum_optimization_request_builder",
            "snapshot_timestamp": snapshot.get("timestamp_utc"),
            "packet_count": snapshot.get("packet_count", 0),
            "bridge_errors": snapshot.get("bridge_errors", {}),
            "source_freshness": snapshot.get("source_freshness", {}),
        },
    }
    return request
