#!/usr/bin/env python3
"""Run evidence-only canary automation using live scorecards."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.research.evidence_only_canary_runner import EvidenceOnlyCanaryRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run evidence-only canary automation")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--window-size", type=int, default=25)
    parser.add_argument("--signal-type", default="online_weighted_encoder")
    parser.add_argument("--output-json")
    return parser.parse_args()


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    runner = EvidenceOnlyCanaryRunner(repo_root, signal_type=args.signal_type)
    artifact = runner.run(limit=args.limit, window_size=args.window_size)

    if args.output_json:
        _write_json(Path(args.output_json).resolve(), artifact)
        print(Path(args.output_json).resolve())
        return

    print(json.dumps(artifact, indent=2, default=str))


if __name__ == "__main__":
    main()
