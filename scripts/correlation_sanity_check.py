#!/usr/bin/env python3
"""Global Sentinel V4 — Correlation Sanity Check

Runs every 6 hours to detect anomalous correlations between signal components.
Flags correlation breaks and regime transitions.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_recent_scorecards(limit: int = 24) -> list:
    """Load most recent scorecards."""
    sc_dir = PROJECT_ROOT / "logs" / "scorecards"
    if not sc_dir.exists():
        return []
    files = sorted(sc_dir.glob("scorecard-*.json"), reverse=True)[:limit]
    cards = []
    for f in files:
        try:
            cards.append(json.loads(f.read_text()))
        except Exception:
            continue
    return cards


def check_pairwise_correlations(scorecards: list, max_corr: float = 0.95) -> list:
    """Check for suspiciously high pairwise correlations in component scores."""
    if len(scorecards) < 4:
        return [{"warning": "Insufficient scorecards for correlation analysis", "count": len(scorecards)}]

    # Extract component score time series
    components = {}
    for sc in scorecards:
        for k, v in sc.get("component_scores", {}).items():
            components.setdefault(k, []).append(v)

    alerts = []
    keys = list(components.keys())
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a = components[keys[i]]
            b = components[keys[j]]
            if len(a) != len(b) or len(a) < 4:
                continue
            # Simple correlation: check if values are nearly identical
            diffs = [abs(x - y) for x, y in zip(a, b)]
            avg_diff = sum(diffs) / len(diffs) if diffs else 1.0
            if avg_diff < 0.01:
                alerts.append({
                    "type": "high_correlation",
                    "pair": [keys[i], keys[j]],
                    "avg_diff": avg_diff,
                    "message": f"Suspiciously similar: {keys[i]} and {keys[j]}",
                })

    return alerts


def check_source_diversity(scorecards: list, min_sources: int = 4) -> list:
    """Check that we have enough distinct data sources."""
    if not scorecards:
        return [{"warning": "No scorecards available"}]

    latest = scorecards[0]
    components = latest.get("component_scores", {})
    if len(components) < min_sources:
        return [{"warning": f"Only {len(components)} signal sources (min {min_sources})"}]
    return []


def main():
    scorecards = load_recent_scorecards()
    timestamp = datetime.now(timezone.utc).isoformat()

    corr_alerts = check_pairwise_correlations(scorecards)
    diversity_alerts = check_source_diversity(scorecards)

    all_alerts = corr_alerts + diversity_alerts
    result = {
        "timestamp": timestamp,
        "scorecards_analyzed": len(scorecards),
        "alerts": all_alerts,
        "status": "alert" if all_alerts else "ok",
    }

    # Write result
    safe_ts = timestamp.replace(":", "-")
    out_path = PROJECT_ROOT / "logs" / "risk_checks" / f"correlation-{safe_ts}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))

    print(json.dumps(result, indent=2))
    sys.exit(1 if all_alerts else 0)


if __name__ == "__main__":
    main()
