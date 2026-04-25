#!/usr/bin/env python3
"""
Global Sentinel - orchestrator-mediated trade approval boundary.

Legacy Telegram callback approvals and local pending files are intentionally
demoted. Callers must present guarded approval context up front and submit one
scoped `gs.trade.execute_shadow` task through wrkflo-orchestrator.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.core.orchestrator_task_client import (
    OrchestratorTaskClientError,
    build_guarded_task_payload,
    submit_task,
)

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
APPROVAL_LOG_PATH = REPO_ROOT / "logs" / "trade_approvals.jsonl"
# Legacy path kept only for stale cleanup/backwards-compatible test patching.
PENDING_DIR = Path("/tmp/gs_pending_approvals")
APPROVAL_AUDIT_SCHEMA_VERSION = "trade_approval_audit.v3"
DEFAULT_PROJECT = "global-sentinel"
GUARDED_TRADE_KIND = "gs.trade.execute_shadow"
DEFAULT_REQUESTER_KIND = "scheduler"
DEFAULT_REQUESTER_CHANNEL = "trade_approval"
DEFAULT_SOURCE_SURFACE = "trade_approval"
DEFAULT_TASK_TIMEOUT_SECONDS = 15.0
TICKET_TARGET_PREFIX = f"{DEFAULT_PROJECT}/trade-ticket/"
LEGACY_APPROVAL_BRIDGE_DISABLED_REASON = (
    "Local pending-approval files are disabled; route Tier-2 mediation through "
    "orchestrator approval tokens instead."
)
LEGACY_TELEGRAM_TRANSPORT_DISABLED_REASON = (
    "Direct Telegram trade approval transport is disabled; obtain an "
    "orchestrator approval token and submit the guarded GS trade task once."
)

logger = logging.getLogger("global_sentinel.trade_approval")

# Minimum notional threshold used for policy classification. In fail-closed mode,
# below-threshold orders still block unless an explicit approval path says otherwise.
MIN_NOTIONAL_FOR_APPROVAL = 500.0


def _env_bool(key: str, default: bool = False) -> bool:
    value = os.getenv(key, "").lower().strip()
    if value in ("true", "1", "yes"):
        return True
    if value in ("false", "0", "no"):
        return False
    return default


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _config_timeout_seconds() -> float:
    raw = (
        os.getenv("ORCHESTRATOR_TASK_TIMEOUT_SECONDS")
        or os.getenv("TRADE_APPROVAL_TIMEOUT")
        or str(DEFAULT_TASK_TIMEOUT_SECONDS)
    )
    try:
        timeout = float(raw)
    except ValueError as exc:
        raise ValueError(f"Invalid trade approval config: timeout must be numeric ({raw!r})") from exc
    if timeout <= 0:
        raise ValueError(f"Invalid trade approval config: timeout must be > 0 ({raw!r})")
    return timeout


def _requesting_agent(order_info: Dict[str, Any]) -> str:
    for key in (
        "requesting_agent",
        "source_agent",
        "agent_id",
        "agent",
        "worker",
        "signal_source",
    ):
        value = order_info.get(key)
        normalized = _optional_text(value)
        if normalized is not None:
            return normalized
    return "system"


def _safe_order_summary(order_info: Dict[str, Any]) -> dict:
    return {
        "symbol": order_info.get("symbol"),
        "side": order_info.get("side"),
        "qty": order_info.get("qty"),
        "type": order_info.get("type") or order_info.get("order_type"),
        "limit_price": order_info.get("limit_price"),
        "notional": order_info.get("notional"),
        "asset_class": order_info.get("asset_class"),
        "strategy_style": order_info.get("strategy_style"),
        "signal_source": order_info.get("signal_source"),
        "contract_id": order_info.get("contract_id"),
        "ticket_id": order_info.get("ticket_id"),
        "ticket_hash": order_info.get("ticket_hash"),
        "target": order_info.get("target"),
        "source_surface": order_info.get("source_surface"),
    }


def _log_approval_json(entry: Dict[str, Any]) -> None:
    try:
        APPROVAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(APPROVAL_LOG_PATH, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    except Exception as exc:
        logger.warning("Failed to write trade approval audit JSON log: %s", exc)


def _record_approval_event(
    *,
    event_type: str,
    timestamp: str,
    approval_id: str,
    order_info: Dict[str, Any],
    decision: Optional[str],
    reason: str,
    approved: Optional[bool],
    fail_closed_trigger: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    agent_id = _requesting_agent(order_info)
    cleaned_metadata = {
        str(key): value
        for key, value in (metadata or {}).items()
        if value is not None
    } or None
    entry = {
        "schema_version": APPROVAL_AUDIT_SCHEMA_VERSION,
        "event_type": event_type,
        "timestamp": timestamp,
        "approval_id": approval_id,
        "agent_id": agent_id,
        "requesting_agent": agent_id,
        "trade_details": _safe_order_summary(order_info),
        "order": _safe_order_summary(order_info),
        "decision": decision,
        "reason": reason,
        "approved": approved,
        "fail_closed_trigger": fail_closed_trigger,
        "metadata": cleaned_metadata,
    }
    _log_approval_json(entry)


def _log_trade_blocked(order_info: Dict[str, Any], decision: str, reason: str) -> None:
    logger.warning(
        "Trade blocked (%s): %s | order=%s",
        decision,
        reason,
        _safe_order_summary(order_info),
    )


def _fail_closed_trigger(decision: str, reason: str) -> Optional[str]:
    if decision == "disabled":
        return "approval_disabled"
    if decision == "below_threshold":
        return "minimum_notional_gate"
    if decision != "error":
        return None

    lowered_reason = reason.lower()
    if "invalid trade sizing" in lowered_reason:
        return "invalid_trade_sizing"
    if "invalid trade approval config" in lowered_reason:
        return "invalid_approval_config"
    if "missing orchestrator approval context" in lowered_reason:
        return "missing_guarded_context"
    if "stale orchestrator approval context" in lowered_reason:
        return "stale_guarded_context"
    if "guarded trade target mismatch" in lowered_reason:
        return "guarded_target_mismatch"
    if "guarded trade kind mismatch" in lowered_reason:
        return "guarded_kind_mismatch"
    if "guarded trade project mismatch" in lowered_reason:
        return "guarded_project_mismatch"
    if "guarded trade ticket hash mismatch" in lowered_reason:
        return "guarded_ticket_hash_mismatch"
    if "orchestrator mediation failed" in lowered_reason:
        return "orchestrator_submission_failed"
    return "approval_error"


def _blocked_result(
    ts: str,
    approval_id: str,
    order_info: Dict[str, Any],
    decision: str,
    reason: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    _log_trade_blocked(order_info, decision, reason)
    result = {"approved": False, "decision": decision, "reason": reason}
    _record_approval_event(
        event_type="approval_decision",
        timestamp=ts,
        approval_id=approval_id,
        order_info=order_info,
        decision=decision,
        reason=reason,
        approved=False,
        fail_closed_trigger=_fail_closed_trigger(decision, reason),
        metadata=metadata,
    )
    return result


def _approval_context_value(order_info: Dict[str, Any], *keys: str) -> Any:
    nested = order_info.get("approval_context")
    if isinstance(nested, dict):
        for key in keys:
            if key in nested and nested[key] is not None:
                return nested[key]
    for key in keys:
        if key in order_info and order_info[key] is not None:
            return order_info[key]
    return None


def _normalize_ticket_id(order_info: Dict[str, Any]) -> tuple[str, str]:
    target = _optional_text(order_info.get("target"))
    ticket_id = _optional_text(order_info.get("ticket_id"))

    if ticket_id is None and target and target.startswith(TICKET_TARGET_PREFIX):
        ticket_id = target[len(TICKET_TARGET_PREFIX) :]

    if ticket_id is None:
        raise ValueError("Missing orchestrator approval context: ticket_id")

    expected_target = f"{TICKET_TARGET_PREFIX}{ticket_id}"
    if target is None:
        target = expected_target
    if target != expected_target:
        raise ValueError(
            f"Guarded trade target mismatch: expected {expected_target!r}, got {target!r}"
        )
    return ticket_id, target


def _parse_approval_exp(raw_value: Any) -> int:
    if raw_value is None:
        raise ValueError("Missing orchestrator approval context: approval_exp")
    if isinstance(raw_value, bool):
        return int(raw_value)
    if isinstance(raw_value, (int, float)):
        return int(raw_value)

    text = _optional_text(raw_value)
    if text is None:
        raise ValueError("Missing orchestrator approval context: approval_exp")
    if text.isdigit():
        return int(text)

    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"Invalid trade approval config: approval_exp is not parseable ({text!r})") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


def _resolve_guarded_context(order_info: Dict[str, Any]) -> dict[str, Any]:
    project = _optional_text(order_info.get("project")) or DEFAULT_PROJECT
    if project != DEFAULT_PROJECT:
        raise ValueError(
            f"Guarded trade project mismatch: expected {DEFAULT_PROJECT!r}, got {project!r}"
        )

    kind = _optional_text(order_info.get("kind")) or GUARDED_TRADE_KIND
    if kind != GUARDED_TRADE_KIND:
        raise ValueError(
            f"Guarded trade kind mismatch: expected {GUARDED_TRADE_KIND!r}, got {kind!r}"
        )

    ticket_id, target = _normalize_ticket_id(order_info)
    approval_jti = _optional_text(_approval_context_value(order_info, "approval_jti", "jti"))
    approval_issued_by = _optional_text(
        _approval_context_value(order_info, "approval_issued_by", "issued_by")
    )
    approval_reason = _optional_text(
        _approval_context_value(order_info, "approval_reason", "reason")
    )
    approval_token = _optional_text(
        _approval_context_value(
            order_info,
            "approval_token",
            "approval_bearer_token",
            "orchestrator_bearer_token",
            "bearer_token",
            "token",
        )
    )

    missing_fields: list[str] = []
    if approval_jti is None:
        missing_fields.append("approval_jti")
    if approval_issued_by is None:
        missing_fields.append("approval_issued_by")
    if approval_reason is None:
        missing_fields.append("approval_reason")
    if approval_token is None:
        missing_fields.append("approval_token")
    if missing_fields:
        raise ValueError(
            f"Missing orchestrator approval context: {', '.join(missing_fields)}"
        )

    approval_exp = _parse_approval_exp(
        _approval_context_value(order_info, "approval_exp", "exp")
    )
    now = int(time.time())
    if approval_exp <= now:
        raise ValueError(
            f"Stale orchestrator approval context: approval_exp {approval_exp} <= now {now}"
        )

    requester_kind = _optional_text(order_info.get("requester_kind")) or DEFAULT_REQUESTER_KIND
    requester_id = _optional_text(order_info.get("requester_id")) or _requesting_agent(order_info)
    requester_channel = (
        _optional_text(order_info.get("requester_channel")) or DEFAULT_REQUESTER_CHANNEL
    )

    return {
        "project": project,
        "kind": kind,
        "ticket_id": ticket_id,
        "target": target,
        "approval_jti": approval_jti,
        "approval_issued_by": approval_issued_by,
        "approval_reason": approval_reason,
        "approval_exp": approval_exp,
        "approval_token": approval_token,
        "requester_kind": requester_kind,
        "requester_id": requester_id,
        "requester_channel": requester_channel,
    }


def _ticket_hash_payload(order_info: Dict[str, Any], ticket_id: str) -> dict[str, Any]:
    return {
        "ticket_id": ticket_id,
        "symbol": _optional_text(order_info.get("symbol")) or "",
        "side": _optional_text(order_info.get("side")) or "",
        "qty": order_info.get("qty"),
        "notional": order_info.get("notional"),
        "asset_class": _optional_text(order_info.get("asset_class")) or "equity",
        "order_type": _order_type(order_info),
        "time_in_force": _optional_text(order_info.get("time_in_force")) or "day",
        "limit_price": order_info.get("limit_price"),
        "strategy": _optional_text(order_info.get("strategy"))
        or _optional_text(order_info.get("strategy_style"))
        or "",
        "account": _optional_text(order_info.get("account"))
        or _optional_text(order_info.get("strategy_family"))
        or "default",
    }


def _ticket_hash(order_info: Dict[str, Any], ticket_id: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            _ticket_hash_payload(order_info, ticket_id),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return digest


def _validated_ticket_hash(order_info: Dict[str, Any], ticket_id: str) -> str:
    computed = _ticket_hash(order_info, ticket_id)
    provided = _optional_text(order_info.get("ticket_hash"))
    if provided is None:
        raise ValueError("Missing orchestrator approval context: ticket_hash")
    if provided != computed:
        raise ValueError(
            f"Guarded trade ticket hash mismatch: provided {provided!r} does not match computed {computed!r}"
        )
    return provided


def _order_type(order_info: Dict[str, Any]) -> str:
    explicit = _optional_text(order_info.get("order_type")) or _optional_text(order_info.get("type"))
    if explicit is not None:
        return explicit
    limit_price = order_info.get("limit_price")
    if limit_price in (None, "", "market"):
        return "market"
    return "limit"


def _build_guarded_trade_payload(
    order_info: Dict[str, Any],
    *,
    notional: float,
    guarded: Dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "requester_kind": guarded["requester_kind"],
        "ticket_id": guarded["ticket_id"],
        "ticket_hash": _validated_ticket_hash(order_info, guarded["ticket_id"]),
        "strategy": _optional_text(order_info.get("strategy"))
        or _optional_text(order_info.get("strategy_style"))
        or "unspecified",
        "account": _optional_text(order_info.get("account"))
        or _optional_text(order_info.get("strategy_family"))
        or "default",
        "symbol": _optional_text(order_info.get("symbol")) or "",
        "side": _optional_text(order_info.get("side")) or "",
        "notional": notional,
        "asset_class": _optional_text(order_info.get("asset_class")) or "equity",
        "order_type": _order_type(order_info),
        "time_in_force": _optional_text(order_info.get("time_in_force")) or "day",
        "source_surface": _optional_text(order_info.get("source_surface")) or DEFAULT_SOURCE_SURFACE,
        "approval_issued_by": guarded["approval_issued_by"],
    }
    if order_info.get("qty") is not None:
        payload["qty"] = order_info.get("qty")
    if order_info.get("limit_price") not in (None, "", "market"):
        payload["limit_price"] = order_info.get("limit_price")
    for key in ("candidate_id", "package_id", "client_order_id", "router_run_id", "run_id"):
        if order_info.get(key) is not None:
            payload[key] = order_info.get(key)

    task_payload = build_guarded_task_payload(
        kind=guarded["kind"],
        target=guarded["target"],
        project=guarded["project"],
        requester_id=guarded["requester_id"],
        requester_name=guarded["requester_kind"],
        requester_channel=guarded["requester_channel"],
        approval_jti=guarded["approval_jti"],
        approval_reason=guarded["approval_reason"],
        approval_exp=guarded["approval_exp"],
        payload=payload,
    )
    approval_context = dict(task_payload.get("approval_context", {}))
    approval_context["approval_issued_by"] = guarded["approval_issued_by"]
    task_payload["approval_context"] = approval_context
    task_payload["approval_issued_by"] = guarded["approval_issued_by"]
    return task_payload


def request_approval(order_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate guarded approval context and submit one GS execution task.

    Args:
        order_info: normalized trade ticket plus approval context.

    Returns:
        dict with keys:
            approved (bool): whether the guarded submit succeeded
            decision (str): 'approved', 'disabled', 'below_threshold', or 'error'
            reason (str): human-readable reason
    """
    ts = datetime.now(timezone.utc).isoformat()
    approval_id = uuid.uuid4().hex[:12]

    _record_approval_event(
        event_type="approval_requested",
        timestamp=ts,
        approval_id=approval_id,
        order_info=order_info,
        decision="requested",
        reason="trade approval requested",
        approved=None,
        metadata={
            "fail_closed_mode": True,
            "mediation": "orchestrator",
            "trade_approval_enabled": _env_bool("TRADE_APPROVAL_ENABLED", False),
            "telegram_transport_disabled": True,
            "legacy_pending_file_bridge_disabled": True,
            "target": order_info.get("target"),
            "ticket_id": order_info.get("ticket_id"),
            "approval_jti": _approval_context_value(order_info, "approval_jti", "jti"),
            "approval_exp": _approval_context_value(order_info, "approval_exp", "exp"),
        },
    )

    if not _env_bool("TRADE_APPROVAL_ENABLED", False):
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "disabled",
            "TRADE_APPROVAL_ENABLED is false",
            metadata={"stage": "preflight", "mediation": "orchestrator"},
        )

    try:
        notional = float(order_info.get("notional", 0) or 0)
        if notional == 0:
            qty = float(order_info.get("qty", 0) or 0)
            price = float(order_info.get("limit_price", 0) or 0)
            notional = qty * price
    except (TypeError, ValueError) as exc:
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "error",
            f"Invalid trade sizing for approval: {exc}",
            metadata={
                "raw_notional": order_info.get("notional"),
                "raw_qty": order_info.get("qty"),
                "raw_limit_price": order_info.get("limit_price"),
            },
        )

    if notional < MIN_NOTIONAL_FOR_APPROVAL:
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "below_threshold",
            (
                f"Notional ${notional:.2f} < ${MIN_NOTIONAL_FOR_APPROVAL:.2f} "
                "threshold; blocking without explicit approval"
            ),
            metadata={
                "evaluated_notional": notional,
                "minimum_notional_for_approval": MIN_NOTIONAL_FOR_APPROVAL,
            },
        )

    try:
        timeout_seconds = _config_timeout_seconds()
    except ValueError as exc:
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "error",
            str(exc),
            metadata={"mediation": "orchestrator"},
        )

    try:
        guarded = _resolve_guarded_context(order_info)
        task_payload = _build_guarded_trade_payload(
            order_info,
            notional=notional,
            guarded=guarded,
        )
    except ValueError as exc:
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "error",
            str(exc),
            metadata={"mediation": "orchestrator"},
        )

    try:
        response = submit_task(
            task_payload,
            bearer_token=guarded["approval_token"],
            timeout=timeout_seconds,
        )
    except OrchestratorTaskClientError as exc:
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "error",
            f"Orchestrator mediation failed: {exc}",
            metadata={
                "target": guarded["target"],
                "kind": guarded["kind"],
                "approval_jti": guarded["approval_jti"],
                "timeout_seconds": timeout_seconds,
            },
        )

    run_id = _optional_text(response.get("run_id")) or _optional_text(response.get("task_id"))
    status = _optional_text(response.get("status")) or "submitted"
    if run_id is None or status.lower() in {"error", "failed", "rejected"}:
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "error",
            "Orchestrator mediation failed: guarded submit did not return an accepted run",
            metadata={
                "response": dict(response),
                "target": guarded["target"],
                "kind": guarded["kind"],
            },
        )

    result = {
        "approved": True,
        "decision": "approved",
        "reason": f"Orchestrator accepted guarded trade execution (run_id={run_id})",
    }
    _record_approval_event(
        event_type="approval_decision",
        timestamp=datetime.now(timezone.utc).isoformat(),
        approval_id=approval_id,
        order_info=order_info,
        decision=result["decision"],
        reason=result["reason"],
        approved=result["approved"],
        fail_closed_trigger=None,
        metadata={
            "run_id": run_id,
            "status": status,
            "kind": guarded["kind"],
            "target": guarded["target"],
            "approval_jti": guarded["approval_jti"],
            "approval_issued_by": guarded["approval_issued_by"],
            "approval_reason": guarded["approval_reason"],
            "approval_exp": guarded["approval_exp"],
            "requester_kind": guarded["requester_kind"],
            "requester_id": guarded["requester_id"],
            "requester_channel": guarded["requester_channel"],
        },
    )
    return result


def resolve_pending_approval(approval_id: str, decision: str):
    """
    Legacy local-file approval bridge.

    Text replies should no longer resolve trade approvals by writing local
    decision files; Tier-2 mediation belongs on the orchestrator token flow,
    not Telegram callback polling or local pending files.
    """
    logger.warning(
        "Rejecting legacy local-file approval resolution for %s (%r); %s %s",
        approval_id,
        decision,
        LEGACY_APPROVAL_BRIDGE_DISABLED_REASON,
        LEGACY_TELEGRAM_TRANSPORT_DISABLED_REASON,
    )
    return False


def get_pending_approvals() -> list:
    """Legacy local-file pending approvals are disabled."""
    return []


if __name__ == "__main__":
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Submit a guarded GS trade approval through wrkflo-orchestrator"
    )
    parser.add_argument("--test", action="store_true", help="Submit a mock guarded approval")
    parser.add_argument("--symbol", default="NVDA", help="Test symbol")
    parser.add_argument("--side", default="buy", help="Test side")
    parser.add_argument("--qty", type=int, default=1, help="Test quantity")
    parser.add_argument("--price", type=float, default=1000.0, help="Limit price")
    parser.add_argument("--ticket-id", default="demo-ticket", help="Trade ticket id")
    args = parser.parse_args()

    if not args.test:
        parser.print_help()
    else:
        mock_order = {
            "symbol": args.symbol,
            "side": args.side,
            "qty": args.qty,
            "limit_price": args.price,
            "notional": args.qty * args.price,
            "strategy_style": "demo",
            "requesting_agent": "trade_approval_cli",
            "requester_kind": "cli",
            "requester_id": "trade_approval_cli",
            "requester_channel": "trade_approval",
            "source_surface": "trade_approval_cli",
            "ticket_id": args.ticket_id,
            "approval_token": os.getenv("APPROVAL_TOKEN", ""),
            "approval_jti": os.getenv("APPROVAL_JTI", "demo-jti"),
            "approval_issued_by": os.getenv("APPROVAL_ISSUED_BY", "demo"),
            "approval_reason": os.getenv("APPROVAL_REASON", "demo approval"),
            "approval_exp": os.getenv(
                "APPROVAL_EXP",
                str(int(time.time()) + 600),
            ),
        }
        mock_order["ticket_hash"] = _ticket_hash(mock_order, mock_order["ticket_id"])
        os.environ.setdefault("TRADE_APPROVAL_ENABLED", "true")
        print(json.dumps(request_approval(mock_order), indent=2))
