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

from src.monitoring.notification_window import mute_reason, notifications_muted
from src.execution.strategy_learning import infer_strategy_family


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

        # Notification buffer for batched hourly digests
        self._event_buffer: Dict[str, List[Dict[str, Any]]] = {
            "orders_submitted": [],
            "orders_filled": [],
            "positions_closed": [],
        }
        self._buffer_lock = threading.Lock()
        self._seen_events: Dict[str, float] = {}  # key -> timestamp, for dedup
        self._dedup_window = 3600  # 1 hour dedup window

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

    def send_message(
        self,
        text: str,
        bot_name: str = "mo2darkbot",
        parse_mode: str = "",
        chat_id: str = "",
        message_thread_id: int | None = None,
    ):
        """Send a message via Telegram Bot API.

        Args:
            chat_id: Override the default chat_id for this bot.
            message_thread_id: Telegram forum topic ID (for supergroups with topics).
        """
        if notifications_muted():
            self._log("send_suppressed", {
                "bot": bot_name,
                "reason": mute_reason(),
            })
            return

        token = self._get_bot_token(bot_name)
        chat_id = chat_id or self._get_chat_id(bot_name)

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
        if message_thread_id is not None:
            payload["message_thread_id"] = message_thread_id

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
        """Buffer new order notification for hourly digest."""
        bot = order_summary.get("bot", "mo2darkbot")
        orders = order_summary.get("orders", [])

        with self._buffer_lock:
            for order in orders:
                symbol = order.get("symbol", "unknown")
                side = order.get("side", "buy")
                strategy = order.get("strategy") or order_summary.get("strategy") or "day_trade"
                strategy_family = (
                    order.get("strategy_family")
                    or order_summary.get("strategy_family")
                    or infer_strategy_family(
                        {
                            "strategy": strategy,
                            "strategy_style": order.get("strategy_style") or order_summary.get("strategy_style"),
                            "holding_period": order.get("holding_period") or order_summary.get("holding_period"),
                        },
                        default_family="day_trade",
                    )
                    or "day_trade"
                )
                dedup_key = f"order:{symbol}:{side}:{strategy}"

                now = time.time()
                if dedup_key in self._seen_events:
                    if now - self._seen_events[dedup_key] < self._dedup_window:
                        continue  # Skip duplicate
                self._seen_events[dedup_key] = now

                self._event_buffer["orders_submitted"].append({
                    "symbol": symbol,
                    "side": side,
                    "qty": order.get("qty", 0),
                    "strategy": strategy,
                    "strategy_family": strategy_family,
                    "underlying_strategy": (
                        order.get("underlying_strategy") or order_summary.get("underlying_strategy")
                    ),
                    "learning_adjusted": bool(
                        order.get(
                            "learning_adjusted",
                            order_summary.get("learning_adjusted", False),
                        )
                    ),
                    "learning_adjustment_detail": (
                        order.get("learning_adjustment_detail")
                        or order_summary.get("learning_adjustment_detail")
                    ),
                    "bot": bot,
                    "confidence": order.get("confidence", 0),
                    "timestamp": iso_now(),
                })

        self._log("order_notification_buffered", {
            "strategy": order_summary.get("strategy"),
            "bot": bot,
            "order_count": order_summary.get("order_count", 0),
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
        strategy_family: Optional[str] = None,
        underlying_strategy: Optional[str] = None,
        learning_adjusted: bool = False,
        learning_adjustment_detail: Optional[Dict[str, Any]] = None,
    ):
        """Buffer order fill notification for hourly digest."""
        bot = self.config.get("strategies", {}).get(strategy_name, {}).get("bot", "mo2darkbot")
        resolved_family = strategy_family or infer_strategy_family(
            {"strategy": strategy_name},
            default_family="day_trade",
        )

        with self._buffer_lock:
            dedup_key = f"fill:{symbol}:{side}:{strategy_name}"

            now = time.time()
            if dedup_key in self._seen_events:
                if now - self._seen_events[dedup_key] < self._dedup_window:
                    self._log("fill_notification_deduped", {"symbol": symbol, "strategy": strategy_name})
                    return
            self._seen_events[dedup_key] = now

            self._event_buffer["orders_filled"].append({
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "fill_price": fill_price,
                "strategy": strategy_name,
                "strategy_family": resolved_family,
                "underlying_strategy": underlying_strategy,
                "learning_adjusted": learning_adjusted,
                "learning_adjustment_detail": learning_adjustment_detail,
                "bot": bot,
                "timestamp": iso_now(),
            })

        self._log("fill_notification_buffered", {"symbol": symbol, "strategy": strategy_name})

    def notify_position_closed(
        self,
        symbol: str,
        reason: str,
        pnl_pct: float,
        pnl_usd: float,
        strategy_name: str,
        strategy_family: Optional[str] = None,
        underlying_strategy: Optional[str] = None,
        learning_adjusted: bool = False,
        learning_adjustment_detail: Optional[Dict[str, Any]] = None,
    ):
        """Buffer position close notification for hourly digest."""
        bot = self.config.get("strategies", {}).get(strategy_name, {}).get("bot", "mo2darkbot")
        resolved_family = strategy_family or infer_strategy_family(
            {"strategy": strategy_name},
            default_family="day_trade",
        )

        with self._buffer_lock:
            dedup_key = f"close:{symbol}:{strategy_name}"

            now = time.time()
            if dedup_key in self._seen_events:
                if now - self._seen_events[dedup_key] < self._dedup_window:
                    self._log("close_notification_deduped", {"symbol": symbol, "strategy": strategy_name})
                    return
            self._seen_events[dedup_key] = now

            self._event_buffer["positions_closed"].append({
                "symbol": symbol,
                "reason": reason,
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
                "strategy": strategy_name,
                "strategy_family": resolved_family,
                "underlying_strategy": underlying_strategy,
                "learning_adjusted": learning_adjusted,
                "learning_adjustment_detail": learning_adjustment_detail,
                "bot": bot,
                "timestamp": iso_now(),
            })

        self._log("close_notification_buffered", {"symbol": symbol, "strategy": strategy_name})

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

            try:
                self._send_hourly_digest()
            except Exception as e:
                self._log("hourly_digest_error", {"error": str(e)})

            # Sleep with interruptibility
            for _ in range(interval):
                if not self._running:
                    break
                time.sleep(1)

    def _send_hourly_digest(self):
        """Send a consolidated hourly digest of all buffered events."""
        with self._buffer_lock:
            buffer = {k: list(v) for k, v in self._event_buffer.items()}
            self._event_buffer = {
                "orders_submitted": [],
                "orders_filled": [],
                "positions_closed": [],
            }
            # Clean old dedup entries
            now = time.time()
            self._seen_events = {
                k: v for k, v in self._seen_events.items()
                if now - v < self._dedup_window
            }

        # Build digest for each strategy/bot
        for strategy in ("day_trade", "medium_long"):
            bot = self.config.get("strategies", {}).get(strategy, {}).get("bot", "mo2darkbot")

            submitted = [e for e in buffer["orders_submitted"] if e.get("strategy_family", e["strategy"]) == strategy]
            filled = [e for e in buffer["orders_filled"] if e.get("strategy_family", e["strategy"]) == strategy]
            closed = [e for e in buffer["positions_closed"] if e.get("strategy_family", e["strategy"]) == strategy]

            if not submitted and not filled and not closed:
                continue

            strategy_label = "Day Trade" if strategy == "day_trade" else "Medium/Long"
            lines = [f"HOURLY TRADE DIGEST - {strategy_label}"]
            lines.append("=" * 30)
            lines.append("Period: Last 60 minutes")
            lines.append("")

            if submitted:
                lines.append(f"NEW ORDERS ({len(submitted)}):")
                for e in submitted:
                    lines.append(
                        f"  {e['symbol']} {e['side'].upper()} x{e['qty']}{self._format_strategy_digest_context(e)}"
                    )
                lines.append("")

            if filled:
                lines.append(f"FILLS ({len(filled)}):")
                total_notional = 0
                for e in filled:
                    notional = e.get("fill_price", 0) * e.get("qty", 0)
                    total_notional += notional
                    lines.append(
                        f"  {e['symbol']} {e['side'].upper()} x{e['qty']} @ ${e.get('fill_price', 0):,.2f}"
                        f"{self._format_strategy_digest_context(e)}"
                    )
                lines.append(f"  Total notional: ${total_notional:,.2f}")
                lines.append("")

            if closed:
                total_pnl = sum(e.get("pnl_usd", 0) for e in closed)
                winners = sum(1 for e in closed if e.get("pnl_usd", 0) >= 0)
                lines.append(f"CLOSED ({len(closed)}) | {winners}W/{len(closed)-winners}L:")
                for e in closed:
                    pnl = e.get("pnl_usd", 0)
                    label = "+" if pnl >= 0 else ""
                    lines.append(
                        f"  {e['symbol']} {e['reason']} ${label}{pnl:,.2f}{self._format_strategy_digest_context(e)}"
                    )
                lines.append(f"  Net P&L: ${total_pnl:+,.2f}")

            msg = "\n".join(lines)
            self.send_message(msg, bot_name=bot)
            self._log("hourly_digest_sent", {
                "strategy": strategy,
                "submitted": len(submitted),
                "filled": len(filled),
                "closed": len(closed),
            })

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
            strategy = infer_strategy_family(entry, default_family="day_trade") or "day_trade"
            strategy_positions[strategy].append(pos)

        # Send updates for each strategy
        for strategy_name, positions in strategy_positions.items():
            if not positions:
                continue
            bot = strategy_manager.get_bot_for_strategy(strategy_name)
            msg = strategy_manager.format_telegram_position_update(positions, strategy_name)
            context_lines = self._position_strategy_context_lines(positions, order_history)
            if context_lines:
                msg = "\n".join([msg, "", *context_lines])
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
                                metadata = cand.get("metadata") or {}
                                history[sym] = {
                                    "strategy": cand.get("strategy") or metadata.get("strategy"),
                                    "strategy_style": cand.get("strategy_style") or metadata.get("strategy_style"),
                                    "strategy_family": cand.get("strategy_family") or metadata.get("strategy_family"),
                                    "underlying_strategy": cand.get("underlying_strategy") or metadata.get("underlying_strategy"),
                                    "learning_adjusted": cand.get("learning_adjusted", metadata.get("learning_adjusted", False)),
                                    "learning_adjustment_detail": cand.get("learning_adjustment_detail") or metadata.get("learning_adjustment_detail"),
                                    "holding_period": cand.get("holding_period")
                                    or metadata.get("holding_period")
                                    or ("day" if payload.get("time_window_name") == "us_regular_hours" else "swing"),
                                }
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass
        return history

    def _position_strategy_context_lines(
        self,
        positions: List[Dict[str, Any]],
        order_history: Dict[str, Dict[str, Any]],
    ) -> List[str]:
        lines: List[str] = []
        for pos in positions:
            symbol = pos.get("symbol")
            if not symbol:
                continue
            entry = order_history.get(symbol, {})
            strategy = str(entry.get("strategy") or entry.get("strategy_style") or "").strip()
            family = str(
                entry.get("strategy_family")
                or infer_strategy_family(entry, default_family="day_trade")
                or ""
            ).strip()
            underlying = str(entry.get("underlying_strategy") or "").strip()
            learning_adjusted = bool(entry.get("learning_adjusted", False))
            if not any((strategy, family, underlying, learning_adjusted)):
                continue

            parts = [symbol]
            if strategy:
                parts.append(strategy)
            if family:
                parts.append(f"[{family}]")
            if underlying:
                parts.append(f"underlying={underlying}")
            if learning_adjusted:
                parts.append("learning=on")
            lines.append("  " + " ".join(parts))

        if not lines:
            return []
        return ["Strategy Context:", *lines]

    def _format_strategy_digest_context(self, event: Dict[str, Any]) -> str:
        strategy = str(event.get("strategy") or "").strip()
        family = str(event.get("strategy_family") or "").strip()
        underlying = str(event.get("underlying_strategy") or "").strip()
        learning_adjusted = bool(event.get("learning_adjusted", False))

        if not any((strategy, family, underlying, learning_adjusted)):
            return ""

        parts = []
        if strategy:
            parts.append(strategy)
        if family:
            parts.append(f"family={family}")
        if underlying:
            parts.append(f"underlying={underlying}")
        if learning_adjusted:
            parts.append("learning=on")
        return f" ({', '.join(parts)})"

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
