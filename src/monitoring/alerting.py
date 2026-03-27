#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Alerting Module

Sends alerts on mode transitions and critical events via:
- Telegram (if TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID set)
- Slack webhook (if SLACK_WEBHOOK_URL set)
- Log file (always, as fallback)

Usage from crisis_monitor:
    from src.monitoring.alerting import AlertDispatcher
    alerter = AlertDispatcher(repo_root)
    alerter.send_mode_transition("NORMAL", "ELEVATED", scorecard)
"""

from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.monitoring.notification_window import notifications_muted

# Notification categories that should be routed to topic group, not main chat
_TOPIC_ONLY_EVENTS = frozenset({
    'startup',
    'scorecard_summary',
    'mode_transition',       # NORMAL->ELEVATED etc
    'performance_summary',
})


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AlertDispatcher:
    """Dispatches alerts to configured channels."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.alert_log = repo_root / "logs" / "events" / "alerts.jsonl"
        self.alert_log.parent.mkdir(parents=True, exist_ok=True)

        # Telegram config
        self.telegram_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID")

        # Slack config
        self.slack_webhook = os.getenv("SLACK_WEBHOOK_URL")

        # Throttle: only send non-critical alerts once per hour
        import time as _time
        self._last_sent: Dict[str, float] = {}  # event_type -> timestamp
        self._throttle_seconds = 3600  # 1 hour
        self._time = _time

    def send_mode_transition(
        self,
        from_mode: str,
        to_mode: str,
        scorecard: Dict[str, Any],
    ):
        """Alert on operating mode transition."""
        regime_p = scorecard.get("regime_shift_probability", 0)
        confidence = scorecard.get("confidence", 0)
        cycle = scorecard.get("cycle", 0)
        evidence = scorecard.get("evidence", [])[:3]

        emoji = self._mode_emoji(to_mode)
        title = f"{emoji} MODE TRANSITION: {from_mode} → {to_mode}"

        body = (
            f"Regime shift probability: {regime_p:.3f}\n"
            f"Confidence: {confidence:.3f}\n"
            f"Cycle: {cycle}\n"
        )
        if evidence:
            body += "Evidence:\n"
            for e in evidence:
                body += f"  • {e}\n"

        self._dispatch(title, body, level=self._transition_level(to_mode), extra={
            "event": "mode_transition",
            "from_mode": from_mode,
            "to_mode": to_mode,
            "regime_p": regime_p,
            "confidence": confidence,
        })

    def send_startup_alert(self):
        """Alert when the crisis monitor starts up."""
        self._dispatch(
            "🚀 Global Sentinel Started",
            "Crisis Monitor is online and beginning monitoring cycles.\n"
            f"Dashboard: http://20.124.180.8:8501",
            level="info",
            extra={"event": "startup"},
        )

    def send_kill_switch_alert(self):
        """Alert when kill switch is activated."""
        self._dispatch(
            "🛑 KILL SWITCH ACTIVATED",
            "All shadow execution suspended. Manual intervention required.",
            level="critical",
            extra={"event": "kill_switch"},
        )

    def send_shadow_execution_alert(
        self,
        route_result: Dict[str, Any],
        scorecard: Dict[str, Any],
    ):
        """Alert when shadow orders are submitted."""
        submitted = route_result.get("submitted_open_or_ack_count", 0)
        rejected = route_result.get("broker_rejected_count", 0)
        mode = scorecard.get("mode", "UNKNOWN")
        regime_p = scorecard.get("regime_shift_probability", 0)

        title = f"📊 Shadow Orders: {submitted} submitted"

        body = (
            f"Mode: {mode} | Regime P: {regime_p:.3f}\n"
            f"Submitted: {submitted} | Rejected: {rejected}\n"
        )

        for cand in route_result.get("selected_candidates", []):
            body += f"  • {cand.get('symbol')} ({cand.get('direction', '?')}) — conf: {cand.get('confidence_score', 0):.2f}\n"

        if route_result.get("skipped_candidates"):
            skipped = len(route_result["skipped_candidates"])
            body += f"Skipped: {skipped} candidates\n"

        self._dispatch(title, body, level="info", extra={
            "event": "shadow_execution",
            "submitted": submitted,
            "rejected": rejected,
            "mode": mode,
        })

    def send_performance_summary(self, summary: Dict[str, Any]):
        """Send periodic shadow trading performance summary."""
        total = summary.get("total_trades", 0)
        win_rate = summary.get("win_rate", 0)
        total_pnl = summary.get("total_pnl", 0)
        pnl_emoji = "📈" if total_pnl >= 0 else "📉"

        title = f"{pnl_emoji} Shadow Performance: {total} trades, {win_rate:.0%} win rate"
        body = (
            f"Total P&L: ${total_pnl:+,.2f}\n"
            f"Wins: {summary.get('wins', 0)} | Losses: {summary.get('losses', 0)}\n"
            f"Avg Win: ${summary.get('avg_win', 0):+,.2f} | Avg Loss: ${summary.get('avg_loss', 0):+,.2f}\n"
        )
        pf = summary.get("profit_factor")
        if pf is not None:
            body += f"Profit Factor: {pf:.2f}\n"

        by_sym = summary.get("by_symbol", {})
        if by_sym:
            body += "Top symbols:\n"
            for sym, data in list(by_sym.items())[:5]:
                body += f"  {sym}: {data['trades']} trades, ${data['pnl']:+,.2f}\n"

        self._dispatch(title, body, level="info", extra={
            "event": "performance_summary",
            "total_trades": total,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
        })

    def send_bridge_failure(self, bridge_name: str, error: str):
        """Alert on persistent bridge failure."""
        self._dispatch(
            f"⚠️ Bridge Failure: {bridge_name}",
            f"Error: {error}",
            level="warning",
            extra={"event": "bridge_failure", "bridge": bridge_name},
        )

    def send_scorecard_summary(self, scorecard: Dict[str, Any]):
        """Send periodic scorecard summary (for ELEVATED/CRISIS modes)."""
        mode = scorecard.get("mode", "UNKNOWN")
        regime_p = scorecard.get("regime_shift_probability", 0)
        confidence = scorecard.get("confidence", 0)
        cycle = scorecard.get("cycle", 0)
        bridge_summary = scorecard.get("bridge_summary", {})

        emoji = self._mode_emoji(mode)
        title = f"{emoji} Scorecard #{cycle} — {mode}"
        body = (
            f"Regime P: {regime_p:.3f} | Confidence: {confidence:.3f}\n"
            f"Bridges: {json.dumps(bridge_summary)}\n"
        )
        components = scorecard.get("component_scores", {})
        if components:
            top = sorted(components.items(), key=lambda x: x[1], reverse=True)[:3]
            body += "Top signals: " + ", ".join(f"{k}={v:.2f}" for k, v in top) + "\n"

        exposure = scorecard.get("v6_exposure_summary", {}) or {}
        if exposure:
            body += (
                f"💰 Equity: ${float(exposure.get('combined_equity', 0.0)):,.0f} | "
                f"Gross: {float(exposure.get('gross_exposure_pct', 0.0)):.0%} | "
                f"Net: {float(exposure.get('net_exposure_pct', 0.0)):+.0%} | "
                f"OilΔ: ${float(exposure.get('oil_delta', 0.0)):+,.0f}/pt\n"
            )

        edge_summary = str(scorecard.get("v6_edge_summary") or "").strip()
        if edge_summary and "no actionable signals" not in edge_summary.lower():
            body += edge_summary + "\n"

        strategy_summary = scorecard.get("v6_strategy_summary", {}) or {}
        if strategy_summary:
            body += (
                f"📊 Strategies: {int(strategy_summary.get('active_count', 0))}/15 firing | "
                f"Ideas: {int(strategy_summary.get('idea_count', 0))}\n"
            )

        self._dispatch(title, body, level="info", extra={
            "event": "scorecard_summary",
            "mode": mode,
            "regime_p": regime_p,
        })

    # --- Internal dispatch ---

    def _dispatch(self, title: str, body: str, level: str = "info", extra: Optional[Dict] = None):
        """Send to all configured channels. Non-critical events throttled to once/hour."""
        message = f"{title}\n\n{body}"

        # Always log
        self._log_alert(title, body, level, extra)

        # Throttle non-critical alerts (info level) to once per hour per event type
        event_type = (extra or {}).get("event", title[:50])
        if level == "info":
            now = self._time.time()
            last = self._last_sent.get(event_type, 0)
            if now - last < self._throttle_seconds:
                return  # Suppressed — already sent this hour
            self._last_sent[event_type] = now

        # Telegram — route system noise to topic group, not main chat
        if self.telegram_token and not notifications_muted():
            if event_type in _TOPIC_ONLY_EVENTS:
                try:
                    self._send_telegram_topic(message)
                except Exception:
                    pass
            elif self.telegram_chat_id:
                try:
                    self._send_telegram(message)
                except Exception:
                    pass

        # Slack
        if self.slack_webhook:
            try:
                self._send_slack(title, body)
            except Exception:
                pass

    def _send_telegram(self, text: str):
        """Send message via Telegram Bot API."""
        if notifications_muted():
            return
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        _payload_dict = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if str(self.telegram_chat_id).startswith("-100"):
            _dt = os.getenv("TELEGRAM_DEFAULT_THREAD_ID")
            if _dt:
                _payload_dict["message_thread_id"] = int(_dt)
        payload = json.dumps(_payload_dict).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)

    def _send_telegram_topic(self, text: str):
        """Send message to topic group chat (noisy system updates channel)."""
        if notifications_muted():
            return
        import os as _os
        topic_chat = _os.getenv('TELEGRAM_TOPIC_CHAT_ID', '')
        thread_id = _os.getenv('TELEGRAM_V6_DIGEST_THREAD_ID', '')
        if not topic_chat or not self.telegram_token:
            return  # fallback: don't send at all (suppress noise)
        payload_d = {
            'chat_id': topic_chat,
            'text': text,
            'parse_mode': 'HTML',
            'disable_web_page_preview': True,
            'disable_notification': True,
        }
        if thread_id:
            payload_d['message_thread_id'] = int(thread_id)
        payload = json.dumps(payload_d).encode('utf-8')
        req = urllib.request.Request(
            f'https://api.telegram.org/bot{self.telegram_token}/sendMessage',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        urllib.request.urlopen(req, timeout=10)

    def _send_slack(self, title: str, body: str):
        """Send message via Slack incoming webhook."""
        payload = json.dumps({
            "text": title,
            "blocks": [
                {"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
                {"type": "section", "text": {"type": "mrkdwn", "text": body[:2000]}},
            ],
        }).encode("utf-8")

        req = urllib.request.Request(
            self.slack_webhook,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)

    def _log_alert(self, title: str, body: str, level: str, extra: Optional[Dict]):
        row = {
            "timestamp_utc": iso_now(),
            "level": level,
            "title": title,
            "body": body,
            **(extra or {}),
        }
        with self.alert_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _mode_emoji(self, mode: str) -> str:
        return {
            "NORMAL": "🟢",
            "ELEVATED": "🟡",
            "CRISIS": "🔴",
            "MANUAL_REVIEW": "🟠",
        }.get(mode, "⚪")

    def _transition_level(self, to_mode: str) -> str:
        return {
            "CRISIS": "critical",
            "ELEVATED": "warning",
            "MANUAL_REVIEW": "critical",
            "NORMAL": "info",
        }.get(to_mode, "info")
