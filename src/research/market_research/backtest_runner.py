#!/usr/bin/env python3
"""
Backtest runner (Stream 3) for Global Sentinel market research.

This is a lightweight, dependency-minimal alternative to vectorbt for v1.
It supports a few generic daily-bar strategies and stores summary metrics
in a SQLite DB.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _default_repo_root() -> Path:
    return Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", Path(__file__).resolve().parents[3]))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _results_db_path(repo_root: Path) -> Path:
    return repo_root / "data" / "market_warehouse" / "backtest_results.sqlite"


def _connect(repo_root: Path) -> sqlite3.Connection:
    db_path = _results_db_path(repo_root)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS backtest_results (
          symbol TEXT NOT NULL,
          strategy TEXT NOT NULL,
          params_json TEXT NOT NULL,
          start_date TEXT,
          end_date TEXT,
          bars INTEGER,
          total_return REAL,
          cagr REAL,
          sharpe REAL,
          max_drawdown REAL,
          trades INTEGER,
          win_rate REAL,
          updated_at TEXT,
          PRIMARY KEY (symbol, strategy, params_json)
        )
        """
    )
    conn.commit()


def _sma(values: Sequence[float], window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if window <= 0:
        return out
    s = 0.0
    for i, v in enumerate(values):
        s += v
        if i >= window:
            s -= values[i - window]
        if i >= window - 1:
            out[i] = s / window
    return out


def _rsi(values: Sequence[float], period: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(values)
    if period <= 0 or len(values) < period + 1:
        return out
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(values)):
        chg = values[i] - values[i - 1]
        gains.append(max(0.0, chg))
        losses.append(max(0.0, -chg))

    # Wilder's smoothing
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    if avg_loss == 0:
        out[period] = 100.0
    else:
        rs = avg_gain / avg_loss
        out[period] = 100.0 - (100.0 / (1.0 + rs))

    for i in range(period + 1, len(values)):
        g = gains[i - 1]
        l = losses[i - 1]
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
        if avg_loss == 0:
            out[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def _compute_returns(close: Sequence[float]) -> List[float]:
    rets: List[float] = [0.0]
    for i in range(1, len(close)):
        c0 = close[i - 1]
        c1 = close[i]
        if c0 == 0:
            rets.append(0.0)
        else:
            rets.append((c1 / c0) - 1.0)
    return rets


def _run_backtest(close: Sequence[float], position: Sequence[float]) -> Tuple[List[float], List[float]]:
    """
    Returns (returns, equity_curve) with a 1.0 starting equity.
    """
    if len(close) != len(position):
        raise ValueError("close/position length mismatch")
    px_rets = _compute_returns(close)
    rets: List[float] = [0.0]
    eq: List[float] = [1.0]
    for i in range(1, len(close)):
        # Use prior day's position
        p = float(position[i - 1])
        r = p * float(px_rets[i])
        rets.append(r)
        eq.append(eq[-1] * (1.0 + r))
    return rets, eq


def _max_drawdown(equity: Sequence[float]) -> float:
    peak = float("-inf")
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v / peak) - 1.0
            mdd = min(mdd, dd)
    return float(mdd)


def _sharpe(returns: Sequence[float], periods_per_year: int = 252) -> Optional[float]:
    if len(returns) < 2:
        return None
    mu = sum(returns) / len(returns)
    var = sum((r - mu) ** 2 for r in returns) / len(returns)
    sd = var**0.5
    if sd == 0:
        return None
    return (mu / sd) * (periods_per_year**0.5)


def _cagr(equity: Sequence[float], periods_per_year: int = 252) -> Optional[float]:
    if not equity:
        return None
    total = float(equity[-1])
    if total <= 0:
        return None
    years = len(equity) / periods_per_year
    if years <= 0:
        return None
    return total ** (1.0 / years) - 1.0


def _count_trades(position: Sequence[float]) -> int:
    trades = 0
    for i in range(1, len(position)):
        if float(position[i]) != float(position[i - 1]):
            trades += 1
    return trades


def _win_rate(returns: Sequence[float]) -> Optional[float]:
    # Ignore the first return (0.0 placeholder)
    vals = [r for r in returns[1:] if r != 0.0]
    if not vals:
        return None
    wins = sum(1 for r in vals if r > 0)
    return wins / len(vals)


def strategy_sma_trend(close: Sequence[float], fast: int = 20, slow: int = 50) -> List[float]:
    f = _sma(close, fast)
    s = _sma(close, slow)
    pos: List[float] = []
    for i in range(len(close)):
        if f[i] is None or s[i] is None:
            pos.append(0.0)
            continue
        pos.append(1.0 if float(f[i]) >= float(s[i]) else 0.0)
    return pos


def strategy_breakout(close: Sequence[float], lookback: int = 20, exit_lookback: int = 10) -> List[float]:
    pos: List[float] = [0.0] * len(close)
    in_pos = False
    for i in range(len(close)):
        if i < lookback:
            pos[i] = 0.0
            continue
        hi = max(close[i - lookback : i])
        lo = min(close[max(0, i - exit_lookback) : i]) if i > 0 else close[i]
        if not in_pos and close[i] > hi:
            in_pos = True
        elif in_pos and close[i] < lo:
            in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pos


def strategy_rsi_mean_reversion(close: Sequence[float], period: int = 14, enter_below: float = 30.0, exit_above: float = 55.0) -> List[float]:
    rsi = _rsi(close, period=period)
    pos: List[float] = [0.0] * len(close)
    in_pos = False
    for i in range(len(close)):
        if rsi[i] is None:
            pos[i] = 0.0
            continue
        if not in_pos and float(rsi[i]) <= enter_below:
            in_pos = True
        elif in_pos and float(rsi[i]) >= exit_above:
            in_pos = False
        pos[i] = 1.0 if in_pos else 0.0
    return pos


def strategy_momentum(close: Sequence[float], lookback: int = 20) -> List[float]:
    pos: List[float] = [0.0] * len(close)
    for i in range(len(close)):
        if i < lookback:
            continue
        c0 = close[i - lookback]
        c1 = close[i]
        if c0 == 0:
            continue
        ret = (c1 / c0) - 1.0
        pos[i] = 1.0 if ret > 0 else 0.0
    return pos


STRATEGIES = {
    "sma_trend": strategy_sma_trend,
    "breakout": strategy_breakout,
    "rsi_mean_reversion": strategy_rsi_mean_reversion,
    "momentum": strategy_momentum,
}


@dataclass(frozen=True)
class BacktestSummary:
    symbol: str
    strategy: str
    params: Dict[str, Any]
    start_date: str
    end_date: str
    bars: int
    total_return: float
    cagr: Optional[float]
    sharpe: Optional[float]
    max_drawdown: float
    trades: int
    win_rate: Optional[float]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "strategy": self.strategy,
            "params": self.params,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "bars": self.bars,
            "total_return": self.total_return,
            "cagr": self.cagr,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "trades": self.trades,
            "win_rate": self.win_rate,
        }


def backtest_close_series(
    close: Sequence[float],
    *,
    dates: Sequence[str],
    strategy: str,
    params: Optional[Dict[str, Any]] = None,
) -> BacktestSummary:
    if strategy not in STRATEGIES:
        raise KeyError(strategy)
    if len(close) != len(dates):
        raise ValueError("close/dates length mismatch")
    if len(close) < 5:
        raise ValueError("not enough bars")

    p = params or {}
    pos = STRATEGIES[strategy](close, **p)
    rets, eq = _run_backtest(close, pos)

    return BacktestSummary(
        symbol="",
        strategy=strategy,
        params=p,
        start_date=str(dates[0]),
        end_date=str(dates[-1]),
        bars=len(close),
        total_return=float(eq[-1] - 1.0),
        cagr=_cagr(eq),
        sharpe=_sharpe(rets),
        max_drawdown=_max_drawdown(eq),
        trades=_count_trades(pos),
        win_rate=_win_rate(rets),
    )


def store_backtest_summary(
    summary: BacktestSummary,
    *,
    symbol: str,
    repo_root: Optional[Path] = None,
) -> None:
    root = repo_root or _default_repo_root()
    conn = _connect(root)
    _ensure_schema(conn)
    params_json = json.dumps(summary.params or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    conn.execute(
        """
        INSERT OR REPLACE INTO backtest_results (
          symbol, strategy, params_json, start_date, end_date, bars,
          total_return, cagr, sharpe, max_drawdown, trades, win_rate, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            summary.strategy,
            params_json,
            summary.start_date,
            summary.end_date,
            int(summary.bars),
            float(summary.total_return),
            float(summary.cagr) if summary.cagr is not None else None,
            float(summary.sharpe) if summary.sharpe is not None else None,
            float(summary.max_drawdown),
            int(summary.trades),
            float(summary.win_rate) if summary.win_rate is not None else None,
            _utc_now_iso(),
        ),
    )
    conn.commit()
    conn.close()

