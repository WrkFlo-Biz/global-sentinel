#!/usr/bin/env python3
"""What-If Quantum Bridge — feeds learner performance data into quantum optimizer.

Pipeline:
1. Load what-if learner snapshots (5-min interval performance data)
2. Build QuantumOptimizationRequest (scenario_allocation objective)
3. Run MultiBackendOrchestrator (QPanda3 + Qiskit + PennyLane + Classical)
4. Score with research_score_writer
5. Write quantum-weighted quality scores back to learner data dir
6. Learner reads these scores to improve recommendation confidence

All outputs are artifact-only per quantum_lane_policy.yaml.
NOT_FOR_DIRECT_EXECUTION — recommendations still require human approval.
"""
from __future__ import annotations

import json
import hashlib
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.packets.quantum_optimization_request import QuantumOptimizationRequest
from src.research.research_score_writer import build_research_score

DATA_DIR = REPO_ROOT / "data" / "whatif_learning"
QUANTUM_OUT = REPO_ROOT / "reports" / "research" / "whatif_quantum"
QUANTUM_OUT.mkdir(parents=True, exist_ok=True)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _req_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    h = hashlib.sha256(ts.encode()).hexdigest()[:8]
    return f"whatif-{ts}-{h}"


def load_learner_snapshots(today_str: Optional[str] = None) -> List[Dict]:
    """Load today's learner snapshots from JSONL."""
    if today_str is None:
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = DATA_DIR / f"snapshots_{today_str}.jsonl"
    if not log_file.exists():
        return []
    snapshots = []
    with open(log_file) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    snapshots.append(json.loads(line))
                except Exception:
                    continue
    return snapshots


def build_candidate_universe(snapshots: List[Dict]) -> List[Dict]:
    """Convert learner snapshots into quantum candidate universe.

    Each candidate represents a symbol with performance metrics as features.
    The quantum optimizer finds the optimal allocation across candidates.
    """
    if not snapshots:
        return []

    # Aggregate: for each symbol, compute avg/trend/consistency
    sym_data: Dict[str, List] = {}
    for snap in snapshots:
        for p in snap.get("picks", []):
            sym = p.get("sym", "")
            if not sym:
                continue
            if sym not in sym_data:
                sym_data[sym] = []
            sym_data[sym].append({
                "ts": snap["ts"],
                "change_pct": p.get("change_pct", 0),
                "hyp_pnl_pct": p.get("hyp_pnl_pct", 0),
                "confidence": p.get("confidence", 0),
                "bucket": p.get("bucket", ""),
                "direction": p.get("direction", "LONG"),
            })

    candidates = []
    for sym, series in sym_data.items():
        pnls = [s["hyp_pnl_pct"] for s in series]
        changes = [s["change_pct"] for s in series]
        conf = series[-1]["confidence"]
        bucket = series[-1]["bucket"]
        direction = series[-1]["direction"]

        avg_pnl = sum(pnls) / len(pnls) if pnls else 0
        latest_pnl = pnls[-1] if pnls else 0
        avg_change = sum(changes) / len(changes) if changes else 0

        # Trend: late vs early
        n = max(1, len(pnls) // 3)
        trend = (sum(pnls[-n:]) / n - sum(pnls[:n]) / n) if len(pnls) >= 3 else 0

        # Volatility (std dev of daily change)
        mean_c = sum(changes) / len(changes) if changes else 0
        vol = (sum((x - mean_c) ** 2 for x in changes) / max(len(changes), 1)) ** 0.5

        # Sharpe-like: avg_pnl / vol (capped)
        sharpe = (avg_pnl / vol) if vol > 0.1 else avg_pnl * 2.0
        sharpe = max(-5.0, min(5.0, sharpe))

        # Momentum score [0, 1]
        raw_momentum = 0.5 + (latest_pnl / 10.0)
        momentum = max(0.0, min(1.0, raw_momentum))

        candidates.append({
            "symbol": sym,
            "bucket": bucket,
            "direction": direction,
            "engine_confidence": conf / 100.0,
            "avg_pnl_pct": round(avg_pnl, 3),
            "latest_pnl_pct": round(latest_pnl, 3),
            "trend_pct": round(trend, 3),
            "volatility": round(vol, 3),
            "sharpe_proxy": round(sharpe, 3),
            "momentum_score": round(momentum, 3),
            "sample_count": len(series),
            # Quantum feature vector: normalized inputs for QAOA
            "feature_vector": [
                max(0.0, min(1.0, (avg_pnl + 5) / 10)),     # normalized avg pnl
                max(0.0, min(1.0, momentum)),                  # momentum
                max(0.0, min(1.0, (sharpe + 5) / 10)),       # normalized sharpe
                conf / 100.0,                                  # engine confidence
                max(0.0, min(1.0, (trend + 5) / 10)),        # normalized trend
            ],
        })

    # Sort by sharpe proxy descending (best first for quantum)
    candidates.sort(key=lambda x: x["sharpe_proxy"], reverse=True)
    return candidates


def build_request(candidates: List[Dict], snapshots: List[Dict]) -> QuantumOptimizationRequest:
    """Build a QuantumOptimizationRequest from what-if performance data."""
    req_id = _req_id()
    ts = _utc_now()

    # Compute regime metrics from latest snapshot
    latest = snapshots[-1] if snapshots else {}
    bull_pnls = [p["hyp_pnl_pct"] for p in latest.get("picks", [])
                 if p.get("bucket", "") not in ("TECH_SELLOFF", "AVIATION", "MELTDOWN_HEDGE")]
    bear_pnls = [p["hyp_pnl_pct"] for p in latest.get("picks", [])
                 if p.get("bucket", "") in ("TECH_SELLOFF", "AVIATION", "MELTDOWN_HEDGE")]

    avg_bull = sum(bull_pnls) / len(bull_pnls) if bull_pnls else 0
    avg_bear = sum(bear_pnls) / len(bear_pnls) if bear_pnls else 0
    regime_directional = "bull" if avg_bull > avg_bear else "bear"

    return QuantumOptimizationRequest(
        request_id=req_id,
        package_id=f"whatif-bridge-{ts[:10]}",
        timestamp_utc=ts,
        runtime_flags={
            "artifact_only": True,
            "not_for_direct_execution": True,
            "quantum_direct_execution_forbidden": True,
            "bounded_secondary_signal_only": True,
            "source": "whatif_quantum_bridge",
        },
        time_window_state={
            "horizon": "intraday",
            "snapshot_count": len(snapshots),
            "first_snapshot": snapshots[0]["ts"] if snapshots else ts,
            "latest_snapshot": snapshots[-1]["ts"] if snapshots else ts,
        },
        regime_state={
            "mode": "CRISIS",
            "directional_bias": regime_directional,
            "avg_bull_pnl_pct": round(avg_bull, 3),
            "avg_bear_pnl_pct": round(avg_bear, 3),
            "war_regime": True,
            "source": "whatif_intraday_learning",
        },
        objective={
            "type": "scenario_allocation",
            "description": "Optimal allocation across war-volatility picks based on intraday performance",
            "maximize": "risk_adjusted_return",
            "constraint_mode": "sharpe_weighted",
        },
        constraints={
            "max_single_allocation": 0.40,  # max 40% in one pick
            "min_allocation": 0.05,         # min 5% per pick if included
            "long_bias_required": True,      # must be net long
            "max_candidates": min(len(candidates), 8),
        },
        candidate_universe=candidates,
        market_microstructure={
            "liquidity": "high",
            "regime": "crisis_war",
            "data_source": "yahoo_finance_intraday",
            "tracking_interval_minutes": 5,
        },
        provenance={
            "source": "whatif_quantum_bridge.py",
            "schema_version": "whatif_bridge.v1",
            "pipeline": "learner→quantum→research_score→weighted_recommendation",
            "not_for_direct_execution": True,
        },
    )


def run_quantum_analysis(request: QuantumOptimizationRequest) -> Dict[str, Any]:
    """Run request through available quantum backends."""
    try:
        from src.research.backends.multi_backend_orchestrator import MultiBackendOrchestrator
        orchestrator = MultiBackendOrchestrator(
            artifact_dir=QUANTUM_OUT / "comparisons"
        )
        result = orchestrator.run_comparison(request.to_dict(), mode="quick")
        return result
    except Exception as e:
        print(f"  [QUANTUM] MultiBackend error: {e}. Falling back to classical.")

    # Classical strong baseline fallback
    try:
        from src.research.backends.classical_strong_baseline import ClassicalStrongBaseline
        baseline = ClassicalStrongBaseline()
        result = baseline.run(request.to_dict())
        return {"classical_only": True, "result": result}
    except Exception as e:
        print(f"  [QUANTUM] Classical fallback error: {e}")
        return {"error": str(e)}


def extract_ranked_allocations(quantum_result: Dict, candidates: List[Dict]) -> List[Dict]:
    """Extract optimal allocations from quantum result.

    Falls back to sharpe-weighted classical ranking if quantum unavailable.
    """
    # Try to get quantum ranked solutions
    ranked = quantum_result.get("ranked_solutions", [])
    if ranked:
        return ranked

    # Try nested result structure
    for backend_name, backend_result in quantum_result.items():
        if isinstance(backend_result, dict):
            ranked = backend_result.get("ranked_solutions", [])
            if ranked:
                return ranked

    # Classical fallback: rank by sharpe proxy
    total_sharpe = sum(max(0, c["sharpe_proxy"]) for c in candidates) or 1.0
    allocations = []
    for c in candidates[:8]:
        weight = max(0.05, c["sharpe_proxy"] / total_sharpe) if c["sharpe_proxy"] > 0 else 0.05
        allocations.append({
            "symbol": c["symbol"],
            "weight": round(min(0.40, weight), 3),
            "sharpe_proxy": c["sharpe_proxy"],
            "confidence": c["engine_confidence"],
            "bucket": c["bucket"],
            "direction": c["direction"],
            "latest_pnl_pct": c["latest_pnl_pct"],
            "trend_pct": c["trend_pct"],
        })

    # Normalize weights to sum to 1.0
    total_w = sum(a["weight"] for a in allocations) or 1.0
    for a in allocations:
        a["weight"] = round(a["weight"] / total_w, 3)

    return allocations


def build_evaluation(quantum_result: Dict, candidates: List[Dict]) -> Dict[str, Any]:
    """Build evaluation dict for research_score_writer from quantum result + candidates."""
    # Extract quantum vs classical metrics
    quantum_obj = 0.0
    classical_obj = 0.0
    quantum_overlap = 0.5
    quantum_dir = 0.5
    classical_overlap = 0.5
    classical_dir = 0.5

    for key, val in quantum_result.items():
        if isinstance(val, dict):
            obj = val.get("objective_value", 0)
            if "classical" in key.lower():
                classical_obj = max(classical_obj, obj)
                classical_overlap = val.get("overlap_score", 0.5)
                classical_dir = val.get("directional_score", 0.5)
            elif key in ("qpanda3", "qiskit_finance", "pennylane"):
                if obj > quantum_obj:
                    quantum_obj = obj
                    quantum_overlap = val.get("overlap_score", 0.5)
                    quantum_dir = val.get("directional_score", 0.5)

    # Realized return: weighted avg of pick pnls from candidates
    q_realized = sum(c["avg_pnl_pct"] * c["engine_confidence"] for c in candidates) * 100
    c_realized = sum(c["avg_pnl_pct"] for c in candidates) / max(len(candidates), 1) * 100

    return {
        "request_id": None,
        "package_id": "whatif-bridge",
        "quantum_overlap_score": quantum_overlap,
        "quantum_directional_score": quantum_dir,
        "classical_overlap_score": classical_overlap,
        "classical_directional_score": classical_dir,
        "quantum_realized_return_bps_sum": q_realized,
        "classical_realized_return_bps_sum": c_realized,
    }


def compute_quantum_weights(allocations: List[Dict], research_score: float) -> Dict[str, float]:
    """Combine quantum allocations with research score to produce final pick weights.

    Final weight = quantum_allocation_weight × research_score_multiplier
    research_score 0.7+ → 1.3x boost
    research_score 0.55-0.7 → 1.0x (neutral)
    research_score <0.35 → 0.7x penalty
    """
    if research_score >= 0.70:
        multiplier = 1.3
    elif research_score >= 0.55:
        multiplier = 1.0
    else:
        multiplier = 0.7

    weights = {}
    for a in allocations:
        sym = a.get("symbol", "")
        base_weight = a.get("weight", 0)
        weights[sym] = round(base_weight * multiplier, 3)

    return weights


def write_quantum_scores(weights: Dict[str, float], research_score_data: Dict,
                          allocations: List[Dict], request_id: str) -> Path:
    """Write quantum-enhanced weights back to learner data dir."""
    output = {
        "schema": "whatif_quantum_scores.v1",
        "not_for_direct_execution": True,
        "request_id": request_id,
        "timestamp_utc": _utc_now(),
        "research_score": research_score_data.get("research_score", 0),
        "recommended_influence": research_score_data.get("recommended_influence", "none"),
        "quantum_weights": weights,
        "ranked_allocations": allocations[:8],
    }

    out_file = DATA_DIR / "quantum_scores.json"
    out_file.write_text(json.dumps(output, indent=2))

    # Also write timestamped artifact
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    artifact = QUANTUM_OUT / f"whatif_quantum_{ts_str}.json"
    artifact.write_text(json.dumps(output, indent=2))

    return out_file


def run(verbose: bool = True) -> Dict[str, Any]:
    """Main entry point — run full what-if quantum analysis pipeline."""
    print(f"\n  [WQ] What-If Quantum Bridge starting...")
    t0 = time.time()

    # 1. Load snapshots
    snapshots = load_learner_snapshots()
    if len(snapshots) < 3:
        print(f"  [WQ] Only {len(snapshots)} snapshots — need at least 3. Skipping.")
        return {"status": "insufficient_data", "snapshots": len(snapshots)}

    print(f"  [WQ] Loaded {len(snapshots)} snapshots")

    # 2. Build candidate universe
    candidates = build_candidate_universe(snapshots)
    if not candidates:
        return {"status": "no_candidates"}

    print(f"  [WQ] {len(candidates)} candidates: {[c['symbol'] for c in candidates]}")

    # 3. Build quantum request
    request = build_request(candidates, snapshots)
    print(f"  [WQ] Request built: {request.request_id}")

    # 4. Run quantum/classical analysis
    print(f"  [WQ] Running quantum backends (artifact-only)...")
    quantum_result = run_quantum_analysis(request)

    # 5. Extract allocations
    allocations = extract_ranked_allocations(quantum_result, candidates)
    print(f"  [WQ] Top allocations: {[(a.get('symbol'), a.get('weight', 0)) for a in allocations[:4]]}")

    # 6. Build evaluation + research score
    evaluation = build_evaluation(quantum_result, candidates)
    evaluation["request_id"] = request.request_id
    research_score_data = build_research_score(evaluation)
    research_score = research_score_data.get("research_score", 0.5)
    influence = research_score_data.get("recommended_influence", "none")
    print(f"  [WQ] Research score: {research_score:.3f} ({influence})")

    # 7. Compute final quantum-weighted scores
    weights = compute_quantum_weights(allocations, research_score)
    top = sorted(weights.items(), key=lambda x: x[1], reverse=True)[:3]
    print(f"  [WQ] Top quantum weights: {top}")

    # 8. Write output
    out_file = write_quantum_scores(weights, research_score_data, allocations, request.request_id)
    elapsed = time.time() - t0
    print(f"  [WQ] Done in {elapsed:.1f}s → {out_file}")

    return {
        "status": "success",
        "request_id": request.request_id,
        "research_score": research_score,
        "recommended_influence": influence,
        "top_weights": dict(top),
        "candidate_count": len(candidates),
        "snapshot_count": len(snapshots),
        "elapsed_seconds": round(elapsed, 1),
    }


if __name__ == "__main__":
    result = run()
    print(json.dumps(result, indent=2))
