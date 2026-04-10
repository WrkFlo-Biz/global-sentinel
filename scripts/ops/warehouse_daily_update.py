#!/usr/bin/env python3
"""
Market Warehouse Daily Update (Stream 1)

Incrementally updates all already-known symbols (or a selected universe) in the
market warehouse.
"""

from __future__ import annotations

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Set

from src.research.market_research.ingestion import fetch_alpaca_asset_universe, incremental_update, load_env
from src.research.market_research.universe import load_universe_configs, resolve_universe_items
from src.research.market_research.warehouse import list_symbols


def _setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("market_warehouse_daily_update")
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
    ap.add_argument("--universes", default="", help="Comma list of universes to update (default: all manifest symbols)")
    ap.add_argument("--exclude", default="", help="Comma list of universes to skip")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    load_env(repo_root)
    log_dir = repo_root / "logs" / "market_research"
    logger = _setup_logger(log_dir / "warehouse_daily_update.log")
    report_path = log_dir / "warehouse_daily_update_report.json"

    include = _parse_csv_set(args.universes)
    exclude = _parse_csv_set(args.exclude)

    symbols: List[str] = []
    if include:
        cfgs = load_universe_configs(repo_root=repo_root)
        cfgs = [c for c in cfgs if c.name in include and c.name not in exclude]
        for cfg in cfgs:
            items = resolve_universe_items(cfg, alpaca_asset_fetcher=fetch_alpaca_asset_universe)
            symbols.extend(items)
    else:
        symbols = list_symbols(repo_root=repo_root)

    # De-dup and keep order
    seen: Set[str] = set()
    ordered: List[str] = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            ordered.append(s)
    symbols = ordered

    logger.info("Incremental update symbols=%d", len(symbols))
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    for sym in symbols:
        out = incremental_update(sym, repo_root=repo_root)
        results.append(out)
        if out.get("status") != "ok":
            errors.append({"symbol": sym, "error": out.get("error")})
            logger.warning("Update failed symbol=%s error=%s", sym, out.get("error"))
        else:
            logger.info("Update ok symbol=%s bars=%s last=%s", sym, out.get("bars_written"), out.get("last_bar_date"))

    payload = {
        "stream": "warehouse",
        "operation": "daily_update",
        "symbols": len(symbols),
        "results_count": len(results),
        "error_count": len(errors),
        "notes": "Incremental update complete",
    }
    _write_report(report_path, payload)
    logger.info("Wrote report: %s", report_path)

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

