#!/usr/bin/env python3
"""Generate a stabilization-window report over evidence-only canary artifacts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.reports.canary_stabilization_report import CanaryStabilizationReportBuilder


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build canary stabilization report")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--output-json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    builder = CanaryStabilizationReportBuilder(Path(args.repo_root).resolve())
    report = builder.build_report(limit=args.limit)
    out = Path(args.output_json).resolve() if args.output_json else Path(args.repo_root).resolve() / "reports" / "operational" / "canary_stabilization_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
