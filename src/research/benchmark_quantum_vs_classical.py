#!/usr/bin/env python3
"""Benchmark quantum vs classical optimization results.

Includes finance capability mode tracking and slippage-adjusted evaluation.
"""

import json
import statistics
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone


def load_results(directory: Path) -> List[Dict[str, Any]]:
    results = []
    if not directory.exists():
        return results
    for p in sorted(directory.glob("*.json")):
        try:
            results.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return results


def compute_metrics(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not results:
        return {"count": 0}

    obj_values = [r.get("objective_value", 0) for r in results]
    wall_times = [r.get("diagnostics", {}).get("wall_clock_seconds", 0) for r in results]
    feasibilities = [r.get("feasibility", 0) for r in results]
    fallback_count = sum(1 for r in results if r.get("diagnostics", {}).get("fallback_used", False))

    return {
        "count": len(results),
        "objective_value": {
            "mean": statistics.mean(obj_values) if obj_values else 0,
            "median": statistics.median(obj_values) if obj_values else 0,
            "stdev": statistics.stdev(obj_values) if len(obj_values) > 1 else 0,
        },
        "wall_clock_seconds": {
            "mean": statistics.mean(wall_times) if wall_times else 0,
            "max": max(wall_times) if wall_times else 0,
        },
        "feasibility": {
            "mean": statistics.mean(feasibilities) if feasibilities else 0,
            "min": min(feasibilities) if feasibilities else 0,
        },
        "fallback_rate": fallback_count / len(results) if results else 0,
    }


def compute_by_finance_mode(results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Break down metrics by finance capability / objective type."""
    by_mode: Dict[str, List[Dict[str, Any]]] = {}
    for r in results:
        mode = (r.get("diagnostics") or {}).get("objective_type", "unknown")
        by_mode.setdefault(mode, []).append(r)
    return {mode: compute_metrics(runs) for mode, runs in by_mode.items()}


def compare(quantum_dir: Path, classical_dir: Path) -> Dict[str, Any]:
    quantum = load_results(quantum_dir)
    classical = load_results(classical_dir)

    q_metrics = compute_metrics(quantum)
    c_metrics = compute_metrics(classical)

    # Match by request_id for paired comparison
    q_by_id = {r.get("request_id"): r for r in quantum}
    c_by_id = {r.get("request_id"): r for r in classical}
    common_ids = set(q_by_id.keys()) & set(c_by_id.keys())

    paired_deltas = []
    for rid in common_ids:
        q_obj = q_by_id[rid].get("objective_value", 0)
        c_obj = c_by_id[rid].get("objective_value", 0)
        if c_obj != 0:
            paired_deltas.append((q_obj - c_obj) / abs(c_obj))

    # Finance capability breakdown
    finance_modes_seen = sorted(set(
        (r.get("diagnostics") or {}).get("objective_type", "unknown") for r in quantum
    ))
    q_by_mode = compute_by_finance_mode(quantum)
    c_by_mode = compute_by_finance_mode(classical)

    return {
        "schema_version": "benchmark_quantum_vs_classical.v2",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "quantum": q_metrics,
        "classical": c_metrics,
        "paired_comparison": {
            "matched_pairs": len(common_ids),
            "mean_relative_improvement": statistics.mean(paired_deltas) if paired_deltas else None,
            "median_relative_improvement": statistics.median(paired_deltas) if paired_deltas else None,
        },
        "finance_modes_seen": finance_modes_seen,
        "quantum_by_mode": q_by_mode,
        "classical_by_mode": c_by_mode,
        "recommendation": (
            "quantum_shows_improvement" if paired_deltas and statistics.mean(paired_deltas) > 0.01
            else "no_significant_improvement" if paired_deltas
            else "insufficient_data_for_comparison"
        ),
    }


def load_multi_backend_comparisons(directory: Path) -> List[Dict[str, Any]]:
    """Load comparison artifacts produced by MultiBackendOrchestrator."""
    results = []
    if not directory.exists():
        return results
    for p in sorted(directory.glob("comparison_*.json")):
        try:
            results.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return results


def compute_multi_backend_metrics(comparisons: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate metrics across multi-backend comparison artifacts."""
    if not comparisons:
        return {"count": 0, "note": "no_multi_backend_comparisons_found"}

    all_backends = set()
    success_counts: Dict[str, int] = {}
    objective_by_backend: Dict[str, List[float]] = {}
    quantum_vs_classical_deltas: List[float] = []

    for comp in comparisons:
        for bk in comp.get("backends_succeeded", []):
            all_backends.add(bk)
            success_counts[bk] = success_counts.get(bk, 0) + 1
        comparison_block = comp.get("comparison", {})
        for bk, obj in comparison_block.get("objective_values", {}).items():
            if obj is not None:
                objective_by_backend.setdefault(bk, []).append(float(obj))
        delta = comparison_block.get("quantum_vs_strong_classical_delta")
        if delta is not None:
            quantum_vs_classical_deltas.append(float(delta))

    backend_summary = {}
    for bk in sorted(all_backends):
        vals = objective_by_backend.get(bk, [])
        backend_summary[bk] = {
            "success_count": success_counts.get(bk, 0),
            "objective_mean": statistics.mean(vals) if vals else None,
            "objective_median": statistics.median(vals) if vals else None,
        }

    return {
        "count": len(comparisons),
        "backends_seen": sorted(all_backends),
        "backend_summary": backend_summary,
        "quantum_vs_strong_classical_delta": {
            "mean": statistics.mean(quantum_vs_classical_deltas) if quantum_vs_classical_deltas else None,
            "median": statistics.median(quantum_vs_classical_deltas) if quantum_vs_classical_deltas else None,
            "count": len(quantum_vs_classical_deltas),
        },
        "recommendation": (
            "quantum_shows_improvement"
            if quantum_vs_classical_deltas and statistics.mean(quantum_vs_classical_deltas) > 0.01
            else "no_significant_improvement"
            if quantum_vs_classical_deltas
            else "insufficient_data"
        ),
    }


def main():
    import argparse
    p = argparse.ArgumentParser(description="Benchmark quantum vs classical results")
    p.add_argument("--quantum-dir", default="artifacts/quantum", help="Quantum results directory")
    p.add_argument("--classical-dir", default="artifacts/classical", help="Classical results directory")
    p.add_argument("--multi-backend-dir", default=None, help="Directory with multi-backend comparison JSONs")
    p.add_argument("--output", default="reports/research/benchmark_quantum_vs_classical.json")
    args = p.parse_args()

    result = compare(Path(args.quantum_dir), Path(args.classical_dir))

    if args.multi_backend_dir:
        multi_comparisons = load_multi_backend_comparisons(Path(args.multi_backend_dir))
        result["multi_backend"] = compute_multi_backend_metrics(multi_comparisons)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
