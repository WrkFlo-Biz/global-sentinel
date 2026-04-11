#!/usr/bin/env python3
"""
Global Sentinel V5.2 — Adaptive Feedback Loop

Recursive self-improvement system that learns from trade outcomes to
continuously optimize order submission decisions.

Pipeline:
1. Reads closed trade history from performance_tracker
2. Analyzes which signal combinations led to wins vs losses
3. Computes per-signal effectiveness scores
4. Adjusts signal_boost weights in real-time for next cycle
5. Logs all adjustments for auditability

This is the "brain" that gets smarter every cycle — the entire point
of collecting all the data is to feed it back into better decisions.

Safety: All adjustments are bounded. Max adjustment per cycle is ±0.03.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AdaptiveFeedbackLoop:
    """
    Learns from trade results to adjust signal weights and strategy parameters.

    Tracks which bridge signals (GSS, consciousness, narrative, microstructure,
    options, fed, OFAC, WH policy, BLS, politician alpha, AI disruption)
    correlated with winning vs losing trades, and adjusts confidence boosts.
    """

    # Max adjustment per signal per feedback cycle
    MAX_ADJUSTMENT = 0.03
    # Decay factor for old observations (exponential moving average)
    DECAY = 0.92
    # Minimum trades needed before adjustments kick in
    MIN_TRADES_FOR_LEARNING = 5

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.state_path = repo_root / "logs" / "execution" / "feedback_state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.history_path = repo_root / "logs" / "execution" / "performance_history.jsonl"
        self.adjustment_log_path = repo_root / "logs" / "execution" / "feedback_adjustments.jsonl"
        self.adjustment_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.state = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "signal_adjustments": {},
            "signal_win_counts": {},
            "signal_loss_counts": {},
            "total_trades_analyzed": 0,
            "last_analysis_time": None,
            "cumulative_pnl": 0.0,
            "daily_pnl_history": [],
            "strategy_adjustments": {
                "day_trade": {"stop_loss_tightness": 1.0, "profit_target_mult": 1.0},
                "medium_long": {"stop_loss_tightness": 1.0, "profit_target_mult": 1.0},
            },
        }

    def _save_state(self):
        self.state_path.write_text(
            json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def analyze_and_adjust(self) -> Dict[str, Any]:
        """
        Main feedback loop entry point. Called each monitoring cycle.

        1. Read recent closed trades
        2. Extract signal_boost_detail from each trade's metadata
        3. Correlate signals with win/loss
        4. Compute adjustments
        5. Return adjustment dict for use by trade_idea_packager
        """
        trades = self._read_recent_trades()
        if len(trades) < self.MIN_TRADES_FOR_LEARNING:
            return {
                "adjustments": self.state.get("signal_adjustments", {}),
                "status": "insufficient_data",
                "trades_analyzed": len(trades),
            }

        # Analyze signal effectiveness
        signal_stats = self._compute_signal_stats(trades)

        # Compute new adjustments
        new_adjustments = self._compute_adjustments(signal_stats)

        # Update state
        self.state["signal_adjustments"] = new_adjustments
        self.state["total_trades_analyzed"] = len(trades)
        self.state["last_analysis_time"] = iso_now()

        # Track daily P&L for $250/day target monitoring
        self._update_daily_pnl(trades)

        # Adjust strategy parameters based on recent performance
        self._adjust_strategy_params(trades)

        self._save_state()

        # Log the adjustment
        log_entry = {
            "timestamp_utc": iso_now(),
            "trades_analyzed": len(trades),
            "adjustments": new_adjustments,
            "signal_stats": {k: {"win_rate": v["win_rate"], "avg_pnl": v["avg_pnl"]}
                            for k, v in signal_stats.items()},
            "daily_target_progress": self._get_daily_target_progress(),
        }
        with self.adjustment_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        return {
            "adjustments": new_adjustments,
            "status": "active",
            "trades_analyzed": len(trades),
            "signal_stats": signal_stats,
            "daily_target": self._get_daily_target_progress(),
        }

    def get_signal_adjustments(self) -> Dict[str, float]:
        """Return current learned signal adjustments for the packager."""
        return self.state.get("signal_adjustments", {})

    def get_strategy_adjustments(self, strategy: str) -> Dict[str, float]:
        """Return strategy-specific learned adjustments."""
        return self.state.get("strategy_adjustments", {}).get(strategy, {})

    def _read_recent_trades(self, lookback_days: int = 14) -> List[Dict[str, Any]]:
        """Read closed trades from performance history."""
        if not self.history_path.exists():
            return []

        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        trades = []
        for line in self.history_path.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if row.get("type") == "position_snapshot":
                    continue
                if "pnl" not in row:
                    continue
                if row.get("timestamp_utc", "") >= cutoff:
                    trades.append(row)
            except Exception:
                continue
        return trades

    def _compute_signal_stats(
        self, trades: List[Dict[str, Any]]
    ) -> Dict[str, Dict[str, Any]]:
        """
        Compute per-signal win rate and average P&L.

        Reads signal_boost_detail from trade metadata to determine
        which signals were active when each trade was made.
        """
        signal_wins: Dict[str, List[float]] = {}
        signal_losses: Dict[str, List[float]] = {}

        for trade in trades:
            # Get signal boost detail from order metadata (stored when order was created)
            boost_detail = self._get_trade_signals(trade)
            pnl = trade.get("pnl", 0)
            win = trade.get("win", pnl > 0)

            for signal_name, boost_value in boost_detail.items():
                if signal_name not in signal_wins:
                    signal_wins[signal_name] = []
                    signal_losses[signal_name] = []

                if win:
                    signal_wins[signal_name].append(pnl)
                else:
                    signal_losses[signal_name].append(pnl)

        stats: Dict[str, Dict[str, Any]] = {}
        for signal_name in set(list(signal_wins.keys()) + list(signal_losses.keys())):
            wins = signal_wins.get(signal_name, [])
            losses = signal_losses.get(signal_name, [])
            total = len(wins) + len(losses)
            all_pnl = wins + losses
            stats[signal_name] = {
                "total_trades": total,
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": len(wins) / total if total > 0 else 0.5,
                "avg_pnl": sum(all_pnl) / len(all_pnl) if all_pnl else 0,
                "total_pnl": sum(all_pnl),
                "avg_win": sum(wins) / len(wins) if wins else 0,
                "avg_loss": sum(losses) / len(losses) if losses else 0,
            }

        return stats

    def _get_trade_signals(self, trade: Dict[str, Any]) -> Dict[str, float]:
        """Extract signal_boost_detail from a trade record.

        The signal detail is stored in the order metadata when the trade
        was originally submitted. If not available, infer from trade context.
        """
        # Direct from metadata (ideal path — packager stores this)
        metadata = trade.get("metadata", {})
        if isinstance(metadata, dict):
            detail = metadata.get("signal_boost_detail", {})
            if detail:
                return detail

        # Fallback: check order_metadata field
        order_meta = trade.get("order_metadata", {})
        if isinstance(order_meta, dict):
            detail = order_meta.get("signal_boost_detail", {})
            if detail:
                return detail

        # If no signal detail available, return empty (trade predates feedback system)
        return {}

    def _compute_adjustments(
        self, signal_stats: Dict[str, Dict[str, Any]]
    ) -> Dict[str, float]:
        """
        Compute adjustment multipliers for each signal based on effectiveness.

        Signals with high win rates get boosted. Signals with negative
        expected value get dampened. Bounded by MAX_ADJUSTMENT per cycle.
        """
        current = self.state.get("signal_adjustments", {})
        adjustments: Dict[str, float] = {}

        for signal_name, stats in signal_stats.items():
            if stats["total_trades"] < 3:
                # Not enough data for this signal yet
                adjustments[signal_name] = current.get(signal_name, 0.0)
                continue

            wr = stats["win_rate"]
            avg_pnl = stats["avg_pnl"]

            # Effectiveness score: combines win rate deviation from 50%
            # with P&L direction
            effectiveness = (wr - 0.5) * 2  # -1 to +1

            # P&L magnitude factor
            if avg_pnl > 0:
                pnl_factor = min(avg_pnl / 100, 0.5)  # cap at 0.5
            else:
                pnl_factor = max(avg_pnl / 100, -0.5)

            # Combined signal quality
            quality = (effectiveness * 0.6 + pnl_factor * 0.4)

            # Compute adjustment (bounded)
            raw_adj = quality * self.MAX_ADJUSTMENT
            adj = max(-self.MAX_ADJUSTMENT, min(self.MAX_ADJUSTMENT, raw_adj))

            # Exponential moving average with current adjustment
            prev = current.get(signal_name, 0.0)
            new_adj = prev * self.DECAY + adj * (1 - self.DECAY)

            # Hard bounds: no single signal adjustment exceeds ±0.15
            new_adj = max(-0.15, min(0.15, new_adj))

            adjustments[signal_name] = round(new_adj, 4)

        return adjustments

    def _update_daily_pnl(self, trades: List[Dict[str, Any]]):
        """Track daily P&L towards $250/day target."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_pnl = sum(
            t.get("pnl", 0) for t in trades
            if t.get("exit_time", "").startswith(today)
        )

        history = self.state.get("daily_pnl_history", [])
        # Update or append today's entry
        if history and history[-1].get("date") == today:
            history[-1]["pnl"] = round(today_pnl, 2)
            history[-1]["target_met"] = today_pnl >= 250
        else:
            history.append({
                "date": today,
                "pnl": round(today_pnl, 2),
                "target_met": today_pnl >= 250,
            })

        # Keep last 30 days
        self.state["daily_pnl_history"] = history[-30:]
        self.state["cumulative_pnl"] = round(sum(h["pnl"] for h in history), 2)

    def _get_daily_target_progress(self) -> Dict[str, Any]:
        """Get progress towards $250/day target."""
        history = self.state.get("daily_pnl_history", [])
        if not history:
            return {"today_pnl": 0, "target": 250, "days_met": 0, "avg_daily": 0}

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_entry = next((h for h in history if h["date"] == today), None)
        days_met = sum(1 for h in history if h.get("target_met"))
        avg_daily = sum(h["pnl"] for h in history) / len(history)

        return {
            "today_pnl": today_entry["pnl"] if today_entry else 0,
            "target": 250,
            "target_met_today": today_entry.get("target_met", False) if today_entry else False,
            "days_met": days_met,
            "total_days": len(history),
            "avg_daily_pnl": round(avg_daily, 2),
            "cumulative_pnl": self.state.get("cumulative_pnl", 0),
        }

    def _adjust_strategy_params(self, trades: List[Dict[str, Any]]):
        """
        Adjust strategy parameters (stop loss tightness, profit targets)
        based on recent trade patterns.

        If we're seeing many small losses followed by recoveries → loosen stops slightly
        If we're seeing large losses from holding too long → tighten stops
        If we're consistently hitting targets → increase targets slightly
        If we're rarely hitting targets → decrease slightly
        """
        for strategy in ["day_trade", "medium_long"]:
            strat_trades = [t for t in trades if t.get("strategy") == strategy]
            if len(strat_trades) < 3:
                continue

            params = self.state["strategy_adjustments"].get(strategy, {
                "stop_loss_tightness": 1.0,
                "profit_target_mult": 1.0,
            })

            # Analyze loss patterns
            losses = [t for t in strat_trades if not t.get("win")]
            wins = [t for t in strat_trades if t.get("win")]

            if losses:
                avg_loss_pct = sum(abs(t.get("pnl_pct", 0)) for t in losses) / len(losses)
                # If avg loss is too large, tighten stops
                if avg_loss_pct > 3.0:
                    params["stop_loss_tightness"] = min(1.5, params.get("stop_loss_tightness", 1.0) + 0.05)
                elif avg_loss_pct < 0.5:
                    # Stops might be too tight (getting stopped out on noise)
                    params["stop_loss_tightness"] = max(0.7, params.get("stop_loss_tightness", 1.0) - 0.03)

            if wins:
                avg_win_pct = sum(t.get("pnl_pct", 0) for t in wins) / len(wins)
                # If wins are consistently small, we might be taking profit too early
                if strategy == "day_trade" and avg_win_pct < 2.0:
                    params["profit_target_mult"] = min(1.5, params.get("profit_target_mult", 1.0) + 0.02)
                elif strategy == "medium_long" and avg_win_pct < 5.0:
                    params["profit_target_mult"] = min(2.0, params.get("profit_target_mult", 1.0) + 0.03)

            self.state["strategy_adjustments"][strategy] = params
