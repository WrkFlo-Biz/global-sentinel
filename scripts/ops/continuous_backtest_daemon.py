#!/usr/bin/env python3
"""
Continuous backtest daemon (Stream 3).

Loops over symbols in the market warehouse and runs lightweight daily-bar
strategy backtests, storing summary metrics in a SQLite DB.

This worker is designed to run continuously under systemd.
"""

from __future__ import annotations

import argparse
import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.research.market_research.backtest_runner import STRATEGIES, backtest_close_series, store_backtest_summary


def _setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("continuous_backtest_daemon")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh = RotatingFileHandler(str(log_path), maxBytes=4_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    logger.addHandler(sh)
    return logger


def _maybe_load_warehouse() -> Any:
    try:
        from src.research.market_research import warehouse  # type: ignore
    except Exception:
        return None
    return warehouse


def _bars_to_close_and_dates(bars: List[Dict[str, Any]]) -> tuple[list[float], list[str]]:
    close: List[float] = []
    dates: List[str] = []
    for r in bars or []:
        if not isinstance(r, dict):
            continue
        d = str(r.get("date") or "").strip()
        if not d:
            continue
        try:
            c = float(r.get("close"))
        except Exception:
            continue
        dates.append(d[:10])
        close.append(c)
    return close, dates


def run_once(*, repo_root: Path, logger: logging.Logger, max_symbols: int = 0) -> int:
    warehouse = _maybe_load_warehouse()
    if warehouse is None:
        logger.error("Warehouse module not found. Merge Stream 1 first (market_warehouse).")
        return 2

    symbols = warehouse.list_symbols(repo_root=repo_root)
    if max_symbols and len(symbols) > max_symbols:
        symbols = symbols[:max_symbols]

    logger.info("Backtest loop start symbols=%d strategies=%d", len(symbols), len(STRATEGIES))
    errors = 0
    for sym in symbols:
        try:
            bars = warehouse.read_bars(sym, repo_root=repo_root)
            close, dates = _bars_to_close_and_dates(bars)
            if len(close) < 60:
                continue
            for strat in STRATEGIES.keys():
                summary = backtest_close_series(close, dates=dates, strategy=strat, params={})
                store_backtest_summary(summary, symbol=sym, repo_root=repo_root)
            logger.info("Backtest ok symbol=%s bars=%d", sym, len(close))
        except Exception as e:
            errors += 1
            logger.warning("Backtest failed symbol=%s err=%s", sym, e)
    logger.info("Backtest loop complete errors=%d", errors)
    return 0 if errors == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo-root", default=".", help="Repo root (default: .)")
    ap.add_argument("--sleep-seconds", type=int, default=3600, help="Sleep between loops (default: 3600)")
    ap.add_argument("--max-symbols", type=int, default=0, help="Cap symbols per loop (safety)")
    ap.add_argument("--once", action="store_true", help="Run one loop and exit")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).resolve()
    log_dir = repo_root / "logs" / "market_research"
    logger = _setup_logger(log_dir / "continuous_backtest_daemon.log")

    if args.once:
        return run_once(repo_root=repo_root, logger=logger, max_symbols=int(args.max_symbols))

    while True:
        run_once(repo_root=repo_root, logger=logger, max_symbols=int(args.max_symbols))
        time.sleep(max(5, int(args.sleep_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())

