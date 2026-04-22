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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Trade approval workflow
try:
    from src.execution.trade_approval import request_approval as _trade_approval
    _HAS_TRADE_APPROVAL = True
except ImportError:
    _HAS_TRADE_APPROVAL = False

# --- Paper/training broker constants ---
_TRUTHY_ENV = {"1", "true", "yes", "on"}
_FALSY_ENV = {"0", "false", "no", "off"}
_PAPER_TRAINING_BROKERS = {"mock", "alpaca_paper", "tradier_sandbox"}


def _env_flag(name: str, default: bool = False) -> bool:
    """Read an env var as a boolean flag."""
    raw = str(os.getenv(name, "")).strip().lower()
    if raw in _TRUTHY_ENV:
        return True
    if raw in _FALSY_ENV:
        return False
    return default


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _load_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        import yaml

        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


class CandidateRouteBlock(Exception):
    """Non-fatal candidate-level block reason."""

    def __init__(self, reason: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


class ShadowOrderRouter:
    def __init__(
        self,
        repo_root: Path,
        broker_name: Optional[str] = None,
        alpaca_credentials: Optional[Dict[str, str]] = None,
    ):
        self.repo_root = repo_root
        self.log_dir = repo_root / "logs" / "execution"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.route_log_path = self.log_dir / "shadow_order_router.jsonl"
        self.bindings_log_path = self.log_dir / "router_order_bindings.jsonl"

        # delayed imports
        from src.execution.order_intent_registry import OrderIntentRegistry
        self.registry = OrderIntentRegistry(repo_root)

        self.broker_name = (broker_name or os.getenv("BROKER_ADAPTER", "mock")).strip().lower()
        self.alpaca_credentials = alpaca_credentials
        self.adapter = self._build_adapter(self.broker_name)
        self.broker_account_id = self._infer_broker_account_id()
        self.ttl_policy_engine = self._load_ttl_policy_engine(self.repo_root / "config" / "order_ttl_policy.yaml")
        self.risk_gate = self._load_risk_gate()
        self.options_guard = self._load_options_guard()
        self._cached_equity: Optional[float] = None
        self._latest_trade_price_cache: Dict[str, float] = {}
        self._shortable_cache: Dict[str, Optional[bool]] = {}

        # V4 execution hardening modules
        self._v4_circuit_breaker = self._load_v4_circuit_breaker()
        self._v4_pre_trade = self._load_v4_pre_trade_controls()
        self._v4_regime_classifier = self._load_v4_regime_classifier()
        self._v6_order_book = self._load_v6_order_book()
        self._v6_slippage_model = self._load_v6_slippage_model()
        self._v6_buying_power = self._load_v6_buying_power()
        self._v6_compliance = self._load_v6_compliance()
        self._v6_execution_blocks_enabled = str(os.getenv("GS_ENABLE_V6_EXECUTION_BLOCKS", "false")).lower() in {"1", "true", "yes", "on"}

    # -------------------------
    # Public API
    # -------------------------
    def route_package(
        self,
        package: Dict[str, Any],
        max_orders: int = 999,
        min_confidence: float = 0.0,
        symbols_allowlist: Optional[List[str]] = None,
        strategy_config: Optional[Dict[str, Any]] = None,
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
            "strategy_name": (strategy_config or {}).get("name") or (strategy_config or {}).get("holding_period"),
            "selected_candidates": [],
            "bound_order_attempts": [],
            "skipped_candidates": [],
            "errors": [],
            "submit_attempt_count": 0,
            "broker_rejected_count": 0,
            "submitted_open_or_ack_count": 0,
            "v6_check_warnings": [],
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

        # V4 microstructure regime classification
        if self._v4_regime_classifier is not None:
            try:
                snapshot = package.get("snapshot") or {}
                micro = snapshot.get("market_microstructure") or {}
                regime = self._v4_regime_classifier.classify(micro)
                route_summary["v4_microstructure_regime"] = regime.get("regime")
                route_summary["v4_regime_params"] = regime.get("execution_params", {})
            except Exception:
                pass

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
                order_req = self._candidate_to_order_request(package, cand, strategy_config=strategy_config)
                self._v6_attach_slippage_estimate(order_req)
                order_record = self._v6_create_order_record(cand, order_req, strategy_config)

                # --- V4 pre-trade controls ---
                v4_block = self._v4_pre_trade_check(order_req, package)
                if v4_block:
                    self._v6_reject_order_record(order_record, v4_block)
                    route_summary["skipped_candidates"].append({
                        "symbol": cand.get("symbol"),
                        "candidate_id": cand.get("candidate_id"),
                        "reason": v4_block,
                    })
                    continue

                v6_checks = self._v6_execution_checks(order_req, cand, package, strategy_config)
                order_req["_v6_checks"] = v6_checks
                if order_record is not None:
                    self._v6_mark_validated(order_record, v6_checks)
                if v6_checks.get("warnings"):
                    route_summary["v6_check_warnings"].append({
                        "symbol": cand.get("symbol"),
                        "candidate_id": cand.get("candidate_id"),
                        "warnings": list(v6_checks.get("warnings") or []),
                    })
                if self._v6_execution_blocks_enabled and not v6_checks.get("passed", True):
                    reason = "v6_check:%s" % ((v6_checks.get("warnings") or ["blocked"])[0])
                    self._v6_reject_order_record(order_record, reason)
                    route_summary["skipped_candidates"].append({
                        "symbol": cand.get("symbol"),
                        "candidate_id": cand.get("candidate_id"),
                        "reason": reason,
                        "v6_checks": v6_checks,
                    })
                    continue

                # --- Risk gate check (impact budget + VaR) ---
                gate_result = self._run_risk_gate(cand, order_req, package, route_summary)
                if gate_result and not gate_result.get("pass", True):
                    # Downsize if recommended cap is usable, otherwise skip
                    cap = gate_result.get("recommended_qty_cap", 0)
                    if cap >= 1:
                        order_req["qty"] = max(1, int(cap))
                        order_req["risk_gate_downsized"] = True
                    else:
                        self._v6_reject_order_record(order_record, "risk_gate_blocked")
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
                    "holding_period": cand.get("holding_period"),
                    "order_side": order_req.get("side"),
                    "order_qty": order_req.get("qty"),
                    "order_type": order_req.get("type"),
                    "limit_price": order_req.get("limit_price"),
                    "time_in_force": order_req.get("time_in_force"),
                    "decision_price": ((order_req.get("_gs_sizing") or {}).get("decision_price")),
                    "decision_price_source": ((order_req.get("_gs_sizing") or {}).get("decision_price_source")),
                    "sizing_method_used": ((order_req.get("_gs_sizing") or {}).get("sizing_method_used")),
                    "size_multiplier_applied": ((order_req.get("_gs_sizing") or {}).get("size_multiplier_applied")),
                    "account_equity": ((order_req.get("_gs_sizing") or {}).get("account_equity")),
                    "target_notional": ((order_req.get("_gs_sizing") or {}).get("target_notional")),
                    "min_notional": ((order_req.get("_gs_sizing") or {}).get("min_notional")),
                    "max_notional": ((order_req.get("_gs_sizing") or {}).get("max_notional")),
                    "qty_cap": ((order_req.get("_gs_sizing") or {}).get("qty_cap")),
                    "qty_cap_source": ((order_req.get("_gs_sizing") or {}).get("qty_cap_source")),
                })

                # -- Trade Approval Workflow --
                req = intent["order_request"]
                _skip_approval = self.broker_name in _PAPER_TRAINING_BROKERS
                if _HAS_TRADE_APPROVAL and not _skip_approval:
                    _notional = 0
                    try:
                        _qty = float(req.get("qty", 0) or 0)
                        _price = float(req.get("limit_price", 0) or 0)
                        _notional = _qty * _price
                        if req.get("asset_class") == "option":
                            _notional *= 100
                    except Exception:
                        pass
                    _approval_info = {
                        "symbol": req.get("symbol"),
                        "side": req.get("side"),
                        "qty": req.get("qty"),
                        "type": req.get("type"),
                        "limit_price": req.get("limit_price"),
                        "notional": _notional,
                        "signal_source": cand.get("signal_source", cand.get("strategy_style", "")),
                        "strategy_style": cand.get("strategy_style", ""),
                        "asset_class": req.get("asset_class", "equity"),
                        "contract_id": req.get("contract_id", ""),
                    }
                    try:
                        _approval = _trade_approval(_approval_info)
                        if not _approval.get("approved", True):
                            import logging as _ta_log
                            _ta_log.getLogger("global_sentinel.shadow_order_router").info(
                                "Trade REJECTED by approval: %s - %s",
                                req.get("symbol"), _approval.get("reason"))
                            route_summary["skipped_by_approval"] = route_summary.get("skipped_by_approval", 0) + 1
                            continue
                    except Exception as _ae:
                        import logging as _ta_log
                        _ta_log.getLogger("global_sentinel.shadow_order_router").warning(
                            "Trade approval error (proceeding): %s", _ae)

                # Route options vs equity orders through appropriate adapter method
                self._v6_mark_submitted(order_record)
                if req.get("asset_class") == "option" and hasattr(self.adapter, "place_option_order"):
                    broker_order = self.adapter.place_option_order(
                        symbol=req["symbol"],
                        contract_id=req.get("contract_id", ""),
                        qty=req["qty"],
                        side=req["side"],
                        order_type=req.get("type", "limit"),
                        limit_price=req.get("limit_price"),
                        time_in_force=req.get("time_in_force", "day"),
                    )
                else:
                    broker_order = self._v4_broker_submit_with_breaker(req)
                route_summary["submit_attempt_count"] += 1
                if broker_order.get("status") == "rejected":
                    self._v6_reject_order_record(order_record, broker_order.get("reject_reason_message") or "broker_rejected")
                    route_summary["broker_rejected_count"] += 1
                else:
                    self._v6_mark_broker_state(order_record, broker_order)
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
                    "holding_period": cand.get("holding_period"),
                    "shadow_mode": True,
                    "side": order_req.get("side"),
                    "qty": order_req.get("qty"),
                    "type": order_req.get("type"),
                    "limit_price": order_req.get("limit_price"),
                    "time_in_force": order_req.get("time_in_force"),
                    "extended_hours": order_req.get("extended_hours"),
                    "decision_price": ((order_req.get("_gs_sizing") or {}).get("decision_price")),
                    "decision_price_source": ((order_req.get("_gs_sizing") or {}).get("decision_price_source")),
                    "sizing_method_used": ((order_req.get("_gs_sizing") or {}).get("sizing_method_used")),
                    "size_multiplier_applied": ((order_req.get("_gs_sizing") or {}).get("size_multiplier_applied")),
                    "account_equity": ((order_req.get("_gs_sizing") or {}).get("account_equity")),
                    "target_notional": ((order_req.get("_gs_sizing") or {}).get("target_notional")),
                    "min_notional": ((order_req.get("_gs_sizing") or {}).get("min_notional")),
                    "max_notional": ((order_req.get("_gs_sizing") or {}).get("max_notional")),
                    "qty_cap": ((order_req.get("_gs_sizing") or {}).get("qty_cap")),
                    "qty_cap_source": ((order_req.get("_gs_sizing") or {}).get("qty_cap_source")),
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
                    "side": order_req.get("side"),
                    "qty": order_req.get("qty"),
                    "type": order_req.get("type"),
                    "limit_price": order_req.get("limit_price"),
                    "time_in_force": order_req.get("time_in_force"),
                    "extended_hours": order_req.get("extended_hours"),
                    "decision_price": ((order_req.get("_gs_sizing") or {}).get("decision_price")),
                    "decision_price_source": ((order_req.get("_gs_sizing") or {}).get("decision_price_source")),
                    "sizing_method_used": ((order_req.get("_gs_sizing") or {}).get("sizing_method_used")),
                    "size_multiplier_applied": ((order_req.get("_gs_sizing") or {}).get("size_multiplier_applied")),
                    "account_equity": ((order_req.get("_gs_sizing") or {}).get("account_equity")),
                    "target_notional": ((order_req.get("_gs_sizing") or {}).get("target_notional")),
                    "min_notional": ((order_req.get("_gs_sizing") or {}).get("min_notional")),
                    "max_notional": ((order_req.get("_gs_sizing") or {}).get("max_notional")),
                    "qty_cap": ((order_req.get("_gs_sizing") or {}).get("qty_cap")),
                    "qty_cap_source": ((order_req.get("_gs_sizing") or {}).get("qty_cap_source")),
                })

                submitted += 1

            except CandidateRouteBlock as e:
                self._v6_reject_order_record(locals().get("order_record"), e.reason)
                route_summary["skipped_candidates"].append({
                    "symbol": cand.get("symbol"),
                    "candidate_id": cand.get("candidate_id"),
                    "reason": e.reason,
                    "details": e.details,
                })
            except Exception as e:
                self._v6_error_order_record(locals().get("order_record"), str(e))
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
        max_orders: int = 999,
        min_confidence: float = 0.0,
        symbols_allowlist: Optional[List[str]] = None,
        strategy_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        package = json.loads(package_path.read_text(encoding="utf-8"))
        out = self.route_package(
            package=package,
            max_orders=max_orders,
            min_confidence=min_confidence,
            symbols_allowlist=symbols_allowlist,
            strategy_config=strategy_config,
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

        instrument_types = [str(x).lower() for x in (candidate.get("instrument_types") or [])]
        instrument_type = str(candidate.get("instrument_type", "")).lower()
        is_option = any("option" in x for x in instrument_types) or instrument_type == "option"
        if is_option:
            # Check if the adapter supports options trading
            caps = getattr(self.adapter, "get_capabilities", lambda: {})()
            if caps.get("supports_options"):
                # Options enabled — run guardrails if available, but don't block
                if self.options_guard:
                    guard = self.options_guard.evaluate_candidate(candidate)
                    if not guard.get("pass", False):
                        return str(guard.get("reason_code") or "options_guard_blocked")
                # Options are allowed — fall through to remaining checks
            else:
                return "options_not_enabled"

        # Strategy / instrument routeability checks (shadow still needs sane structure)
        symbol = candidate.get("symbol")
        direction = (candidate.get("direction") or "").lower()
        if not symbol:
            return "missing_symbol"
        if not direction:
            return "missing_direction"

        explicit_side = str(candidate.get("side", "")).lower()
        if explicit_side in ("short", "sell"):
            side = "sell"
        elif explicit_side in ("long", "buy"):
            side = "buy"
        elif "short" in direction or "bearish" in direction:
            side = "sell"
        else:
            side = "buy"

        if side == "sell":
            shortable = self._is_symbol_shortable(str(symbol))
            if shortable is False:
                return "symbol_not_shortable"

        return None

    def _candidate_to_order_request(self, package: Dict[str, Any], candidate: Dict[str, Any], strategy_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Convert candidate -> canonical broker order request.
        Conservative defaults:
          - type: limit (market if no price data)
          - time_in_force: gtc for swing/macro trades, day for tactical
          - extended_hours: true for swing trades (24/7 global coverage)
          - qty: derived from a small shadow sizing heuristic
        """
        symbol = candidate["symbol"]
        direction = str(candidate.get("direction", "")).lower()
        instrument_types = [str(x).lower() for x in (candidate.get("instrument_types") or [])]
        instrument_type = str(candidate.get("instrument_type", "")).lower()
        is_option = any("option" in x for x in instrument_types) or instrument_type == "option"

        # Route options via dedicated options path
        if is_option:
            return self._candidate_to_option_order_request(package, candidate, strategy_config=strategy_config)

        # Use explicit side field from packager if available, else infer from direction
        explicit_side = str(candidate.get("side", "")).lower()
        if explicit_side in ("short", "sell"):
            side = "sell"
        elif explicit_side in ("long", "buy"):
            side = "buy"
        elif "short" in direction or "bearish" in direction:
            side = "sell"
        else:
            side = "buy"

        confidence = safe_float(candidate.get("confidence_score"), 0.5)
        size_mult = safe_float(candidate.get("size_multiplier_suggestion"), 1.0)
        fs = candidate.get("fill_sim_assessment") or {}
        exec_constraints = candidate.get("execution_constraints") or {}

        # Resolve decision price using layered fallbacks:
        # candidate price_hints -> execution fallback -> package microstructure -> broker quote.
        decision_price, decision_price_source = self._resolve_decision_price(package, candidate)

        # Shadow quantity sizing: prefer notional % sizing with explicit diagnostics.
        is_medium_long = (strategy_config or {}).get("holding_period") in ("swing", "medium", "long")
        position_sizing = (strategy_config or {}).get("position_sizing") or {}
        sizing_method_requested = str(position_sizing.get("method", "share_count")).lower()
        min_size_mult = safe_float(position_sizing.get("min_size_multiplier"), 0.25)
        size_mult_applied = max(size_mult, min_size_mult)

        qty = 0
        pct = None
        account_equity = None
        target_notional = None
        min_notional = None
        max_notional = None
        sizing_method_used = "share_count"

        if sizing_method_requested == "notional_pct":
            fail_if_price_missing = bool(position_sizing.get("fail_if_price_missing", True))
            if decision_price <= 0:
                if fail_if_price_missing:
                    raise CandidateRouteBlock(
                        "missing_decision_price_for_notional_sizing",
                        {
                            "symbol": symbol,
                            "strategy_holding_period": (strategy_config or {}).get("holding_period"),
                            "decision_price_source": decision_price_source,
                        },
                    )
                sizing_method_used = "share_count_fallback_missing_price"
            else:
                # Notional-based sizing: % of account equity per trade
                account_equity = self._get_account_equity()
                if confidence >= 0.75:
                    pct = safe_float(position_sizing.get("high_confidence_pct"), 12.0)
                elif confidence >= 0.50:
                    pct = safe_float(position_sizing.get("base_pct_of_equity"), 8.0)
                elif confidence >= 0.25:
                    pct = safe_float(position_sizing.get("base_pct_of_equity"), 8.0) * 0.75
                else:
                    pct = safe_float(position_sizing.get("base_pct_of_equity"), 8.0) * 0.5

                target_notional = account_equity * (pct / 100.0) * size_mult_applied
                min_notional = safe_float(position_sizing.get("min_notional"), 2000)
                max_pct = safe_float(position_sizing.get("max_single_position_pct"), 15.0)
                max_notional = account_equity * (max_pct / 100.0)
                target_notional = max(min_notional, min(target_notional, max_notional))

                qty = max(1, int(target_notional / decision_price))
                sizing_method_used = "notional_pct"

        if qty <= 0:
            # Legacy share-count sizing (explicit fallback path)
            base_qty = 1
            if confidence >= 0.8:
                base_qty = 10 if is_medium_long else 5
            elif confidence >= 0.65:
                base_qty = 7 if is_medium_long else 3
            elif confidence >= 0.5:
                base_qty = 4 if is_medium_long else 2
            qty = max(1, int(round(base_qty * size_mult_applied)))
            if sizing_method_used == "share_count":
                sizing_method_used = "share_count"

        # For notional sizing, the configured max notional already bounds exposure.
        # Keep only a high sanity cap unless strategy config explicitly tightens it.
        if sizing_method_used == "notional_pct":
            qty_cap = int(position_sizing.get("max_qty_cap") or 10000)
        else:
            qty_cap = 30 if is_medium_long else 100
        qty = min(qty, qty_cap)

        # Limit price from decision_price (already extracted above)
        limit_price = None
        if decision_price > 0:
            dp = decision_price
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

        # Strategy-aware time_in_force and extended_hours
        if strategy_config:
            tif = str(strategy_config.get("time_in_force", "day")).lower()
            ext_hours = bool(strategy_config.get("extended_hours", True))
        else:
            # Default: day trades with extended hours for pre/post-market
            tif = "day"
            ext_hours = True

        # Alpaca constraint: extended_hours requires limit orders with day/gtc TIF
        if ext_hours and order_type != "limit":
            ext_hours = False

        order_request = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "time_in_force": tif,
            "qty": qty,
            "extended_hours": ext_hours,
            "shadow_mode": True,
            "_gs_sizing": {
                "sizing_method_requested": sizing_method_requested,
                "sizing_method_used": sizing_method_used,
                "confidence_score": confidence,
                "size_multiplier_suggestion": size_mult,
                "size_multiplier_applied": size_mult_applied,
                "account_equity": account_equity,
                "target_notional": target_notional,
                "min_notional": min_notional,
                "max_notional": max_notional,
                "final_qty": qty,
                "qty_cap": qty_cap,
                "qty_cap_source": (
                    "position_sizing.max_qty_cap"
                    if sizing_method_used == "notional_pct" and position_sizing.get("max_qty_cap") is not None
                    else ("notional_default" if sizing_method_used == "notional_pct" else "share_count_default")
                ),
                "decision_price": decision_price if decision_price > 0 else None,
                "decision_price_source": decision_price_source,
            },
        }
        if limit_price is not None:
            order_request["limit_price"] = limit_price
        return order_request

    # -------------------------
    # Options order routing
    # -------------------------
    def _candidate_to_option_order_request(
        self, package: Dict[str, Any], candidate: Dict[str, Any], strategy_config: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Convert an options candidate into a canonical broker order request.
        Selects the appropriate contract, sizes the position, and returns
        an order request suitable for adapter.place_option_order().
        """
        symbol = candidate["symbol"]
        direction = str(candidate.get("direction", "")).lower()
        option_type = str(candidate.get("option_type", "")).lower()

        # Determine option type from direction if not explicit
        if not option_type or option_type not in ("call", "put"):
            if "bearish" in direction or "short" in direction:
                option_type = "put"
            else:
                option_type = "call"

        # Determine side: for options, buying puts/calls is the primary strategy
        side = "buy"
        strategy_hint = str(candidate.get("strategy", "")).lower()
        if "sell" in strategy_hint or "write" in strategy_hint:
            side = "sell"

        # Expiry preference
        target_expiry = str(candidate.get("target_expiry", "weekly")).lower()
        strike_preference = str(candidate.get("strike_preference", "atm")).lower()

        # Get decision price for the underlying
        decision_price, decision_price_source = self._resolve_decision_price(package, candidate)

        # Select the best option contract
        contract = self._select_option_contract(
            symbol=symbol,
            direction=option_type,
            expiry_preference=target_expiry,
            strike_preference=strike_preference,
            underlying_price=decision_price,
        )

        if not contract:
            raise CandidateRouteBlock(
                "no_option_contract_found",
                {
                    "symbol": symbol,
                    "option_type": option_type,
                    "target_expiry": target_expiry,
                    "strike_preference": strike_preference,
                },
            )

        # Size the options position
        # Options sizing: cap at a percentage of account equity, measured by premium cost
        confidence = safe_float(candidate.get("confidence_score"), 0.5)
        position_sizing = (strategy_config or {}).get("position_sizing") or {}
        account_equity = self._get_account_equity()

        # Options allocation: smaller % of equity since options are leveraged
        if confidence >= 0.75:
            alloc_pct = safe_float(position_sizing.get("options_high_confidence_pct"), 5.0)
        elif confidence >= 0.50:
            alloc_pct = safe_float(position_sizing.get("options_base_pct"), 3.0)
        else:
            alloc_pct = safe_float(position_sizing.get("options_low_confidence_pct"), 1.5)

        max_premium_budget = account_equity * (alloc_pct / 100.0)

        # Estimate per-contract cost (premium * 100 shares per contract)
        contract_price = safe_float(contract.get("close_price"), 0.0)
        contract_multiplier = safe_float(contract.get("size"), 100.0)

        if contract_price > 0:
            per_contract_cost = contract_price * contract_multiplier
            qty = max(1, int(max_premium_budget / per_contract_cost))
        else:
            # No price data — conservative 1 contract
            qty = 1

        # Cap at a max contract count for safety
        max_contracts = int(position_sizing.get("max_option_contracts", 20))
        qty = min(qty, max_contracts)

        # Limit price: use the contract's last close as reference
        limit_price = None
        if contract_price > 0:
            # Slightly above close for buy orders, slightly below for sell
            slip_bps = 50  # wider slippage for options
            adj = contract_price * (slip_bps / 10000.0)
            if side == "buy":
                limit_price = round(contract_price + adj, 2)
            else:
                limit_price = round(max(contract_price - adj, 0.01), 2)

        order_type = "limit" if limit_price else "market"

        order_request = {
            "symbol": contract.get("symbol"),  # OCC symbol
            "underlying_symbol": symbol,
            "side": side,
            "type": order_type,
            "time_in_force": "day",
            "qty": qty,
            "shadow_mode": True,
            "asset_class": "option",
            "option_type": option_type,
            "contract_id": contract.get("id"),
            "strike_price": contract.get("strike_price"),
            "expiration_date": contract.get("expiration_date"),
            "_gs_sizing": {
                "sizing_method_used": "options_premium_pct",
                "confidence_score": confidence,
                "account_equity": account_equity,
                "alloc_pct": alloc_pct,
                "max_premium_budget": max_premium_budget,
                "contract_price": contract_price,
                "per_contract_cost": per_contract_cost if contract_price > 0 else None,
                "final_qty": qty,
                "decision_price": decision_price if decision_price > 0 else None,
                "decision_price_source": decision_price_source,
            },
        }
        if limit_price is not None:
            order_request["limit_price"] = limit_price

        return order_request

    def _select_option_contract(
        self,
        symbol: str,
        direction: str,
        expiry_preference: str = "weekly",
        strike_preference: str = "atm",
        underlying_price: float = 0.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Select the best option contract from the chain.

        Args:
            symbol: Underlying symbol (e.g., 'SPY')
            direction: 'call' or 'put'
            expiry_preference: 'weekly' (nearest weekly) or 'monthly' (nearest monthly)
            strike_preference: 'atm' (at the money) or 'otm_1' (one strike OTM)
            underlying_price: Current underlying price for strike selection

        Returns:
            Best matching option contract dict, or None if unavailable.
        """
        get_chain = getattr(self.adapter, "get_option_chain", None)
        if not callable(get_chain):
            return None

        try:
            chain = get_chain(symbol)
        except Exception:
            return None

        if not chain:
            return None

        # Filter by option type (call/put)
        filtered = [c for c in chain if str(c.get("type", "")).lower() == direction]
        if not filtered:
            return None

        # Filter to tradable contracts only
        filtered = [c for c in filtered if c.get("tradable") is not False]

        # Determine target expiry date
        today = date.today()
        if expiry_preference == "weekly":
            # Nearest Friday (or next week's Friday if today is Fri/Sat)
            days_until_friday = (4 - today.weekday()) % 7
            if days_until_friday < 1:
                days_until_friday += 7
            target_expiry = today + timedelta(days=days_until_friday)
        else:
            # Monthly: 3rd Friday of current or next month
            target_expiry = self._third_friday(today)
            if target_expiry <= today:
                # Use next month
                next_month = today.replace(day=1) + timedelta(days=32)
                target_expiry = self._third_friday(next_month)

        # Sort by closest expiry to target
        def expiry_distance(c: Dict) -> int:
            exp_str = c.get("expiration_date", "")
            try:
                exp = date.fromisoformat(exp_str)
                return abs((exp - target_expiry).days)
            except (ValueError, TypeError):
                return 9999

        filtered.sort(key=expiry_distance)

        # Take contracts near the closest expiry (within 3 days of the best match)
        if not filtered:
            return None
        best_expiry_dist = expiry_distance(filtered[0])
        near_expiry = [c for c in filtered if expiry_distance(c) <= best_expiry_dist + 3]

        if not near_expiry:
            return None

        # Select strike based on preference
        if underlying_price <= 0:
            # No price data — just return the first available contract
            return near_expiry[0]

        # Sort by distance from ATM
        def strike_distance(c: Dict) -> float:
            strike = safe_float(c.get("strike_price"), 0.0)
            if strike <= 0:
                return 9999.0
            return abs(strike - underlying_price)

        near_expiry.sort(key=strike_distance)

        if strike_preference == "atm":
            # Closest to underlying price
            return near_expiry[0]
        elif strike_preference == "otm_1":
            # One strike OTM: for calls, strike > price; for puts, strike < price
            if direction == "call":
                otm = [c for c in near_expiry if safe_float(c.get("strike_price"), 0) > underlying_price]
            else:
                otm = [c for c in near_expiry if safe_float(c.get("strike_price"), 0) < underlying_price]
            if otm:
                return otm[0]
            # Fallback to ATM
            return near_expiry[0]

        return near_expiry[0]

    @staticmethod
    def _third_friday(ref_date: date) -> date:
        """Calculate the third Friday of the month containing ref_date."""
        first_day = ref_date.replace(day=1)
        # Find first Friday
        day_of_week = first_day.weekday()
        days_until_friday = (4 - day_of_week) % 7
        first_friday = first_day + timedelta(days=days_until_friday)
        # Third Friday = first Friday + 14 days
        return first_friday + timedelta(days=14)

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

    def _resolve_decision_price(self, package: Dict[str, Any], candidate: Dict[str, Any]) -> tuple[float, str]:
        """Resolve a decision price using progressively broader sources."""
        symbol = str(candidate.get("symbol") or "").upper()
        price_hints = candidate.get("price_hints") or {}
        exec_constraints = candidate.get("execution_constraints") or {}

        for field in ("decision_price", "last_price"):
            px = safe_float(price_hints.get(field), 0.0)
            if px > 0:
                return px, f"price_hints.{field}"

        px = safe_float(exec_constraints.get("limit_price_fallback"), 0.0)
        if px > 0:
            return px, "execution_constraints.limit_price_fallback"

        snapshot = package.get("snapshot") or {}
        micro = snapshot.get("market_microstructure") or {}
        sym_micro = micro.get(symbol) or micro.get(symbol.upper()) or micro.get(symbol.lower()) or {}
        for field in ("last_price", "mid_price", "mark_price"):
            px = safe_float(sym_micro.get(field), 0.0)
            if px > 0:
                return px, f"snapshot.market_microstructure.{field}"

        px = self._get_broker_quote_price(symbol)
        if px > 0:
            return px, "broker_quote.latest_trade_or_quote"

        return 0.0, "missing"

    def _get_broker_quote_price(self, symbol: str) -> float:
        if not symbol:
            return 0.0

        cached = self._latest_trade_price_cache.get(symbol)
        if cached and cached > 0:
            return cached

        getter = getattr(self.adapter, "get_latest_trade_price", None)
        if not callable(getter):
            return 0.0

        try:
            px = safe_float(getter(symbol), 0.0)
            if px > 0:
                self._latest_trade_price_cache[symbol] = px
                return px
        except Exception:
            return 0.0
        return 0.0

    def _is_symbol_shortable(self, symbol: str) -> Optional[bool]:
        sym = str(symbol or "").upper().strip()
        if not sym:
            return None
        if sym in self._shortable_cache:
            return self._shortable_cache[sym]

        checker = getattr(self.adapter, "is_symbol_shortable", None)
        if not callable(checker):
            self._shortable_cache[sym] = None
            return None

        try:
            value = checker(sym)
        except Exception:
            value = None
        self._shortable_cache[sym] = value
        return value

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

    def _load_options_guard(self):
        try:
            from src.execution.options_guardrails import OptionsGuardrails
            return OptionsGuardrails(self.repo_root)
        except Exception:
            return None

    def _load_v4_circuit_breaker(self):
        """Load V4 circuit breaker for broker call protection."""
        try:
            from src.execution.circuit_breaker import CircuitBreaker
            return CircuitBreaker(name="broker_submit", failure_threshold=3, recovery_timeout=60.0)
        except Exception:
            return None

    def _load_v4_pre_trade_controls(self):
        """Load V4 pre-trade controls from live_trading_guardrails.yaml."""
        try:
            from src.execution.pre_trade_controls import PreTradeControls
            guardrails_path = self.repo_root / "config" / "live_trading_guardrails.yaml"
            if guardrails_path.exists():
                return PreTradeControls.from_guardrails(self.repo_root / "config")
            return PreTradeControls()
        except Exception:
            return None

    def _load_v4_regime_classifier(self):
        """Load V4 microstructure regime classifier."""
        try:
            from src.execution.microstructure_regime_classifier import MicrostructureRegimeClassifier
            return MicrostructureRegimeClassifier()
        except Exception:
            return None

    def _load_v6_order_book(self):
        try:
            from src.execution.order_book import OrderBook
            return OrderBook(repo_root=self.repo_root)
        except Exception:
            return None

    def _load_v6_slippage_model(self):
        try:
            from src.execution.slippage_model import SlippageModel
            return SlippageModel()
        except Exception:
            return None

    def _load_v6_buying_power(self):
        try:
            from src.risk.buying_power import BuyingPowerTracker
            return BuyingPowerTracker()
        except Exception:
            return None

    def _load_v6_compliance(self):
        try:
            from src.risk.compliance import ComplianceEngine
            return ComplianceEngine()
        except Exception:
            return None

    def _v6_attach_slippage_estimate(self, order_req: Dict[str, Any]) -> None:
        if self._v6_slippage_model is None:
            return
        sizing = order_req.get("_gs_sizing") or {}
        decision_price = safe_float(sizing.get("decision_price"), safe_float(order_req.get("limit_price"), 0.0))
        try:
            estimate = self._v6_slippage_model.estimate(
                symbol=str(order_req.get("symbol") or ""),
                direction="long" if str(order_req.get("side") or "buy").lower() == "buy" else "short",
                quantity=int(order_req.get("qty") or 0),
                order_type=str(order_req.get("type") or "market"),
                market_data={
                    "last_price": decision_price,
                    "bid": safe_float(order_req.get("limit_price"), decision_price),
                    "ask": safe_float(order_req.get("limit_price"), decision_price),
                    "avg_daily_volume": safe_float((order_req.get("market_data") or {}).get("avg_daily_volume"), 0.0),
                    "realized_vol": safe_float((order_req.get("market_data") or {}).get("realized_vol"), 0.0),
                    "vix": safe_float((order_req.get("market_data") or {}).get("vix"), 0.0),
                },
            )
            order_req["_slippage_estimate"] = estimate
        except Exception:
            pass

    def _v6_create_order_record(self, candidate: Dict[str, Any], order_req: Dict[str, Any], strategy_config: Optional[Dict[str, Any]]):
        if self._v6_order_book is None:
            return None
        try:
            from src.execution.order_book import OrderState
            strategy_name = (strategy_config or {}).get("name") or candidate.get("strategy_name") or "router"
            account_name = (strategy_config or {}).get("account") or candidate.get("account") or "unassigned"
            order = self._v6_order_book.create_order(
                symbol=str(order_req.get("symbol") or candidate.get("symbol") or ""),
                direction="long" if str(order_req.get("side") or "buy").lower() == "buy" else "short",
                quantity=int(order_req.get("qty") or 0),
                strategy=strategy_name,
                account=account_name,
                price_type=str(order_req.get("type") or "market"),
                limit_price=safe_float(order_req.get("limit_price"), None) if order_req.get("limit_price") is not None else None,
                trade_idea_id=candidate.get("candidate_id"),
                analog_match=(candidate.get("_historical_analog") or {}).get("matched_event"),
                chokepoint_score=safe_float((candidate.get("_chokepoint_playbook") or {}).get("score"), 0.0) or None,
                strategy_ref=strategy_name,
                decision_price=safe_float((order_req.get("_gs_sizing") or {}).get("decision_price"), 0.0) or None,
                metadata={
                    "candidate_id": candidate.get("candidate_id"),
                    "slippage_estimate": order_req.get("_slippage_estimate"),
                },
            )
            self._v6_order_book.transition(order.order_id, OrderState.APPROVED, "router_selected_candidate")
            return order
        except Exception:
            return None

    def _v6_execution_checks(
        self,
        order_req: Dict[str, Any],
        candidate: Dict[str, Any],
        package: Dict[str, Any],
        strategy_config: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        warnings: List[str] = []
        checks: Dict[str, Any] = {}
        if self.adapter is None:
            return {"passed": True, "warnings": warnings, "checks": checks}

        account_state = {}
        positions_list = []
        try:
            if hasattr(self.adapter, "get_account_state"):
                account_state = self.adapter.get_account_state() or {}
            if hasattr(self.adapter, "list_positions"):
                positions_list = self.adapter.list_positions() or []
        except Exception as exc:
            warnings.append(f"account_state_unavailable:{exc}")
            return {"passed": True, "warnings": warnings, "checks": checks}

        if self._v6_buying_power is not None:
            try:
                bp_state = self._v6_buying_power.compute(account_state, [])
                fit = self._v6_buying_power.will_this_order_fit(order_req, bp_state)
                checks["buying_power"] = fit
                if not fit.get("fits", True):
                    warnings.append(f"buying_power:{fit.get('reason')}")
            except Exception as exc:
                warnings.append(f"buying_power_error:{exc}")

        if self._v6_compliance is not None:
            try:
                # --- max_positions enforcement (fix 2026-03-11) ---
                _exec_mode_cfg = _load_yaml(self.repo_root / "config" / "execution_mode.yaml")
                if not _exec_mode_cfg:
                    _exec_mode_cfg = _load_yaml(self.repo_root / "execution_mode.yaml")
                _strategies_cfg = _exec_mode_cfg.get("strategies", {})
                _order_strategy = (strategy_config or {}).get("name") or candidate.get("strategy_name") or "day_trade"
                _strategy_position_counts = {_order_strategy: len(positions_list)}
                _strategy_limits = {}
                for _sname, _scfg in _strategies_cfg.items():
                    if "max_positions" in _scfg:
                        _strategy_limits[_sname] = {"max_positions": _scfg["max_positions"]}
                portfolio = {
                    "equity": safe_float(account_state.get("equity"), 0.0),
                    "positions": {str(p.get("symbol")): p for p in positions_list},
                    "strategy_position_counts": _strategy_position_counts,
                }
                rules = {}
                guardrails_path = self.repo_root / "config" / "live_trading_guardrails.yaml"
                if guardrails_path.exists():
                    rules = _load_yaml(guardrails_path)
                    rules.setdefault("max_single_name_pct", ((rules.get("position_limits") or {}).get("max_single_name_pct")))
                rules["strategy_limits"] = _strategy_limits
                comp = self._v6_compliance.pre_trade_check(
                    {
                        "symbol": order_req.get("symbol"),
                        "qty": order_req.get("qty"),
                        "limit_price": order_req.get("limit_price"),
                        "decision_price": (order_req.get("_gs_sizing") or {}).get("decision_price"),
                        "strategy": (strategy_config or {}).get("name") or candidate.get("strategy_name"),
                        "direction": "short" if str(order_req.get("side") or "").lower() == "sell" else "long",
                        "avg_daily_volume": safe_float((order_req.get("market_data") or {}).get("avg_daily_volume"), 0.0),
                        "notional": safe_float((order_req.get("_gs_sizing") or {}).get("target_notional"), 0.0),
                    },
                    portfolio,
                    rules,
                )
                checks["compliance"] = comp
                if not comp.get("passed", True):
                    warnings.extend([f"compliance:{msg}" for msg in comp.get("violations", [])])
            except Exception as exc:
                warnings.append(f"compliance_error:{exc}")

        try:
            from src.risk.exposure_book import ExposureBook
            exposure = ExposureBook({"router": self.adapter})
            exposure_snapshot = exposure.snapshot()
            guardrails = _load_yaml(self.repo_root / "config" / "live_trading_guardrails.yaml")
            proposed_order = {
                "symbol": order_req.get("symbol"),
                "side": order_req.get("side"),
                "qty": order_req.get("qty"),
                "limit_price": order_req.get("limit_price"),
                "decision_price": (order_req.get("_gs_sizing") or {}).get("decision_price"),
                "notional": safe_float((order_req.get("_gs_sizing") or {}).get("target_notional"), 0.0),
            }
            exposure_check = exposure.check_limits(guardrails, snapshot=exposure_snapshot, proposed_order=proposed_order)
            checks["exposure"] = exposure_check
            if not exposure_check.get("ok", True):
                warnings.extend([f"exposure:{msg}" for msg in exposure_check.get("violations", [])])
        except Exception as exc:
            warnings.append(f"exposure_error:{exc}")

        return {"passed": not warnings, "warnings": warnings, "checks": checks}

    def _v6_mark_validated(self, order_record: Any, v6_checks: Dict[str, Any]) -> None:
        if self._v6_order_book is None or order_record is None:
            return
        try:
            from src.execution.order_book import OrderState
            self._v6_order_book.transition(order_record.order_id, OrderState.VALIDATED, "v6_checks_complete", v6_checks)
        except Exception:
            pass

    def _v6_mark_submitted(self, order_record: Any) -> None:
        if self._v6_order_book is None or order_record is None:
            return
        try:
            from src.execution.order_book import OrderState
            self._v6_order_book.transition(order_record.order_id, OrderState.SUBMITTED, "broker_submit_attempt")
        except Exception:
            pass

    def _v6_mark_broker_state(self, order_record: Any, broker_order: Dict[str, Any]) -> None:
        if self._v6_order_book is None or order_record is None:
            return
        try:
            from src.execution.order_book import OrderState
            broker_state = str(broker_order.get("status") or "").lower()
            if broker_state in {"accepted", "new", "open", "pending", "done_for_day"}:
                new_state = OrderState.ACKNOWLEDGED
            elif broker_state == "partially_filled":
                new_state = OrderState.PARTIAL_FILL
            elif broker_state == "filled":
                new_state = OrderState.FILLED
            else:
                return
            self._v6_order_book.transition(
                order_record.order_id,
                new_state,
                f"broker_state:{broker_state}",
                {
                    "broker_order_id": broker_order.get("order_id"),
                    "filled_quantity": broker_order.get("filled_qty"),
                    "avg_fill_price": broker_order.get("avg_fill_price"),
                    "commission": broker_order.get("commission"),
                },
            )
        except Exception:
            pass

    def _v6_reject_order_record(self, order_record: Any, reason: str) -> None:
        if self._v6_order_book is None or order_record is None:
            return
        try:
            from src.execution.order_book import OrderState
            self._v6_order_book.transition(order_record.order_id, OrderState.REJECTED, reason)
        except Exception:
            pass

    def _v6_error_order_record(self, order_record: Any, reason: str) -> None:
        if self._v6_order_book is None or order_record is None:
            return
        try:
            from src.execution.order_book import OrderState
            self._v6_order_book.transition(order_record.order_id, OrderState.ERROR, reason)
        except Exception:
            pass

    def _v4_pre_trade_check(self, order_req: Dict[str, Any], package: Dict[str, Any]) -> Optional[str]:
        """Run V4 pre-trade controls. Returns block reason or None."""
        if self._v4_pre_trade is None:
            return None
        try:
            portfolio_state = {
                "equity": self._cached_equity or 100000.0,
                "gross_exposure": safe_float(
                    package.get("snapshot", {}).get("gross_exposure"), 0.0
                ),
                "positions": package.get("snapshot", {}).get("positions", {}),
            }
            result = self._v4_pre_trade.check(order_req, portfolio_state)
            if hasattr(result, "passed"):
                if not result.passed:
                    failed = [c["name"] for c in result.checks if not c.get("passed", True)]
                    return "v4_pre_trade:%s" % (failed[0] if failed else "blocked")
            elif isinstance(result, dict) and not result.get("pass", True):
                return "v4_pre_trade:%s" % result.get("reason", "blocked")
        except Exception:
            pass  # degrade gracefully — don't block on V4 module failures
        return None

    def _v4_broker_submit_with_breaker(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Submit order through circuit breaker if available."""
        if self._v4_circuit_breaker is not None:
            try:
                return self._v4_circuit_breaker.call(lambda: self.adapter.submit_order(req))
            except Exception:
                return self.adapter.submit_order(req)
        return self.adapter.submit_order(req)

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
            creds = self.alpaca_credentials or {}
            return AlpacaPaperAdapter(
                api_key=creds.get("api_key"),
                api_secret=creds.get("api_secret"),
            )
        if broker_name == "tradier_sandbox":
            from src.execution.tradier_sandbox_adapter import TradierSandboxAdapter
            return TradierSandboxAdapter()
        raise ValueError(f"Unsupported broker adapter: {broker_name}")

    def _get_account_equity(self) -> float:
        """Get account equity for notional sizing. Cached per router instance."""
        if self._cached_equity is not None:
            return self._cached_equity
        try:
            acct = self.adapter.get_account_state()
            equity = safe_float(acct.get("equity"), 100000.0)
            self._cached_equity = equity if equity > 0 else 100000.0
        except Exception:
            self._cached_equity = 100000.0  # Conservative fallback
        return self._cached_equity

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
