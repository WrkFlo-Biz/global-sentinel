#!/usr/bin/env python3
"""
Analog library rebuild (Stream 4).

Writes:
- data/analog_library/analog_vectors.(parquet|jsonl.gz)
- data/analog_library/analog_index.sqlite
- logs/market_research/analog_discovery.log
- logs/market_research/analog_engine_build_report.json
"""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict

from src.research.market_research.analog_engine import build_and_persist_analog_library


def _setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("analog_discovery")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = RotatingFileHandler(str(log_path), maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def _write_report(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".", help="Repo root (default: .)")
    ap.add_argument("--window", type=int, default=20, help="Window length in days (default: 20)")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    log_dir = repo_root / "logs" / "market_research"
    logger = _setup_logger(log_dir / "analog_discovery.log")
    report_path = log_dir / "analog_engine_build_report.json"

    logger.info("Rebuilding analog library window=%d", int(args.window))
    out = build_and_persist_analog_library(repo_root=repo_root, window=int(args.window))
    logger.info("Analog library rebuilt dates=%s columns=%s", out.get("dates"), out.get("columns"))

    payload = {
        "stream": "analog_engine",
        "files_created": [
            "data/analog_library/analog_vectors.(parquet|jsonl.gz)",
            "data/analog_library/analog_index.sqlite",
        ],
        "smoke_test": "ok" if out.get("dates") else "error",
        "notes": "Analog rebuild completed",
        "result": out,
    }
    _write_report(report_path, payload)
    logger.info("Wrote build report: %s", report_path)

    return 0 if out.get("dates") else 1


if __name__ == "__main__":
    raise SystemExit(main())

