#!/usr/bin/env python3
"""Build role-based oversight briefs for OpenClaw subagents."""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.core.market_session_classifier import MarketSessionClassifier
from src.core.openclaw_role_registry import OpenClawRoleConfig


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _latest_file(folder: Path, pattern: str = "*.json") -> Optional[Path]:
    if not folder.exists():
        return None
    files = sorted(folder.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _top_components(scorecard: Dict[str, Any], limit: int = 3) -> List[Tuple[str, float]]:
    components = scorecard.get("component_scores", {}) or {}
    pairs: List[Tuple[str, float]] = []
    for key, value in components.items():
        try:
            pairs.append((str(key), float(value)))
        except (TypeError, ValueError):
            continue
    return sorted(pairs, key=lambda item: item[1], reverse=True)[:limit]


class OpenClawRoleBriefingBuilder:
    """Aggregate runtime artifacts into structured role-specific briefs."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.market_session_classifier = MarketSessionClassifier()

    def build_role_artifact(self, role: OpenClawRoleConfig) -> Dict[str, Any]:
        context = self._load_context()
        build_fn = getattr(self, f"_build_{role.role_id}", self._build_generic)
        artifact = build_fn(role, context)
        artifact["schema_version"] = "openclaw_role_brief.v1"
        artifact["generated_at"] = _utc_now_iso()
        artifact["role_id"] = role.role_id
        artifact["title"] = role.title
        artifact["backend"] = role.backend
        artifact["bot"] = role.bot
        artifact["prompt_path"] = role.prompt_path
        artifact["safety"] = {
            "paper_only": role.paper_only,
            "no_live_orders": True,
            "no_promotion_authority": True,
        }
        artifact["inputs"] = context["input_paths"]
        return artifact

    def write_role_artifact(self, role: OpenClawRoleConfig) -> Path:
        artifact = self.build_role_artifact(role)
        out_dir = self.repo_root / role.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = out_dir / f"{role.artifact_prefix}_{timestamp}.json"
        output_path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        return output_path

    def _load_context(self) -> Dict[str, Any]:
        scorecard_path = _latest_file(self.repo_root / "logs" / "scorecards")
        scorecard = _read_json(scorecard_path, {}) if scorecard_path else {}
        readiness_path = self.repo_root / "reports" / "operational" / "canary_readiness_report.json"
        stabilization_path = self.repo_root / "reports" / "operational" / "canary_stabilization_report.json"
        observability_path = self.repo_root / "reports" / "operational" / "canary_observability_report.json"
        checkpoint_path = self.repo_root / "reports" / "operational" / "canary_stabilization_checkpoint.json"
        blob_health_path = self.repo_root / "reports" / "operational" / "blob_persistence_health.json"
        decision_audit_path = self.repo_root / "reports" / "operational" / "decision_audit_report.json"

        readiness = _read_json(readiness_path, {})
        stabilization = _read_json(stabilization_path, {})
        observability = _read_json(observability_path, {})
        checkpoint = _read_json(checkpoint_path, {})
        blob_health = _read_json(blob_health_path, {})
        decision_audit = _read_json(decision_audit_path, {})
        control_dir = self.repo_root / "control"
        kill_switch = _read_json(control_dir / "kill_switch.json", {"kill_switch": False})
        manual_veto = _read_json(control_dir / "manual_veto.json", {"manual_veto": False})
        dead_letter_dir = self.repo_root / "logs" / "dead_letter"
        dead_letter_count = len(list(dead_letter_dir.glob("*.json"))) if dead_letter_dir.exists() else 0

        current_session = self.market_session_classifier.classify(_utc_now_iso(), asset_class="equity").to_dict()
        scorecard_timestamp = scorecard.get("timestamp_utc") or _utc_now_iso()
        scorecard_session = self.market_session_classifier.classify(scorecard_timestamp, asset_class="equity").to_dict()

        return {
            "scorecard": scorecard,
            "readiness": readiness,
            "stabilization": stabilization,
            "observability": observability,
            "checkpoint": checkpoint,
            "blob_health": blob_health,
            "decision_audit": decision_audit,
            "control_flags": {
                "kill_switch": bool(kill_switch.get("kill_switch", False)),
                "manual_veto": bool(manual_veto.get("manual_veto", False)),
            },
            "dead_letter_count": dead_letter_count,
            "current_session": current_session,
            "scorecard_session": scorecard_session,
            "top_components": _top_components(scorecard),
            "input_paths": {
                "scorecard": str(scorecard_path) if scorecard_path else "",
                "canary_readiness_report": str(readiness_path),
                "canary_stabilization_report": str(stabilization_path),
                "canary_observability_report": str(observability_path),
                "canary_stabilization_checkpoint": str(checkpoint_path),
                "blob_persistence_health": str(blob_health_path),
                "decision_audit_report": str(decision_audit_path),
            },
        }

    def _build_generic(self, role: OpenClawRoleConfig, context: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "status": "yellow",
            "observed_facts": ["No specialized builder registered for this role."],
            "inferences": [],
            "actions": ["Keep this role in observation mode until a dedicated builder is implemented."],
            "metrics": {},
        }

    def _build_cio(self, role: OpenClawRoleConfig, context: Dict[str, Any]) -> Dict[str, Any]:
        scorecard = context["scorecard"]
        observability = context["observability"]
        checkpoint = context["checkpoint"]
        stabilization = context["stabilization"]
        summary = observability.get("summary", {}) or {}
        checkpoint_status = checkpoint.get("checkpoint_status", "unknown")
        primary_blockers = checkpoint.get("primary_blockers", []) or []
        status = "yellow" if summary.get("rollback_recommended_count", 0) else "green"
        return {
            "status": status,
            "observed_facts": [
                f"Mode: {scorecard.get('mode', 'UNKNOWN')}",
                f"Regime shift probability: {scorecard.get('regime_shift_probability', 'unknown')}",
                f"Confidence: {scorecard.get('confidence', 'unknown')}",
                f"Evidence-only canary rollback recommendations: {summary.get('rollback_recommended_count', 0)} of {summary.get('total_canary_artifacts', 0)} artifacts.",
                f"Latest stabilization session: {(stabilization.get('summary', {}) or {}).get('latest_session', 'unknown')}",
                f"Checkpoint status: {checkpoint_status}",
            ],
            "inferences": [
                "The canary path is providing evidence, but promotion remains correctly blocked.",
                "Research focus should stay session-aware and paper-only while failure gates accumulate more samples.",
            ],
            "actions": [
                "Keep promotion authority with policy gates only.",
                "Monitor eval_days and trade_count growth before revisiting canary quality.",
                f"Track checkpoint blockers: {', '.join(primary_blockers[:2]) or 'none'}.",
            ],
            "metrics": {
                "mode": scorecard.get("mode"),
                "regime_shift_probability": scorecard.get("regime_shift_probability"),
                "confidence": scorecard.get("confidence"),
                "rollback_recommended_count": summary.get("rollback_recommended_count", 0),
                "market_session": (stabilization.get("summary", {}) or {}).get("latest_session", "unknown"),
                "checkpoint_status": checkpoint_status,
            },
        }

    def _build_cfo(self, role: OpenClawRoleConfig, context: Dict[str, Any]) -> Dict[str, Any]:
        blob_health = context["blob_health"]
        observability = context["observability"]
        rollback = observability.get("rollback_telemetry", {}) or {}
        summary = observability.get("summary", {}) or {}
        status = "green" if blob_health.get("status") == "healthy" and rollback.get("rollback_path_proven") else "yellow"
        return {
            "status": status,
            "observed_facts": [
                f"Persistence mode: {blob_health.get('persistence_mode', 'unknown')}",
                f"Blob health status: {blob_health.get('status', 'unknown')}",
                f"Rollback proven: {rollback.get('rollback_path_proven', False)}",
                f"Promotion-eligible canaries: {summary.get('promotion_eligible_count', 0)}",
                f"Control flags: {context['control_flags']}",
            ],
            "inferences": [
                "Operational durability is good enough for evidence generation.",
                "Real capital should remain gated until canary quality, not just system readiness, improves materially.",
            ],
            "actions": [
                "Keep blob-primary persistence as the system of record.",
                "Do not widen live capital deployment from these role briefs.",
            ],
            "metrics": {
                "persistence_mode": blob_health.get("persistence_mode"),
                "blob_health": blob_health.get("status"),
                "rollback_proven": rollback.get("rollback_path_proven", False),
                "promotion_eligible_count": summary.get("promotion_eligible_count", 0),
            },
        }

    def _build_coo(self, role: OpenClawRoleConfig, context: Dict[str, Any]) -> Dict[str, Any]:
        blob_health = context["blob_health"]
        readiness = context["readiness"]
        status = "green" if blob_health.get("status") == "healthy" else "yellow"
        return {
            "status": status,
            "observed_facts": [
                f"Blob persistence health: {blob_health.get('status', 'unknown')}",
                f"Latest canary-readiness result: {readiness.get('readiness_status', 'unknown')}",
                f"Dead-letter artifact count: {context['dead_letter_count']}",
                f"Current classified session: {(context['current_session'] or {}).get('session', 'unknown')}",
            ],
            "inferences": [
                "Operational automation is active and should keep emitting replay-grade artifacts.",
            ],
            "actions": [
                "Review dead-letter growth if it starts to rise materially.",
                "Keep Telegram topic updates isolated from the main alert stream.",
            ],
            "metrics": {
                "blob_health": blob_health.get("status"),
                "readiness_status": readiness.get("readiness_status"),
                "dead_letter_count": context["dead_letter_count"],
                "market_session": (context["current_session"] or {}).get("session"),
            },
        }

    def _build_chief_of_staff(self, role: OpenClawRoleConfig, context: Dict[str, Any]) -> Dict[str, Any]:
        readiness = context["readiness"]
        stabilization = context["stabilization"]
        observability = context["observability"]
        checkpoint = context["checkpoint"]
        summary = observability.get("summary", {}) or {}
        status = "yellow" if summary.get("rollback_recommended_count", 0) else "green"
        return {
            "status": status,
            "observed_facts": [
                f"Readiness status: {readiness.get('readiness_status', 'unknown')}",
                f"Top blocker: {readiness.get('top_blocker', 'none')}",
                f"Rollback-recommended ratio: {(stabilization.get('summary', {}) or {}).get('rollback_recommended_ratio', 'unknown')}",
                f"Config fingerprint consistent: {summary.get('config_fingerprint_consistent', False)}",
                f"Checkpoint status: {checkpoint.get('checkpoint_status', 'unknown')}",
            ],
            "inferences": [
                "The system is ready for evidence-only canary, but the candidate remains in stabilization.",
            ],
            "actions": [
                "Require human review only if rollback recommendations reverse materially or config consistency breaks.",
                "Keep subagent reporting focused on blocked/degraded reasons during stabilization.",
                f"Next checkpoint questions: {(checkpoint.get('next_questions', []) or [])[:2]}",
            ],
            "metrics": {
                "readiness_status": readiness.get("readiness_status"),
                "rollback_recommended_count": summary.get("rollback_recommended_count", 0),
                "config_fingerprint_consistent": summary.get("config_fingerprint_consistent", False),
                "checkpoint_status": checkpoint.get("checkpoint_status"),
            },
        }

    def _build_volatility_researcher(self, role: OpenClawRoleConfig, context: Dict[str, Any]) -> Dict[str, Any]:
        scorecard = context["scorecard"]
        stabilization = context["stabilization"]
        observability = context["observability"]
        checkpoint = context["checkpoint"]
        top_components = context["top_components"]
        focus_areas = self._volatility_focus_areas(top_components)
        current_session = context["current_session"] or {}
        summary = observability.get("summary", {}) or {}
        status = "yellow" if summary.get("rollback_recommended_count", 0) else "green"
        facts = [
            f"Current session: {current_session.get('session', 'unknown')}",
            f"Scorecard mode: {scorecard.get('mode', 'UNKNOWN')}",
            f"Top components: {top_components or 'unavailable'}",
            f"Stabilization recommendations: {stabilization.get('recommendations', [])[:2]}",
            f"Checkpoint blockers: {checkpoint.get('primary_blockers', [])[:2]}",
        ]
        if current_session.get("session") == "overnight":
            facts.append("Overnight equity constraints apply: limit-only, DAY TIF only, overnight_tradable required.")
        return {
            "status": status,
            "observed_facts": facts,
            "inferences": [
                "Use this role to prioritize paper-only research in the highest-stress themes, not to bypass execution gates.",
            ],
            "actions": [
                f"Focus paper research on: {', '.join(focus_areas)}.",
                "Separate degraded-runtime effects from true signal weakness in all canary reviews.",
                "Prefer liquid ETF/index/futures proxy research before any single-name analysis.",
            ],
            "metrics": {
                "market_session": current_session.get("session"),
                "focus_areas": focus_areas,
                "rollback_recommended_count": summary.get("rollback_recommended_count", 0),
                "top_components": [{"name": name, "score": score} for name, score in top_components],
            },
        }

    def _volatility_focus_areas(self, top_components: List[Tuple[str, float]]) -> List[str]:
        areas: List[str] = []
        for name, _score in top_components:
            lowered = name.lower()
            if any(token in lowered for token in ("commodity", "oil", "energy", "physical")):
                areas.append("energy shock / transport stress paper basket")
            elif any(token in lowered for token in ("credit", "liquidity", "yield", "rate")):
                areas.append("rates and credit stress paper basket")
            elif any(token in lowered for token in ("vol", "market", "equity")):
                areas.append("index volatility and hedge paper basket")
            elif any(token in lowered for token in ("policy", "geo", "geopolitical", "currency")):
                areas.append("macro-policy and FX spillover paper basket")
        if not areas:
            areas = [
                "index volatility and hedge paper basket",
                "energy shock / transport stress paper basket",
                "rates and credit stress paper basket",
            ]
        return list(dict.fromkeys(areas))
