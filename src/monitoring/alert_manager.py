"""Global Sentinel V6 — Alert Manager.

Centralized alerting engine that evaluates rules from alerting_rules.yaml,
enforces deduplication windows, auto-escalates repeated warnings, and
dispatches to configured channels (telegram, dashboard, log).
"""
from __future__ import annotations

import json
import logging
import operator
import re
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Alert level enum (IntEnum so comparisons like WARN < CRITICAL work)
# ---------------------------------------------------------------------------

class AlertLevel(IntEnum):
    INFO = 0
    WARN = 1
    CRITICAL = 2
    EMERGENCY = 3

LEVEL_MAP: dict[str, AlertLevel] = {
    "INFO": AlertLevel.INFO,
    "WARN": AlertLevel.WARN,
    "CRITICAL": AlertLevel.CRITICAL,
    "EMERGENCY": AlertLevel.EMERGENCY,
}

LEVEL_FORMAT: dict[AlertLevel, str] = {
    AlertLevel.INFO: "\u2139\ufe0f {message}",
    AlertLevel.WARN: "\u26a0\ufe0f {message}",
    AlertLevel.CRITICAL: "\U0001f534 {message}",
    AlertLevel.EMERGENCY: "\U0001f6a8\U0001f6a8\U0001f6a8 {message}",
}

# Simple numeric comparators used when evaluating conditions
_COMPARATORS: dict[str, Any] = {
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
    "==": operator.eq,
}

# Regex to pull "key > number" style conditions
_NUMERIC_RE = re.compile(r"^(\w+)\s*(>|>=|<|<=|==)\s*([\d.]+)$")


class AlertManager:
    """Evaluate alerting rules, deduplicate, escalate, and dispatch."""

    def __init__(
        self,
        config_path: str = "config/alerting_rules.yaml",
        repo_root: str | Path | None = None,
    ) -> None:
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]
        self._repo_root = Path(repo_root)
        self._config_path = self._repo_root / config_path

        # Load rules
        with open(self._config_path, "r") as fh:
            raw = yaml.safe_load(fh)

        self._rules: dict[str, dict] = raw.get("rules", {})
        self._escalation: dict[str, Any] = raw.get("escalation", {})

        # Dedup cache: {rule_name: last_alert_utc}
        self._dedup_cache: dict[str, datetime] = {}

        # Escalation tracker: {rule_name: [timestamps]}
        self._escalation_tracker: dict[str, list[datetime]] = {}

        # Full alert history (in-memory, recent)
        self._history: list[dict] = []

        # Dashboard alert queue file
        self._alert_queue_path = self._repo_root / "logs" / "alert_queue.jsonl"
        self._alert_queue_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("AlertManager loaded %d rules from %s", len(self._rules), self._config_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def alert(
        self,
        rule_name: str,
        context: dict | None = None,
        level: str | AlertLevel | None = None,
    ) -> dict:
        """Fire a single alert by rule name.

        Returns dict with keys: sent, level, channels, deduped.
        """
        now = datetime.now(timezone.utc)
        rule = self._rules.get(rule_name)
        if rule is None:
            logger.warning("Unknown rule: %s", rule_name)
            return {"sent": False, "level": None, "channels": [], "deduped": False}

        # Resolve level
        if level is not None:
            resolved_level = level if isinstance(level, AlertLevel) else LEVEL_MAP.get(str(level).upper(), AlertLevel.INFO)
        else:
            resolved_level = LEVEL_MAP.get(rule.get("level", "INFO"), AlertLevel.INFO)

        # Check escalation before dedup (escalation may raise the level)
        resolved_level = self._check_escalation(rule_name, resolved_level, now)

        # Dedup check
        dedup_minutes = rule.get("dedup_minutes", 5)
        last_fired = self._dedup_cache.get(rule_name)
        if last_fired is not None and (now - last_fired) < timedelta(minutes=dedup_minutes):
            logger.debug("Deduped alert %s (window %d min)", rule_name, dedup_minutes)
            return {"sent": False, "level": resolved_level.name, "channels": [], "deduped": True}

        # Build message
        condition = rule.get("condition", rule_name)
        message = f"[{rule_name}] {condition}"
        if context:
            message += f" | ctx: {json.dumps(context, default=str)}"
        formatted = self._format_message(resolved_level, message)

        channels = rule.get("channels", ["log"])
        sent_channels: list[str] = []

        # Dispatch to channels
        for ch in channels:
            try:
                if ch == "telegram":
                    self._send_telegram(formatted, resolved_level)
                    sent_channels.append("telegram")
                elif ch == "dashboard":
                    self._write_dashboard_queue(rule_name, resolved_level, message, context, now)
                    sent_channels.append("dashboard")
                else:
                    logger.info("Unrecognised channel %s, skipping", ch)
            except Exception:
                logger.exception("Failed to send alert to channel %s", ch)

        # Always log
        logger.log(
            self._python_log_level(resolved_level),
            "ALERT %s: %s",
            resolved_level.name,
            formatted,
        )
        sent_channels.append("log")

        # Record
        self._dedup_cache[rule_name] = now
        record = {
            "rule_name": rule_name,
            "level": resolved_level.name,
            "message": message,
            "formatted": formatted,
            "channels": sent_channels,
            "context": context,
            "timestamp_utc": now.isoformat(),
        }
        self._history.append(record)

        return {"sent": True, "level": resolved_level.name, "channels": sent_channels, "deduped": False}

    def check_conditions(self, system_state: dict) -> list[dict]:
        """Evaluate every rule against *system_state* and return alerts to fire.

        Supported condition formats:
          - "key > number"  (numeric comparison)
          - Any condition string is also checked as a boolean flag in system_state
        """
        pending: list[dict] = []
        for rule_name, rule in self._rules.items():
            condition = rule.get("condition", "")
            level = LEVEL_MAP.get(rule.get("level", "INFO"), AlertLevel.INFO)

            triggered = False
            ctx: dict[str, Any] = {}

            # Try numeric comparison
            m = _NUMERIC_RE.match(condition)
            if m:
                key, cmp_str, threshold_str = m.groups()
                value = system_state.get(key)
                if value is not None:
                    cmp_fn = _COMPARATORS[cmp_str]
                    if cmp_fn(float(value), float(threshold_str)):
                        triggered = True
                        ctx = {"key": key, "value": value, "threshold": float(threshold_str)}
            else:
                # Check as boolean flag (e.g. system_state["bridge_stale_tier1"] = True)
                flag = system_state.get(rule_name)
                if flag:
                    triggered = True
                    ctx = {"flag": rule_name, "value": flag}

            if triggered:
                pending.append({
                    "rule_name": rule_name,
                    "level": level.name,
                    "message": condition,
                    "context": ctx,
                })

        return pending

    def fire_all(self, system_state: dict) -> list[dict]:
        """Evaluate all conditions and send every triggered alert."""
        results: list[dict] = []
        for item in self.check_conditions(system_state):
            result = self.alert(
                rule_name=item["rule_name"],
                context=item["context"],
                level=item["level"],
            )
            result["rule_name"] = item["rule_name"]
            results.append(result)
        return results

    def get_recent_alerts(self, minutes: int = 60) -> list[dict]:
        """Return alerts fired within the last *minutes*."""
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        return [
            a for a in self._history
            if datetime.fromisoformat(a["timestamp_utc"]) >= cutoff
        ]

    def get_alert_history(self, hours: int = 24) -> list[dict]:
        """Return full alert history within the last *hours*."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        return [
            a for a in self._history
            if datetime.fromisoformat(a["timestamp_utc"]) >= cutoff
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_escalation(self, rule_name: str, level: AlertLevel, now: datetime) -> AlertLevel:
        """Auto-escalate if the same rule fires repeatedly within the window."""
        window = timedelta(minutes=self._escalation.get("escalation_window_minutes", 30))
        warn_threshold = self._escalation.get("warn_to_critical_count", 3)
        critical_threshold = self._escalation.get("critical_to_emergency_count", 2)

        tracker = self._escalation_tracker.setdefault(rule_name, [])
        # Prune old entries
        tracker[:] = [ts for ts in tracker if (now - ts) < window]
        tracker.append(now)

        count = len(tracker)

        if level == AlertLevel.WARN and count >= warn_threshold:
            logger.info("Escalating %s from WARN to CRITICAL (%d fires in window)", rule_name, count)
            return AlertLevel.CRITICAL
        if level == AlertLevel.CRITICAL and count >= critical_threshold:
            logger.info("Escalating %s from CRITICAL to EMERGENCY (%d fires in window)", rule_name, count)
            return AlertLevel.EMERGENCY

        return level

    @staticmethod
    def _format_message(level: AlertLevel, message: str) -> str:
        template = LEVEL_FORMAT.get(level, "{message}")
        return template.format(message=message)

    @staticmethod
    def _python_log_level(level: AlertLevel) -> int:
        return {
            AlertLevel.INFO: logging.INFO,
            AlertLevel.WARN: logging.WARNING,
            AlertLevel.CRITICAL: logging.CRITICAL,
            AlertLevel.EMERGENCY: logging.CRITICAL,
        }.get(level, logging.INFO)

    def _send_telegram(self, formatted: str, level: AlertLevel) -> None:
        """Dispatch to the existing telegram_notifier if available."""
        try:
            from src.monitoring.telegram_notifier import send_alert  # type: ignore[import-untyped]
            send_alert(formatted, level=level.name)
        except ImportError:
            logger.debug("telegram_notifier not available, skipping telegram channel")
        except Exception:
            logger.exception("telegram_notifier.send_alert failed")

    def _write_dashboard_queue(
        self,
        rule_name: str,
        level: AlertLevel,
        message: str,
        context: dict | None,
        timestamp: datetime,
    ) -> None:
        """Append an alert record to the dashboard JSONL queue file."""
        record = {
            "rule_name": rule_name,
            "level": level.name,
            "message": message,
            "context": context,
            "timestamp_utc": timestamp.isoformat(),
        }
        with open(self._alert_queue_path, "a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
