#!/usr/bin/env python3
"""
Build the daily macro/geopolitical feature matrix (Stream 2).

Writes:
- data/event_features/daily_features.(parquet|jsonl.gz)
- data/event_features/regime_tags.(parquet|jsonl.gz) [optional]
- logs/market_research/build_event_features.log
- logs/market_research/event_features_build_report.json
"""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict

from src.research.market_research.event_features import (
    build_daily_features,
    build_regime_tags_from_features,
    write_daily_features,
    write_regime_tags,
)


def _setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("build_event_features")
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
    ap.add_argument("--start", default="", help="Start date YYYY-MM-DD (optional)")
    ap.add_argument("--end", default="", help="End date YYYY-MM-DD (optional)")
    ap.add_argument("--regime-tags/--no-regime-tags", default=True, help="Write regime tags (default: yes)")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    log_dir = repo_root / "logs" / "market_research"
    logger = _setup_logger(log_dir / "build_event_features.log")
    report_path = log_dir / "event_features_build_report.json"

    start = args.start.strip() or None
    end = args.end.strip() or None

    logger.info("Building daily features start=%s end=%s", start or "-", end or "-")
    features = build_daily_features(start=start, end=end, repo_root=repo_root)
    write_daily_features(features, repo_root=repo_root)
    logger.info("Wrote daily features rows=%d", len(features))

    tags_written = 0
    if args.regime_tags:
        tags = build_regime_tags_from_features(features)
        write_regime_tags(tags, repo_root=repo_root)
        tags_written = len(tags)
        logger.info("Wrote regime tags rows=%d", tags_written)

    payload = {
        "stream": "event_features",
        "files_created": [
            "data/event_features/daily_features.(parquet|jsonl.gz)",
            "data/event_features/regime_tags.(parquet|jsonl.gz)",
        ],
        "smoke_test": "ok" if features else "error",
        "features_rows": len(features),
        "regime_tag_rows": tags_written,
        "notes": "Feature build complete",
    }
    _write_report(report_path, payload)
    logger.info("Wrote build report: %s", report_path)

    return 0 if features else 1


if __name__ == "__main__":
    raise SystemExit(main())

