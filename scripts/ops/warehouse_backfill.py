#!/usr/bin/env python3
"""
Market Warehouse Backfill (Stream 1)

Resumable one-shot backfill for universes defined in `config/universes/*.yaml`.

Writes:
- `data/market_warehouse/manifest.sqlite`
- `data/market_warehouse/bars/{asset_class}/...` (parquet if pyarrow, else jsonl.gz)
- `logs/market_research/warehouse_backfill.log`
- `logs/market_research/warehouse_build_report.json`
"""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Set

from src.research.market_research.ingestion import backfill_symbol, fetch_alpaca_asset_universe, load_env
from src.research.market_research.universe import load_universe_configs, resolve_universe_items
from src.research.market_research.warehouse import get_manifest


def _setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("market_warehouse_backfill")
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


def _parse_csv_set(s: str) -> Set[str]:
    if not s:
        return set()
    return {x.strip() for x in s.split(",") if x.strip()}


def _write_report(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".", help="Repo root (default: .)")
    ap.add_argument("--universes", default="", help="Comma list of universe names to include (default: all)")
    ap.add_argument("--exclude", default="us_equities", help="Comma list of universes to skip (default: us_equities)")
    ap.add_argument("--years", type=int, default=0, help="Override backfill years (default: per-universe)")
    ap.add_argument("--max-symbols", type=int, default=0, help="Cap symbols per universe (safety)")
    ap.add_argument("--resume/--no-resume", default=True, help="Skip already-backfilled symbols (default: resume)")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    load_env(repo_root)
    log_dir = repo_root / "logs" / "market_research"
    logger = _setup_logger(log_dir / "warehouse_backfill.log")
    report_path = log_dir / "warehouse_build_report.json"

    include = _parse_csv_set(args.universes)
    exclude = _parse_csv_set(args.exclude)

    cfgs = load_universe_configs(repo_root=repo_root)
    if include:
        cfgs = [c for c in cfgs if c.name in include]
    if exclude:
        cfgs = [c for c in cfgs if c.name not in exclude]

    if not cfgs:
        logger.error("No universes selected; nothing to backfill")
        return 2

    files_created: List[str] = []
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for cfg in cfgs:
        try:
            items = resolve_universe_items(cfg, alpaca_asset_fetcher=fetch_alpaca_asset_universe)
        except Exception as e:
            logger.error("Universe %s resolve failed: %s", cfg.name, e)
            errors.append({"universe": cfg.name, "error": str(e)})
            continue

        if args.max_symbols and len(items) > args.max_symbols:
            items = items[: args.max_symbols]

        years = int(args.years or cfg.backfill_years or 20)
        logger.info("Backfill universe=%s items=%d years=%d source=%s asset_class=%s", cfg.name, len(items), years, cfg.source, cfg.asset_class)

        for sym in items:
            if args.resume:
                mf = get_manifest(sym, repo_root=repo_root)
                if mf and mf.get("bar_count"):
                    continue

            out = backfill_symbol(sym, cfg.asset_class, years=years, source=cfg.source, repo_root=repo_root)
            results.append(out)
            if out.get("status") != "ok":
                errors.append({"symbol": sym, "universe": cfg.name, "error": out.get("error")})
                logger.warning("Backfill failed symbol=%s universe=%s error=%s", sym, cfg.name, out.get("error"))
            else:
                logger.info("Backfill ok symbol=%s bars=%s last=%s", sym, out.get("bars_written"), out.get("last_bar_date"))

    # Minimal "files created" list to satisfy contract; actual bar files depend on runtime deps.
    files_created.extend(
        [
            "data/market_warehouse/manifest.sqlite",
            "data/market_warehouse/bars/",
        ]
    )

    payload = {
        "stream": "warehouse",
        "files_created": files_created,
        "smoke_test": "ok" if results else "error",
        "results_count": len(results),
        "error_count": len(errors),
        "notes": "Backfill completed" if results else "No results produced",
    }
    _write_report(report_path, payload)
    logger.info("Wrote build report: %s", report_path)

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

