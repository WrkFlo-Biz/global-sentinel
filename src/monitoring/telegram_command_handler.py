#!/usr/bin/env python3
"""
Global Sentinel -- Telegram Command Handler

Listens for incoming Telegram messages via getUpdates long-polling
and executes system control commands. Each bot instance runs in its
own daemon thread.

Security:
- Only responds to messages from known chat IDs
- Rate-limited to 1 command per 2 seconds
- All state-changing commands logged to JSONL audit trail
- Kill switch / veto changes require confirmation
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import requests

try:
    import yaml
except ImportError:
    yaml = None


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TelegramCommandHandler:
    """Handles incoming Telegram commands for a single bot."""
    # Tier-2 commands demoted per docs/openclaw-demotion.md

    RATE_LIMIT_SECONDS = 2.0
    POLL_TIMEOUT = 30  # long-poll timeout in seconds
    ORCHESTRATOR_MODE_APPROVAL_MESSAGE = (
        "⚠️ This command requires orchestrator approval. Use: "
        "wrkflo-orchestrator approve --kind gs.control.execution_mode.set "
        "--target global-sentinel/control/execution-mode/day_trade/manual"
    )
    ORCHESTRATOR_KILL_APPROVAL_MESSAGE = (
        "⚠️ This command requires orchestrator approval. Use: "
        "wrkflo-orchestrator approve --kind gs.control.kill_switch.set "
        "--target global-sentinel/control/kill-switch/on"
    )
    ORCHESTRATOR_VETO_APPROVAL_MESSAGE = (
        "⚠️ This command requires orchestrator approval. Use: "
        "wrkflo-orchestrator approve --kind gs.control.manual_veto.set "
        "--target global-sentinel/control/manual-veto/on"
    )
    ORCHESTRATOR_TRADE_APPROVAL_MESSAGE = (
        "⚠️ This command requires orchestrator approval. Prepare a GS trade "
        "ticket first, then use: wrkflo-orchestrator approve --kind "
        "gs.trade.execute_shadow --target global-sentinel/trade-ticket/<ticket_id>"
    )
    ORCHESTRATOR_REFRESH_APPROVAL_MESSAGE = (
        "⚠️ This command is no longer a bare GS project approval. Follow "
        "docs/gs-guarded-task-kinds-plan.md and use a scoped orchestrator "
        "`--kind` plus exact `--target` for the control surface you need."
    )

    def __init__(
        self,
        bot_token: str,
        allowed_chat_ids: Set[str],
        strategy: str,
        repo_root: Path,
        dashboard_base_url: str = "http://localhost:8501",
    ):
        self.bot_token = bot_token
        self.allowed_chat_ids = allowed_chat_ids
        self.strategy = strategy  # "day_trade" or "medium_long"
        self.repo_root = repo_root
        self.dashboard_url = dashboard_base_url
        self.api_key = os.getenv("GS_DASHBOARD_API_KEY", "")

        # Polling state
        self._offset: int = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Rate limiting
        self._last_command_time: float = 0.0

        # Confirmation state: chat_id -> pending action
        self._pending_confirmations: Dict[str, Dict[str, Any]] = {}

        # Logging
        self.log_dir = repo_root / "logs" / "notifications"
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Command registry
        self._commands: Dict[str, Callable[[str, str], str]] = {
            "status": self._cmd_status,
            "mode": self._cmd_mode,
            "kill": self._cmd_kill,
            "veto": self._cmd_veto,
            "approve": self._cmd_approve,
            "reject": self._cmd_reject,
            "gss": self._cmd_gss,
            "portfolio": self._cmd_portfolio,
            "positions": self._cmd_portfolio,
            "orders": self._cmd_orders,
            "alerts": self._cmd_alerts,
            "config": self._cmd_config,
            "refresh": self._cmd_refresh,
            "help": self._cmd_help,
            "start": self._cmd_help,
        }

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Start the polling thread."""
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name=f"tg-cmd-{self.strategy}",
        )
        self._thread.start()

    def stop(self):
        """Signal the polling thread to stop."""
        self._running = False

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    def _api_url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"

    def _get_updates(self) -> List[Dict[str, Any]]:
        """Fetch new updates via long polling."""
        try:
            resp = requests.get(
                self._api_url("getUpdates"),
                params={"offset": self._offset, "timeout": self.POLL_TIMEOUT},
                timeout=self.POLL_TIMEOUT + 10,
            )
            if resp.status_code != 200:
                return []
            data = resp.json()
            if not data.get("ok"):
                return []
            return data.get("result", [])
        except Exception:
            return []

    def _send_message(self, chat_id: str, text: str, parse_mode: str = ""):
        """Send a message to a chat."""
        payload: Dict[str, Any] = {
            "chat_id": chat_id,
            "text": text[:4096],
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        try:
            requests.post(
                self._api_url("sendMessage"),
                json=payload,
                timeout=15,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Dashboard API helper
    # ------------------------------------------------------------------

    def _dashboard_get(self, path: str) -> Optional[Dict[str, Any]]:
        """GET request to the local dashboard API."""
        headers: Dict[str, str] = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        try:
            resp = requests.get(
                f"{self.dashboard_url}{path}",
                headers=headers,
                timeout=10,
            )
            if resp.status_code == 200:
                return resp.json()
            return None
        except Exception:
            return None

    def _dashboard_post(self, path: str, body: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """POST request to the local dashboard API."""
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        try:
            resp = requests.post(
                f"{self.dashboard_url}{path}",
                json=body,
                headers=headers,
                timeout=10,
            )
            return resp.json()
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    def _poll_loop(self):
        """Main polling loop (runs in daemon thread)."""
        strategy_label = "day_trade" if self.strategy == "day_trade" else "medium_long"
        print(f"[{iso_now()}] Telegram command handler started for {strategy_label}")

        while self._running:
            try:
                updates = self._get_updates()
                for update in updates:
                    update_id = update.get("update_id", 0)
                    self._offset = update_id + 1

                    message = update.get("message")
                    if not message:
                        continue

                    chat = message.get("chat", {}) or {}
                    chat_id = str(chat.get("id", ""))
                    text = (message.get("text") or "").strip()

                    if not text or not chat_id:
                        continue

                    # Security: only respond to allowed chat IDs
                    if chat_id not in self.allowed_chat_ids:
                        self._log_unauthorized_chat(message)
                        continue

                    # Rate limiting
                    now = time.monotonic()
                    with self._lock:
                        elapsed = now - self._last_command_time
                        if elapsed < self.RATE_LIMIT_SECONDS:
                            self._send_message(chat_id, "Rate limited. Please wait.")
                            continue
                        self._last_command_time = now

                    # Handle confirmations
                    if chat_id in self._pending_confirmations:
                        self._handle_confirmation(chat_id, text)
                        continue

                    # Parse and dispatch command
                    self._dispatch_command(chat_id, text)

            except Exception as e:
                print(f"[{iso_now()}] Telegram poll error ({self.strategy}): {e}")
                # Back off on error
                time.sleep(5)

    # ------------------------------------------------------------------
    # Command dispatcher
    # ------------------------------------------------------------------

    def _llm_reply(self, chat_id: str, text: str) -> None:
        """Send a Claude LLM response to a free-form user message."""
        try:
            self._send_chat_action(chat_id, "typing")
            import anthropic
            client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
            # Load latest scorecard context
            ctx = ""
            try:
                import json as _json
                sc_path = Path(self.repo_root) / "logs" / "scorecards" / "latest_signal.json"
                if sc_path.exists():
                    sc = _json.loads(sc_path.read_text())
                    mode = sc.get("mode", "?")
                    prob = sc.get("regime_shift_probability", "?")
                    ctx = f"[GS context: mode={mode}, regime_shift_prob={prob}]\n\n"
            except Exception:
                pass
            system = (
                "You are the Global Sentinel AI assistant — a live geopolitical risk and "
                f"trading intelligence system monitoring a ${600_000:,} portfolio. "
                "Strategy: {self.strategy}. "
                "Answer questions about markets, positions, risk, and macro events concisely. "
                "Use HTML formatting for Telegram (bold=<b>, code=<code>)."
            )
            msg = client.messages.create(
                model="claude-opus-4-6",
                max_tokens=800,
                system=system,
                messages=[{"role": "user", "content": ctx + text}],
            )
            reply = msg.content[0].text if msg.content else "No response."
            self._send_message(chat_id, reply, parse_mode="HTML")
        except Exception as exc:
            self._send_message(chat_id, f"⚠️ LLM error: {str(exc)[:120]}")

    def _send_chat_action(self, chat_id: str, action: str) -> None:
        try:
            requests.post(
                self._api_url("sendChatAction"),
                json={"chat_id": chat_id, "action": action},
                timeout=5,
            )
        except Exception:
            pass

    def _dispatch_command(self, chat_id: str, text: str):
        """Dispatch /gs_ commands; route everything else to LLM."""
        # Strip leading / if present
        if text.startswith("/"):
            text = text[1:]

        # Non-/gs_ messages → LLM chat response
        if not text.lower().startswith("gs_"):
            self._llm_reply(chat_id, text)
            return

        # Strip the gs_ prefix for dispatch
        text = text[3:]  # Remove "gs_"

        parts = text.split(None, 1)
        cmd = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""

        if "@" in cmd:
            cmd = cmd.split("@", 1)[0]

        handler = self._commands.get(cmd)
        if handler:
            try:
                response = handler(args, chat_id)
            except Exception as e:
                response = f"Error: {str(e)[:200]}"
            self._send_message(chat_id, response)
        else:
            self._send_message(chat_id, self._cmd_help("", chat_id))

    # ------------------------------------------------------------------
    # Confirmation flow
    # ------------------------------------------------------------------

    def _request_confirmation(self, chat_id: str, action: str, payload: Dict[str, Any]) -> str:
        """Request confirmation for a dangerous action."""
        with self._lock:
            self._pending_confirmations[chat_id] = {
                "action": action,
                "payload": payload,
                "requested_at": time.monotonic(),
            }
        return f"Are you sure? Reply YES to confirm: {action}"

    def _handle_confirmation(self, chat_id: str, text: str):
        """Handle a confirmation reply."""
        with self._lock:
            pending = self._pending_confirmations.pop(chat_id, None)

        if not pending:
            return

        # Check for timeout (60 seconds)
        if time.monotonic() - pending["requested_at"] > 60:
            self._send_message(chat_id, "Confirmation expired. Please try again.")
            return

        if text.strip().upper() == "YES":
            action = pending["action"]
            payload = pending["payload"]

            if action == "kill_on":
                response = self._execute_kill_switch(True, payload.get("reason", ""))
            elif action == "kill_off":
                response = self._execute_kill_switch(False, "")
            elif action == "veto_on":
                response = self._execute_veto(True, payload.get("reason", ""))
            elif action == "veto_off":
                response = self._execute_veto(False, "")
            else:
                response = "Unknown action."

            self._send_message(chat_id, response)
        else:
            self._send_message(chat_id, "Cancelled.")

    # ------------------------------------------------------------------
    # Command implementations
    # ------------------------------------------------------------------

    def _cmd_status(self, args: str, chat_id: str) -> str:
        """Return current system status."""
        heartbeat = self._dashboard_get("/api/heartbeat")
        scorecard = self._dashboard_get("/api/scorecard/latest")
        controls = self._dashboard_get("/api/controls")
        exec_mode = self._dashboard_get("/api/execution-mode")
        portfolio = self._dashboard_get("/api/portfolio")

        if not heartbeat and not scorecard:
            return "System temporarily unavailable"

        lines = ["SYSTEM STATUS"]
        lines.append("=" * 20)

        if heartbeat:
            lines.append(f"Mode: {heartbeat.get('mode', '?')}")
            lines.append(f"Cycle: {heartbeat.get('cycle', '?')}")
            lines.append(f"Status: {heartbeat.get('status', '?')}")

        if scorecard and not scorecard.get("error"):
            lines.append(f"Regime P: {scorecard.get('regime_shift_probability', 0):.3f}")
            lines.append(f"Confidence: {scorecard.get('confidence', 0):.3f}")
            gss = scorecard.get("gss_signal")
            if gss and isinstance(gss, dict):
                lines.append(f"GSS Signal: {gss.get('signal', 'N/A')}")

        if controls:
            ks = controls.get("kill_switch", {})
            mv = controls.get("manual_veto", {})
            lines.append(f"Kill Switch: {'ACTIVE' if ks.get('active') or ks.get('kill_switch') else 'OFF'}")
            lines.append(f"Manual Veto: {'ACTIVE' if mv.get('active') or mv.get('manual_veto') else 'OFF'}")

        if exec_mode and not exec_mode.get("error"):
            em = exec_mode.get("execution_mode", {})
            lines.append(f"Day Trade: {em.get('day_trade', '?')}")
            lines.append(f"Medium/Long: {em.get('medium_long', '?')}")

        if portfolio and not portfolio.get("error"):
            positions = portfolio.get("positions", [])
            lines.append(f"Positions: {len(positions)}")
            lines.append(f"Equity: ${portfolio.get('equity', 0):,.2f}")

        return "\n".join(lines)

    def _cmd_mode(self, args: str, chat_id: str) -> str:
        """Toggle execution mode. Usage: /mode auto day_trade"""
        return self.ORCHESTRATOR_MODE_APPROVAL_MESSAGE

    def _cmd_kill(self, args: str, chat_id: str) -> str:
        """Activate/deactivate kill switch. Usage: /kill on [reason] or /kill off"""
        return self.ORCHESTRATOR_KILL_APPROVAL_MESSAGE

    def _cmd_veto(self, args: str, chat_id: str) -> str:
        """Activate/deactivate manual veto. Usage: /veto on [reason] or /veto off"""
        return self.ORCHESTRATOR_VETO_APPROVAL_MESSAGE

    def _cmd_approve(self, args: str, chat_id: str) -> str:
        """Approve pending orders for this bot's strategy."""
        return self.ORCHESTRATOR_TRADE_APPROVAL_MESSAGE

    def _cmd_reject(self, args: str, chat_id: str) -> str:
        """Reject pending orders for this bot's strategy."""
        return self.ORCHESTRATOR_TRADE_APPROVAL_MESSAGE

    def _cmd_gss(self, args: str, chat_id: str) -> str:
        """Return latest GSS signal analysis."""
        data = self._dashboard_get("/api/gss-latest")
        if not data or data.get("error"):
            return f"GSS unavailable: {data.get('error', 'no data') if data else 'API down'}"

        signal = data.get("gss_signal", "N/A")
        action = data.get("action", "N/A")
        confidence = data.get("confidence", 0)
        reason = data.get("reason", "")

        lines = ["GSS SIGNAL ANALYSIS"]
        lines.append("=" * 20)
        lines.append(f"Signal: {signal}")
        lines.append(f"Action: {action}")
        lines.append(f"Confidence: {confidence:.0%}" if isinstance(confidence, (int, float)) else f"Confidence: {confidence}")
        if reason:
            lines.append(f"Reason: {reason[:200]}")

        hedges = data.get("hedge_recommendations", [])
        if hedges:
            lines.append(f"\nHedge Recommendations ({len(hedges)}):")
            for h in hedges[:5]:
                lines.append(f"  {h.get('symbol', '?')} {h.get('action', '?')}")

        return "\n".join(lines)

    def _cmd_portfolio(self, args: str, chat_id: str) -> str:
        """Return current Alpaca paper portfolio with P&L."""
        data = self._dashboard_get("/api/portfolio")
        if not data or data.get("error"):
            return f"Portfolio unavailable: {data.get('error', 'API down') if data else 'API down'}"

        lines = ["PORTFOLIO"]
        lines.append("=" * 20)
        lines.append(f"Equity: ${data.get('equity', 0):,.2f}")
        lines.append(f"Cash: ${data.get('cash', 0):,.2f}")
        lines.append(f"Buying Power: ${data.get('buying_power', 0):,.2f}")

        positions = data.get("positions", [])
        if not positions:
            lines.append("\nNo open positions")
        else:
            lines.append(f"\nPositions ({len(positions)}):")
            total_pnl = 0.0
            for p in positions:
                pnl = p.get("unrealized_pl", 0)
                pnl_pct = p.get("unrealized_plpc", 0) * 100
                total_pnl += pnl
                lines.append(
                    f"  {p.get('symbol', '?')} x{p.get('qty', 0):.0f} "
                    f"${p.get('current_price', 0):,.2f} "
                    f"P&L: {pnl_pct:+.2f}% (${pnl:+,.2f})"
                )
            lines.append(f"\nTotal Unrealized P&L: ${total_pnl:+,.2f}")

        return "\n".join(lines)

    def _cmd_orders(self, args: str, chat_id: str) -> str:
        """Show recent order flow."""
        data = self._dashboard_get("/api/execution/orders?limit=10")
        if not data:
            return "Orders unavailable"

        if isinstance(data, list) and not data:
            return "No recent orders"

        orders = data if isinstance(data, list) else []
        lines = ["RECENT ORDERS"]
        lines.append("=" * 20)

        for order in orders[-10:]:
            payload = order.get("payload", order)
            ts = order.get("timestamp_utc", "")[:16]
            candidates = payload.get("selected_candidates", [])
            submitted = payload.get("submitted_open_or_ack_count", 0)
            lines.append(f"{ts} | {submitted} orders | {len(candidates)} candidates")
            for c in candidates[:3]:
                lines.append(f"  {c.get('symbol', '?')} {c.get('side', '?')} x{c.get('qty', '?')}")

        return "\n".join(lines)

    def _cmd_alerts(self, args: str, chat_id: str) -> str:
        """Show recent alerts."""
        data = self._dashboard_get("/api/alerts?limit=10")
        if not data:
            return "Alerts unavailable"

        if isinstance(data, list) and not data:
            return "No recent alerts"

        alerts = data if isinstance(data, list) else []
        lines = ["RECENT ALERTS"]
        lines.append("=" * 20)

        for alert in alerts[-10:]:
            ts = alert.get("timestamp_utc", "")[:16]
            level = alert.get("level", "?")
            title = alert.get("title", alert.get("event_type", "?"))
            lines.append(f"{ts} [{level}] {title}")

        return "\n".join(lines)

    def _cmd_config(self, args: str, chat_id: str) -> str:
        """Show current execution mode config for both strategies."""
        data = self._dashboard_get("/api/execution-mode")
        if not data or data.get("error"):
            return "Config unavailable"

        lines = ["EXECUTION CONFIG"]
        lines.append("=" * 20)

        em = data.get("execution_mode", {})
        strategies = data.get("strategies", {})

        for name in ("day_trade", "medium_long"):
            label = "Day Trade" if name == "day_trade" else "Medium/Long"
            mode = em.get(name, "?")
            strat = strategies.get(name, {})
            bot = strat.get("bot_username", "?")
            lines.append(f"\n{label} ({bot}):")
            lines.append(f"  Mode: {mode}")
            lines.append(f"  Max Positions: {strat.get('max_positions', '?')}")
            lines.append(f"  Profit Target: {strat.get('profit_target_pct', '?')}%")
            lines.append(f"  Stop Loss: {strat.get('stop_loss_pct', '?')}%")
            lines.append(f"  TIF: {strat.get('time_in_force', '?')}")

        return "\n".join(lines)

    def _cmd_refresh(self, args: str, chat_id: str) -> str:
        """Force a data refresh by triggering a single crisis monitor cycle."""
        return self.ORCHESTRATOR_REFRESH_APPROVAL_MESSAGE

    def _cmd_help(self, args: str, chat_id: str) -> str:
        """List available commands."""
        strategy_label = "Day Trade" if self.strategy == "day_trade" else "Medium/Long"
        lines = [
            f"Global Sentinel - {strategy_label}",
            "=" * 25,
            "",
            "All commands use /gs_ prefix:",
            "",
            "Read-Only Status:",
            "  /gs_status - System status",
            "Market Intelligence:",
            "  /gs_gss - GSS signal analysis",
            "  /gs_portfolio - Portfolio & P&L",
            "  /gs_orders - Recent order flow",
            "  /gs_alerts - Recent alerts",
            "",
            "Configuration:",
            "  /gs_config - Execution config",
            "  /gs_help - This message",
            "",
            "Tier-2 commands moved to orchestrator approval:",
            "  gs.control.execution_mode.set -> global-sentinel/control/execution-mode/day_trade/manual",
            "  gs.control.kill_switch.set -> global-sentinel/control/kill-switch/on",
            "  gs.control.manual_veto.set -> global-sentinel/control/manual-veto/on",
            "  gs.trade.execute_shadow -> global-sentinel/trade-ticket/<ticket_id>",
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Kill switch / veto execution
    # ------------------------------------------------------------------

    def _execute_kill_switch(self, active: bool, reason: str) -> str:
        """Write kill switch state to control file."""
        return self.ORCHESTRATOR_KILL_APPROVAL_MESSAGE

    def _execute_veto(self, active: bool, reason: str) -> str:
        """Write manual veto state to control file."""
        return self.ORCHESTRATOR_VETO_APPROVAL_MESSAGE

    # ------------------------------------------------------------------
    # Audit logging
    # ------------------------------------------------------------------

    def _log_command(self, event_type: str, payload: Dict[str, Any]):
        """Log a command to the JSONL audit trail."""
        row = {
            "timestamp_utc": iso_now(),
            "event_type": event_type,
            "strategy": self.strategy,
            **payload,
        }
        log_path = self.log_dir / "telegram_commands.jsonl"
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _log_unauthorized_chat(self, message: Dict[str, Any]):
        """Record unauthorized inbound chats so DM allowlists can be updated safely."""
        chat = message.get("chat", {}) or {}
        sender = message.get("from", {}) or {}
        row = {
            "timestamp_utc": iso_now(),
            "event_type": "unauthorized_chat_attempt",
            "strategy": self.strategy,
            "chat_id": str(chat.get("id", "")),
            "chat_type": chat.get("type", ""),
            "chat_title": chat.get("title", ""),
            "message_thread_id": message.get("message_thread_id"),
            "from_user_id": sender.get("id"),
            "from_username": sender.get("username", ""),
            "from_first_name": sender.get("first_name", ""),
            "text_preview": (message.get("text") or "")[:200],
        }
        log_path = self.log_dir / "telegram_unauthorized.jsonl"
        try:
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass
