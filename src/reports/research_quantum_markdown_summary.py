#!/usr/bin/env python3
"""Generate a human-readable markdown summary from research artifacts.

Reads evaluation, score, and aggregate summary JSONs and produces
a markdown file suitable for GitHub step summaries or dashboards.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    research_dir = Path("reports/research")
    summary_json = research_dir / "research_quantum_summary.json"
    latest_eval = research_dir / "evaluation_latest.json"
    latest_score = research_dir / "research_score_latest.json"

    parts = ["# Research Quantum Summary", ""]

    if summary_json.exists():
        data = load_json(summary_json)
        parts.append("## Aggregate")
        parts.append(f"- Evaluation count: {data.get('evaluation_count')}")
        parts.append(f"- Quantum win rate: {data.get('quantum_win_rate')}")
        parts.append(f"- Classical win rate: {data.get('classical_win_rate')}")
        parts.append(f"- Tie rate: {data.get('tie_rate')}")
        parts.append("")

    if latest_eval.exists():
        data = load_json(latest_eval)
        parts.append("## Latest Evaluation")
        parts.append(f"- Request ID: {data.get('request_id')}")
        parts.append(f"- Package ID: {data.get('package_id')}")
        parts.append(f"- Winner: **{data.get('winner')}**")
        parts.append(f"- Quantum overlap: {data.get('quantum_overlap_score')}")
        parts.append(f"- Classical overlap: {data.get('classical_overlap_score')}")
        parts.append(f"- Quantum directional: {data.get('quantum_directional_score')}")
        parts.append(f"- Classical directional: {data.get('classical_directional_score')}")
        parts.append("")

    if latest_score.exists():
        data = load_json(latest_score)
        parts.append("## Latest Research Score")
        parts.append(f"- Research score: **{data.get('research_score')}**")
        parts.append(f"- Recommended influence: `{data.get('recommended_influence')}`")
        parts.append(f"- Direct execution forbidden: `{data.get('guardrails', {}).get('not_for_direct_execution')}`")
        parts.append("")

    out = research_dir / "research_quantum_summary.md"
    research_dir.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
