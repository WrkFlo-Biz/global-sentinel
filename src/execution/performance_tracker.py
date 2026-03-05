#!/usr/bin/env python3
"""
Global Sentinel V5.1 — Shadow Performance Tracker

Tracks paper trade P&L, win/loss ratio, and key metrics for graduation.
Reads from order intents + Alpaca positions to build a performance record.

Outputs:
- logs/execution/performance_history.jsonl (append-only trade records)
- reports/weekly/shadow_performance.json (summary stats)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PerformanceTracker:
    """Track shadow trading performance for graduation metrics."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.history_path = repo_root / "logs" / "execution" / "performance_history.jsonl"
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.summary_path = repo_root / "reports" / "weekly" / "shadow_performance.json"
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)

    def record_closed_trade(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        exit_price: float,
        qty: float,
        entry_time: str,
        exit_time: str,
        reason: str = "",
        strategy: str = "regime_playbook",
    ):
        """Record a completed (closed) trade."""
        if side == "buy" or side == "long":
            pnl = (exit_price - entry_price) * qty
            pnl_pct = (exit_price - entry_price) / entry_price * 100
        else:
            pnl = (entry_price - exit_price) * qty
            pnl_pct = (entry_price - exit_price) / entry_price * 100

        record = {
            "timestamp_utc": iso_now(),
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "entry_time": entry_time,
            "exit_time": exit_time,
            "win": pnl > 0,
            "reason": reason,
            "strategy": strategy,
        }

        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        return record

    def snapshot_open_positions(self, positions: List[Dict[str, Any]]):
        """Record current open position P&L snapshot for daily tracking."""
        snapshot = {
            "timestamp_utc": iso_now(),
            "type": "position_snapshot",
            "positions": [],
            "total_unrealized_pnl": 0,
        }
        for p in positions:
            upl = p.get("unrealized_pl", 0)
            snapshot["positions"].append({
                "symbol": p.get("symbol"),
                "qty": p.get("qty"),
                "avg_entry": p.get("avg_entry_price"),
                "current_price": p.get("current_price"),
                "unrealized_pl": upl,
            })
            snapshot["total_unrealized_pnl"] += upl

        snapshot["total_unrealized_pnl"] = round(snapshot["total_unrealized_pnl"], 2)

        with self.history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")

        return snapshot

    def generate_summary(self) -> Dict[str, Any]:
        """Generate performance summary from trade history."""
        if not self.history_path.exists():
            return {"error": "no performance history"}

        trades = []
        snapshots = []
        for line in self.history_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if row.get("type") == "position_snapshot":
                    snapshots.append(row)
                elif "pnl" in row:
                    trades.append(row)
            except Exception:
                continue

        if not trades and not snapshots:
            return {"total_trades": 0, "note": "no completed trades yet"}

        total_trades = len(trades)
        wins = sum(1 for t in trades if t.get("win"))
        losses = total_trades - wins
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        avg_pnl = total_pnl / total_trades if total_trades > 0 else 0

        win_pnls = [t["pnl"] for t in trades if t.get("win")]
        loss_pnls = [t["pnl"] for t in trades if not t.get("win")]
        avg_win = sum(win_pnls) / len(win_pnls) if win_pnls else 0
        avg_loss = sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0

        # By symbol
        by_symbol: Dict[str, Dict] = {}
        for t in trades:
            sym = t.get("symbol", "?")
            if sym not in by_symbol:
                by_symbol[sym] = {"trades": 0, "wins": 0, "pnl": 0}
            by_symbol[sym]["trades"] += 1
            by_symbol[sym]["pnl"] += t.get("pnl", 0)
            if t.get("win"):
                by_symbol[sym]["wins"] += 1

        # Latest snapshot
        latest_snapshot = snapshots[-1] if snapshots else None

        summary = {
            "timestamp_utc": iso_now(),
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / total_trades, 3) if total_trades > 0 else 0,
            "total_pnl": round(total_pnl, 2),
            "avg_pnl_per_trade": round(avg_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "profit_factor": round(abs(sum(win_pnls) / sum(loss_pnls)), 2) if loss_pnls and sum(loss_pnls) != 0 else None,
            "by_symbol": {k: {**v, "pnl": round(v["pnl"], 2)} for k, v in sorted(by_symbol.items(), key=lambda x: x[1]["pnl"], reverse=True)},
            "open_positions_snapshot": latest_snapshot,
            "total_snapshots": len(snapshots),
        }

        self.summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return summary
