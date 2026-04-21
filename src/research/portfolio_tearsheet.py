#!/usr/bin/env python3
"""
Global Sentinel — Portfolio Tearsheet Generator

Uses QuantStats to generate daily portfolio performance reports from paper
trade results.  Produces an HTML tear sheet and posts a Telegram summary.

Runs daily at 4:30 PM ET (20:30 UTC) after market close.
Output: reports/tearsheets/daily_YYYY-MM-DD.html
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger("global_sentinel.portfolio_tearsheet")

REPO_ROOT = Path(__file__).resolve().parents[2]
PAPER_TRADES_DIR = REPO_ROOT / "reports" / "paper_trades"
TEARSHEETS_DIR = REPO_ROOT / "reports" / "tearsheets"
QUANTUM_FEED_DIR = REPO_ROOT / "data" / "quantum_feed"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_paper_trades(date_str: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load paper trade JSON files.  If date_str given, load only that date."""
    trades: List[Dict[str, Any]] = []
    if not PAPER_TRADES_DIR.exists():
        logger.warning("Paper trades directory not found: %s", PAPER_TRADES_DIR)
        return trades

    files = sorted(PAPER_TRADES_DIR.glob("*.json"))
    if date_str:
        files = [f for f in files if date_str in f.name]

    for fpath in files:
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            if isinstance(data, list):
                trades.extend(data)
            elif isinstance(data, dict):
                # Could be a single trade or a wrapper with a list inside
                if "trades" in data:
                    trades.extend(data["trades"])
                else:
                    trades.append(data)
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", fpath.name, exc)
    return trades


def _trades_to_returns(trades: List[Dict[str, Any]]) -> pd.Series:
    """Convert trade records to a daily returns series."""
    if not trades:
        return pd.Series(dtype=float)

    rows = []
    for t in trades:
        pnl = t.get("realized_pnl") or t.get("pnl") or t.get("profit") or 0.0
        ts = t.get("closed_at") or t.get("exit_time") or t.get("timestamp") or t.get("date")
        cost_basis = t.get("cost_basis") or t.get("entry_notional") or t.get("notional") or 10000
        if ts and cost_basis:
            try:
                dt = pd.Timestamp(ts)
                ret = float(pnl) / float(cost_basis) if float(cost_basis) != 0 else 0.0
                rows.append({"date": dt.normalize(), "return": ret})
            except Exception:
                continue

    if not rows:
        return pd.Series(dtype=float)

    df = pd.DataFrame(rows)
    daily = df.groupby("date")["return"].sum()
    daily.index = pd.DatetimeIndex(daily.index)
    daily = daily.sort_index()
    return daily


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(returns: pd.Series) -> Dict[str, Any]:
    """Compute key performance metrics from a returns series."""
    import quantstats as qs

    if returns.empty or len(returns) < 2:
        return {"error": "insufficient data", "n_days": len(returns)}

    sharpe = qs.stats.sharpe(returns)
    sortino = qs.stats.sortino(returns)
    max_dd = qs.stats.max_drawdown(returns)
    calmar = qs.stats.calmar(returns)

    # Win rate and profit factor
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    win_rate = len(wins) / len(returns) if len(returns) > 0 else 0.0
    gross_profit = wins.sum() if len(wins) > 0 else 0.0
    gross_loss = abs(losses.sum()) if len(losses) > 0 else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    total_return = (1 + returns).prod() - 1
    avg_daily = returns.mean()
    vol = returns.std()
    n_days = len(returns)

    return {
        "sharpe_ratio": round(float(sharpe), 4) if pd.notna(sharpe) else None,
        "sortino_ratio": round(float(sortino), 4) if pd.notna(sortino) else None,
        "max_drawdown": round(float(max_dd), 4) if pd.notna(max_dd) else None,
        "calmar_ratio": round(float(calmar), 4) if pd.notna(calmar) else None,
        "win_rate": round(float(win_rate), 4),
        "profit_factor": round(float(profit_factor), 4) if profit_factor != float("inf") else 999.99,
        "total_return": round(float(total_return), 6),
        "avg_daily_return": round(float(avg_daily), 6),
        "daily_volatility": round(float(vol), 6) if pd.notna(vol) else None,
        "n_trading_days": n_days,
    }


# ---------------------------------------------------------------------------
# HTML tear sheet
# ---------------------------------------------------------------------------

def generate_tearsheet(returns: pd.Series, output_path: Path, title: str = "Global Sentinel") -> bool:
    """Generate QuantStats HTML tear sheet."""
    import quantstats as qs

    if returns.empty or len(returns) < 2:
        logger.warning("Not enough data for tear sheet (%d days)", len(returns))
        return False

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        qs.reports.html(returns, output=str(output_path), title=title, download_filename=output_path.name)
        logger.info("Tearsheet saved to %s", output_path)
        return True
    except Exception as exc:
        logger.error("Failed to generate tearsheet: %s", exc)
        return False


# ---------------------------------------------------------------------------
# Telegram summary
# ---------------------------------------------------------------------------

def send_telegram_summary(metrics: Dict[str, Any], date_str: str) -> None:
    """Post metrics summary to Telegram research thread."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_TOPIC_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID", "")
    thread_id = os.environ.get("TELEGRAM_RESEARCH_THREAD_ID", "")

    if not bot_token or not chat_id:
        logger.warning("Telegram credentials not set, skipping notification")
        return

    lines = [
        f"📊 *Portfolio Tearsheet — {date_str}*",
        "",
        f"Sharpe: {metrics.get('sharpe_ratio', 'N/A')}",
        f"Sortino: {metrics.get('sortino_ratio', 'N/A')}",
        f"Max DD: {metrics.get('max_drawdown', 'N/A')}",
        f"Calmar: {metrics.get('calmar_ratio', 'N/A')}",
        f"Win Rate: {metrics.get('win_rate', 'N/A')}",
        f"Profit Factor: {metrics.get('profit_factor', 'N/A')}",
        f"Total Return: {metrics.get('total_return', 'N/A')}",
        f"Trading Days: {metrics.get('n_trading_days', 'N/A')}",
    ]
    text = "\n".join(lines)

    import urllib.request
    import urllib.parse
    params = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if thread_id:
        params["message_thread_id"] = thread_id
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urllib.parse.urlencode(params).encode()
    try:
        req = urllib.request.Request(url, data=data)
        urllib.request.urlopen(req, timeout=15)
        logger.info("Telegram summary sent")
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(date_str: Optional[str] = None) -> Dict[str, Any]:
    """Run tearsheet generation pipeline. Returns metrics dict."""
    if date_str is None:
        # Use today in ET
        et = timezone(timedelta(hours=-4))
        date_str = datetime.now(et).strftime("%Y-%m-%d")

    logger.info("Generating portfolio tearsheet for %s", date_str)

    # Load ALL paper trades for cumulative performance
    all_trades = _load_paper_trades()
    returns = _trades_to_returns(all_trades)

    if returns.empty:
        logger.warning("No trade data found")
        return {"error": "no_data", "date": date_str}

    metrics = compute_metrics(returns)
    metrics["date"] = date_str

    # Generate HTML tearsheet
    output_path = TEARSHEETS_DIR / f"daily_{date_str}.html"
    generated = generate_tearsheet(returns, output_path, title=f"Global Sentinel — {date_str}")
    metrics["tearsheet_path"] = str(output_path) if generated else None

    # Send Telegram summary
    send_telegram_summary(metrics, date_str)

    # Also save metrics JSON
    metrics_path = QUANTUM_FEED_DIR / "tearsheet_metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    logger.info("Metrics saved to %s", metrics_path)

    return metrics


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # Load .env
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    result = run(date_arg)
    print(json.dumps(result, indent=2, default=str))
