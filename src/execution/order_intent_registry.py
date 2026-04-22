#!/usr/bin/env python3
"""
Global Sentinel V4.6 - Order Intent Registry

Purpose:
- Persist order intents BEFORE any broker submission
- Link package/candidate decisions to broker order identifiers
- Provide durable reconciliation keys (intent_id, candidate_id, package_id, client_order_id)
- Maintain append-only audit trail + latest state snapshots

Shadow-first:
- Safe for paper/sandbox and shadow routing
- No direct broker routing in this module
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


VALID_INTENT_STATUSES = {
    "draft",
    "submitted",
    "acknowledged",
    "open",
    "partially_filled",
    "filled",
    "canceled",
    "rejected",
    "expired",
    "manual_review",
}


class OrderIntentRegistry:
    def __init__(
        self,
        repo_root: Path,
        intents_relpath: str = "logs/execution/order_intents.jsonl",
        snapshots_relpath: str = "logs/execution/order_intent_snapshots.jsonl",
    ):
        self.repo_root = repo_root
        self.intents_path = repo_root / intents_relpath
        self.snapshots_path = repo_root / snapshots_relpath

        self.intents_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshots_path.parent.mkdir(parents=True, exist_ok=True)

    # -------------------------
    # Creation
    # -------------------------
    def create_intent_from_candidate(
        self,
        package: Dict[str, Any],
        candidate: Dict[str, Any],
        order_request: Dict[str, Any],
        shadow_mode: bool = True,
        extra_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Create an order intent before broker submission.

        `order_request` should roughly match canonical adapter request schema:
        {
          symbol, side, type, qty, limit_price, time_in_force, ...
        }
        """
        package_id = self._ensure_package_id(package)
        candidate_id = self._ensure_candidate_id(candidate, package_id=package_id)
        intent_id = f"intent-{uuid.uuid4().hex[:16]}"
        client_order_id = self._build_client_order_id(
            package_id=package_id,
            candidate_id=candidate_id,
            symbol=order_request.get("symbol") or candidate.get("symbol"),
        )

        intent = {
            "schema_version": "order_intent.v1",
            "intent_id": intent_id,
            "package_id": package_id,
            "candidate_id": candidate_id,
            "client_order_id": client_order_id,

            "timestamp_utc": iso_now(),
            "status": "draft",
            "shadow_mode": bool(shadow_mode),

            "package_context": {
                "package_type": package.get("package_type"),
                "package_timestamp_utc": package.get("timestamp_utc"),
                "effective_mode": package.get("effective_mode"),
                "regime_shift_probability": package.get("regime_shift_probability"),
                "time_window_name": ((package.get("window_context") or {}).get("time_window_name")),
                "watchlist_only_window": ((package.get("window_context") or {}).get("watchlist_only_window")),
            },

            "candidate_context": {
                "symbol": candidate.get("symbol"),
                "strategy": candidate.get("strategy"),
                "strategy_style": candidate.get("strategy_style"),
                "strategy_family": candidate.get("strategy_family"),
                "underlying_strategy": candidate.get("underlying_strategy"),
                "learning_adjusted": candidate.get("learning_adjusted"),
                "learning_adjustment_detail": candidate.get("learning_adjustment_detail"),
                "event_novelty_score": candidate.get("event_novelty_score"),
                "expected_edge_bps": candidate.get("expected_edge_bps"),
                "expected_cost_bps": candidate.get("expected_cost_bps"),
                "net_expected_value_bps": candidate.get("net_expected_value_bps"),
                "template_key": candidate.get("template_key"),
                "direction": candidate.get("direction"),
                "holding_period": candidate.get("holding_period"),
                "confidence_score": candidate.get("confidence_score"),
                "size_multiplier_suggestion": candidate.get("size_multiplier_suggestion"),
                "entry_signal": candidate.get("entry_signal"),
                "rationale": candidate.get("rationale"),
                "price_hints": candidate.get("price_hints"),
                "themes": candidate.get("themes"),
                "metadata": candidate.get("metadata"),
                "block_reasons": candidate.get("block_reasons"),
                "execution_constraints": candidate.get("execution_constraints"),
                "fill_sim_assessment": candidate.get("fill_sim_assessment"),
            },

            "order_request": {
                **order_request,
                "client_order_id": client_order_id,
                "shadow_mode": bool(shadow_mode),
                "strategy_context": {
                    "package_id": package_id,
                    "candidate_id": candidate_id,
                    "intent_id": intent_id,
                    "time_window_name": ((package.get("window_context") or {}).get("time_window_name")),
                },
            },

            "broker_binding": {
                "broker_name": None,
                "broker_account_id": None,
                "broker_order_id": None,
                "submitted_at_utc": None,
                "acknowledged_at_utc": None,
            },

            "broker_state": None,      # latest normalized broker order state snapshot
            "broker_trades": [],       # latest normalized fills (optional)
            "reconciliation": {
                "last_reconciled_at_utc": None,
                "reconciler_status": None,
                "notes": [],
            },

            "audit": {
                "created_at_utc": iso_now(),
                "updated_at_utc": iso_now(),
                "history": [
                    {
                        "ts": iso_now(),
                        "event": "intent_created",
                        "status": "draft",
                        "details": {"shadow_mode": bool(shadow_mode)}
                    }
                ]
            },

            "runtime_flags": ((extra_context or {}).get("runtime_flags") or {}) if isinstance(extra_context, dict) else {},
            "order_lifecycle_policy": ((extra_context or {}).get("order_lifecycle_policy") or {}) if isinstance(extra_context, dict) else {},

            "extra_context": extra_context or {},
        }

        self._append_jsonl(self.intents_path, intent)
        self._append_snapshot(intent, event_type="intent_created")

        return intent

    # -------------------------
    # Lookup / latest state
    # -------------------------
    def list_intents(
        self,
        statuses: Optional[List[str]] = None,
        package_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        rows = self._read_jsonl(self.intents_path)
        latest = self._latest_by_intent_id(rows)

        if statuses:
            allowed = set(statuses)
            latest = [r for r in latest if r.get("status") in allowed]
        if package_ids is not None:
            allowed_pkgs = set(package_ids)
            latest = [r for r in latest if r.get("package_id") in allowed_pkgs]
        return latest

    def get_intent(self, intent_id: str) -> Optional[Dict[str, Any]]:
        rows = self._read_jsonl(self.intents_path)
        matches = [r for r in rows if r.get("intent_id") == intent_id]
        return matches[-1] if matches else None

    def get_by_client_order_id(self, client_order_id: str) -> Optional[Dict[str, Any]]:
        rows = self._read_jsonl(self.intents_path)
        matches = [r for r in rows if r.get("client_order_id") == client_order_id]
        return matches[-1] if matches else None

    def get_by_broker_order_id(self, broker_order_id: str) -> Optional[Dict[str, Any]]:
        rows = self._read_jsonl(self.intents_path)
        matches = [
            r for r in rows
            if str((((r.get("broker_binding") or {}).get("broker_order_id")) or "")) == str(broker_order_id)
        ]
        return matches[-1] if matches else None

    # -------------------------
    # Mutations (append-only)
    # -------------------------
    def update_status(
        self,
        intent_id: str,
        new_status: str,
        event: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if new_status not in VALID_INTENT_STATUSES:
            raise ValueError(f"Invalid intent status: {new_status}")

        intent = self.get_intent(intent_id)
        if not intent:
            raise ValueError(f"Intent not found: {intent_id}")

        intent = dict(intent)
        intent["status"] = new_status
        self._add_history(intent, event=event, status=new_status, details=details or {})
        self._stamp_updated(intent)

        self._append_jsonl(self.intents_path, intent)
        self._append_snapshot(intent, event_type=event)
        return intent

    def bind_broker_order(
        self,
        intent_id: str,
        broker_name: str,
        broker_account_id: Optional[str],
        broker_order: Dict[str, Any],
    ) -> Dict[str, Any]:
        intent = self.get_intent(intent_id)
        if not intent:
            raise ValueError(f"Intent not found: {intent_id}")

        intent = dict(intent)
        broker_binding = dict(intent.get("broker_binding") or {})
        broker_binding.update({
            "broker_name": broker_name,
            "broker_account_id": broker_account_id,
            "broker_order_id": broker_order.get("order_id"),
            "submitted_at_utc": broker_order.get("submitted_at_utc") or broker_binding.get("submitted_at_utc"),
            "acknowledged_at_utc": broker_order.get("acknowledged_at_utc") or broker_binding.get("acknowledged_at_utc"),
        })
        intent["broker_binding"] = broker_binding
        intent["broker_state"] = broker_order

        mapped_status = self._map_broker_status_to_intent_status(broker_order.get("status"))
        intent["status"] = mapped_status

        self._add_history(
            intent,
            event="broker_order_bound",
            status=mapped_status,
            details={
                "broker_name": broker_name,
                "broker_order_id": broker_order.get("order_id"),
                "broker_status": broker_order.get("status"),
            },
        )
        self._stamp_updated(intent)
        self._append_jsonl(self.intents_path, intent)
        self._append_snapshot(intent, event_type="broker_order_bound")
        return intent

    def record_broker_reconciliation(
        self,
        intent_id: str,
        broker_order: Optional[Dict[str, Any]] = None,
        broker_trades: Optional[List[Dict[str, Any]]] = None,
        reconciler_status: str = "ok",
        notes: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        intent = self.get_intent(intent_id)
        if not intent:
            raise ValueError(f"Intent not found: {intent_id}")

        intent = dict(intent)

        if broker_order is not None:
            intent["broker_state"] = broker_order
            intent["status"] = self._map_broker_status_to_intent_status(broker_order.get("status"))

        if broker_trades is not None:
            intent["broker_trades"] = broker_trades

        recon = dict(intent.get("reconciliation") or {})
        recon["last_reconciled_at_utc"] = iso_now()
        recon["reconciler_status"] = reconciler_status
        if notes:
            existing = recon.get("notes") or []
            recon["notes"] = (existing + notes)[-50:]
        intent["reconciliation"] = recon

        self._add_history(
            intent,
            event="broker_reconciled",
            status=intent["status"],
            details={
                "reconciler_status": reconciler_status,
                "broker_order_status": (broker_order or {}).get("status") if broker_order else None,
                "trade_count": len(broker_trades or []),
            },
        )
        self._stamp_updated(intent)
        self._append_jsonl(self.intents_path, intent)
        self._append_snapshot(intent, event_type="broker_reconciled")
        return intent

    def mark_manual_review(self, intent_id: str, reason: str, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        payload = {"reason": reason}
        if details:
            payload.update(details)
        return self.update_status(intent_id, "manual_review", event="manual_review_required", details=payload)

    # -------------------------
    # Internal helpers
    # -------------------------
    def _ensure_package_id(self, package: Dict[str, Any]) -> str:
        if package.get("package_id"):
            return str(package["package_id"])
        # derive deterministic-ish id from package timestamp/type
        raw = f"{package.get('timestamp_utc')}|{package.get('package_type')}|{package.get('effective_mode')}"
        return f"pkg-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:12]}"

    def _ensure_candidate_id(self, candidate: Dict[str, Any], package_id: str) -> str:
        if candidate.get("candidate_id"):
            return str(candidate["candidate_id"])
        raw = f"{package_id}|{candidate.get('symbol')}|{candidate.get('template_key')}|{candidate.get('strategy_style')}|{candidate.get('window_name')}"
        return f"cand-{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:14]}"

    def _build_client_order_id(self, package_id: str, candidate_id: str, symbol: str) -> str:
        # keep short for broker limits; deterministic enough
        raw = f"{package_id}|{candidate_id}|{symbol}"
        h = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return f"gs-{h}"

    def _map_broker_status_to_intent_status(self, broker_status: Any) -> str:
        s = str(broker_status or "").lower()
        if s in {"new", "accepted", "pending"}:
            return "open"
        if s in {"partially_filled"}:
            return "partially_filled"
        if s in {"filled"}:
            return "filled"
        if s in {"canceled"}:
            return "canceled"
        if s in {"expired"}:
            return "expired"
        if s in {"rejected"}:
            return "rejected"
        if s in {"done_for_day"}:
            return "open"
        return "manual_review"

    def _add_history(self, intent: Dict[str, Any], event: str, status: str, details: Dict[str, Any]):
        audit = dict(intent.get("audit") or {})
        history = list(audit.get("history") or [])
        history.append({
            "ts": iso_now(),
            "event": event,
            "status": status,
            "details": details,
        })
        audit["history"] = history[-200:]
        intent["audit"] = audit

    def _stamp_updated(self, intent: Dict[str, Any]):
        audit = dict(intent.get("audit") or {})
        audit["updated_at_utc"] = iso_now()
        intent["audit"] = audit

    def _append_snapshot(self, intent: Dict[str, Any], event_type: str):
        snapshot = {
            "schema_version": "order_intent_snapshot.v1",
            "timestamp_utc": iso_now(),
            "event_type": event_type,
            "intent_id": intent.get("intent_id"),
            "package_id": intent.get("package_id"),
            "candidate_id": intent.get("candidate_id"),
            "client_order_id": intent.get("client_order_id"),
            "status": intent.get("status"),
            "shadow_mode": intent.get("shadow_mode"),
            "symbol": ((intent.get("candidate_context") or {}).get("symbol")),
            "broker_name": ((intent.get("broker_binding") or {}).get("broker_name")),
            "broker_order_id": ((intent.get("broker_binding") or {}).get("broker_order_id")),
            "broker_status": ((intent.get("broker_state") or {}).get("status")) if intent.get("broker_state") else None,
            "reconciler_status": ((intent.get("reconciliation") or {}).get("reconciler_status")),
        }
        self._append_jsonl(self.snapshots_path, snapshot)

    def _append_jsonl(self, path: Path, record: Dict[str, Any]):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    out.append(obj)
            except Exception:
                continue
        return out

    def _latest_by_intent_id(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        latest = {}
        for r in rows:
            iid = r.get("intent_id")
            if iid:
                latest[iid] = r
        return list(latest.values())


# -------------------------
# CLI (utility)
# -------------------------
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    sub = p.add_subparsers(dest="cmd", required=True)

    ls = sub.add_parser("list")
    ls.add_argument("--statuses", nargs="*", default=None)

    get = sub.add_parser("get")
    get.add_argument("--intent-id", required=True)

    manual = sub.add_parser("manual-review")
    manual.add_argument("--intent-id", required=True)
    manual.add_argument("--reason", required=True)

    return p.parse_args()


def main():
    args = parse_args()
    reg = OrderIntentRegistry(Path(args.repo_root).resolve())

    if args.cmd == "list":
        rows = reg.list_intents(statuses=args.statuses)
        print(json.dumps({"count": len(rows), "intents": rows}, indent=2))
        return

    if args.cmd == "get":
        row = reg.get_intent(args.intent_id)
        print(json.dumps(row, indent=2))
        return

    if args.cmd == "manual-review":
        row = reg.mark_manual_review(args.intent_id, args.reason)
        print(json.dumps(row, indent=2))
        return


if __name__ == "__main__":
    main()
