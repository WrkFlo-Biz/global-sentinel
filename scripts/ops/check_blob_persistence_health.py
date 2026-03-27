#!/usr/bin/env python3
"""Check Blob-backed learning-state persistence health."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.blob_persistence_health import BlobPersistenceHealthChecker


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check learning-state Blob persistence health")
    parser.add_argument("--repo-root", default=str(PROJECT_ROOT))
    parser.add_argument("--output-json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    checker = BlobPersistenceHealthChecker(repo_root)
    report = checker.check().to_dict()

    if args.output_json:
        out = Path(args.output_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(out)
    else:
        print(json.dumps(report, indent=2, default=str))

    if report["status"] == "healthy":
        sys.exit(0)
    if report["status"] == "degraded":
        sys.exit(1)
    sys.exit(2)


if __name__ == "__main__":
    main()
