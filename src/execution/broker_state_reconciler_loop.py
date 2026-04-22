#!/usr/bin/env python3
"""
Global Sentinel V4.6 - Broker State Reconciler Loop

Purpose:
- Continuously reconcile broker order state + fills against Order Intent Registry
- Keep intent lifecycle current and auditable
- Detect mismatches and escalate to manual_review when needed

Supports:
- MockBrokerAdapter (CI/smoke)
- AlpacaPaperAdapter
- TradierSandboxAdapter

Env:
- BROKER_ADAPTER=mock | alpaca_paper | tradier_sandbox
- RECONCILER_LOOP_SECONDS (default 30)
"""

from __future__ import annotations
import gc

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BrokerStateReconcilerLoop:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.log_path = repo_root / "logs" / "execution" / "broker_reconciler_loop.jsonl"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        # imports kept local to avoid hard dependency issues when not used
        from src.execution.order_intent_registry import OrderIntentRegistry
        self.registry = OrderIntentRegistry(repo_root)

        self.broker_name = os.getenv("BROKER_ADAPTER", "alpaca_paper").strip().lower()
        self.adapter = self._build_adapter(self.broker_name)
        self.broker_account_id = self._infer_broker_account_id()

    def _build_adapter(self, broker_name: str):
        if broker_name == "mock":
            from tests.broker_conformance.fixtures.mock_broker_adapter import MockBrokerAdapter
            return MockBrokerAdapter()
        if broker_name == "alpaca_paper":
            from src.execution.alpaca_paper_adapter import AlpacaPaperAdapter
            return AlpacaPaperAdapter()
        if broker_name == "tradier_sandbox":
            from src.execution.tradier_sandbox_adapter import TradierSandboxAdapter
            return TradierSandboxAdapter()
        raise ValueError(f"Unsupported BROKER_ADAPTER: {broker_name}")

    def _infer_broker_account_id(self) -> Optional[str]:
        if self.broker_name == "tradier_sandbox":
            return os.getenv("TRADIER_ACCOUNT_ID")
        if self.broker_name == "alpaca_paper":
            # Alpaca account id not required for API path in our adapter; keep optional
            return None
        return None

    def run_once(self) -> Dict[str, Any]:
        active_statuses = ["submitted", "acknowledged", "open", "partially_filled", "manual_review"]
        intents = self.registry.list_intents(statuses=active_statuses)

        # Fetch open orders once for faster client_order_id lookup fallback
        try:
            open_orders = self.adapter.list_open_orders()
        except Exception as e:
            self._log_event("reconciler_error", {"stage": "list_open_orders", "error": str(e)})
            open_orders = []

        # Snapshot positions for P&L tracking
        try:
            from src.execution.performance_tracker import PerformanceTracker
            tracker = PerformanceTracker(self.repo_root)
            positions = self.adapter.list_positions()
            if positions:
                tracker.snapshot_open_positions([{
                    "symbol": p.get("symbol"),
                    "qty": p.get("qty"),
                    "avg_entry_price": p.get("avg_entry_price"),
                    "current_price": p.get("market_value", 0) / max(abs(p.get("qty", 1)), 1) if p.get("qty") else 0,
                    "unrealized_pl": p.get("unrealized_pl", 0),
                } for p in positions])
        except Exception as _pos_err:
            import logging as _rec_log
            _rec_log.getLogger("global_sentinel.reconciler").warning(
                "Position snapshot failed: %s", _pos_err)

        open_orders_by_broker_id = {str(o.get("order_id")): o for o in open_orders if o.get("order_id")}
        open_orders_by_client_id = {str(o.get("client_order_id")): o for o in open_orders if o.get("client_order_id")}

        results = {
            "timestamp_utc": iso_now(),
            "broker_name": self.broker_name,
            "active_intent_count": len(intents),
            "reconciled_count": 0,
            "manual_review_count": 0,
            "errors": [],
            "notes": [],
        }

        for intent in intents:
            try:
                self._reconcile_intent(
                    intent=intent,
                    open_orders_by_broker_id=open_orders_by_broker_id,
                    open_orders_by_client_id=open_orders_by_client_id,
                )
                results["reconciled_count"] += 1
            except Exception as e:
                results["errors"].append({
                    "intent_id": intent.get("intent_id"),
                    "error": str(e),
                })
                self._log_event("reconcile_intent_error", {
                    "intent_id": intent.get("intent_id"),
                    "error": str(e),
                })

        self._log_event("run_once_summary", results)
        return results

    def _reconcile_intent(
        self,
        intent: Dict[str, Any],
        open_orders_by_broker_id: Dict[str, Dict[str, Any]],
        open_orders_by_client_id: Dict[str, Dict[str, Any]],
    ):
        intent_id = intent["intent_id"]
        client_order_id = intent.get("client_order_id")
        broker_binding = intent.get("broker_binding") or {}
        broker_order_id = broker_binding.get("broker_order_id")

        broker_order = None
        broker_trades = []

        # Priority 1: known broker order id
        if broker_order_id:
            # Skip API lookup for mock order IDs - they don't exist in the broker
            if str(broker_order_id).startswith("mock-"):
                self._log_event("skip_mock_order", {
                    "intent_id": intent_id,
                    "broker_order_id": broker_order_id,
                    "reason": "mock_order_id_skipped",
                })
                return
            try:
                broker_order = self.adapter.get_order(str(broker_order_id))
            except Exception as e:
                # fallback to open orders cache
                broker_order = open_orders_by_broker_id.get(str(broker_order_id))
                if broker_order is None:
                    self._log_event("broker_get_order_failed", {
                        "intent_id": intent_id,
                        "broker_order_id": broker_order_id,
                        "error": str(e),
                    })

        # Priority 2: client_order_id lookup from open orders cache
        if broker_order is None and client_order_id:
            broker_order = open_orders_by_client_id.get(str(client_order_id))

        # Priority 3: if still no broker order found
        if broker_order is None:
            # If intent is still draft-like, mark manual review if it should have been submitted
            notes = [f"no_broker_match_found_for_client_order_id:{client_order_id}"]
            self.registry.record_broker_reconciliation(
                intent_id=intent_id,
                broker_order=None,
                broker_trades=[],
                reconciler_status="no_match",
                notes=notes,
            )
            return

        # Bind broker order if missing binding
        if not broker_binding.get("broker_order_id"):
            self.registry.bind_broker_order(
                intent_id=intent_id,
                broker_name=self.broker_name,
                broker_account_id=self.broker_account_id,
                broker_order=broker_order,
            )

        # Pull trades (best effort)
        try:
            if broker_order.get("order_id"):
                broker_trades = self.adapter.get_trades(str(broker_order.get("order_id")))
        except Exception as e:
            self._log_event("broker_get_trades_failed", {
                "intent_id": intent_id,
                "broker_order_id": broker_order.get("order_id"),
                "error": str(e),
            })
            broker_trades = []

        # Reconcile/update registry
        notes = self._compare_intent_vs_broker(intent, broker_order, broker_trades)
        reconciler_status = "ok" if not notes else "warning"
        updated = self.registry.record_broker_reconciliation(
            intent_id=intent_id,
            broker_order=broker_order,
            broker_trades=broker_trades,
            reconciler_status=reconciler_status,
            notes=notes,
        )

        # Escalate certain mismatches to manual review
        if any("mismatch" in n or "unexpected" in n for n in notes):
            self.registry.mark_manual_review(intent_id, reason="reconciliation_mismatch", details={"notes": notes})

        self._log_event("intent_reconciled", {
            "intent_id": intent_id,
            "status": updated.get("status"),
            "broker_order_id": ((updated.get("broker_binding") or {}).get("broker_order_id")),
            "broker_status": ((updated.get("broker_state") or {}).get("status")) if updated.get("broker_state") else None,
            "trade_count": len(updated.get("broker_trades") or []),
            "notes": notes,
        })

    def _compare_intent_vs_broker(
        self,
        intent: Dict[str, Any],
        broker_order: Dict[str, Any],
        broker_trades: List[Dict[str, Any]],
    ) -> List[str]:
        notes = []
        order_req = intent.get("order_request") or {}
        intent.get("candidate_context")  # noqa: F841

        # Symbol mismatch
        if order_req.get("symbol") and broker_order.get("symbol") and str(order_req["symbol"]) != str(broker_order["symbol"]):
            notes.append(f"mismatch_symbol:{order_req.get('symbol')}!={broker_order.get('symbol')}")

        # Side mismatch
        if order_req.get("side") and broker_order.get("side") and str(order_req["side"]).lower() != str(broker_order["side"]).lower():
            notes.append(f"mismatch_side:{order_req.get('side')}!={broker_order.get('side')}")

        # Type mismatch
        if order_req.get("type") and broker_order.get("type") and str(order_req["type"]).lower() != str(broker_order["type"]).lower():
            notes.append(f"mismatch_type:{order_req.get('type')}!={broker_order.get('type')}")

        # Quantity mismatch (allow float tolerance)
        rq_qty = order_req.get("qty")
        bo_qty = broker_order.get("qty")
        try:
            if rq_qty is not None and bo_qty is not None and abs(float(rq_qty) - float(bo_qty)) > 1e-9:
                notes.append(f"mismatch_qty:{rq_qty}!={bo_qty}")
        except Exception:
            pass

        # Shadow guard
        if not order_req.get("shadow_mode", False):
            notes.append("unexpected_live_intent_in_shadow_pipeline")

        # Broker reject
        if str(broker_order.get("status", "")).lower() == "rejected":
            notes.append(f"broker_rejected:{broker_order.get('reject_reason_code')}")

        # Filled but no trades present (adapter limitation may explain, so soft note)
        if str(broker_order.get("status", "")).lower() in {"filled", "partially_filled"} and len(broker_trades) == 0:
            notes.append("warning_filled_status_without_trade_records")

        return notes

    def _log_event(self, event_type: str, payload: Dict[str, Any]):
        rec = {
            "schema_version": "broker_reconciler_event.v1",
            "timestamp_utc": iso_now(),
            "broker_name": self.broker_name,
            "event_type": event_type,
            "payload": payload,
        }
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def run_forever(self, loop_seconds: int = 30):
        while True:
            try:
                self.run_once()
            except Exception as e:
                self._log_event("reconciler_loop_crash", {"error": str(e)})
            time.sleep(loop_seconds)
            gc.collect()


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--once", action="store_true")
    p.add_argument("--loop-seconds", type=int, default=int(os.getenv("RECONCILER_LOOP_SECONDS", "30")))
    return p.parse_args()


def main():
    args = parse_args()
    loop = BrokerStateReconcilerLoop(Path(args.repo_root).resolve())
    if args.once:
        print(json.dumps(loop.run_once(), indent=2))
    else:
        loop.run_forever(loop_seconds=args.loop_seconds)


if __name__ == "__main__":
    main()
