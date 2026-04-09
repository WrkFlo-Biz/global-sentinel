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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class OperationalAlerts:
    """Emit high-value operational alerts via existing alerter infrastructure."""

    def __init__(self, repo_root: Path, alerter: Optional[Any] = None):
        self.repo_root = repo_root
        self.alerter = alerter
        self._state_path = self.repo_root / "logs" / "alert_state" / "operational_alerts_state.json"
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
        self._last_config_fingerprint = str(self._state.get("config_fingerprint", ""))
        self._cooldowns = {
            "blocked_escalation": 15,
            "quorum_blocked_escalation": 30,
            "freshness_degradation": 60,
            "degraded_mode": 60,
            "blocked_promotion": 60,
            "blob_fallback": 60,
        }

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

        emitted_alerts: List[Dict[str, Any]] = []
        for alert in alerts:
            if self._should_emit(alert):
                self._emit(alert)
                self._mark_emitted(alert)
                emitted_alerts.append(alert)
            else:
                logger.debug(
                    "Suppressed alert %s due to cooldown", alert.get("alert_type", "unknown")
                )

        return emitted_alerts

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
            if self._should_emit(alert):
                self._emit(alert)
                self._mark_emitted(alert)
                return alert
            return None
        return None

    def check_blocked_promotion(
        self, signal_type: str, reason: str, decision: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]:
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
        if self._should_emit(alert):
            self._emit(alert)
            self._mark_emitted(alert)
            return alert
        return None

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
        ff = scorecard.get("feature_freshness") or {}
        penalty = ff.get("critical_max_confidence_penalty")
        if penalty is None:
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
            self._state["config_fingerprint"] = cfp
            self._save_state()
            return None
        if cfp == self._last_config_fingerprint:
            return None
        old_fp = self._last_config_fingerprint
        self._last_config_fingerprint = cfp
        self._state["config_fingerprint"] = cfp
        self._save_state()
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
        ff = scorecard.get("feature_freshness") or {}
        critical_degraded = ff.get("critical_degraded_groups")
        if critical_degraded is None:
            critical_degraded = ff.get("active_degraded_groups")

        if critical_degraded is not None:
            if int(critical_degraded) <= 0:
                return None
        elif not scorecard.get("degraded_mode"):
            return None
        # Only alert when penalty is meaningful (>= 0.2), not for minor best_effort gaps
        penalty = ff.get("critical_max_confidence_penalty")
        if penalty is None:
            penalty = ff.get("max_confidence_penalty", 0)
        if float(penalty or 0.0) < 0.2:
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

    def _load_state(self) -> Dict[str, Any]:
        try:
            if not self._state_path.exists():
                return {"last_emitted": {}, "config_fingerprint": ""}
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                last_emitted = raw.get("last_emitted", {})
                if not isinstance(last_emitted, dict):
                    last_emitted = {}
                return {
                    "last_emitted": {str(k): str(v) for k, v in last_emitted.items()},
                    "config_fingerprint": str(raw.get("config_fingerprint", "")),
                }
        except Exception as e:
            logger.debug("Failed to load operational alert state: %s", e)
        return {"last_emitted": {}, "config_fingerprint": ""}

    def _save_state(self) -> None:
        try:
            self._state_path.write_text(json.dumps(self._state, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.debug("Failed to persist operational alert state: %s", e)

    def _cooldown_minutes(self, alert_type: str) -> int:
        return int(self._cooldowns.get(alert_type, 30))

    def _should_emit(self, alert: Dict[str, Any]) -> bool:
        alert_type = str(alert.get("alert_type") or "").strip()
        if not alert_type:
            return True
        cooldown_minutes = self._cooldown_minutes(alert_type)
        if cooldown_minutes <= 0:
            return True
        last_emitted = self._state.get("last_emitted", {}).get(alert_type)
        if not last_emitted:
            return True
        try:
            last_dt = datetime.fromisoformat(str(last_emitted))
        except Exception:
            return True
        return (datetime.now(timezone.utc) - last_dt) >= timedelta(minutes=cooldown_minutes)

    def _mark_emitted(self, alert: Dict[str, Any]) -> None:
        alert_type = str(alert.get("alert_type") or "").strip()
        if not alert_type:
            return
        self._state.setdefault("last_emitted", {})[alert_type] = datetime.now(timezone.utc).isoformat()
        self._save_state()
