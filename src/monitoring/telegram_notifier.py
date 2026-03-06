#!/usr/bin/env python3
"""
Global Sentinel — Telegram Notifier

Handles:
1. Hourly position updates with P&L from entry
2. Instant new-order notifications with upside analysis
3. Per-strategy notifications routed to the assigned bot
4. Manual mode approval requests

Uses the existing AlertDispatcher for transport,
adds strategy-aware formatting on top.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

try:
    import yaml
except ImportError:
    yaml = None


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TelegramNotifier:
    """Strategy-aware Telegram notification system."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.config = self._load_config()

        # Bot tokens — each strategy bot can have its own token
        # Falls back to global TELEGRAM_BOT_TOKEN
        self.default_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.default_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

        # Per-bot tokens (optional — if not set, use default)
        self.bot_tokens = {
            "mo2darkbot": os.getenv("TELEGRAM_BOT_TOKEN_DARKBOT", self.default_token),
            "mo2drkbot": os.getenv("TELEGRAM_BOT_TOKEN_DRKBOT", self.default_token),
        }
        self.bot_chat_ids = {
            "mo2darkbot": os.getenv("TELEGRAM_CHAT_ID_DARKBOT", self.default_chat_id),
            "mo2drkbot": os.getenv("TELEGRAM_CHAT_ID_DRKBOT", self.default_chat_id),
        }

        # Hourly update thread
        self._hourly_thread: Optional[threading.Thread] = None
        self._running = False

        # Log
        self.log_dir = repo_root / "logs" / "notifications"
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _load_config(self) -> Dict[str, Any]:
        path = self.repo_root / "config" / "execution_mode.yaml"
        if not path.exists() or yaml is None:
            return {}
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    def _get_bot_token(self, bot_name: str) -> str:
        return self.bot_tokens.get(bot_name, self.default_token)

    def _get_chat_id(self, bot_name: str) -> str:
        return self.bot_chat_ids.get(bot_name, self.default_chat_id)

    def send_message(self, text: str, bot_name: str = "mo2darkbot", parse_mode: str = ""):
        """Send a message via Telegram Bot API."""
        token = self._get_bot_token(bot_name)
        chat_id = self._get_chat_id(bot_name)

        if not token or not chat_id:
            self._log("send_failed", {"bot": bot_name, "reason": "missing_token_or_chat_id"})
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],  # Telegram max message length
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            resp = requests.post(url, json=payload, timeout=15)
            if resp.status_code != 200:
                self._log("send_error", {
                    "bot": bot_name,
                    "status": resp.status_code,
                    "response": resp.text[:200],
                })
        except Exception as e:
            self._log("send_error", {"bot": bot_name, "error": str(e)})

    def notify_new_orders(
        self,
        order_summary: Dict[str, Any],
        formatted_message: str,
    ):
        """Send instant notification when new orders are submitted or pending approval."""
        strategy = order_summary.get("strategy", "day_trade")
        bot = order_summary.get("bot", "mo2darkbot")

        self.send_message(formatted_message, bot_name=bot)
        self._log("order_notification_sent", {
            "strategy": strategy,
            "bot": bot,
            "order_count": order_summary.get("order_count", 0),
            "execution_mode": order_summary.get("execution_mode", "auto"),
        })

    def notify_position_update(
        self,
        formatted_message: str,
        strategy_name: str,
        bot_name: str,
    ):
        """Send hourly position update."""
        self.send_message(formatted_message, bot_name=bot_name)
        self._log("position_update_sent", {
            "strategy": strategy_name,
            "bot": bot_name,
        })

    def notify_order_filled(
        self,
        symbol: str,
        side: str,
        qty: float,
        fill_price: float,
        strategy_name: str,
    ):
        """Instant notification when an order is filled."""
        bot = self.config.get("strategies", {}).get(strategy_name, {}).get("bot", "mo2darkbot")
        strategy_label = "Day Trade" if strategy_name == "day_trade" else "Medium/Long Hold"

        msg = (
            f"ORDER FILLED - {strategy_label}\n"
            f"{symbol} {side.upper()} x{qty}\n"
            f"Fill Price: ${fill_price:,.2f}\n"
            f"Time: {iso_now()[:19]}Z"
        )
        self.send_message(msg, bot_name=bot)

    def notify_position_closed(
        self,
        symbol: str,
        reason: str,
        pnl_pct: float,
        pnl_usd: float,
        strategy_name: str,
    ):
        """Instant notification when a position is closed."""
        bot = self.config.get("strategies", {}).get(strategy_name, {}).get("bot", "mo2darkbot")
        strategy_label = "Day Trade" if strategy_name == "day_trade" else "Medium/Long Hold"

        result_label = "PROFIT" if pnl_usd >= 0 else "LOSS"
        msg = (
            f"POSITION CLOSED - {strategy_label}\n"
            f"{symbol} | {reason.upper()}\n"
            f"P&L: {pnl_pct:+.2f}% (${pnl_usd:+,.2f}) [{result_label}]\n"
            f"Time: {iso_now()[:19]}Z"
        )
        self.send_message(msg, bot_name=bot)

    def start_hourly_updates(self, position_fetcher, strategy_manager):
        """Start background thread for hourly position updates."""
        if self._hourly_thread and self._hourly_thread.is_alive():
            return

        self._running = True
        self._hourly_thread = threading.Thread(
            target=self._hourly_update_loop,
            args=(position_fetcher, strategy_manager),
            daemon=True,
        )
        self._hourly_thread.start()

    def stop_hourly_updates(self):
        self._running = False

    def _hourly_update_loop(self, position_fetcher, strategy_manager):
        """Background loop that sends position updates every hour."""
        interval = self.config.get("notifications", {}).get(
            "hourly_update_interval_minutes", 60
        ) * 60

        while self._running:
            try:
                self._send_all_position_updates(position_fetcher, strategy_manager)
            except Exception as e:
                self._log("hourly_update_error", {"error": str(e)})

            # Sleep with interruptibility
            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

    def _send_all_position_updates(self, position_fetcher, strategy_manager):
        """Fetch positions and send updates for each strategy."""
        try:
            all_positions = position_fetcher()
        except Exception as e:
            self._log("position_fetch_error", {"error": str(e)})
            return

        if not all_positions:
            return

        # Load order history to classify positions by strategy
        order_history = self._load_order_history()

        # Split positions by strategy
        strategy_positions: Dict[str, List] = {"day_trade": [], "medium_long": []}
        for pos in all_positions:
            symbol = pos.get("symbol", "")
            entry = order_history.get(symbol, {})
            holding = entry.get("holding_period", "day")
            strategy = "medium_long" if holding in ("swing", "medium", "long", "macro") else "day_trade"
            strategy_positions[strategy].append(pos)

        # Send updates for each strategy
        for strategy_name, positions in strategy_positions.items():
            if not positions:
                continue
            bot = strategy_manager.get_bot_for_strategy(strategy_name)
            msg = strategy_manager.format_telegram_position_update(positions, strategy_name)
            self.notify_position_update(msg, strategy_name, bot)

    def _load_order_history(self) -> Dict[str, Dict]:
        """Load order history to determine which strategy each position belongs to."""
        history: Dict[str, Dict] = {}
        log_path = self.repo_root / "logs" / "execution" / "shadow_order_router.jsonl"
        if not log_path.exists():
            return history
        try:
            with log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        payload = row.get("payload", row)
                        for cand in payload.get("selected_candidates", []):
                            sym = cand.get("symbol")
                            if sym:
                                history[sym] = {
                                    "strategy_style": cand.get("strategy_style"),
                                    "holding_period": "day" if payload.get("time_window_name") == "us_regular_hours" else "swing",
                                }
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return history

    def send_manual_mode_summary(self, summary: Dict[str, Any], formatted: str):
        """Send order summary in manual mode and wait for approval via Telegram."""
        bot = summary.get("bot", "mo2darkbot")
        self.send_message(formatted, bot_name=bot)
        self._log("manual_approval_requested", {
            "strategy": summary.get("strategy"),
            "bot": bot,
            "order_count": summary.get("order_count", 0),
        })

    def _log(self, event_type: str, payload: Dict[str, Any]):
        row = {
            "timestamp_utc": iso_now(),
            "event_type": event_type,
            **payload,
        }
        log_path = self.log_dir / "telegram_notifier.jsonl"
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass
