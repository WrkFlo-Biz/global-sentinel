#!/usr/bin/env python3
"""
Global Sentinel V4.7 - Shadow Order Router

Purpose:
- Convert package candidates into canonical broker order requests (shadow only)
- Register intents in OrderIntentRegistry before submission
- Submit to broker adapter (mock/alpaca paper/tradier sandbox)
- Bind broker order state back to intent registry
- Emit route logs for audit + reconciliation

Safety:
- Shadow mode only (hard-enforced in generated order requests)
"""

from __future__ import annotations

import argparse
import json
import os
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


class ShadowOrderRouter:
    def __init__(self, repo_root: Path, broker_name: Optional[str] = None):
        self.repo_root = repo_root
        self.log_dir = repo_root / "logs" / "execution"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.route_log_path = self.log_dir / "shadow_order_router.jsonl"
        self.bindings_log_path = self.log_dir / "router_order_bindings.jsonl"

        # delayed imports
        from src.execution.order_intent_registry import OrderIntentRegistry
        self.registry = OrderIntentRegistry(repo_root)

        self.broker_name = (broker_name or os.getenv("BROKER_ADAPTER", "mock")).strip().lower()
        self.adapter = self._build_adapter(self.broker_name)
        self.broker_account_id = self._infer_broker_account_id()
        self.ttl_policy_engine = self._load_ttl_policy_engine(self.repo_root / "config" / "order_ttl_policy.yaml")
        self.risk_gate = self._load_risk_gate()

    # -------------------------
    # Public API
    # -------------------------
    def route_package(
        self,
        package: Dict[str, Any],
        max_orders: int = 5,
        min_confidence: float = 0.0,
        symbols_allowlist: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Routes a package in shadow mode.
        Returns a routing summary.
        """
        package = dict(package)
        package_id = self._ensure_package_id(package)

        candidates = list(package.get("candidates", []) or [])
        blocked = list(package.get("blocked_candidates", []) or [])

        route_summary = {
            "schema_version": "shadow_order_router_run.v1",
            "timestamp_utc": iso_now(),
            "router_run_id": f"router-{uuid.uuid4().hex[:10]}",
            "broker_name": self.broker_name,
            "broker_account_id": self.broker_account_id,
            "package_id": package_id,
            "package_timestamp_utc": package.get("timestamp_utc"),
            "effective_mode": package.get("effective_mode"),
            "time_window_name": ((package.get("window_context") or {}).get("time_window_name")),
            "watchlist_only_window": ((package.get("window_context") or {}).get("watchlist_only_window")),
            "global_blocks": package.get("global_blocks") or [],
            "candidate_count_in_package": len(candidates),
            "blocked_candidate_count_in_package": len(blocked),
            "selected_candidates": [],
            "bound_order_attempts": [],
            "skipped_candidates": [],
            "errors": [],
            "submit_attempt_count": 0,
            "broker_rejected_count": 0,
            "submitted_open_or_ack_count": 0,
        }

        # package-level hard stop
        if (package.get("window_context") or {}).get("watchlist_only_window") is True:
            route_summary["skipped_candidates"].append({
                "reason": "watchlist_only_window",
                "count": len(candidates),
            })
            self._log_route_event("route_package_skipped_watchlist_only", route_summary)
            return route_summary

        if package.get("global_blocks"):
            route_summary["skipped_candidates"].append({
                "reason": "package_global_blocks",
                "global_blocks": package.get("global_blocks"),
                "count": len(candidates),
            })
            self._log_route_event("route_package_skipped_global_blocks", route_summary)
            return route_summary

        # Enrich snapshot with live microstructure data for risk gate
        self._enrich_microstructure(package, candidates)

        # rank candidates conservatively
        ranked = self._rank_candidates(candidates)

        submitted = 0
        for cand in ranked:
            if submitted >= max_orders:
                route_summary["skipped_candidates"].append({
                    "symbol": cand.get("symbol"),
                    "candidate_id": cand.get("candidate_id"),
                    "reason": "max_orders_reached",
                })
                continue

            reason = self._candidate_route_block_reason(cand, min_confidence=min_confidence, symbols_allowlist=symbols_allowlist)
            if reason:
                route_summary["skipped_candidates"].append({
                    "symbol": cand.get("symbol"),
                    "candidate_id": cand.get("candidate_id"),
                    "reason": reason,
                })
                continue

            try:
                order_req = self._candidate_to_order_request(package, cand)

                # --- Risk gate check (impact budget + VaR) ---
                gate_result = self._run_risk_gate(cand, order_req, package, route_summary)
                if gate_result and not gate_result.get("pass", True):
                    # Downsize if recommended cap is usable, otherwise skip
                    cap = gate_result.get("recommended_qty_cap", 0)
                    if cap >= 1:
                        order_req["qty"] = max(1, int(cap))
                        order_req["risk_gate_downsized"] = True
                    else:
                        route_summary["skipped_candidates"].append({
                            "symbol": cand.get("symbol"),
                            "candidate_id": cand.get("candidate_id"),
                            "reason": "risk_gate_blocked",
                            "risk_gate": gate_result,
                        })
                        continue

                ttl_policy = self._resolve_order_ttl_policy(package, cand, order_req)
                intent = self.registry.create_intent_from_candidate(
                    package=package,
                    candidate=cand,
                    order_request=order_req,
                    shadow_mode=True,
                    extra_context={
                        "router_run_id": route_summary["router_run_id"],
                        "runtime_flags": ttl_policy.get("runtime_flags") or {},
                        "order_lifecycle_policy": {
                            "resolved_ttl_minutes": ttl_policy.get("resolved_ttl_minutes"),
                            "ttl_resolved": ttl_policy.get("ttl_resolved"),
                            "ttl_policy_source": ttl_policy.get("ttl_policy_source"),
                            "ttl_explanation": ttl_policy.get("ttl_explanation"),
                            "created_with_time_window_hint": ((package.get("window_context") or {}).get("time_window_name")),
                        },
                    },
                )

                route_summary["selected_candidates"].append({
                    "symbol": cand.get("symbol"),
                    "candidate_id": intent.get("candidate_id"),
                    "intent_id": intent.get("intent_id"),
                    "client_order_id": intent.get("client_order_id"),
                    "confidence_score": cand.get("confidence_score"),
                    "strategy_style": cand.get("strategy_style"),
                    "template_key": cand.get("template_key"),
                    "direction": cand.get("direction"),
                })

                broker_order = self.adapter.submit_order(intent["order_request"])
                route_summary["submit_attempt_count"] += 1
                if broker_order.get("status") == "rejected":
                    route_summary["broker_rejected_count"] += 1
                updated = self.registry.bind_broker_order(
                    intent_id=intent["intent_id"],
                    broker_name=self.broker_name,
                    broker_account_id=self.broker_account_id,
                    broker_order=broker_order,
                )

                bound_row = {
                    "symbol": cand.get("symbol"),
                    "candidate_id": intent.get("candidate_id"),
                    "intent_id": intent.get("intent_id"),
                    "client_order_id": intent.get("client_order_id"),
                    "broker_order_id": ((updated.get("broker_binding") or {}).get("broker_order_id")),
                    "broker_status": ((updated.get("broker_state") or {}).get("status")) if updated.get("broker_state") else None,
                    "shadow_mode": True,
                }
                route_summary["bound_order_attempts"].append(bound_row)

                self._append_router_binding({
                    "router_run_id": route_summary["router_run_id"],
                    "broker_name": self.broker_name,
                    "broker_account_id": self.broker_account_id,
                    "package_id": package_id,
                    "candidate_id": intent.get("candidate_id"),
                    "intent_id": intent.get("intent_id"),
                    "client_order_id": intent.get("client_order_id"),
                    "broker_order_id": ((updated.get("broker_binding") or {}).get("broker_order_id")),
                    "symbol": cand.get("symbol"),
                    "strategy_style": cand.get("strategy_style"),
                    "template_key": cand.get("template_key"),
                    "direction": cand.get("direction"),
                    "shadow_mode": True,
                    "broker_status": ((updated.get("broker_state") or {}).get("status")) if updated.get("broker_state") else None,
                })

                submitted += 1

            except Exception as e:
                route_summary["errors"].append({
                    "symbol": cand.get("symbol"),
                    "candidate_id": cand.get("candidate_id"),
                    "error": str(e),
                })

        route_summary["submitted_open_or_ack_count"] = (
            route_summary["submit_attempt_count"] - route_summary["broker_rejected_count"]
        )
        self._log_route_event("route_package_complete", route_summary)
        return route_summary

    def route_package_file(
        self,
        package_path: Path,
        max_orders: int = 5,
        min_confidence: float = 0.0,
        symbols_allowlist: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        out = self.route_package(
            package=package,
            max_orders=max_orders,
            min_confidence=min_confidence,
            symbols_allowlist=symbols_allowlist,
        )
        return out

    # -------------------------
    # Candidate routing logic
    # -------------------------
    def _rank_candidates(self, candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        def score(c: Dict[str, Any]) -> float:
            conf = safe_float(c.get("confidence_score"), 0.0)
            size_mult = safe_float(c.get("size_multiplier_suggestion"), 1.0)
            fs = c.get("fill_sim_assessment") or {}
            fill_feas = safe_float(fs.get("fill_feasibility_score"), 0.5)
            reject_risk = safe_float(fs.get("reject_risk_probability"), 0.0)
            dnr = 1.0 if fs.get("do_not_route_even_in_shadow") else 0.0
            penalty = (1.0 - min(reject_risk, 0.8) * 0.5) * (0.2 if dnr else 1.0)
            return conf * max(size_mult, 0.01) * fill_feas * penalty

        return sorted(candidates, key=score, reverse=True)

    def _candidate_route_block_reason(
        self,
        candidate: Dict[str, Any],
        min_confidence: float,
        symbols_allowlist: Optional[List[str]],
    ) -> Optional[str]:
        if candidate.get("status") == "blocked":
            return "candidate_status_blocked"

        if candidate.get("block_reasons"):
            return "candidate_block_reasons_present"

        if safe_float(candidate.get("confidence_score"), 0.0) < min_confidence:
            return "below_min_confidence"

        if symbols_allowlist and str(candidate.get("symbol")) not in set(symbols_allowlist):
            return "symbol_not_in_allowlist"

        fs = candidate.get("fill_sim_assessment") or {}
        if fs.get("do_not_route_even_in_shadow") is True:
            return "fill_sim_do_not_route"

        exec_constraints = candidate.get("execution_constraints") or {}
        if exec_constraints.get("manual_review_required") is True:
            return "manual_review_required"

        # Strategy / instrument routeability checks (shadow still needs sane structure)
        symbol = candidate.get("symbol")
        direction = (candidate.get("direction") or "").lower()
        if not symbol:
            return "missing_symbol"
        if not direction:
            return "missing_direction"

        return None

    def _candidate_to_order_request(self, package: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert candidate -> canonical broker order request.
        Conservative defaults:
          - type: limit
          - time_in_force: day
          - qty: derived from a small shadow sizing heuristic
        """
        symbol = candidate["symbol"]
        direction = str(candidate.get("direction", "")).lower()
        instrument_types = [str(x).lower() for x in (candidate.get("instrument_types") or [])]

        # Only equity orders in this router for now
        if any("option" in x for x in instrument_types):
            raise ValueError("shadow_order_router currently supports equity orders only; options route via dedicated options shadow router")

        if "short" in direction or "bearish" in direction:
            side = "sell"
        else:
            side = "buy"

        confidence = safe_float(candidate.get("confidence_score"), 0.5)
        size_mult = safe_float(candidate.get("size_multiplier_suggestion"), 1.0)
        fs = candidate.get("fill_sim_assessment") or {}
        exec_constraints = candidate.get("execution_constraints") or {}

        # Shadow quantity sizing: small, bounded, confidence-aware
        base_qty = 1
        if confidence >= 0.8:
            base_qty = 3
        elif confidence >= 0.65:
            base_qty = 2

        qty = max(1, int(round(base_qty * max(size_mult, 0.25))))
        qty = min(qty, 10)  # keep shadow sizing bounded

        # Limit price: if candidate has decision/reference price, use it.
        # Otherwise placeholder tiny limit may cause no fills in paper; for mock it's okay.
        # Better: use price hints if available in candidate.
        price_hints = candidate.get("price_hints") or {}
        decision_price = price_hints.get("decision_price") or price_hints.get("last_price")
        limit_price = None
        if decision_price is not None:
            dp = safe_float(decision_price, 0.0)
            # Slightly conservative limit relative to side
            slip_bps = safe_float((fs or {}).get("expected_slippage_bps"), 10.0)
            adj = dp * (slip_bps / 10000.0)
            limit_price = round(dp + adj, 2) if side == "buy" else round(max(dp - adj, 0.01), 2)
        elif exec_constraints.get("limit_price_fallback") is not None:
            limit_price = safe_float(exec_constraints.get("limit_price_fallback"))
        else:
            # No price data — use market order instead of bad limit
            limit_price = None

        order_type = "limit" if limit_price else "market"
        order_request = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "time_in_force": "day",
            "qty": qty,
            "extended_hours": False,
            "shadow_mode": True,
        }
        if limit_price is not None:
            order_request["limit_price"] = limit_price
        return order_request

    # -------------------------
    # TTL policy
    # -------------------------
    def _load_ttl_policy_engine(self, path: Path):
        try:
            if not path.exists():
                return None
            from src.execution.time_window_ttl_policy import TimeWindowTTLPolicyEngine
            return TimeWindowTTLPolicyEngine.from_yaml_file(path)
        except Exception:
            return None

    def _build_runtime_flags(self, package: Dict[str, Any], candidate: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "time_window_hint": ((package.get("window_context") or {}).get("time_window_name")),
            "watchlist_only_window": ((package.get("window_context") or {}).get("watchlist_only_window")),
            "package_effective_mode": package.get("effective_mode"),
            "macro_event_quorum_pass": ((package.get("macro_context") or {}).get("macro_event_quorum_pass")),
        }

    def _resolve_order_ttl_policy(self, package: Dict[str, Any], candidate: Dict[str, Any], draft_order_request: Dict[str, Any]) -> Dict[str, Any]:
        runtime_flags = self._build_runtime_flags(package, candidate)

        if self.ttl_policy_engine is None:
            return {
                "ttl_resolved": False,
                "resolved_ttl_minutes": None,
                "ttl_policy_source": None,
                "ttl_explanation": None,
                "runtime_flags": runtime_flags,
            }

        pseudo_intent = {
            "candidate_context": {
                "symbol": candidate.get("symbol"),
                "strategy_style": candidate.get("strategy_style"),
            },
            "package_context": {
                "time_window_name": ((package.get("window_context") or {}).get("time_window_name")),
            },
            "order_request": {
                **draft_order_request,
                "strategy_context": {
                    "time_window_name": ((package.get("window_context") or {}).get("time_window_name")),
                },
            },
            "extra_context": {
                "runtime_flags": runtime_flags,
            },
        }

        ttl_minutes, expl = self.ttl_policy_engine.resolve_ttl_minutes(pseudo_intent)
        return {
            "ttl_resolved": True,
            "resolved_ttl_minutes": float(ttl_minutes),
            "ttl_policy_source": "config/order_ttl_policy.yaml",
            "ttl_explanation": expl,
            "runtime_flags": runtime_flags,
        }

    # -------------------------
    # Microstructure enrichment
    # -------------------------
    def _enrich_microstructure(self, package: Dict[str, Any], candidates: List[Dict[str, Any]]):
        """Fetch live ADV/sigma for candidate symbols and inject into package snapshot."""
        if not candidates:
            return
        # Only enrich if snapshot doesn't already have microstructure data
        snapshot = package.get("snapshot") or {}
        existing = snapshot.get("market_microstructure") or {}
        candidate_symbols = [c.get("symbol") for c in candidates if c.get("symbol")]
        missing = [s for s in candidate_symbols if s not in existing]
        if not missing:
            return
        try:
            from src.bridges.market_microstructure_bridge import MarketMicrostructureBridge
            bridge = MarketMicrostructureBridge(self.repo_root)
            micro = bridge.poll(symbols=missing)
            if micro:
                if "snapshot" not in package:
                    package["snapshot"] = {}
                if "market_microstructure" not in package["snapshot"]:
                    package["snapshot"]["market_microstructure"] = {}
                package["snapshot"]["market_microstructure"].update(micro)
        except Exception:
            pass  # Fail open — risk gate will handle missing data

    # -------------------------
    # Risk gate
    # -------------------------
    def _load_risk_gate(self):
        try:
            from src.risk.var_gate import RiskGate
            return RiskGate()
        except Exception:
            return None

    def _run_risk_gate(
        self,
        candidate: Dict[str, Any],
        order_req: Dict[str, Any],
        package: Dict[str, Any],
        route_summary: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Run risk gate if available. Returns gate result or None."""
        if self.risk_gate is None:
            return None

        snapshot = package.get("snapshot") or {}
        time_window_name = (package.get("window_context") or {}).get("time_window_name")
        regime = str(package.get("effective_mode") or "normal").lower()
        runtime_flags = package.get("runtime_flags") or {}

        intent_stub = {
            "intent_id": None,
            "package_id": route_summary.get("package_id"),
            "router_run_id": route_summary.get("router_run_id"),
            "symbol": candidate.get("symbol"),
            "order_request": order_req,
        }

        try:
            return self.risk_gate.check_intent(
                intent=intent_stub,
                snapshot=snapshot,
                time_window_name=time_window_name,
                regime=regime,
                runtime_flags=runtime_flags,
            )
        except Exception:
            return None

    # -------------------------
    # Helpers
    # -------------------------
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
        raise ValueError(f"Unsupported broker adapter: {broker_name}")

    def _infer_broker_account_id(self) -> Optional[str]:
        if self.broker_name == "tradier_sandbox":
            return os.getenv("TRADIER_ACCOUNT_ID")
        return None

    def _ensure_package_id(self, package: Dict[str, Any]) -> str:
        if package.get("package_id"):
            return str(package["package_id"])
        ts = str(package.get("timestamp_utc", ""))
        ptype = str(package.get("package_type", "pkg"))
        return f"pkg-{uuid.uuid5(uuid.NAMESPACE_URL, ts + '|' + ptype).hex[:12]}"

    def _append_router_binding(self, row: Dict[str, Any]):
        binding = {
            "schema_version": "router_order_binding.v1",
            "timestamp_utc": iso_now(),
            **row,
        }
        with self.bindings_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(binding, ensure_ascii=False) + "\n")

    def _log_route_event(self, event_type: str, payload: Dict[str, Any]):
        row = {
            "schema_version": "shadow_order_router_event.v1",
            "timestamp_utc": iso_now(),
            "event_type": event_type,
            "broker_name": self.broker_name,
            "payload": payload,
        }
        with self.route_log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


# -------------------------
# CLI
# -------------------------
def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-root", default=".")
    p.add_argument("--package-json", required=True, help="Package JSON file to route")
    p.add_argument("--broker", default=None, choices=["mock", "alpaca_paper", "tradier_sandbox"])
    p.add_argument("--max-orders", type=int, default=5)
    p.add_argument("--min-confidence", type=float, default=0.0)
    p.add_argument("--allow-symbols", nargs="*", default=None)
    p.add_argument("--output-json", default=None)
    return p.parse_args()


def main():
    args = parse_args()
    router = ShadowOrderRouter(Path(args.repo_root).resolve(), broker_name=args.broker)
    out = router.route_package_file(
        package_path=Path(args.package_json),
        max_orders=args.max_orders,
        min_confidence=args.min_confidence,
        symbols_allowlist=args.allow_symbols,
    )

    if args.output_json:
        p = Path(args.output_json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    else:
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
