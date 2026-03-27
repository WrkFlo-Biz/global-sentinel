#!/usr/bin/env python3
"""High-value operational alerts for Global Sentinel V4.

Small set of operator-useful alerts with reason-rich payloads:
- Blocked promotion
- Freshness degradation
- Quorum-blocked escalation
- Blob fallback
- Config fingerprint drift

Each alert includes the blocking/degradation reason so operators
can act without digging through logs.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class OperationalAlerts:
    """Emit high-value operational alerts via existing alerter infrastructure."""

    def __init__(self, repo_root: Path, alerter: Optional[Any] = None):
        self.repo_root = repo_root
        self.alerter = alerter
        self._last_config_fingerprint: str = ""

    def check_and_alert(self, scorecard: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Check scorecard for alertable conditions. Returns list of emitted alerts."""
        alerts: List[Dict[str, Any]] = []

        a = self._check_blocked_escalation(scorecard)
        if a:
            alerts.append(a)

        a = self._check_freshness_degradation(scorecard)
        if a:
            alerts.append(a)

        a = self._check_quorum_block(scorecard)
        if a:
            alerts.append(a)

        a = self._check_config_drift(scorecard)
        if a:
            alerts.append(a)

        a = self._check_degraded_mode(scorecard)
        if a:
            alerts.append(a)

        for alert in alerts:
            self._emit(alert)

        return alerts

    def check_blob_fallback(self, persistence_mode: str, reason: str = "") -> Optional[Dict[str, Any]]:
        """Alert when Blob persistence falls back to local."""
        if persistence_mode == "local_fallback":
            alert = {
                "alert_type": "blob_fallback",
                "severity": "warning",
                "title": "Blob Persistence Fallback",
                "message": f"Persistence fell back to local storage. Reason: {reason or 'unknown'}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "details": {"persistence_mode": persistence_mode, "reason": reason},
            }
            self._emit(alert)
            return alert
        return None

    def check_blocked_promotion(
        self, signal_type: str, reason: str, decision: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Alert when a promotion is blocked."""
        alert = {
            "alert_type": "blocked_promotion",
            "severity": "info",
            "title": f"Promotion Blocked: {signal_type}",
            "message": f"Signal '{signal_type}' promotion blocked. Reason: {reason}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": {
                "signal_type": signal_type,
                "reason": reason,
                "decision": decision,
            },
        }
        self._emit(alert)
        return alert

    def _check_blocked_escalation(self, scorecard: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mdt = scorecard.get("mode_decision_trace", {})
        if not isinstance(mdt, dict) or not mdt.get("blocked"):
            return None
        return {
            "alert_type": "blocked_escalation",
            "severity": "warning",
            "title": f"Escalation Blocked: {mdt.get('proposed_mode')} -> {mdt.get('final_mode')}",
            "message": (
                f"Mode escalation to {mdt.get('proposed_mode')} was blocked by "
                f"{mdt.get('blocking_reason', 'unknown')}. "
                f"Regime probability: {mdt.get('regime_shift_probability', 0):.3f}"
            ),
            "timestamp": scorecard.get("timestamp_utc", ""),
            "details": {
                "cycle": scorecard.get("cycle"),
                "proposed_mode": mdt.get("proposed_mode"),
                "final_mode": mdt.get("final_mode"),
                "blocking_reason": mdt.get("blocking_reason"),
                "regime_shift_probability": mdt.get("regime_shift_probability"),
            },
        }

    def _check_freshness_degradation(self, scorecard: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        penalty = scorecard.get("freshness_penalty", 0)
        if not penalty or float(penalty) < 0.2:
            return None
        return {
            "alert_type": "freshness_degradation",
            "severity": "warning" if float(penalty) >= 0.3 else "info",
            "title": f"Freshness Penalty: {float(penalty):.0%}",
            "message": (
                f"Feature freshness penalty of {float(penalty):.0%} applied. "
                f"Confidence: {scorecard.get('original_confidence', '?')} -> {scorecard.get('confidence', '?')}"
            ),
            "timestamp": scorecard.get("timestamp_utc", ""),
            "details": {
                "cycle": scorecard.get("cycle"),
                "freshness_penalty": float(penalty),
                "original_confidence": scorecard.get("original_confidence"),
                "adjusted_confidence": scorecard.get("confidence"),
                "feature_freshness": scorecard.get("feature_freshness"),
            },
        }

    def _check_quorum_block(self, scorecard: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        mdt = scorecard.get("mode_decision_trace", {})
        if not isinstance(mdt, dict):
            return None
        if mdt.get("blocking_reason") != "quorum_not_met":
            return None
        return {
            "alert_type": "quorum_blocked_escalation",
            "severity": "warning",
            "title": f"Quorum Block: {mdt.get('proposed_mode')} denied",
            "message": (
                f"Escalation to {mdt.get('proposed_mode')} blocked because source quorum was not met."
            ),
            "timestamp": scorecard.get("timestamp_utc", ""),
            "details": {
                "cycle": scorecard.get("cycle"),
                "proposed_mode": mdt.get("proposed_mode"),
                "quorum_evaluation": mdt.get("quorum_evaluation"),
            },
        }

    def _check_config_drift(self, scorecard: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        cfp = scorecard.get("config_fingerprint", "")
        if not cfp:
            return None
        if not self._last_config_fingerprint:
            self._last_config_fingerprint = cfp
            return None
        if cfp == self._last_config_fingerprint:
            return None
        old_fp = self._last_config_fingerprint
        self._last_config_fingerprint = cfp
        return {
            "alert_type": "config_fingerprint_drift",
            "severity": "warning",
            "title": "Config Fingerprint Changed",
            "message": (
                f"Governance config fingerprint changed from {old_fp[:12]}... to {cfp[:12]}..."
            ),
            "timestamp": scorecard.get("timestamp_utc", ""),
            "details": {
                "cycle": scorecard.get("cycle"),
                "from_fingerprint": old_fp,
                "to_fingerprint": cfp,
                "config_versions": scorecard.get("config_versions", {}),
            },
        }

    def _check_degraded_mode(self, scorecard: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not scorecard.get("degraded_mode"):
            return None
        # Only alert when penalty is meaningful (>= 0.2), not for minor best_effort gaps
        ff = scorecard.get("feature_freshness") or {}
        if ff.get("max_confidence_penalty", 0) < 0.2:
            return None
        return {
            "alert_type": "degraded_mode",
            "severity": "warning",
            "title": "System Running in Degraded Mode",
            "message": "One or more feature groups are non-compliant with freshness policy.",
            "timestamp": scorecard.get("timestamp_utc", ""),
            "details": {
                "cycle": scorecard.get("cycle"),
                "feature_freshness": scorecard.get("feature_freshness"),
            },
        }

    def _emit(self, alert: Dict[str, Any]) -> None:
        """Dispatch alert through existing alerter infrastructure."""
        logger.info("Operational alert: %s — %s", alert.get("alert_type"), alert.get("title"))
        if self.alerter:
            try:
                level = alert.get("severity", "info")
                self.alerter._dispatch(
                    alert.get("title", "Operational Alert"),
                    alert.get("message", ""),
                    level=level,
                    extra={"event": alert.get("alert_type"), "details": alert.get("details")},
                )
            except Exception as e:
                logger.debug("Failed to dispatch alert: %s", e)

        # Persist alert to events log
        try:
            events_dir = self.repo_root / "logs" / "events"
            events_dir.mkdir(parents=True, exist_ok=True)
            ts = alert.get("timestamp", datetime.now(timezone.utc).isoformat())
            safe_ts = ts.replace(":", "-").replace("+", "p")
            fname = f"alert_{alert.get('alert_type', 'unknown')}_{safe_ts}.json"
            (events_dir / fname).write_text(
                json.dumps(alert, indent=2, default=str), encoding="utf-8"
            )
        except Exception as e:
            logger.debug("Failed to persist alert: %s", e)
