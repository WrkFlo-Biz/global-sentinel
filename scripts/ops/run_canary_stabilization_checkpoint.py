#!/usr/bin/env python3
"""Generate an operator-facing canary stabilization checkpoint artifact."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.reports.canary_stabilization_checkpoint import CanaryStabilizationCheckpointBuilder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build canary stabilization checkpoint report")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--output-json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    output_path = (
        Path(args.output_json).resolve()
        if args.output_json
        else repo_root / "reports" / "operational" / "canary_stabilization_checkpoint.json"
    )
    report = CanaryStabilizationCheckpointBuilder(repo_root).build()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
