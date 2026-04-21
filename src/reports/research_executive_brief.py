#!/usr/bin/env python3
"""Generate a concise executive brief from recent research artifacts.

Produces a markdown file suitable for CIO/CAIO-level review
summarizing quantum vs classical performance and research score status.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    research_dir = Path("reports/research")
    brief_path = research_dir / "research_executive_brief.md"
    research_dir.mkdir(parents=True, exist_ok=True)

    parts = ["# Research Executive Brief", ""]

    eval_path = research_dir / "evaluation_latest.json"
    score_path = research_dir / "research_score_latest.json"
    summary_path = research_dir / "research_quantum_summary.json"

    if eval_path.exists():
        ev = load_json(eval_path)
        parts.append("## Latest Quantum vs Classical Evaluation")
        parts.append(f"- Request ID: {ev.get('request_id')}")
        parts.append(f"- Package ID: {ev.get('package_id')}")
        parts.append(f"- Winner: **{ev.get('winner')}**")
        parts.append(f"- Quantum overlap: {ev.get('quantum_overlap_score')}")
        parts.append(f"- Classical overlap: {ev.get('classical_overlap_score')}")
        parts.append(f"- Quantum realized bps sum: {ev.get('quantum_realized_return_bps_sum')}")
        parts.append(f"- Classical realized bps sum: {ev.get('classical_realized_return_bps_sum')}")
        parts.append("")

    if score_path.exists():
        rs = load_json(score_path)
        parts.append("## Current Bounded Research Score")
        parts.append(f"- Score: **{rs.get('research_score')}**")
        parts.append(f"- Influence: `{rs.get('recommended_influence')}`")
        parts.append(f"- Direct execution forbidden: `{rs.get('guardrails', {}).get('not_for_direct_execution')}`")
        parts.append("")

    if summary_path.exists():
        sm = load_json(summary_path)
        parts.append("## Aggregate Research Performance")
        parts.append(f"- Evaluation count: {sm.get('evaluation_count')}")
        parts.append(f"- Quantum win rate: {sm.get('quantum_win_rate')}")
        parts.append(f"- Classical win rate: {sm.get('classical_win_rate')}")
        parts.append(f"- Tie rate: {sm.get('tie_rate')}")
        parts.append("")

    parts.append("## Executive Takeaway")
    parts.append("- QPanda/Origin lane remains research-only and bounded.")
    parts.append("- Promote only if outcome-adjusted performance remains superior over a sustained window.")
    parts.append("- Continue comparing against classical baselines net of slippage and fill quality.")

    brief_path.write_text("\n".join(parts), encoding="utf-8")
    print(brief_path)


if __name__ == "__main__":
    main()
