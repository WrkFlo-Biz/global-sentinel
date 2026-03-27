#!/usr/bin/env python3
"""Dashboard-ready summary across recent quantum vs classical evaluation artifacts.

Reads evaluation JSONs from reports/research/ and computes:
- quantum win rate
- overlap score averages
- directional score averages
"""
from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List


def load_jsons(folder: Path) -> List[Dict[str, Any]]:
    if not folder.exists():
        return []
    out = []
    for p in sorted(folder.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return out


def build_summary(research_dir: Path) -> Dict[str, Any]:
    evals = load_jsons(research_dir)
    evals = [e for e in evals if "winner" in e]

    if not evals:
        return {
            "schema_version": "research_quantum_summary.v1",
            "evaluation_count": 0,
            "note": "No evaluation artifacts found.",
        }

    quantum_wins = sum(1 for e in evals if e.get("winner") == "quantum")
    classical_wins = sum(1 for e in evals if e.get("winner") == "classical")
    ties = sum(1 for e in evals if e.get("winner") == "tie")

    return {
        "schema_version": "research_quantum_summary.v1",
        "evaluation_count": len(evals),
        "quantum_win_rate": quantum_wins / max(len(evals), 1),
        "classical_win_rate": classical_wins / max(len(evals), 1),
        "tie_rate": ties / max(len(evals), 1),
        "avg_quantum_overlap_score": mean(e.get("quantum_overlap_score", 0.0) for e in evals),
        "avg_classical_overlap_score": mean(e.get("classical_overlap_score", 0.0) for e in evals),
        "avg_quantum_directional_score": mean(e.get("quantum_directional_score", 0.0) for e in evals),
        "avg_classical_directional_score": mean(e.get("classical_directional_score", 0.0) for e in evals),
    }


def main():
    research_dir = Path("reports/research")
    research_dir.mkdir(parents=True, exist_ok=True)

    summary = build_summary(research_dir)

    out = research_dir / "research_quantum_summary.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
