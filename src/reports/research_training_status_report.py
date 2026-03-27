#!/usr/bin/env python3
"""Training status dashboard artifact.

Reads training dataset, labels, replay backtest, and research score
to produce a markdown status report.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    research_dir = Path("reports/research")
    research_dir.mkdir(parents=True, exist_ok=True)
    out = research_dir / "research_training_status_report.md"

    ds_path = research_dir / "training_dataset.json"
    labels_path = research_dir / "training_labels.json"
    replay_path = research_dir / "replay_quantum_research_backtest.json"
    score_path = research_dir / "research_score_latest.json"

    lines = ["# Research Training Status Report", ""]

    if ds_path.exists():
        ds = load_json(ds_path)
        lines.append("## Training Dataset")
        lines.append(f"- Row count: {ds.get('row_count')}")
        lines.append("")

    if labels_path.exists():
        labels = load_json(labels_path)
        rows = labels.get("rows", [])
        label_counts: Dict[str, int] = {}
        for r in rows:
            label = str(r.get("alpha_label", "unknown"))
            label_counts[label] = label_counts.get(label, 0) + 1

        lines.append("## Labels")
        for k, v in sorted(label_counts.items()):
            lines.append(f"- {k}: {v}")
        lines.append("")

    if replay_path.exists():
        replay = load_json(replay_path)
        lines.append("## Replay Backtest")
        lines.append(f"- Evaluation count: {replay.get('evaluation_count')}")
        lines.append(f"- Quantum win rate: {replay.get('quantum_win_rate')}")
        lines.append(f"- Classical win rate: {replay.get('classical_win_rate')}")
        lines.append(f"- Avg research score: {replay.get('avg_research_score')}")
        lines.append("")

    if score_path.exists():
        score = load_json(score_path)
        lines.append("## Current Research Score")
        lines.append(f"- Research score: {score.get('research_score')}")
        lines.append(f"- Recommended influence: {score.get('recommended_influence')}")
        lines.append("")

    lines.append("## Training Readiness")
    lines.append("- Keep the quantum lane bounded and research-only.")
    lines.append("- Promote only after sustained slippage-adjusted outperformance versus classical baselines.")
    lines.append("- Use replay plus live telemetry together; do not rely on one alone.")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
