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

from src.execution.strategy_learning import (
    default_feedback_state,
    infer_strategy_family,
    normalize_feedback_state,
)


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
    # Concept drift guardrails
    CONCEPT_DRIFT_TRIGGER_SCORE = 0.58
    CONCEPT_DRIFT_CRITICAL_SCORE = 0.75
    MAX_DRIFT_DAMPING = 0.60

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
                raw = json.loads(self.state_path.read_text(encoding="utf-8"))
                return normalize_feedback_state(raw)
            except Exception:
                pass
        return default_feedback_state()

    def _save_state(self):
        self.state = normalize_feedback_state(self.state)
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
                "strategy_confidence_adjustments": self.state.get("strategy_confidence_adjustments", {}),
                "strategy_adjustments": self.state.get("strategy_adjustments", {}),
                "status": "insufficient_data",
                "trades_analyzed": len(trades),
            }

        # Analyze signal effectiveness
        signal_stats = self._compute_signal_stats(trades)
        drift_profile = self._compute_concept_drift_profile(trades)

        # Compute new adjustments
        new_adjustments = self._compute_adjustments(signal_stats, drift_profile=drift_profile)

        # Update state
        self.state["signal_adjustments"] = new_adjustments
        self.state["total_trades_analyzed"] = len(trades)
        self.state["last_analysis_time"] = iso_now()

        # Track daily P&L for $250/day target monitoring
        self._update_daily_pnl(trades)

        # Adjust strategy parameters based on recent performance
        self._adjust_strategy_params(trades)
        drift_application = self._apply_drift_down_weighting(drift_profile)
        self.state["concept_drift"] = {
            **drift_profile,
            "application": drift_application,
            "updated_at": iso_now(),
        }

        self._save_state()

        # Log the adjustment
        log_entry = {
            "timestamp_utc": iso_now(),
            "trades_analyzed": len(trades),
            "adjustments": new_adjustments,
            "strategy_confidence_adjustments": self.state.get("strategy_confidence_adjustments", {}),
            "strategy_adjustments": self.state.get("strategy_adjustments", {}),
            "signal_stats": {k: {"win_rate": v["win_rate"], "avg_pnl": v["avg_pnl"]}
                            for k, v in signal_stats.items()},
            "concept_drift": self.state.get("concept_drift"),
            "daily_target_progress": self._get_daily_target_progress(),
        }
        with self.adjustment_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

        return {
            "adjustments": new_adjustments,
            "strategy_confidence_adjustments": self.state.get("strategy_confidence_adjustments", {}),
            "strategy_adjustments": self.state.get("strategy_adjustments", {}),
            "status": "active",
            "trades_analyzed": len(trades),
            "signal_stats": signal_stats,
            "strategy_stats": self.state.get("strategy_group_stats", {}),
            "concept_drift": self.state.get("concept_drift"),
            "daily_target": self._get_daily_target_progress(),
        }

    def get_signal_adjustments(self) -> Dict[str, float]:
        """Return current learned signal adjustments for the packager."""
        return self.state.get("signal_adjustments", {})

    def get_strategy_adjustments(self, strategy: str) -> Dict[str, float]:
        """Return strategy-specific learned adjustments."""
        adjustments = self.state.get("strategy_adjustments", {}) or {}
        if strategy in adjustments:
            return adjustments.get(strategy, {})
        family = infer_strategy_family({"strategy": strategy}, default_family=strategy)
        if family in adjustments:
            return adjustments.get(family, {})
        return {}

    def get_strategy_confidence_adjustments(self) -> Dict[str, float]:
        """Return learned confidence nudges for exact strategies and families."""
        return self.state.get("strategy_confidence_adjustments", {})

    @staticmethod
    def _safe_float(value: Any, default: float | None = None) -> float | None:
        try:
            if value is None:
                return default
            return float(value)
        except Exception:
            return default

    @staticmethod
    def _trade_quality_metrics(trade: Dict[str, Any]) -> Dict[str, float | None]:
        metadata = trade.get("metadata", {})
        order_meta = trade.get("order_metadata", {})

        def lookup(*keys: str) -> float | None:
            for key in keys:
                if isinstance(trade, dict) and trade.get(key) is not None:
                    return AdaptiveFeedbackLoop._safe_float(trade.get(key), None)
                if isinstance(metadata, dict) and metadata.get(key) is not None:
                    return AdaptiveFeedbackLoop._safe_float(metadata.get(key), None)
                if isinstance(order_meta, dict) and order_meta.get(key) is not None:
                    return AdaptiveFeedbackLoop._safe_float(order_meta.get(key), None)
            return None

        mfe_pct = lookup("mfe_pct")
        mae_pct = lookup("mae_pct")
        mfe_bps = lookup("max_favorable_excursion_bps", "mfe_bps")
        mae_bps = lookup("max_adverse_excursion_bps", "mae_bps")
        if mfe_bps is None and mfe_pct is not None:
            mfe_bps = abs(mfe_pct) * 100.0
        if mae_bps is None and mae_pct is not None:
            mae_bps = abs(mae_pct) * 100.0

        realized_return_bps = lookup("realized_return_bps")
        if realized_return_bps is None:
            pnl_pct = lookup("pnl_pct")
            if pnl_pct is not None:
                realized_return_bps = pnl_pct * 100.0

        fill_quality_score = lookup("fill_quality_score")
        fill_rate = lookup("fill_rate")
        realized_slippage_bps = lookup("realized_slippage_bps", "fill_slippage_bps")
        if fill_quality_score is None:
            slippage_component = 1.0
            if realized_slippage_bps is not None:
                slippage_component = 1.0 - min(max(realized_slippage_bps, 0.0) / 60.0, 0.8)
            fill_component = fill_rate if fill_rate is not None else 0.7
            fill_quality_score = max(0.0, min(1.0, (slippage_component * 0.55) + (fill_component * 0.45)))

        time_to_edge_minutes = lookup("time_to_edge_minutes")
        time_to_edge_score = lookup("time_to_edge_score")
        if time_to_edge_score is None and time_to_edge_minutes is not None:
            time_to_edge_score = max(0.0, min(1.0, 1.0 - min(time_to_edge_minutes / 240.0, 1.0)))

        edge_capture_ratio = lookup("realized_edge_capture_ratio")
        if edge_capture_ratio is None and realized_return_bps is not None and mfe_bps is not None and mfe_bps > 0:
            edge_capture_ratio = max(-1.0, min(2.0, realized_return_bps / mfe_bps))

        adverse_excursion_ratio = lookup("adverse_excursion_ratio")
        if adverse_excursion_ratio is None and mae_bps is not None and mfe_bps is not None and mfe_bps > 0:
            adverse_excursion_ratio = max(0.0, min(4.0, mae_bps / mfe_bps))

        edge_decay_score = lookup("edge_decay_score")
        if edge_decay_score is None:
            capture_penalty = 0.5
            if edge_capture_ratio is not None:
                capture_penalty = 1.0 - min(max(edge_capture_ratio, 0.0), 1.0)
            time_penalty = 0.5 if time_to_edge_score is None else (1.0 - time_to_edge_score)
            fill_penalty = 0.5 if fill_quality_score is None else (1.0 - fill_quality_score)
            adverse_penalty = 0.5
            if adverse_excursion_ratio is not None:
                adverse_penalty = min(adverse_excursion_ratio / 1.5, 1.0)
            edge_decay_score = max(
                0.0,
                min(
                    1.0,
                    (capture_penalty * 0.4)
                    + (time_penalty * 0.2)
                    + (fill_penalty * 0.2)
                    + (adverse_penalty * 0.2),
                ),
            )

        edge_decay_weight = lookup("edge_decay_weight")
        if edge_decay_weight is None and edge_decay_score is not None:
            edge_decay_weight = max(0.2, min(1.0, 1.0 - (edge_decay_score * 0.7)))

        return {
            "mfe_bps": mfe_bps,
            "mae_bps": mae_bps,
            "realized_return_bps": realized_return_bps,
            "fill_quality_score": fill_quality_score,
            "fill_rate": fill_rate,
            "realized_slippage_bps": realized_slippage_bps,
            "time_to_edge_minutes": time_to_edge_minutes,
            "time_to_edge_score": time_to_edge_score,
            "realized_edge_capture_ratio": edge_capture_ratio,
            "adverse_excursion_ratio": adverse_excursion_ratio,
            "edge_decay_score": edge_decay_score,
            "edge_decay_weight": edge_decay_weight,
        }

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

    def _get_trade_strategy_name(self, trade: Dict[str, Any]) -> str:
        """Extract the most specific strategy label available for a trade."""
        metadata = trade.get("metadata", {})
        order_meta = trade.get("order_metadata", {})
        candidates = (
            metadata.get("strategy_name"),
            order_meta.get("strategy_name"),
            trade.get("strategy"),
            metadata.get("strategy"),
            order_meta.get("strategy"),
            trade.get("strategy_name"),
        )
        for candidate in candidates:
            normalized = str(candidate or "").strip()
            if normalized:
                return normalized
        return ""

    @staticmethod
    def _summarize_trade_bucket(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Summarize win/loss and P&L statistics for a strategy bucket."""
        if not trades:
            return {
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.5,
                "avg_pnl": 0.0,
                "total_pnl": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
            }

        wins = [t for t in trades if t.get("win", t.get("pnl", 0) > 0)]
        losses = [t for t in trades if not t.get("win", t.get("pnl", 0) > 0)]
        all_pnl = [float(t.get("pnl", 0) or 0) for t in trades]
        win_pnls = [float(t.get("pnl", 0) or 0) for t in wins]
        loss_pnls = [float(t.get("pnl", 0) or 0) for t in losses]

        quality_rows = [AdaptiveFeedbackLoop._trade_quality_metrics(t) for t in trades]

        def _avg(key: str) -> float | None:
            vals = [float(row[key]) for row in quality_rows if row.get(key) is not None]
            if not vals:
                return None
            return sum(vals) / len(vals)

        return {
            "total_trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(trades) if trades else 0.5,
            "avg_pnl": sum(all_pnl) / len(all_pnl) if all_pnl else 0.0,
            "total_pnl": sum(all_pnl),
            "avg_win": sum(win_pnls) / len(win_pnls) if win_pnls else 0.0,
            "avg_loss": sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0.0,
            "avg_fill_quality_score": _avg("fill_quality_score"),
            "avg_fill_rate": _avg("fill_rate"),
            "avg_slippage_bps": _avg("realized_slippage_bps"),
            "avg_time_to_edge_minutes": _avg("time_to_edge_minutes"),
            "avg_time_to_edge_score": _avg("time_to_edge_score"),
            "avg_mfe_bps": _avg("mfe_bps"),
            "avg_mae_bps": _avg("mae_bps"),
            "avg_edge_capture_ratio": _avg("realized_edge_capture_ratio"),
            "avg_adverse_excursion_ratio": _avg("adverse_excursion_ratio"),
            "avg_edge_decay_score": _avg("edge_decay_score"),
            "avg_edge_decay_weight": _avg("edge_decay_weight"),
            "decaying_edge_ratio": (
                sum(1 for row in quality_rows if (row.get("edge_decay_score") or 0.0) >= 0.55) / len(quality_rows)
                if quality_rows else 0.0
            ),
        }

    @staticmethod
    def _group_trades_by_strategy(trades: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Group closed trades by exact strategy name and inferred family."""
        groups: Dict[str, List[Dict[str, Any]]] = {}
        for trade in trades:
            exact_strategy = ""
            metadata = trade.get("metadata", {})
            order_meta = trade.get("order_metadata", {})
            for candidate in (
                metadata.get("strategy_name"),
                order_meta.get("strategy_name"),
                trade.get("strategy"),
                metadata.get("strategy"),
                order_meta.get("strategy"),
                trade.get("strategy_name"),
            ):
                normalized = str(candidate or "").strip()
                if normalized:
                    exact_strategy = normalized
                    break
            family = infer_strategy_family(trade)
            keys: List[str] = []
            for key in (exact_strategy, family):
                normalized = str(key or "").strip()
                if normalized and normalized not in keys:
                    keys.append(normalized)
            for key in keys:
                groups.setdefault(key, []).append(trade)
        return groups

    def _compute_strategy_confidence_adjustment(
        self,
        strategy_name: str,
        stats: Dict[str, Any],
    ) -> float:
        """Convert strategy stats into a bounded confidence nudge."""
        current = self.state.get("strategy_confidence_adjustments", {}) or {}
        prev = float(current.get(strategy_name, 0.0) or 0.0)
        wr = float(stats.get("win_rate", 0.5) or 0.5)
        avg_pnl = float(stats.get("avg_pnl", 0.0) or 0.0)
        avg_fill_quality = float(stats.get("avg_fill_quality_score", 0.5) or 0.5)
        avg_capture = float(stats.get("avg_edge_capture_ratio", 0.0) or 0.0)
        avg_decay = float(stats.get("avg_edge_decay_score", 0.5) or 0.5)
        decaying_edge_ratio = float(stats.get("decaying_edge_ratio", 0.0) or 0.0)
        avg_time_score = float(stats.get("avg_time_to_edge_score", 0.5) or 0.5)

        effectiveness = (wr - 0.5) * 2.0
        if avg_pnl > 0:
            pnl_factor = min(avg_pnl / 100.0, 0.5)
        else:
            pnl_factor = max(avg_pnl / 100.0, -0.5)

        edge_quality = (
            (min(max(avg_capture, 0.0), 1.0) * 0.4)
            + (min(max(avg_fill_quality, 0.0), 1.0) * 0.25)
            + (min(max(avg_time_score, 0.0), 1.0) * 0.15)
            - (min(max(avg_decay, 0.0), 1.0) * 0.2)
            - (min(max(decaying_edge_ratio, 0.0), 1.0) * 0.2)
        )

        quality = (effectiveness * 0.45) + (pnl_factor * 0.25) + (edge_quality * 0.30)
        raw_adj = quality * self.MAX_ADJUSTMENT
        adj = max(-self.MAX_ADJUSTMENT, min(self.MAX_ADJUSTMENT, raw_adj))
        new_adj = prev * self.DECAY + adj * (1 - self.DECAY)
        return round(max(-0.15, min(0.15, new_adj)), 4)

    def _update_strategy_controls(
        self,
        strategy_name: str,
        trades: List[Dict[str, Any]],
        stats: Dict[str, Any],
        family: Optional[str],
    ) -> Dict[str, float]:
        """Update stop-loss and profit-target controls for a strategy bucket."""
        current = self.state.get("strategy_adjustments", {}) or {}
        params = dict(current.get(strategy_name) or {})
        if not params and family and family in current and family != strategy_name:
            params = dict(current.get(family) or {})
        if not params:
            params = {"stop_loss_tightness": 1.0, "profit_target_mult": 1.0}

        losses = [t for t in trades if not t.get("win", t.get("pnl", 0) > 0)]
        wins = [t for t in trades if t.get("win", t.get("pnl", 0) > 0)]
        avg_decay = float(stats.get("avg_edge_decay_score", 0.5) or 0.5)
        avg_fill_quality = float(stats.get("avg_fill_quality_score", 0.5) or 0.5)
        avg_time_to_edge = float(stats.get("avg_time_to_edge_minutes", 0.0) or 0.0)

        if losses:
            avg_loss_pct = sum(abs(float(t.get("pnl_pct", 0) or 0)) for t in losses) / len(losses)
            if avg_loss_pct > 3.0:
                params["stop_loss_tightness"] = min(1.5, float(params.get("stop_loss_tightness", 1.0)) + 0.05)
            elif avg_loss_pct < 0.5:
                params["stop_loss_tightness"] = max(0.7, float(params.get("stop_loss_tightness", 1.0)) - 0.03)

        if wins:
            avg_win_pct = sum(float(t.get("pnl_pct", 0) or 0) for t in wins) / len(wins)
            family_label = family or infer_strategy_family({"strategy": strategy_name}, default_family="day_trade") or "day_trade"
            if family_label == "day_trade" and avg_win_pct < 2.0:
                params["profit_target_mult"] = min(1.5, float(params.get("profit_target_mult", 1.0)) + 0.02)
            elif family_label == "medium_long" and avg_win_pct < 5.0:
                params["profit_target_mult"] = min(2.0, float(params.get("profit_target_mult", 1.0)) + 0.03)
            elif family_label not in {"day_trade", "medium_long"} and avg_win_pct < 3.0:
                params["profit_target_mult"] = min(1.7, float(params.get("profit_target_mult", 1.0)) + 0.02)

        if avg_decay >= 0.6:
            params["profit_target_mult"] = max(0.85, float(params.get("profit_target_mult", 1.0)) - 0.04)
            params["stop_loss_tightness"] = min(1.5, float(params.get("stop_loss_tightness", 1.0)) + 0.04)
        if avg_fill_quality < 0.45:
            params["profit_target_mult"] = max(0.85, float(params.get("profit_target_mult", 1.0)) - 0.03)
        if avg_time_to_edge > 180.0:
            params["profit_target_mult"] = max(0.85, float(params.get("profit_target_mult", 1.0)) - 0.02)

        params["stop_loss_tightness"] = round(max(0.7, min(1.5, float(params.get("stop_loss_tightness", 1.0)))), 4)
        params["profit_target_mult"] = round(max(0.5, min(2.0, float(params.get("profit_target_mult", 1.0)))), 4)
        return params

    def _compute_adjustments(
        self,
        signal_stats: Dict[str, Dict[str, Any]],
        drift_profile: Optional[Dict[str, Any]] = None,
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

            # Concept drift automatically dampens confidence adjustments toward zero.
            if drift_profile and drift_profile.get("triggered"):
                damp = float(drift_profile.get("down_weighting_multiplier", 1.0) or 1.0)
                damp = max(1.0 - self.MAX_DRIFT_DAMPING, min(1.0, damp))
                new_adj *= damp

            adjustments[signal_name] = round(new_adj, 4)

        return adjustments

    def _compute_concept_drift_profile(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        quality_rows = [self._trade_quality_metrics(trade) for trade in trades]
        empty_thresholds = {
            "max_average_edge_decay_score": 0.55,
            "max_decaying_edge_ratio": 0.45,
            "min_average_fill_quality_score": 0.50,
            "min_average_time_to_edge_score": 0.45,
            "concept_drift_trigger_score": self.CONCEPT_DRIFT_TRIGGER_SCORE,
            "concept_drift_critical_score": self.CONCEPT_DRIFT_CRITICAL_SCORE,
        }
        if not quality_rows:
            return {
                "triggered": False,
                "critical": False,
                "severity": "normal",
                "concept_drift_score": 0.0,
                "drift_score": 0.0,
                "down_weighting_multiplier": 1.0,
                "thresholds": empty_thresholds,
                "signals": [],
                "signal_values": {},
                "breaches": [],
                "breach_count": 0,
            }

        def avg(key: str, default: float) -> float:
            vals = [float(row[key]) for row in quality_rows if row.get(key) is not None]
            if not vals:
                return default
            return sum(vals) / len(vals)

        avg_edge_decay = avg("edge_decay_score", 0.5)
        decaying_edge_ratio = (
            sum(1 for row in quality_rows if (row.get("edge_decay_score") or 0.0) >= 0.55) / len(quality_rows)
        )
        avg_fill_quality = avg("fill_quality_score", 0.7)
        avg_time_to_edge_score = avg("time_to_edge_score", 0.5)

        thresholds = {
            "max_average_edge_decay_score": 0.55,
            "max_decaying_edge_ratio": 0.45,
            "min_average_fill_quality_score": 0.50,
            "min_average_time_to_edge_score": 0.45,
            "concept_drift_trigger_score": self.CONCEPT_DRIFT_TRIGGER_SCORE,
            "concept_drift_critical_score": self.CONCEPT_DRIFT_CRITICAL_SCORE,
        }

        signal_values = {
            "avg_edge_decay_score": round(avg_edge_decay, 4),
            "decaying_edge_ratio": round(decaying_edge_ratio, 4),
            "avg_fill_quality_score": round(avg_fill_quality, 4),
            "avg_time_to_edge_score": round(avg_time_to_edge_score, 4),
        }

        breaches: List[str] = []
        if avg_edge_decay >= thresholds["max_average_edge_decay_score"]:
            breaches.append("avg_edge_decay_score")
        if decaying_edge_ratio >= thresholds["max_decaying_edge_ratio"]:
            breaches.append("decaying_edge_ratio")
        if avg_fill_quality <= thresholds["min_average_fill_quality_score"]:
            breaches.append("avg_fill_quality_score")
        if avg_time_to_edge_score <= thresholds["min_average_time_to_edge_score"]:
            breaches.append("avg_time_to_edge_score")

        drift_score = (
            (avg_edge_decay * 0.40)
            + (decaying_edge_ratio * 0.30)
            + ((1.0 - max(0.0, min(avg_fill_quality, 1.0))) * 0.20)
            + ((1.0 - max(0.0, min(avg_time_to_edge_score, 1.0))) * 0.10)
        )
        drift_score = max(0.0, min(1.0, drift_score))
        triggered = drift_score >= thresholds["concept_drift_trigger_score"] or len(breaches) >= 2
        critical = drift_score >= thresholds["concept_drift_critical_score"]
        if critical:
            severity = "critical"
        elif triggered:
            severity = "elevated"
        elif len(breaches) == 1:
            severity = "watch"
        else:
            severity = "normal"

        if triggered:
            damp = 1.0 - min(self.MAX_DRIFT_DAMPING, drift_score * 0.75)
            down_weighting_multiplier = max(1.0 - self.MAX_DRIFT_DAMPING, min(1.0, damp))
        else:
            down_weighting_multiplier = 1.0

        signals = [
            {
                "name": "avg_edge_decay_score",
                "value": signal_values["avg_edge_decay_score"],
                "threshold": thresholds["max_average_edge_decay_score"],
                "comparison": "<=",
                "breached": "avg_edge_decay_score" in breaches,
            },
            {
                "name": "decaying_edge_ratio",
                "value": signal_values["decaying_edge_ratio"],
                "threshold": thresholds["max_decaying_edge_ratio"],
                "comparison": "<=",
                "breached": "decaying_edge_ratio" in breaches,
            },
            {
                "name": "avg_fill_quality_score",
                "value": signal_values["avg_fill_quality_score"],
                "threshold": thresholds["min_average_fill_quality_score"],
                "comparison": ">=",
                "breached": "avg_fill_quality_score" in breaches,
            },
            {
                "name": "avg_time_to_edge_score",
                "value": signal_values["avg_time_to_edge_score"],
                "threshold": thresholds["min_average_time_to_edge_score"],
                "comparison": ">=",
                "breached": "avg_time_to_edge_score" in breaches,
            },
            {
                "name": "concept_drift_score",
                "value": round(drift_score, 4),
                "threshold": thresholds["concept_drift_trigger_score"],
                "comparison": "<=",
                "breached": drift_score >= thresholds["concept_drift_trigger_score"],
            },
        ]

        return {
            "triggered": bool(triggered),
            "critical": bool(critical),
            "severity": severity,
            "concept_drift_score": round(drift_score, 4),
            "drift_score": round(drift_score, 4),
            "down_weighting_multiplier": round(down_weighting_multiplier, 4),
            "thresholds": thresholds,
            "signals": signals,
            "signal_values": signal_values,
            "breaches": breaches,
            "breach_count": len(breaches),
            "sample_size": len(quality_rows),
        }

    def _apply_drift_down_weighting(self, drift_profile: Dict[str, Any]) -> Dict[str, Any]:
        if not drift_profile.get("triggered"):
            return {"applied": False, "reason": "drift_not_triggered"}

        damp = float(drift_profile.get("down_weighting_multiplier", 1.0) or 1.0)
        damp = max(1.0 - self.MAX_DRIFT_DAMPING, min(1.0, damp))

        strategy_conf = dict(self.state.get("strategy_confidence_adjustments", {}) or {})
        adjusted_strategy_conf = 0
        for key, value in list(strategy_conf.items()):
            strategy_conf[key] = round(float(value or 0.0) * damp, 4)
            adjusted_strategy_conf += 1
        self.state["strategy_confidence_adjustments"] = strategy_conf

        strategy_adjustments = dict(self.state.get("strategy_adjustments", {}) or {})
        adjusted_strategy_controls = 0
        risk_increment = (1.0 - damp) * 0.20
        for key, params in list(strategy_adjustments.items()):
            if not isinstance(params, dict):
                continue
            updated = dict(params)
            updated["profit_target_mult"] = round(
                max(0.5, min(2.0, float(updated.get("profit_target_mult", 1.0)) * damp)),
                4,
            )
            updated["stop_loss_tightness"] = round(
                max(0.7, min(1.5, float(updated.get("stop_loss_tightness", 1.0)) + risk_increment)),
                4,
            )
            strategy_adjustments[key] = updated
            adjusted_strategy_controls += 1
        self.state["strategy_adjustments"] = strategy_adjustments

        return {
            "applied": True,
            "severity": drift_profile.get("severity"),
            "down_weighting_multiplier": round(damp, 4),
            "strategy_confidence_adjustments_updated": adjusted_strategy_conf,
            "strategy_control_profiles_updated": adjusted_strategy_controls,
        }

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
        grouped_trades = self._group_trades_by_strategy(trades)
        current_strategy_adjustments = dict(self.state.get("strategy_adjustments", {}) or {})
        current_confidence_adjustments = dict(self.state.get("strategy_confidence_adjustments", {}) or {})
        strategy_stats: Dict[str, Dict[str, Any]] = {}

        for strategy_name, strat_trades in grouped_trades.items():
            if len(strat_trades) < 3:
                continue

            stats = self._summarize_trade_bucket(strat_trades)
            family = infer_strategy_family(strat_trades[0])
            strategy_stats[strategy_name] = {
                **stats,
                "strategy_family": family,
                "sample_size": len(strat_trades),
            }

            params = self._update_strategy_controls(strategy_name, strat_trades, stats, family)
            current_strategy_adjustments[strategy_name] = params
            current_confidence_adjustments[strategy_name] = self._compute_strategy_confidence_adjustment(
                strategy_name,
                stats,
            )

        self.state["strategy_adjustments"] = current_strategy_adjustments
        self.state["strategy_confidence_adjustments"] = current_confidence_adjustments
        self.state["strategy_group_stats"] = strategy_stats
