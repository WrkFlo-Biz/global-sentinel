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

    def send_kill_switch_alert(self):
        """Alert when kill switch is activated."""
        self._dispatch(
            "🛑 KILL SWITCH ACTIVATED",
            "All shadow execution suspended. Manual intervention required.",
            level="critical",
            extra={"event": "kill_switch"},
        )

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

        self._dispatch(title, body, level="info", extra={
            "event": "scorecard_summary",
            "mode": mode,
            "regime_p": regime_p,
        })

    # --- Internal dispatch ---

    def _dispatch(self, title: str, body: str, level: str = "info", extra: Optional[Dict] = None):
        """Send to all configured channels."""
        message = f"{title}\n\n{body}"

        # Always log
        self._log_alert(title, body, level, extra)

        # Telegram
        if self.telegram_token and self.telegram_chat_id:
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
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = json.dumps({
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
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
