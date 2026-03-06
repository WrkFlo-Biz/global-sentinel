#!/usr/bin/env python3
"""
Global Sentinel — Strategy Manager

Routes trade ideas to the correct strategy (day_trade vs medium_long),
respects auto/manual execution modes, and coordinates with bot assignments.

Each strategy has its own:
- Holding period and profit/stop targets
- Bot assignment (mo2darkbot = day_trade, mo2drkbot = medium_long)
- Execution mode (auto = submit immediately, manual = send for approval)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml
except ImportError:
    yaml = None


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StrategyManager:
    """Manages dual trading strategies and execution mode routing."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.config = self._load_config()
        self.strategies = self.config.get("strategies", {})
        self.execution_modes = self.config.get("execution_mode", {})
        self.notifications = self.config.get("notifications", {})
        self.bot_permissions = self.config.get("bot_permissions", {})
        self.log_dir = repo_root / "logs" / "execution"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> Dict[str, Any]:
        path = self.repo_root / "config" / "execution_mode.yaml"
        if not path.exists():
            return {}
        if yaml is None:
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def get_strategy_config(self, strategy_name: str) -> Dict[str, Any]:
        return self.strategies.get(strategy_name, {})

    def get_execution_mode(self, strategy_name: str) -> str:
        """Returns 'auto' or 'manual' for the given strategy."""
        return self.execution_modes.get(strategy_name, "manual")

    def is_auto_mode(self, strategy_name: str) -> bool:
        return self.get_execution_mode(strategy_name) == "auto"

    def get_bot_for_strategy(self, strategy_name: str) -> str:
        return self.strategies.get(strategy_name, {}).get("bot", "mo2darkbot")

    def classify_trade_idea(self, idea: Dict[str, Any]) -> str:
        """Classify a trade idea into day_trade or medium_long strategy."""
        holding = idea.get("holding_period", "day")
        if holding in ("swing", "medium", "long", "macro"):
            return "medium_long"
        return "day_trade"

    def split_ideas_by_strategy(
        self, ideas: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Split trade ideas into day_trade and medium_long buckets."""
        result = {"day_trade": [], "medium_long": []}
        for idea in ideas:
            strategy = self.classify_trade_idea(idea)
            result[strategy].append(idea)
        return result

    def should_submit_order(self, strategy_name: str) -> bool:
        """Check if orders should be auto-submitted for this strategy."""
        return self.is_auto_mode(strategy_name)

    def should_send_for_approval(self, strategy_name: str) -> bool:
        """Check if orders should be sent to Telegram for approval."""
        return not self.is_auto_mode(strategy_name)

    def build_order_summary(
        self,
        candidates: List[Dict[str, Any]],
        strategy_name: str,
        scorecard: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build a summary for Telegram notification (both auto and manual modes)."""
        strategy_cfg = self.get_strategy_config(strategy_name)
        bot = self.get_bot_for_strategy(strategy_name)
        mode = self.get_execution_mode(strategy_name)

        summary = {
            "timestamp_utc": iso_now(),
            "strategy": strategy_name,
            "bot": bot,
            "execution_mode": mode,
            "regime_mode": scorecard.get("mode", "NORMAL"),
            "regime_p": scorecard.get("regime_shift_probability", 0),
            "confidence": scorecard.get("confidence", 0),
            "order_count": len(candidates),
            "orders": [],
        }

        for cand in candidates:
            symbol = cand.get("symbol", "?")
            direction = cand.get("direction", "?")
            confidence = cand.get("confidence_score", 0)
            reason = cand.get("reason", "")
            price_hints = cand.get("price_hints", {})
            meta = cand.get("metadata", {})

            entry_price = price_hints.get("decision_price") or price_hints.get("last_price")
            target = meta.get("target")
            stop = meta.get("stop")

            # Calculate upside
            upside_pct = None
            risk_reward = meta.get("risk_reward")
            if entry_price and target:
                try:
                    upside_pct = round(abs(float(target) - float(entry_price)) / float(entry_price) * 100, 2)
                except (ValueError, ZeroDivisionError):
                    pass

            order_info = {
                "symbol": symbol,
                "direction": direction,
                "confidence": confidence,
                "reason": reason,
                "entry_price": entry_price,
                "target_price": target,
                "stop_price": stop,
                "upside_pct": upside_pct,
                "risk_reward": risk_reward,
                "holding_period": cand.get("holding_period", strategy_cfg.get("holding_period", "day")),
                "profit_target_pct": strategy_cfg.get("profit_target_pct"),
                "stop_loss_pct": strategy_cfg.get("stop_loss_pct"),
            }
            summary["orders"].append(order_info)

        return summary

    def format_telegram_order_alert(self, summary: Dict[str, Any]) -> str:
        """Format an order summary as a Telegram message."""
        strategy = summary["strategy"]
        bot = summary["bot"]
        mode = summary["execution_mode"]
        regime = summary["regime_mode"]

        strategy_label = "Day Trade" if strategy == "day_trade" else "Medium/Long Hold"
        mode_label = "AUTO" if mode == "auto" else "MANUAL (approval required)"

        lines = [
            f"{'=' * 35}",
            f"NEW ORDERS - {strategy_label}",
            f"{'=' * 35}",
            f"Bot: {bot} | Mode: {mode_label}",
            f"Regime: {regime} | Confidence: {summary['confidence']:.1%}",
            f"Orders: {summary['order_count']}",
            "",
        ]

        for i, order in enumerate(summary["orders"], 1):
            sym = order["symbol"]
            direction = order["direction"].upper()
            entry = order.get("entry_price")
            target = order.get("target_price")
            stop = order.get("stop_price")
            upside = order.get("upside_pct")
            rr = order.get("risk_reward")
            conf = order.get("confidence", 0)

            lines.append(f"{i}. {sym} ({direction})")
            lines.append(f"   Confidence: {conf:.0%}")
            if entry:
                lines.append(f"   Entry: ${float(entry):,.2f}")
            if target:
                lines.append(f"   Target: ${float(target):,.2f}")
            if stop:
                lines.append(f"   Stop: ${float(stop):,.2f}")
            if upside is not None:
                lines.append(f"   Upside: +{upside}%")
            if rr:
                lines.append(f"   Risk/Reward: {rr}:1")
            lines.append(f"   Reason: {order['reason'][:80]}")
            lines.append("")

        if mode == "manual":
            lines.append("Reply 'APPROVE' to execute these orders")
            lines.append("Reply 'REJECT' to skip")

        return "\n".join(lines)

    def format_telegram_position_update(
        self, positions: List[Dict[str, Any]], strategy_name: str
    ) -> str:
        """Format hourly position update for Telegram."""
        strategy_cfg = self.get_strategy_config(strategy_name)
        bot = self.get_bot_for_strategy(strategy_name)
        strategy_label = "Day Trade" if strategy_name == "day_trade" else "Medium/Long Hold"

        if not positions:
            return f"[{strategy_label} | {bot}] No open positions."

        total_pnl = 0.0
        lines = [
            f"{'=' * 35}",
            f"POSITION UPDATE - {strategy_label}",
            f"{'=' * 35}",
            f"Bot: {bot} | Positions: {len(positions)}",
            "",
        ]

        for pos in positions:
            sym = pos.get("symbol", "?")
            entry = pos.get("avg_entry_price", 0)
            current = pos.get("current_price", 0)
            pnl_pct = pos.get("unrealized_plpc", 0)
            pnl_usd = pos.get("unrealized_pl", 0)
            qty = pos.get("qty", 0)
            side = pos.get("side", "long")
            market_value = pos.get("market_value", 0)

            try:
                entry = float(entry)
                current = float(current)
                pnl_pct = float(pnl_pct) * 100  # convert to %
                pnl_usd = float(pnl_usd)
                total_pnl += pnl_usd
            except (ValueError, TypeError):
                pnl_pct = 0
                pnl_usd = 0

            # Determine upside based on strategy targets
            profit_target = strategy_cfg.get("profit_target_pct", 2.0)
            remaining_upside = profit_target - pnl_pct if pnl_pct > 0 else profit_target

            pnl_emoji = "+" if pnl_pct >= 0 else ""
            lines.append(f"{sym} ({side.upper()}) x{qty}")
            lines.append(f"  Entry: ${entry:,.2f} | Now: ${current:,.2f}")
            lines.append(f"  P&L: {pnl_emoji}{pnl_pct:.2f}% (${pnl_usd:+,.2f})")
            if pnl_pct < profit_target:
                lines.append(f"  Target: +{profit_target}% ({remaining_upside:+.1f}% remaining)")
            else:
                lines.append(f"  AT TARGET - profit take imminent")
            lines.append("")

        lines.append(f"Total P&L: ${total_pnl:+,.2f}")
        lines.append(f"Updated: {iso_now()[:19]}Z")

        return "\n".join(lines)

    def log_strategy_event(self, event_type: str, payload: Dict[str, Any]):
        """Log strategy events for audit."""
        row = {
            "schema_version": "strategy_manager_event.v1",
            "timestamp_utc": iso_now(),
            "event_type": event_type,
            **payload,
        }
        log_path = self.log_dir / "strategy_manager.jsonl"
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
