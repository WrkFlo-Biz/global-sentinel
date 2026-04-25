#!/usr/bin/env python3
"""
Global Sentinel - Telegram Trade Approval Workflow

Before any trade is placed, sends a Telegram approval request to the Trading topic
with inline keyboard buttons. Uses callback_query polling (separate from getUpdates
message polling) to avoid conflicts with the existing TelegramCommandHandler.

Configuration (.env):
    TRADE_APPROVAL_ENABLED=true
    TRADE_APPROVAL_TIMEOUT=60
    TRADE_APPROVAL_AUTO_EXECUTE=false
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests

from src.core.openclaw_state_db import OpenClawStateDB

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
APPROVAL_LOG_PATH = REPO_ROOT / "logs" / "trade_approvals.jsonl"
# Legacy path kept only for stale cleanup/backwards-compatible test patching.
PENDING_DIR = Path("/tmp/gs_pending_approvals")
APPROVAL_AUDIT_SCHEMA_VERSION = "trade_approval_audit.v2"
STATE_DB_FILENAME = "state.db"
LEGACY_APPROVAL_BRIDGE_DISABLED_REASON = (
    "Local pending-approval files are disabled; route Tier-2 mediation through "
    "orchestrator approval tokens instead."
)

logger = logging.getLogger("global_sentinel.trade_approval")

# Minimum notional threshold used for policy classification. In fail-closed mode,
# below-threshold orders still block unless an explicit approval path says otherwise.
MIN_NOTIONAL_FOR_APPROVAL = 500.0


def _env_bool(key: str, default: bool = False) -> bool:
    v = os.getenv(key, "").lower().strip()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    return default


def _state_db_path() -> Path:
    override = os.getenv("GLOBAL_SENTINEL_STATE_DB_PATH") or os.getenv("OPENCLAW_STATE_DB_PATH")
    if override:
        return Path(override).expanduser()
    return REPO_ROOT / STATE_DB_FILENAME


def _requesting_agent(order_info: Dict[str, Any]) -> str:
    for key in ("requesting_agent", "source_agent", "agent_id", "agent", "worker", "signal_source"):
        value = order_info.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return "system"


def _log_approval_json(entry: Dict[str, Any]) -> None:
    try:
        APPROVAL_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(APPROVAL_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    except Exception as e:
        logger.warning("Failed to write trade approval audit JSON log: %s", e)


def _log_approval_state_db(entry: Dict[str, Any]) -> None:
    try:
        state_db = OpenClawStateDB(_state_db_path())
        state_db.append_audit_log(
            event_type=str(entry["event_type"]),
            timestamp=str(entry["timestamp"]),
            agent_id=str(entry["agent_id"]),
            decision=str(entry["decision"]),
            reason=str(entry["reason"]),
            entry=entry,
        )
    except Exception as e:
        logger.warning("Failed to write trade approval audit row to audit_log in state.db: %s", e)


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
    trade_details = _safe_order_summary(order_info)
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
        "trade_details": trade_details,
        "order": trade_details,
        "decision": decision,
        "reason": reason,
        "approved": approved,
        "fail_closed_trigger": fail_closed_trigger,
        "metadata": cleaned_metadata,
    }
    _log_approval_json(entry)
    _log_approval_state_db(entry)


def _normalize_decision(decision: Any) -> Optional[str]:
    if not isinstance(decision, str):
        return None
    normalized = decision.strip().lower()
    return {
        "approve": "approved",
        "approved": "approved",
        "reject": "rejected",
        "rejected": "rejected",
    }.get(normalized)


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
    if decision == "timeout":
        return "approval_timeout"
    if decision != "error":
        return None

    lowered_reason = reason.lower()
    if "invalid trade sizing" in lowered_reason:
        return "invalid_trade_sizing"
    if "invalid trade approval config" in lowered_reason:
        return "invalid_approval_config"
    if "missing telegram config" in lowered_reason:
        return "missing_telegram_config"
    if "failed to build trade approval request" in lowered_reason:
        return "approval_payload_build_error"
    if "failed to send telegram approval request" in lowered_reason:
        return "telegram_send_failure"
    if "invalid approval decision" in lowered_reason:
        return "invalid_approval_decision"
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


def _format_approval_message(order_info: Dict[str, Any], timeout: int, auto_exec: bool) -> str:
    """Format a trade approval Telegram message."""
    symbol = order_info.get("symbol", "???")
    side = order_info.get("side", "buy").upper()
    qty = order_info.get("qty", "?")
    limit_price = order_info.get("limit_price", "market")
    notional = order_info.get("notional", 0)
    signal = order_info.get("signal_source", "system")
    strategy = order_info.get("strategy_style", "")
    asset_class = order_info.get("asset_class", "equity")
    contract_id = order_info.get("contract_id", "")

    if limit_price and limit_price != "market":
        price_str = f"@ ${float(limit_price):.2f}"
    else:
        price_str = "@ MARKET"

    if asset_class == "option" and contract_id:
        asset_str = f"{symbol} ({contract_id})"
    else:
        asset_str = symbol

    auto_str = "Auto-execute" if auto_exec else "Auto-SKIP"

    lines = [
        "<b>TRADE APPROVAL REQUIRED</b>",
        "",
        f"<b>{side}</b> {qty}x {asset_str} {price_str}",
        f"Signal: {signal}",
        f"Notional: ${float(notional):,.2f}",
    ]
    if strategy:
        lines.append(f"Strategy: {strategy}")
    lines.extend([
        "",
        "Use the inline approval buttons below",
        f"{auto_str} in {timeout}s if no response",
    ])

    return "\n".join(lines)


def _send_with_buttons(
    text: str,
    approval_id: str,
    token: str,
    chat_id: str,
    thread_id: int,
) -> Optional[int]:
    """Send a Telegram message with inline YES/NO buttons. Returns message_id."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    inline_keyboard = {
        "inline_keyboard": [[
            {"text": "YES - Execute", "callback_data": f"approve:{approval_id}"},
            {"text": "NO - Skip", "callback_data": f"reject:{approval_id}"},
        ]]
    }
    payload = {
        "chat_id": chat_id,
        "text": text[:4096],
        "parse_mode": "HTML",
        "message_thread_id": thread_id,
        "reply_markup": json.dumps(inline_keyboard),
    }
    try:
        resp = requests.post(url, json=payload, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("result", {}).get("message_id")
        else:
            logger.error(f"Telegram send failed: {resp.status_code} {resp.text[:200]}")
            return None
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return None


def _poll_callback_query(
    approval_id: str,
    token: str,
    timeout_sec: int,
) -> Tuple[str, Optional[str]]:
    """
    Poll for callback_query matching our approval_id.

    Only processes Telegram callback_query updates. The legacy local-file bridge
    used by text replies is intentionally disabled so Tier-2 mediation does not
    bypass the orchestrator approval-token flow.
    """
    deadline = time.time() + timeout_sec
    poll_interval = 2
    # Use a dedicated offset for callback queries only
    cb_offset_file = Path("/tmp/gs_callback_offset.json")
    cb_offset = 0
    saw_poll_conflict = False
    if cb_offset_file.exists():
        try:
            cb_offset = json.loads(cb_offset_file.read_text()).get("offset", 0)
        except Exception:
            pass

    while time.time() < deadline:
        # Poll for callback queries only.
        try:
            remaining = int(deadline - time.time())
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            params = {
                "timeout": min(3, max(1, remaining)),
                "allowed_updates": json.dumps(["callback_query"]),
            }
            if cb_offset:
                params["offset"] = cb_offset

            resp = requests.get(url, params=params, timeout=10)
            if resp.status_code == 200:
                updates = resp.json().get("result", [])
                for update in updates:
                    update_id = update.get("update_id", 0)
                    cb_offset = update_id + 1
                    try:
                        cb_offset_file.write_text(json.dumps({"offset": cb_offset}))
                    except Exception:
                        pass

                    cb = update.get("callback_query")
                    if not cb:
                        continue

                    cb_data = cb.get("data", "")
                    cb_id = cb.get("id", "")

                    # Answer the callback to clear the loading indicator
                    try:
                        requests.post(
                            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
                            json={"callback_query_id": cb_id},
                            timeout=5,
                        )
                    except Exception:
                        pass

                    if cb_data == f"approve:{approval_id}":
                        _cleanup_pending(approval_id)
                        return "approved", "inline_button_yes"
                    elif cb_data == f"reject:{approval_id}":
                        _cleanup_pending(approval_id)
                        return "rejected", "inline_button_no"
            elif resp.status_code == 409:
                if not saw_poll_conflict:
                    saw_poll_conflict = True
                    logger.warning(
                        "Telegram callback polling conflict for approval %s; %s",
                        approval_id,
                        LEGACY_APPROVAL_BRIDGE_DISABLED_REASON,
                    )
        except Exception as e:
            logger.debug(f"Callback poll error: {e}")

        time.sleep(poll_interval)

    _cleanup_pending(approval_id)
    return "timeout", None


def _cleanup_pending(approval_id: str):
    """Remove pending/decision files."""
    try:
        (PENDING_DIR / f"{approval_id}.pending").unlink(missing_ok=True)
        (PENDING_DIR / f"{approval_id}.decision").unlink(missing_ok=True)
    except Exception:
        pass


def _send_confirmation(approved: bool, order_info: dict, reason: str, token: str, chat_id: str, thread_id: int):
    """Send a confirmation message."""
    emoji = "\u2705" if approved else "\u274c"
    action = "EXECUTED" if approved else "SKIPPED"
    text = f"{emoji} Trade {action}: {order_info.get('side', '').upper()} {order_info.get('qty', '?')}x {order_info.get('symbol', '?')} -- {reason}"
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "message_thread_id": thread_id,
        }, timeout=10)
    except Exception as e:
        logger.warning(f"Failed to send confirmation: {e}")


def request_approval(order_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Request trade approval via Telegram.

    Args:
        order_info: dict with keys like symbol, side, qty, limit_price, notional,
                    signal_source, strategy_style, asset_class, contract_id, etc.

    Returns:
        dict with keys:
            approved (bool): whether the trade should proceed
            decision (str): 'approved', 'rejected', 'timeout', 'disabled',
                          'below_threshold', 'error'
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
            "trade_approval_enabled": _env_bool("TRADE_APPROVAL_ENABLED", False),
            "configured_timeout_sec": os.getenv("TRADE_APPROVAL_TIMEOUT", "60"),
            "auto_execute_requested": _env_bool("TRADE_APPROVAL_AUTO_EXECUTE", False),
        },
    )

    # Check if approval is enabled
    if not _env_bool("TRADE_APPROVAL_ENABLED", False):
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "disabled",
            "TRADE_APPROVAL_ENABLED is false",
            metadata={"stage": "preflight"},
        )

    # Check notional threshold
    try:
        notional = float(order_info.get("notional", 0) or 0)
        if notional == 0:
            qty = float(order_info.get("qty", 0) or 0)
            price = float(order_info.get("limit_price", 0) or 0)
            notional = qty * price
    except (TypeError, ValueError) as e:
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "error",
            f"Invalid trade sizing for approval: {e}",
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
            f"Notional ${notional:.2f} < ${MIN_NOTIONAL_FOR_APPROVAL:.2f} threshold; blocking without explicit approval",
            metadata={
                "evaluated_notional": notional,
                "minimum_notional_for_approval": MIN_NOTIONAL_FOR_APPROVAL,
            },
        )

    # Get Telegram config
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_TOPIC_CHAT_ID", os.getenv("TELEGRAM_CHAT_ID", ""))
    try:
        thread_id = int(os.getenv("TELEGRAM_TRADING_THREAD_ID", "0"))
        timeout_sec = int(os.getenv("TRADE_APPROVAL_TIMEOUT", "60"))
    except ValueError as e:
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "error",
            f"Invalid trade approval config: {e}",
            metadata={
                "configured_thread_id": os.getenv("TELEGRAM_TRADING_THREAD_ID", "0"),
                "configured_timeout_sec": os.getenv("TRADE_APPROVAL_TIMEOUT", "60"),
            },
        )
    auto_execute = _env_bool("TRADE_APPROVAL_AUTO_EXECUTE", False)
    if auto_execute:
        logger.warning("TRADE_APPROVAL_AUTO_EXECUTE is ignored in fail-closed mode")
        auto_execute = False

    if not token or not chat_id:
        logger.error("Missing TELEGRAM_BOT_TOKEN or chat_id for trade approval")
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "error",
            "Missing Telegram config for trade approval",
            metadata={"token_present": bool(token), "chat_id_present": bool(chat_id)},
        )

    # Build approval request; malformed payloads must fail closed.
    try:
        msg_text = _format_approval_message(order_info, timeout_sec, auto_execute)
    except Exception as e:
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "error",
            f"Failed to build trade approval request: {e}",
            metadata={"timeout_sec": timeout_sec},
        )

    # Send approval request with inline buttons
    msg_id = _send_with_buttons(msg_text, approval_id, token, chat_id, thread_id)

    if msg_id is None:
        return _blocked_result(
            ts,
            approval_id,
            order_info,
            "error",
            "Failed to send Telegram approval request",
            metadata={"timeout_sec": timeout_sec, "message_thread_id": thread_id},
        )

    logger.info(f"Trade approval sent (msg_id={msg_id}, approval_id={approval_id}), waiting {timeout_sec}s...")

    # Poll for response
    decision, raw_text = _poll_callback_query(approval_id, token, timeout_sec)

    if decision == "approved":
        result = {"approved": True, "decision": "approved", "reason": f"User approved: '{raw_text}'"}
    elif decision == "rejected":
        result = {"approved": False, "decision": "rejected", "reason": f"User rejected: '{raw_text}'"}
    elif decision == "timeout":
        result = {"approved": False, "decision": "timeout", "reason": f"No response in {timeout_sec}s, blocking trade"}
    else:
        result = {"approved": False, "decision": "error", "reason": f"Invalid approval decision: {decision}"}
    if not result["approved"]:
        _log_trade_blocked(order_info, result["decision"], result["reason"])

    # Send confirmation
    _send_confirmation(result["approved"], order_info, result["reason"], token, chat_id, thread_id)

    _record_approval_event(
        event_type="approval_decision",
        timestamp=datetime.now(timezone.utc).isoformat(),
        approval_id=approval_id,
        order_info=order_info,
        decision=result["decision"],
        reason=result["reason"],
        approved=result["approved"],
        fail_closed_trigger=_fail_closed_trigger(result["decision"], result["reason"]),
        metadata={
            "message_id": msg_id,
            "message_thread_id": thread_id,
            "timeout_sec": timeout_sec,
            "raw_response": raw_text,
        },
    )
    return result


def resolve_pending_approval(approval_id: str, decision: str):
    """
    Legacy local-file approval bridge.

    Text replies should no longer resolve trade approvals by writing local
    decision files; Tier-2 mediation belongs on the orchestrator token flow.
    """
    logger.warning(
        "Rejecting legacy local-file approval resolution for %s (%r); %s",
        approval_id,
        decision,
        LEGACY_APPROVAL_BRIDGE_DISABLED_REASON,
    )
    return False


def get_pending_approvals() -> list:
    """Legacy local-file pending approvals are disabled."""
    return []


def _safe_order_summary(order_info: Dict[str, Any]) -> dict:
    return {
        "symbol": order_info.get("symbol"),
        "side": order_info.get("side"),
        "qty": order_info.get("qty"),
        "type": order_info.get("type"),
        "limit_price": order_info.get("limit_price"),
        "notional": order_info.get("notional"),
        "asset_class": order_info.get("asset_class"),
        "strategy_style": order_info.get("strategy_style"),
        "signal_source": order_info.get("signal_source"),
        "contract_id": order_info.get("contract_id"),
    }


# ── CLI for testing ──
if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Trade Approval Workflow")
    parser.add_argument("--test", action="store_true", help="Send a mock approval request")
    parser.add_argument("--symbol", default="NVDA", help="Test symbol")
    parser.add_argument("--side", default="buy", help="Test side")
    parser.add_argument("--qty", type=int, default=5, help="Test quantity")
    parser.add_argument("--price", type=float, default=2.50, help="Test price")
    parser.add_argument("--timeout", type=int, default=15, help="Approval timeout for test (default 15s)")
    parser.add_argument("--no-wait", action="store_true", help="Just send the message, do not poll")
    args = parser.parse_args()

    # Load .env
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

    if args.test:
        os.environ["TRADE_APPROVAL_ENABLED"] = "true"
        os.environ["TRADE_APPROVAL_TIMEOUT"] = str(args.timeout)
        mock_order = {
            "symbol": args.symbol,
            "side": args.side,
            "qty": args.qty,
            "type": "limit",
            "limit_price": args.price,
            "notional": args.qty * args.price * 100,
            "signal_source": "ICT/SMC + momentum consensus (TEST)",
            "strategy_style": "momentum_day_trade",
            "asset_class": "option",
            "contract_id": f"{args.symbol}260328C00170000",
        }
        if args.no_wait:
            # Just send the message, don't poll
            token = os.getenv("TELEGRAM_BOT_TOKEN", "")
            chat_id = os.getenv("TELEGRAM_TOPIC_CHAT_ID", "")
            thread_id = int(os.getenv("TELEGRAM_TRADING_THREAD_ID", "0"))
            approval_id = uuid.uuid4().hex[:12]
            msg_text = _format_approval_message(mock_order, args.timeout, True)
            msg_id = _send_with_buttons(msg_text, approval_id, token, chat_id, thread_id)
            print(f"Message sent: msg_id={msg_id}, approval_id={approval_id}")
        else:
            print(f"Sending mock approval for {args.side.upper()} {args.qty}x {args.symbol} @ ${args.price}...")
            result = request_approval(mock_order)
            print(f"\nResult: {json.dumps(result, indent=2)}")
    else:
        parser.print_help()
