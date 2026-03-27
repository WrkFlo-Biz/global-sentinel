"""Replay evaluator over saved evaluation artifacts.

Runs research score computation across historical evaluations
to produce aggregate backtest statistics.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List

from src.research.research_score_writer import build_research_score


def load_jsons(folder: Path) -> List[Dict[str, Any]]:
    if not folder.exists():
        return []
    rows = []
    for p in sorted(folder.glob("*.json")):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return rows


class ReplayQuantumResearchBacktest:

    def run(self, evaluations: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not evaluations:
            return {
                "schema_version": "replay_quantum_research_backtest.v1",
                "evaluation_count": 0,
                "note": "No evaluations found.",
            }

        scores = [build_research_score(ev) for ev in evaluations]

        quantum_wins = sum(1 for ev in evaluations if ev.get("winner") == "quantum")
        classical_wins = sum(1 for ev in evaluations if ev.get("winner") == "classical")
        ties = sum(1 for ev in evaluations if ev.get("winner") == "tie")

        return {
            "schema_version": "replay_quantum_research_backtest.v1",
            "evaluation_count": len(evaluations),
            "quantum_win_rate": quantum_wins / len(evaluations),
            "classical_win_rate": classical_wins / len(evaluations),
            "tie_rate": ties / len(evaluations),
            "avg_research_score": mean(float(s.get("research_score", 0.0)) for s in scores),
            "avg_quantum_realized_return_bps_sum": mean(float(ev.get("quantum_realized_return_bps_sum", 0.0)) for ev in evaluations),
            "avg_classical_realized_return_bps_sum": mean(float(ev.get("classical_realized_return_bps_sum", 0.0)) for ev in evaluations),
        }


def parse_args():
    p = argparse.ArgumentParser(description="Replay quantum research backtest")
    p.add_argument("--evaluations-dir", default="reports/research/history")
    p.add_argument("--output-json", default="reports/research/replay_quantum_research_backtest.json")
    return p.parse_args()


def main():
    args = parse_args()
    evals = load_jsons(Path(args.evaluations_dir))
    summary = ReplayQuantumResearchBacktest().run(evals)

    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
