#!/usr/bin/env python3
"""
Auto-Research Engine — Autonomous trading strategy discovery system.

Runs daily at 2 AM ET (06:00 UTC). Discovers patterns, mines alpha factors,
evolves strategies via genetic algorithm, finds cross-asset relationships,
and mines social signals.

Output: data/quantum_feed/auto_research_report.json + individual module outputs.
"""

import os
import sys
import json
import time
import random
import logging
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict, field

import numpy as np
import pandas as pd
from scipy import stats

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

REPO_ROOT = Path(os.environ.get("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
QF_DIR = REPO_ROOT / "data" / "quantum_feed"

TRACKED_SYMBOLS = [
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "AMD", "INTC", "AVGO",
    # Semis / AI
    "MU", "MRVL", "QCOM", "ARM", "SMCI",
    # Financials
    "JPM", "GS", "BAC", "MS", "V",
    # Energy
    "XOM", "CVX", "OXY", "SLB",
    # Healthcare
    "UNH", "JNJ", "LLY", "PFE",
    # Consumer / Industrial
    "WMT", "COST", "HD", "CAT", "BA",
    # ETFs / Indices
    "SPY", "QQQ", "IWM", "XLE", "XLF", "XLK", "GLD", "TLT", "DIA", "ARKK",
]

BACKTEST_SYMBOLS = ["SPY", "QQQ", "NVDA", "TSLA", "AMD"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("auto_researcher")

# ---------------------------------------------------------------------------
# Alpaca data helpers
# ---------------------------------------------------------------------------

def _alpaca_client():
    """Return StockHistoricalDataClient (no keys needed for market data)."""
    from alpaca.data.historical import StockHistoricalDataClient
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
    return StockHistoricalDataClient(api_key, secret_key)


def fetch_daily_bars(symbols: List[str], days: int = 180) -> pd.DataFrame:
    """Fetch daily OHLCV bars for *symbols* over *days*.  Returns multi-index DF."""
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    client = _alpaca_client()
    end = datetime.now(timezone.utc) - timedelta(days=1)
    start = end - timedelta(days=days + 10)  # extra buffer for weekends

    # Fetch in batches of 10 to avoid timeouts
    all_frames = []
    for i in range(0, len(symbols), 10):
        batch = symbols[i:i+10]
        log.info("Fetching bars for %s", batch)
        try:
            req = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=start,
                end=end,
                limit=None,
            )
            bars = client.get_stock_bars(req)
            df = bars.df
            if not df.empty:
                all_frames.append(df)
        except Exception as e:
            log.warning("Failed to fetch bars for %s: %s", batch, e)
        time.sleep(0.5)

    if not all_frames:
        raise RuntimeError("No bar data fetched")

    combined = pd.concat(all_frames)
    return combined


def bars_to_close_df(bars_df: pd.DataFrame) -> pd.DataFrame:
    """Convert multi-index bars DF to pivot table of close prices (date x symbol)."""
    df = bars_df.reset_index()
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    pivot = df.pivot_table(index="date", columns="symbol", values="close", aggfunc="last")
    pivot = pivot.sort_index()
    return pivot


def bars_to_volume_df(bars_df: pd.DataFrame) -> pd.DataFrame:
    df = bars_df.reset_index()
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    pivot = df.pivot_table(index="date", columns="symbol", values="volume", aggfunc="sum")
    pivot = pivot.sort_index()
    return pivot


def bars_to_ohlcv(bars_df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """Return dict[symbol] -> DataFrame with columns [open, high, low, close, volume]."""
    df = bars_df.reset_index()
    df["date"] = pd.to_datetime(df["timestamp"]).dt.date
    result = {}
    for sym, g in df.groupby("symbol"):
        g2 = g.set_index("date")[["open", "high", "low", "close", "volume"]].sort_index()
        g2 = g2[~g2.index.duplicated(keep="last")]
        result[sym] = g2
    return result


# ---------------------------------------------------------------------------
# Module 1: Pattern Discovery
# ---------------------------------------------------------------------------

def discover_patterns(close_df: pd.DataFrame, volume_df: pd.DataFrame,
                      ohlcv: Dict[str, pd.DataFrame]) -> List[Dict]:
    log.info("=== Module 1: Pattern Discovery ===")
    patterns = []

    returns_df = close_df.pct_change().dropna()

    # --- Day-of-week effects ---
    log.info("Scanning day-of-week effects...")
    dow_idx = pd.to_datetime(returns_df.index)
    for sym in returns_df.columns:
        ret = returns_df[sym].dropna()
        dates = pd.to_datetime(ret.index)
        for dow in range(5):  # Mon=0 .. Fri=4
            day_name = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"][dow]
            mask = dates.weekday == dow
            day_rets = ret.values[mask]
            if len(day_rets) < 20:
                continue
            win_rate = float(np.mean(day_rets > 0))
            avg_ret = float(np.mean(day_rets))
            t_stat, p_val = stats.ttest_1samp(day_rets, 0)
            if p_val < 0.05 and win_rate > 0.55 and len(day_rets) >= 20:
                patterns.append({
                    "type": "day_of_week",
                    "symbol": sym,
                    "description": f"{sym} up on {day_name}s {win_rate:.0%} of time (avg {avg_ret:+.3%})",
                    "win_rate": round(win_rate, 4),
                    "expected_return": round(avg_ret, 6),
                    "sample_size": int(len(day_rets)),
                    "p_value": round(float(p_val), 6),
                    "params": {"day_of_week": dow, "day_name": day_name},
                })

    # --- Gap-and-go ---
    log.info("Scanning gap-and-go patterns...")
    for sym, df_sym in ohlcv.items():
        if len(df_sym) < 30:
            continue
        prev_close = df_sym["close"].shift(1)
        gap_pct = (df_sym["open"] - prev_close) / prev_close
        next_day_ret = df_sym["close"].pct_change().shift(-1)

        for gap_thresh in [0.01, 0.02, 0.03]:
            # Gap up → continuation
            mask_up = gap_pct > gap_thresh
            cont_rets = next_day_ret[mask_up].dropna()
            if len(cont_rets) >= 20:
                wr = float(np.mean(cont_rets > 0))
                avg = float(np.mean(cont_rets))
                _, pv = stats.ttest_1samp(cont_rets.values, 0) if len(cont_rets) > 1 else (0, 1)
                if pv < 0.05 and wr > 0.55:
                    patterns.append({
                        "type": "gap_and_go",
                        "symbol": sym,
                        "description": f"{sym}: gap >{gap_thresh:.0%} → next day {wr:.0%} continuation (avg {avg:+.3%})",
                        "win_rate": round(wr, 4),
                        "expected_return": round(avg, 6),
                        "sample_size": int(len(cont_rets)),
                        "p_value": round(float(pv), 6),
                        "params": {"gap_threshold": gap_thresh, "direction": "up"},
                    })
            # Gap down → reversal
            mask_dn = gap_pct < -gap_thresh
            rev_rets = next_day_ret[mask_dn].dropna()
            if len(rev_rets) >= 20:
                wr = float(np.mean(rev_rets > 0))
                avg = float(np.mean(rev_rets))
                _, pv = stats.ttest_1samp(rev_rets.values, 0) if len(rev_rets) > 1 else (0, 1)
                if pv < 0.05 and wr > 0.55:
                    patterns.append({
                        "type": "gap_reversal",
                        "symbol": sym,
                        "description": f"{sym}: gap <-{gap_thresh:.0%} → next day bounce {wr:.0%} (avg {avg:+.3%})",
                        "win_rate": round(wr, 4),
                        "expected_return": round(avg, 6),
                        "sample_size": int(len(rev_rets)),
                        "p_value": round(float(pv), 6),
                        "params": {"gap_threshold": gap_thresh, "direction": "down"},
                    })

    # --- Mean reversion windows ---
    log.info("Scanning mean reversion patterns...")
    for sym in returns_df.columns:
        ret = returns_df[sym].dropna()
        for n_days in [3, 4, 5]:
            # N consecutive up days → next day
            rolling_sign = ret.rolling(n_days).apply(lambda x: int(all(x > 0)), raw=True)
            mask = rolling_sign == 1
            next_ret = ret.shift(-1)
            after_streak = next_ret[mask].dropna()
            if len(after_streak) >= 20:
                wr_down = float(np.mean(after_streak < 0))
                avg = float(np.mean(after_streak))
                _, pv = stats.ttest_1samp(after_streak.values, 0) if len(after_streak) > 1 else (0, 1)
                if pv < 0.05 and wr_down > 0.55:
                    patterns.append({
                        "type": "mean_reversion",
                        "symbol": sym,
                        "description": f"{sym}: after {n_days} up days → reversal {wr_down:.0%} (avg {avg:+.3%})",
                        "win_rate": round(wr_down, 4),
                        "expected_return": round(avg, 6),
                        "sample_size": int(len(after_streak)),
                        "p_value": round(float(pv), 6),
                        "params": {"consecutive_days": n_days, "streak_direction": "up"},
                    })

            # N consecutive down days
            rolling_sign_dn = ret.rolling(n_days).apply(lambda x: int(all(x < 0)), raw=True)
            mask_dn = rolling_sign_dn == 1
            after_streak_dn = next_ret[mask_dn].dropna()
            if len(after_streak_dn) >= 20:
                wr_up = float(np.mean(after_streak_dn > 0))
                avg = float(np.mean(after_streak_dn))
                _, pv = stats.ttest_1samp(after_streak_dn.values, 0) if len(after_streak_dn) > 1 else (0, 1)
                if pv < 0.05 and wr_up > 0.55:
                    patterns.append({
                        "type": "mean_reversion",
                        "symbol": sym,
                        "description": f"{sym}: after {n_days} down days → bounce {wr_up:.0%} (avg {avg:+.3%})",
                        "win_rate": round(wr_up, 4),
                        "expected_return": round(avg, 6),
                        "sample_size": int(len(after_streak_dn)),
                        "p_value": round(float(pv), 6),
                        "params": {"consecutive_days": n_days, "streak_direction": "down"},
                    })

    # --- Volume spike follow-through ---
    log.info("Scanning volume spike follow-through...")
    for sym in returns_df.columns:
        if sym not in volume_df.columns:
            continue
        ret = returns_df[sym].dropna()
        vol = volume_df[sym].reindex(ret.index).dropna()
        common = ret.index.intersection(vol.index)
        ret = ret.loc[common]
        vol = vol.loc[common]
        vol_avg20 = vol.rolling(20).mean()
        vol_ratio = vol / vol_avg20
        spike_mask = vol_ratio > 2.0  # volume > 2x 20d avg
        next_ret = ret.shift(-1)
        spike_next = next_ret[spike_mask].dropna()
        if len(spike_next) >= 20:
            wr = float(np.mean(spike_next > 0))
            avg = float(np.mean(spike_next))
            _, pv = stats.ttest_1samp(spike_next.values, 0) if len(spike_next) > 1 else (0, 1)
            if pv < 0.05 and (wr > 0.55 or (1 - wr) > 0.55):
                actual_wr = max(wr, 1 - wr)
                direction = "up" if wr > 0.55 else "down"
                patterns.append({
                    "type": "volume_spike_followthrough",
                    "symbol": sym,
                    "description": f"{sym}: volume spike (>2x avg) → next day {direction} {actual_wr:.0%} (avg {avg:+.3%})",
                    "win_rate": round(actual_wr, 4),
                    "expected_return": round(avg, 6),
                    "sample_size": int(len(spike_next)),
                    "p_value": round(float(pv), 6),
                    "params": {"volume_multiplier": 2.0},
                })

    # --- Cross-asset lead-lag (pairs) ---
    log.info("Scanning cross-asset lead-lag...")
    sector_pairs = [
        ("XLE", "OXY"), ("XLE", "XOM"), ("XLE", "CVX"), ("XLE", "SLB"),
        ("XLK", "AAPL"), ("XLK", "MSFT"), ("XLK", "NVDA"),
        ("XLF", "JPM"), ("XLF", "GS"), ("XLF", "BAC"),
        ("SPY", "QQQ"), ("SPY", "IWM"), ("QQQ", "ARKK"),
        ("NVDA", "AMD"), ("NVDA", "SMCI"), ("NVDA", "MU"),
    ]
    for leader, follower in sector_pairs:
        if leader not in returns_df.columns or follower not in returns_df.columns:
            continue
        lead_ret = returns_df[leader].dropna()
        follow_ret = returns_df[follower].shift(-1).dropna()
        common = lead_ret.index.intersection(follow_ret.index)
        if len(common) < 30:
            continue
        lr = lead_ret.loc[common].values
        fr = follow_ret.loc[common].values
        # direction agreement
        same_dir = np.mean(np.sign(lr) == np.sign(fr))
        corr, pv = stats.pearsonr(lr, fr)
        if abs(corr) > 0.05 and pv < 0.05 and same_dir > 0.55:
            patterns.append({
                "type": "lead_lag",
                "symbol": f"{leader}->{follower}",
                "description": f"{leader} today predicts {follower} tomorrow (corr={corr:.3f}, same direction {same_dir:.0%})",
                "win_rate": round(same_dir, 4),
                "expected_return": round(float(corr), 6),
                "sample_size": int(len(common)),
                "p_value": round(float(pv), 6),
                "params": {"leader": leader, "follower": follower, "lag": 1},
            })

    # Sort by win_rate descending
    patterns.sort(key=lambda p: p["win_rate"], reverse=True)
    log.info("Discovered %d statistically significant patterns", len(patterns))
    return patterns


# ---------------------------------------------------------------------------
# Module 2: Alpha Factor Mining
# ---------------------------------------------------------------------------

def _compute_features(close_df: pd.DataFrame, volume_df: pd.DataFrame) -> pd.DataFrame:
    """Compute a panel of alpha features. Returns DataFrame with MultiIndex (date, symbol)."""
    returns_1d = close_df.pct_change()
    returns_5d = close_df.pct_change(5)
    returns_20d = close_df.pct_change(20)
    vol_5d = returns_1d.rolling(5).std()
    vol_20d = returns_1d.rolling(20).std()

    # RSI (14-day)
    delta = close_df.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    # MACD (12, 26, 9)
    ema12 = close_df.ewm(span=12).mean()
    ema26 = close_df.ewm(span=26).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9).mean()
    macd_hist = macd - macd_signal

    # Bollinger position
    sma20 = close_df.rolling(20).mean()
    std20 = close_df.rolling(20).std()
    boll_pos = (close_df - sma20) / std20.replace(0, np.nan)

    # Volume features
    vol_ratio = volume_df / volume_df.rolling(20).mean()

    # SPY beta (rolling 20d)
    spy_ret = returns_1d.get("SPY", pd.Series(dtype=float))

    features_dict = {
        "ret_1d": returns_1d,
        "ret_5d": returns_5d,
        "ret_20d": returns_20d,
        "vol_5d": vol_5d,
        "vol_20d": vol_20d,
        "rsi": rsi,
        "macd_hist": macd_hist,
        "boll_pos": boll_pos,
        "vol_ratio": vol_ratio,
    }

    # Stack to long format
    frames = []
    for name, df in features_dict.items():
        stacked = df.stack()
        stacked.name = name
        frames.append(stacked)

    panel = pd.concat(frames, axis=1)
    panel.index.names = ["date", "symbol"]
    return panel


def mine_alpha_factors(close_df: pd.DataFrame, volume_df: pd.DataFrame) -> List[Dict]:
    log.info("=== Module 2: Alpha Factor Mining ===")

    panel = _compute_features(close_df, volume_df)
    returns_1d = close_df.pct_change()

    # Forward return (next day)
    fwd_ret = returns_1d.shift(-1).stack()
    fwd_ret.name = "fwd_ret"
    fwd_ret.index.names = ["date", "symbol"]

    merged = panel.join(fwd_ret, how="inner").dropna()

    feature_cols = [c for c in merged.columns if c != "fwd_ret"]
    ic_results = []

    log.info("Computing IC for %d features across %d rows...", len(feature_cols), len(merged))

    for feat in feature_cols:
        # Cross-sectional IC: for each date, rank-correlation of feature vs fwd return
        ics = []
        for dt in merged.index.get_level_values("date").unique():
            try:
                slice_ = merged.loc[dt]
                if len(slice_) < 5:
                    continue
                ic, _ = stats.spearmanr(slice_[feat], slice_["fwd_ret"])
                if not np.isnan(ic):
                    ics.append(ic)
            except Exception:
                continue
        if len(ics) < 20:
            continue
        mean_ic = float(np.mean(ics))
        ic_ir = float(np.mean(ics) / (np.std(ics) + 1e-9))
        t_stat, p_val = stats.ttest_1samp(ics, 0)

        ic_results.append({
            "factor": feat,
            "mean_ic": round(mean_ic, 6),
            "ic_ir": round(ic_ir, 4),
            "p_value": round(float(p_val), 6),
            "sample_days": len(ics),
        })

    # Also test composite factors (pairs)
    log.info("Testing composite factors (pairs)...")
    for i in range(len(feature_cols)):
        for j in range(i + 1, len(feature_cols)):
            f1, f2 = feature_cols[i], feature_cols[j]
            combo_name = f"{f1}_x_{f2}"
            # Z-score both, then multiply
            z1 = (merged[f1] - merged[f1].mean()) / (merged[f1].std() + 1e-9)
            z2 = (merged[f2] - merged[f2].mean()) / (merged[f2].std() + 1e-9)
            combo = z1 * z2

            ics = []
            for dt in merged.index.get_level_values("date").unique():
                try:
                    idx = merged.index.get_level_values("date") == dt
                    c_slice = combo[idx]
                    r_slice = merged["fwd_ret"][idx]
                    if len(c_slice) < 5:
                        continue
                    ic, _ = stats.spearmanr(c_slice, r_slice)
                    if not np.isnan(ic):
                        ics.append(ic)
                except Exception:
                    continue
            if len(ics) < 20:
                continue
            mean_ic = float(np.mean(ics))
            ic_ir = float(np.mean(ics) / (np.std(ics) + 1e-9))
            t_stat, p_val = stats.ttest_1samp(ics, 0)
            ic_results.append({
                "factor": combo_name,
                "mean_ic": round(mean_ic, 6),
                "ic_ir": round(ic_ir, 4),
                "p_value": round(float(p_val), 6),
                "sample_days": len(ics),
            })

    # Rank by |IC| and filter
    ic_results.sort(key=lambda x: abs(x["mean_ic"]), reverse=True)
    top_factors = [f for f in ic_results if abs(f["mean_ic"]) > 0.03][:20]

    log.info("Found %d alpha factors with |IC| > 0.03 (from %d tested)", len(top_factors), len(ic_results))
    return top_factors


# ---------------------------------------------------------------------------
# Module 3: Strategy Evolution (Genetic Algorithm)
# ---------------------------------------------------------------------------

@dataclass
class StrategyGene:
    rsi_entry: float = 30.0
    rsi_exit: float = 70.0
    momentum_window: int = 5
    momentum_threshold: float = 0.01
    volume_condition: float = 1.2  # vol ratio threshold
    stop_loss: float = 0.02
    take_profit: float = 0.04
    time_limit: int = 5  # max holding days
    position_pct: float = 0.10  # % of equity per trade

    def mutate(self):
        gene = StrategyGene(**{k: v for k, v in asdict(self).items()})
        field_name = random.choice(list(asdict(gene).keys()))
        val = getattr(gene, field_name)
        if isinstance(val, float):
            setattr(gene, field_name, max(0.001, val * random.uniform(0.7, 1.3)))
        elif isinstance(val, int):
            setattr(gene, field_name, max(1, val + random.randint(-2, 2)))
        # Clamp
        gene.rsi_entry = max(10, min(50, gene.rsi_entry))
        gene.rsi_exit = max(50, min(90, gene.rsi_exit))
        gene.momentum_window = max(1, min(20, gene.momentum_window))
        gene.stop_loss = max(0.005, min(0.10, gene.stop_loss))
        gene.take_profit = max(0.01, min(0.20, gene.take_profit))
        gene.time_limit = max(1, min(20, gene.time_limit))
        gene.position_pct = max(0.02, min(0.25, gene.position_pct))
        return gene

    @staticmethod
    def crossover(a: "StrategyGene", b: "StrategyGene") -> "StrategyGene":
        child = {}
        for k in asdict(a).keys():
            child[k] = getattr(a, k) if random.random() < 0.5 else getattr(b, k)
        return StrategyGene(**child)

    @staticmethod
    def random_gene() -> "StrategyGene":
        return StrategyGene(
            rsi_entry=random.uniform(15, 45),
            rsi_exit=random.uniform(55, 85),
            momentum_window=random.randint(2, 15),
            momentum_threshold=random.uniform(0.005, 0.05),
            volume_condition=random.uniform(0.8, 2.5),
            stop_loss=random.uniform(0.005, 0.08),
            take_profit=random.uniform(0.01, 0.15),
            time_limit=random.randint(1, 15),
            position_pct=random.uniform(0.03, 0.20),
        )

    def describe(self) -> str:
        return (
            f"RSI<{self.rsi_entry:.0f} entry, RSI>{self.rsi_exit:.0f} exit, "
            f"mom({self.momentum_window}d)>{self.momentum_threshold:.2%}, "
            f"vol>{self.volume_condition:.1f}x, SL={self.stop_loss:.1%}, "
            f"TP={self.take_profit:.1%}, hold<={self.time_limit}d, "
            f"size={self.position_pct:.0%}"
        )


def _backtest_strategy(gene: StrategyGene, ohlcv: Dict[str, pd.DataFrame],
                       symbols: List[str]) -> Dict:
    """Simple vectorized backtest. Returns performance dict."""
    all_trades = []
    equity = 100_000.0

    for sym in symbols:
        if sym not in ohlcv:
            continue
        df = ohlcv[sym].copy()
        if len(df) < 90:
            continue
        df = df.tail(90)

        # Compute indicators
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(14).mean()
        loss = (-delta.clip(upper=0)).rolling(14).mean()
        rs = gain / loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))
        df["momentum"] = df["close"].pct_change(gene.momentum_window)
        df["vol_ratio"] = df["volume"] / df["volume"].rolling(20).mean()

        df = df.dropna()
        if len(df) < 20:
            continue

        # Simulate trades
        in_trade = False
        entry_price = 0
        entry_idx = 0

        for i in range(len(df)):
            row = df.iloc[i]

            if not in_trade:
                # Entry: RSI < threshold AND momentum > threshold AND volume condition
                if (row["rsi"] < gene.rsi_entry and
                    row["momentum"] > gene.momentum_threshold and
                    row["vol_ratio"] > gene.volume_condition):
                    in_trade = True
                    entry_price = row["close"]
                    entry_idx = i
            else:
                # Check exit conditions
                ret = (row["close"] - entry_price) / entry_price
                holding_days = i - entry_idx

                exit_reason = None
                if ret <= -gene.stop_loss:
                    exit_reason = "stop_loss"
                elif ret >= gene.take_profit:
                    exit_reason = "take_profit"
                elif row["rsi"] > gene.rsi_exit:
                    exit_reason = "rsi_exit"
                elif holding_days >= gene.time_limit:
                    exit_reason = "time_limit"

                if exit_reason:
                    all_trades.append({
                        "symbol": sym,
                        "return": ret,
                        "holding_days": holding_days,
                        "exit_reason": exit_reason,
                    })
                    in_trade = False

    if not all_trades:
        return {"sharpe": -10, "num_trades": 0, "win_rate": 0, "avg_return": 0, "fitness": -10}

    trade_rets = [t["return"] for t in all_trades]
    n = len(trade_rets)
    avg_ret = float(np.mean(trade_rets))
    std_ret = float(np.std(trade_rets)) if n > 1 else 1.0
    sharpe = (avg_ret / (std_ret + 1e-9)) * np.sqrt(252 / max(1, np.mean([t["holding_days"] for t in all_trades])))
    win_rate = float(np.mean([r > 0 for r in trade_rets]))
    fitness = sharpe * np.sqrt(n)  # reward activity

    return {
        "sharpe": round(sharpe, 4),
        "num_trades": n,
        "win_rate": round(win_rate, 4),
        "avg_return": round(avg_ret, 6),
        "max_return": round(float(max(trade_rets)), 6),
        "min_return": round(float(min(trade_rets)), 6),
        "fitness": round(fitness, 4),
    }


def evolve_strategies(ohlcv: Dict[str, pd.DataFrame]) -> List[Dict]:
    log.info("=== Module 3: Strategy Evolution (Genetic Algorithm) ===")
    pop_size = 50
    generations = 20
    elite_size = 10
    offspring_size = 30
    mutant_size = 10

    # Initialize population
    population = [StrategyGene.random_gene() for _ in range(pop_size)]

    best_ever_fitness = -999
    best_ever = None

    for gen in range(generations):
        # Evaluate
        results = []
        for gene in population:
            perf = _backtest_strategy(gene, ohlcv, BACKTEST_SYMBOLS)
            results.append((gene, perf))

        # Sort by fitness
        results.sort(key=lambda x: x[1]["fitness"], reverse=True)

        if results[0][1]["fitness"] > best_ever_fitness:
            best_ever_fitness = results[0][1]["fitness"]
            best_ever = results[0]

        if gen % 5 == 0:
            log.info("Gen %d: best fitness=%.3f, sharpe=%.3f, trades=%d",
                     gen, results[0][1]["fitness"], results[0][1]["sharpe"],
                     results[0][1]["num_trades"])

        # Selection: top elite survive
        elites = [r[0] for r in results[:elite_size]]

        # Crossover
        offspring = []
        for _ in range(offspring_size):
            p1, p2 = random.sample(elites, 2)
            child = StrategyGene.crossover(p1, p2)
            if random.random() < 0.3:
                child = child.mutate()
            offspring.append(child)

        # Mutations (random new)
        mutants = [StrategyGene.random_gene() for _ in range(mutant_size)]

        population = elites + offspring + mutants

    # Final evaluation
    final_results = []
    for gene in population:
        perf = _backtest_strategy(gene, ohlcv, BACKTEST_SYMBOLS)
        final_results.append((gene, perf))

    final_results.sort(key=lambda x: x[1]["fitness"], reverse=True)

    # Top 5
    top5 = []
    for gene, perf in final_results[:5]:
        top5.append({
            "description": gene.describe(),
            "parameters": asdict(gene),
            "backtest": perf,
            "symbols_tested": BACKTEST_SYMBOLS,
        })

    log.info("Evolution complete. Best fitness=%.3f, sharpe=%.3f",
             final_results[0][1]["fitness"], final_results[0][1]["sharpe"])
    return top5


# ---------------------------------------------------------------------------
# Module 4: Cross-Asset Relationship Discovery
# ---------------------------------------------------------------------------

def discover_cross_asset(close_df: pd.DataFrame) -> Dict:
    log.info("=== Module 4: Cross-Asset Relationship Discovery ===")
    returns_df = close_df.pct_change().dropna()

    symbols = list(returns_df.columns)
    n = len(symbols)

    # Rolling 30d correlation
    recent_30d = returns_df.tail(30)
    corr_30d = recent_30d.corr()

    # 90d average correlation
    corr_90d = returns_df.tail(90).corr()

    # Correlation breaks
    corr_diff = (corr_30d - corr_90d).abs()
    breaks = []
    for i in range(n):
        for j in range(i + 1, n):
            s1, s2 = symbols[i], symbols[j]
            diff = corr_diff.loc[s1, s2] if s1 in corr_diff.index and s2 in corr_diff.columns else 0
            if diff > 0.3:
                breaks.append({
                    "pair": f"{s1}/{s2}",
                    "corr_30d": round(float(corr_30d.loc[s1, s2]), 4),
                    "corr_90d": round(float(corr_90d.loc[s1, s2]), 4),
                    "change": round(float(diff), 4),
                    "interpretation": (
                        "Decorrelation" if corr_30d.loc[s1, s2] < corr_90d.loc[s1, s2]
                        else "Convergence"
                    ),
                })

    breaks.sort(key=lambda x: x["change"], reverse=True)

    # Lead-lag relationships
    lead_lags = []
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            s1, s2 = symbols[i], symbols[j]
            # Does s1 today predict s2 tomorrow?
            r1 = returns_df[s1]
            r2 = returns_df[s2].shift(-1)
            common = r1.dropna().index.intersection(r2.dropna().index)
            if len(common) < 30:
                continue
            ic, pv = stats.spearmanr(r1.loc[common].values, r2.loc[common].values)
            if abs(ic) > 0.05 and pv < 0.05:
                lead_lags.append({
                    "leader": s1,
                    "follower": s2,
                    "ic": round(float(ic), 4),
                    "p_value": round(float(pv), 6),
                    "sample_size": int(len(common)),
                })

    lead_lags.sort(key=lambda x: abs(x["ic"]), reverse=True)

    log.info("Found %d correlation breaks, %d lead-lag pairs", len(breaks), len(lead_lags))
    return {
        "correlation_breaks": breaks[:30],
        "lead_lag_pairs": lead_lags[:30],
        "analysis_date": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Module 5: Social Signal Mining
# ---------------------------------------------------------------------------

def mine_social_signals() -> Dict:
    log.info("=== Module 5: Social Signal Mining ===")

    signals = {
        "reddit_signals": [],
        "social_signals": [],
        "news_signals": [],
        "combined_score": {},
        "analysis_date": datetime.now(timezone.utc).isoformat(),
    }

    # --- Reddit trending ---
    reddit_path = QF_DIR / "reddit_trending.json"
    if reddit_path.exists():
        try:
            with open(reddit_path) as f:
                reddit = json.load(f)
            stocks = reddit.get("data", {}).get("all_stocks", [])
            for item in stocks[:30]:
                ticker = item.get("ticker", "")
                mentions = item.get("mentions_24h", 0)
                momentum = item.get("momentum", "")
                change = item.get("mention_change", 0)
                signals["reddit_signals"].append({
                    "ticker": ticker,
                    "mentions_24h": mentions,
                    "mention_change": change,
                    "momentum": momentum,
                    "source": "reddit",
                })
                # Aggregate
                if ticker not in signals["combined_score"]:
                    signals["combined_score"][ticker] = {"mention_score": 0, "sentiment_score": 0, "sources": []}
                signals["combined_score"][ticker]["mention_score"] += mentions
                signals["combined_score"][ticker]["sources"].append("reddit")
            log.info("Parsed %d reddit signals", len(signals["reddit_signals"]))
        except Exception as e:
            log.warning("Failed to parse reddit_trending.json: %s", e)

    # --- Social trending ---
    social_path = QF_DIR / "social_trending.json"
    if social_path.exists():
        try:
            with open(social_path) as f:
                social = json.load(f)
            trending = social.get("hot_trending", [])
            for item in trending[:30]:
                ticker = item.get("ticker", "")
                mentions = item.get("mentions", 0)
                sentiment = item.get("sentiment", "neutral")
                signals["social_signals"].append({
                    "ticker": ticker,
                    "mentions": mentions,
                    "sentiment": sentiment,
                    "source": "stocktwits",
                })
                if ticker not in signals["combined_score"]:
                    signals["combined_score"][ticker] = {"mention_score": 0, "sentiment_score": 0, "sources": []}
                signals["combined_score"][ticker]["mention_score"] += mentions
                sent_val = {"bullish": 1, "bearish": -1, "neutral": 0}.get(sentiment, 0)
                signals["combined_score"][ticker]["sentiment_score"] += sent_val
                signals["combined_score"][ticker]["sources"].append("stocktwits")
            log.info("Parsed %d social signals", len(signals["social_signals"]))
        except Exception as e:
            log.warning("Failed to parse social_trending.json: %s", e)

    # --- News impact ---
    news_path = QF_DIR / "news_impact.json"
    if news_path.exists():
        try:
            with open(news_path) as f:
                news = json.load(f)
            for item in news.get("bullish_tickers", []):
                ticker = item.get("ticker", "")
                impact = item.get("avg_impact_score", 0)
                signals["news_signals"].append({
                    "ticker": ticker,
                    "impact": impact,
                    "direction": "bullish",
                    "source": "news",
                })
                if ticker not in signals["combined_score"]:
                    signals["combined_score"][ticker] = {"mention_score": 0, "sentiment_score": 0, "sources": []}
                signals["combined_score"][ticker]["sentiment_score"] += impact
                signals["combined_score"][ticker]["sources"].append("news_bullish")

            for item in news.get("bearish_tickers", []):
                ticker = item.get("ticker", "")
                impact = item.get("avg_impact_score", 0)
                signals["news_signals"].append({
                    "ticker": ticker,
                    "impact": impact,
                    "direction": "bearish",
                    "source": "news",
                })
                if ticker not in signals["combined_score"]:
                    signals["combined_score"][ticker] = {"mention_score": 0, "sentiment_score": 0, "sources": []}
                signals["combined_score"][ticker]["sentiment_score"] += impact
                signals["combined_score"][ticker]["sources"].append("news_bearish")
            log.info("Parsed %d news signals", len(signals["news_signals"]))
        except Exception as e:
            log.warning("Failed to parse news_impact.json: %s", e)

    # Sort combined by mention_score
    sorted_combined = sorted(
        signals["combined_score"].items(),
        key=lambda x: x[1]["mention_score"],
        reverse=True,
    )
    signals["top_social_tickers"] = [
        {"ticker": k, **v} for k, v in sorted_combined[:20]
    ]

    return signals


# ---------------------------------------------------------------------------
# Output & Integration
# ---------------------------------------------------------------------------

def _html_escape(text: str) -> str:
    """Escape text for Telegram HTML, preserving intentional tags."""
    import re
    # Temporarily replace our intentional tags
    safe_tags = {}
    for tag in re.findall(r'</?b>', text):
        key = f"__SAFE_{len(safe_tags)}__"
        safe_tags[key] = tag
        text = text.replace(tag, key, 1)
    # Escape remaining < and >
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Restore intentional tags
    for key, tag in safe_tags.items():
        text = text.replace(key, tag)
    return text


def _send_telegram(message: str):
    """Send Telegram notification."""
    import requests
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "7091381625")
    if not bot_token:
        log.warning("TELEGRAM_BOT_TOKEN not set, skipping notification")
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML", "message_thread_id": 74,
        }, timeout=15)
        if resp.status_code != 200:
            log.warning("Telegram send failed: %s", resp.text[:200])
    except Exception as e:
        log.warning("Telegram error: %s", e)


def compile_and_output(patterns, alpha_factors, evolved_strategies,
                       cross_asset, social_signals):
    log.info("=== Compiling output ===")
    QF_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).isoformat()

    # --- Main report ---
    report = {
        "research_timestamp": timestamp,
        "next_run": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        "discovered_patterns": patterns,
        "alpha_factors": alpha_factors,
        "evolved_strategies": evolved_strategies,
        "correlation_breaks": cross_asset.get("correlation_breaks", []),
        "lead_lag_pairs": cross_asset.get("lead_lag_pairs", []),
        "social_signals": social_signals.get("top_social_tickers", []),
        "summary": {
            "num_patterns": len(patterns),
            "num_alpha_factors": len(alpha_factors),
            "num_evolved_strategies": len(evolved_strategies),
            "num_correlation_breaks": len(cross_asset.get("correlation_breaks", [])),
            "num_lead_lag_pairs": len(cross_asset.get("lead_lag_pairs", [])),
            "num_social_signals": len(social_signals.get("top_social_tickers", [])),
        },
    }

    report_path = QF_DIR / "auto_research_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    log.info("Wrote %s", report_path)

    # --- Individual module outputs ---
    with open(QF_DIR / "auto_alpha_factors.json", "w") as f:
        json.dump({"timestamp": timestamp, "factors": alpha_factors}, f, indent=2)

    with open(QF_DIR / "evolved_strategies.json", "w") as f:
        json.dump({"timestamp": timestamp, "strategies": evolved_strategies}, f, indent=2)

    with open(QF_DIR / "cross_asset_research.json", "w") as f:
        json.dump(cross_asset, f, indent=2, default=str)

    with open(QF_DIR / "social_alpha.json", "w") as f:
        json.dump(social_signals, f, indent=2, default=str)

    # --- Update strategy_correlation_weights.json ---
    weights_path = QF_DIR / "strategy_correlation_weights.json"
    try:
        existing = {}
        if weights_path.exists():
            with open(weights_path) as f:
                existing = json.load(f)
        # Add research-driven adjustments
        existing["auto_research_update"] = timestamp
        if evolved_strategies:
            best = evolved_strategies[0]
            existing["best_evolved_sharpe"] = best["backtest"]["sharpe"]
            existing["best_evolved_win_rate"] = best["backtest"]["win_rate"]
        with open(weights_path, "w") as f:
            json.dump(existing, f, indent=2)
        log.info("Updated %s", weights_path)
    except Exception as e:
        log.warning("Failed to update strategy_correlation_weights: %s", e)

    # --- Update signal_quality_weights.json ---
    sq_path = QF_DIR / "signal_quality_weights.json"
    try:
        existing = {}
        if sq_path.exists():
            with open(sq_path) as f:
                existing = json.load(f)
        existing["auto_research_update"] = timestamp
        if alpha_factors:
            existing["top_alpha_factor"] = alpha_factors[0]["factor"]
            existing["top_alpha_ic"] = alpha_factors[0]["mean_ic"]
        with open(sq_path, "w") as f:
            json.dump(existing, f, indent=2)
        log.info("Updated %s", sq_path)
    except Exception as e:
        log.warning("Failed to update signal_quality_weights: %s", e)

    # --- Append to research_history.jsonl ---
    history_path = QF_DIR / "research_history.jsonl"
    summary_line = {
        "timestamp": timestamp,
        "num_patterns": len(patterns),
        "num_alpha_factors": len(alpha_factors),
        "num_evolved_strategies": len(evolved_strategies),
        "num_correlation_breaks": len(cross_asset.get("correlation_breaks", [])),
        "num_lead_lag_pairs": len(cross_asset.get("lead_lag_pairs", [])),
        "best_pattern": patterns[0]["description"] if patterns else "none",
        "best_alpha_ic": alpha_factors[0]["mean_ic"] if alpha_factors else 0,
        "best_strategy_sharpe": evolved_strategies[0]["backtest"]["sharpe"] if evolved_strategies else 0,
    }
    with open(history_path, "a") as f:
        f.write(json.dumps(summary_line, default=str) + "\n")
    log.info("Appended to %s", history_path)

    # --- Telegram summary ---
    best_finding = "N/A"
    if patterns:
        best_finding = _html_escape(patterns[0]["description"])
    elif alpha_factors:
        best_finding = _html_escape(f"Alpha factor '{alpha_factors[0]['factor']}' IC={alpha_factors[0]['mean_ic']:.4f}")
    elif evolved_strategies:
        best_finding = _html_escape(f"Evolved strategy: sharpe={evolved_strategies[0]['backtest']['sharpe']:.2f}")

    best_strategy_info = ""
    if evolved_strategies:
        bs = evolved_strategies[0]
        desc_escaped = _html_escape(bs['description'])
        best_strategy_info = (
            f"\n\n<b>Best Evolved Strategy:</b>\n"
            f"{desc_escaped}\n"
            f"Sharpe: {bs['backtest']['sharpe']:.2f} | "
            f"Win: {bs['backtest']['win_rate']:.0%} | "
            f"Trades: {bs['backtest']['num_trades']}"
        )

    tg_msg = (
        f"<b>AUTO-RESEARCH COMPLETE</b>\n\n"
        f"Patterns: {len(patterns)}\n"
        f"Alpha factors: {len(alpha_factors)}\n"
        f"Evolved strategies: {len(evolved_strategies)}\n"
        f"Correlation breaks: {len(cross_asset.get('correlation_breaks', []))}\n"
        f"Lead-lag pairs: {len(cross_asset.get('lead_lag_pairs', []))}\n"
        f"Social signals: {len(social_signals.get('top_social_tickers', []))}\n\n"
        f"<b>Top finding:</b> {best_finding}"
        f"{best_strategy_info}"
    )
    _send_telegram(tg_msg)

    return report


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run():
    """Execute the full auto-research pipeline."""
    t0 = time.time()
    log.info("Auto-Research Engine starting...")

    # Fetch data
    log.info("Fetching 180 days of daily bars for %d symbols...", len(TRACKED_SYMBOLS))
    bars_df = fetch_daily_bars(TRACKED_SYMBOLS, days=180)
    close_df = bars_to_close_df(bars_df)
    volume_df = bars_to_volume_df(bars_df)
    ohlcv = bars_to_ohlcv(bars_df)
    log.info("Data loaded: %d dates x %d symbols", len(close_df), len(close_df.columns))

    # Module 1: Pattern Discovery
    patterns = discover_patterns(close_df, volume_df, ohlcv)

    # Module 2: Alpha Factor Mining
    alpha_factors = mine_alpha_factors(close_df, volume_df)

    # Module 3: Strategy Evolution
    evolved_strategies = evolve_strategies(ohlcv)

    # Module 4: Cross-Asset Relationships
    cross_asset = discover_cross_asset(close_df)

    # Module 5: Social Signal Mining
    social_signals = mine_social_signals()

    # Compile & output
    report = compile_and_output(patterns, alpha_factors, evolved_strategies,
                                cross_asset, social_signals)

    elapsed = time.time() - t0
    log.info("Auto-Research Engine complete in %.1f seconds", elapsed)
    log.info("Summary: %s", json.dumps(report["summary"], indent=2))
    return report


if __name__ == "__main__":
    run()
