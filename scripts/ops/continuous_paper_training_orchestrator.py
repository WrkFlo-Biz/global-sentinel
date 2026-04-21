#!/usr/bin/env python3
"""Continuous paper-training control plane for Global Sentinel.

This loop closes the gap between strategy generation and learning:
1. Load the latest scorecard, bridge cache, and market-data context.
2. Evaluate the war-engine and transcript-driven strategy modules.
3. Keep stale-source candidates in the training inventory, but penalize them.
4. Route routeable candidates through the existing shadow router.
5. Simulate fills/exits in mock mode so the system can learn continuously.
6. Reconcile closures into performance history and trigger feedback updates.

It can run in continuous mode or in replay mode from a sequence of prebuilt
contexts, which makes it usable for both 24/7 training and backtest-style runs.
"""

from __future__ import annotations

import argparse
import inspect
import json
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency in some envs
    yaml = None  # type: ignore[assignment]


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def parse_timestamp(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def latest_file(paths: Iterable[Path]) -> Optional[Path]:
    existing = [p for p in paths if p.exists()]
    if not existing:
        return None
    return max(existing, key=lambda p: p.stat().st_mtime)


@dataclass
class RuntimeContext:
    scorecard: Dict[str, Any]
    bridge_results: Dict[str, Any]
    market_data: Dict[str, Dict[str, Any]]
    source_timestamps: Dict[str, str]
    scorecard_path: Optional[str] = None
    bridge_results_path: Optional[str] = None
    market_data_path: Optional[str] = None
    source_timestamps_path: Optional[str] = None


class ContinuousPaperTrainingOrchestrator:
    """Continuously evaluate, paper-route, and learn from strategy ideas."""

    def __init__(
        self,
        repo_root: Path,
        config_path: Optional[Path] = None,
        broker_name: Optional[str] = None,
    ) -> None:
        self.repo_root = repo_root
        self.config_path = config_path or repo_root / "config" / "paper_training_system.yaml"
        self.config = load_yaml(self.config_path)

        loop_cfg = self.config.get("loop") or {}
        logs_cfg = self.config.get("logs") or {}
        state_cfg = self.config.get("state") or {}

        self.state_path = repo_root / str(
            state_cfg.get("path", "logs/execution/continuous_paper_training_state.json")
        )
        self.cycle_log_path = repo_root / str(
            logs_cfg.get("cycle_log", "logs/execution/paper_training_cycles.jsonl")
        )
        self.inventory_log_path = repo_root / str(
            logs_cfg.get("inventory_log", "logs/execution/paper_training_candidate_inventory.jsonl")
        )
        self.status_report_path = repo_root / str(
            logs_cfg.get("status_report", "reports/weekly/paper_training_status.json")
        )
        self.replay_report_path = repo_root / str(
            logs_cfg.get("replay_report", "reports/weekly/paper_training_replay.json")
        )

        for path in (
            self.state_path,
            self.cycle_log_path,
            self.inventory_log_path,
            self.status_report_path,
            self.replay_report_path,
        ):
            path.parent.mkdir(parents=True, exist_ok=True)

        from src.alpha.strategy_engine import StrategyEngine
        from src.execution.adaptive_feedback_loop import AdaptiveFeedbackLoop
        from src.execution.order_intent_registry import OrderIntentRegistry
        from src.execution.performance_tracker import PerformanceTracker
        from src.execution.shadow_order_router import ShadowOrderRouter
        from src.strategies import (
            evaluate_commodity_regime_rotation,
            evaluate_hormuz_osint_geopolitical,
            evaluate_ict_candle_range_theory,
            evaluate_kronos_forecast_overlay,
            evaluate_mgc_ai_optimized,
            evaluate_options_flow_model,
            evaluate_parrondo_paradox,
            evaluate_quant_probability_pricing,
        )

        self.strategy_engine = StrategyEngine(repo_root=str(repo_root))
        self.performance_tracker = PerformanceTracker(repo_root)
        self.feedback_loop = AdaptiveFeedbackLoop(repo_root)
        self.intent_registry = OrderIntentRegistry(repo_root)
        self.broker_name = (broker_name or loop_cfg.get("broker_adapter") or "mock").strip().lower()
        self.router = ShadowOrderRouter(repo_root=repo_root, broker_name=self.broker_name)
        self.strategy_evaluators: Dict[str, Callable[..., List[Dict[str, Any]]]] = {
            "commodity_regime_rotation": evaluate_commodity_regime_rotation,
            "kronos_forecast_overlay": evaluate_kronos_forecast_overlay,
            "mgc_ai_optimized": evaluate_mgc_ai_optimized,
            "hormuz_osint_geopolitical": evaluate_hormuz_osint_geopolitical,
            "options_flow_model": evaluate_options_flow_model,
            "parrondo_paradox": evaluate_parrondo_paradox,
            "ict_candle_range_theory": evaluate_ict_candle_range_theory,
            "quant_probability_pricing": evaluate_quant_probability_pricing,
        }

        self.state = self._load_state()

    # ------------------------------------------------------------------
    # State / logging
    # ------------------------------------------------------------------
    def _load_state(self) -> Dict[str, Any]:
        if self.state_path.exists():
            try:
                return load_json(self.state_path)
            except Exception:
                pass
        return {
            "schema_version": "continuous_paper_training_state.v1",
            "updated_at_utc": None,
            "cycle_count": 0,
            "recent_realized_pnl": 0.0,
            "previous_positions": {},
            "last_cycle_summary": {},
        }

    def _save_state(self) -> None:
        self.state["updated_at_utc"] = iso_now()
        self.state_path.write_text(
            json.dumps(self.state, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _append_jsonl(path: Path, row: Dict[str, Any]) -> None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    # ------------------------------------------------------------------
    # Context loading
    # ------------------------------------------------------------------
    def _load_runtime_context(
        self,
        *,
        scorecard_json: Optional[Path] = None,
        bridge_results_json: Optional[Path] = None,
        market_data_json: Optional[Path] = None,
        source_timestamps_json: Optional[Path] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> RuntimeContext:
        if context is not None:
            return self._context_from_inline(context)

        bridge_results, derived_timestamps, bridge_path = self._load_bridge_results(bridge_results_json)
        scorecard, scorecard_path = self._load_scorecard(scorecard_json)

        market_data: Dict[str, Dict[str, Any]] = {}
        market_data_path = None
        if market_data_json and market_data_json.exists():
            market_data = load_json(market_data_json)
            market_data_path = str(market_data_json)
        elif isinstance(bridge_results.get("market_data"), dict):
            market_data = bridge_results.get("market_data") or {}
        elif isinstance(scorecard.get("market_data"), dict):
            market_data = scorecard.get("market_data") or {}

        source_timestamps = dict(derived_timestamps)
        if source_timestamps_json and source_timestamps_json.exists():
            source_timestamps.update(self._coerce_source_timestamps(load_json(source_timestamps_json)))
        if scorecard_path:
            source_timestamps.setdefault("scorecard", datetime.fromtimestamp(Path(scorecard_path).stat().st_mtime, tz=timezone.utc).isoformat())
        if market_data:
            ts_value = (
                datetime.fromtimestamp(market_data_json.stat().st_mtime, tz=timezone.utc).isoformat()
                if market_data_json and market_data_json.exists()
                else iso_now()
            )
            source_timestamps.setdefault("market_data", ts_value)

        return RuntimeContext(
            scorecard=scorecard,
            bridge_results=bridge_results,
            market_data=market_data,
            source_timestamps=source_timestamps,
            scorecard_path=scorecard_path,
            bridge_results_path=bridge_path,
            market_data_path=market_data_path,
            source_timestamps_path=str(source_timestamps_json) if source_timestamps_json else None,
        )

    def _context_from_inline(self, context: Dict[str, Any]) -> RuntimeContext:
        source_timestamps = self._coerce_source_timestamps(context.get("source_timestamps") or {})
        if context.get("market_data"):
            source_timestamps.setdefault("market_data", iso_now())
        return RuntimeContext(
            scorecard=dict(context.get("scorecard") or {}),
            bridge_results=dict(context.get("bridge_results") or {}),
            market_data=dict(context.get("market_data") or {}),
            source_timestamps=source_timestamps,
        )

    def _load_scorecard(self, explicit_path: Optional[Path]) -> tuple[Dict[str, Any], Optional[str]]:
        path = explicit_path
        if path is None:
            path = latest_file((self.repo_root / "logs" / "scorecards").glob("scorecard_*.json"))
        if path and path.exists():
            return load_json(path), str(path)
        return {}, None

    def _load_bridge_results(self, explicit_path: Optional[Path]) -> tuple[Dict[str, Any], Dict[str, str], Optional[str]]:
        if explicit_path and explicit_path.exists():
            payload = load_json(explicit_path)
            bridge_results = dict(payload.get("bridge_results") or payload)
            source_timestamps = self._coerce_source_timestamps(payload.get("source_timestamps") or {})
            source_timestamps.setdefault(
                "bridge_results",
                datetime.fromtimestamp(explicit_path.stat().st_mtime, tz=timezone.utc).isoformat(),
            )
            return bridge_results, source_timestamps, str(explicit_path)
        return self._load_latest_bridge_cache()

    def _load_latest_bridge_cache(self) -> tuple[Dict[str, Any], Dict[str, str], Optional[str]]:
        cache_root = self.repo_root / "logs" / "bridge_cache"
        bridge_results: Dict[str, Any] = {}
        source_timestamps: Dict[str, str] = {}
        latest_paths: List[Path] = []
        if not cache_root.exists():
            return bridge_results, source_timestamps, None

        for source_dir in sorted(cache_root.iterdir()):
            if not source_dir.is_dir():
                continue
            latest = latest_file(source_dir.glob("*.json"))
            if latest is None:
                continue
            latest_paths.append(latest)
            try:
                payload = load_json(latest)
            except Exception:
                continue
            source_name = source_dir.name
            bridge_results[source_name] = payload
            ts = self._payload_timestamp(payload) or datetime.fromtimestamp(
                latest.stat().st_mtime,
                tz=timezone.utc,
            )
            self._register_source_timestamp(source_timestamps, source_name, ts)
        latest_path = str(max(latest_paths, key=lambda p: p.stat().st_mtime)) if latest_paths else None
        return bridge_results, source_timestamps, latest_path

    def _register_source_timestamp(
        self,
        source_timestamps: Dict[str, str],
        source_name: str,
        timestamp: datetime,
    ) -> None:
        iso_value = timestamp.isoformat()
        source_timestamps[source_name] = iso_value
        if source_name.endswith("_bridge"):
            source_timestamps.setdefault(source_name[:-7], iso_value)

    @staticmethod
    def _payload_timestamp(payload: Any) -> Optional[datetime]:
        if isinstance(payload, dict):
            for key in ("timestamp_utc", "as_of_utc", "updated_at_utc", "generated_at_utc"):
                parsed = parse_timestamp(payload.get(key))
                if parsed is not None:
                    return parsed
        return None

    @staticmethod
    def _coerce_source_timestamps(raw: Dict[str, Any]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for source, value in (raw or {}).items():
            if isinstance(value, dict):
                value = value.get("timestamp_utc") or value.get("updated_at_utc") or value.get("as_of_utc")
            parsed = parse_timestamp(value)
            if parsed is not None:
                out[str(source)] = parsed.isoformat()
        return out

    # ------------------------------------------------------------------
    # Freshness handling
    # ------------------------------------------------------------------
    def _evaluate_freshness(
        self,
        source_keys: List[str],
        source_timestamps: Dict[str, str],
        *,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        if now is None:
            now = datetime.now(timezone.utc)

        cfg = load_yaml(self.repo_root / "config" / "freshness_policy.yaml")
        source_cfg = cfg.get("sources") or {}
        stale_cfg = self.config.get("stale_training") or {}
        missing_weight = safe_float(stale_cfg.get("missing_source_weight"), 0.25)
        expired_weight = safe_float(stale_cfg.get("expired_source_weight"), 0.15)

        details: List[Dict[str, Any]] = []
        weights: List[float] = []
        for source in source_keys:
            ts_text = source_timestamps.get(source)
            source_policy = (
                source_cfg.get(source)
                or source_cfg.get(f"{source}_bridge")
                or {}
            )
            ttl = int(source_policy.get("freshness_ttl_minutes", 60))
            stale_weight = safe_float(source_policy.get("stale_trust_weight_override"), 0.4)
            parsed = parse_timestamp(ts_text)
            if parsed is None:
                details.append({
                    "source": source,
                    "status": "missing",
                    "age_minutes": None,
                    "ttl_minutes": ttl,
                    "weight": round(missing_weight, 3),
                })
                weights.append(missing_weight)
                continue

            age_minutes = max(0.0, (now - parsed).total_seconds() / 60.0)
            if age_minutes <= ttl:
                status = "fresh"
                weight = 1.0
            elif age_minutes <= ttl * 2:
                status = "stale"
                weight = stale_weight
            else:
                status = "expired"
                weight = min(stale_weight, expired_weight)
            details.append({
                "source": source,
                "status": status,
                "age_minutes": round(age_minutes, 2),
                "ttl_minutes": ttl,
                "weight": round(weight, 3),
            })
            weights.append(weight)

        multiplier = sum(weights) / len(weights) if weights else 1.0
        multiplier = max(
            safe_float(stale_cfg.get("min_multiplier_floor"), 0.1),
            min(multiplier, 1.0),
        )
        return {
            "source_keys": list(source_keys),
            "details": details,
            "multiplier": round(multiplier, 3),
            "penalty": round(max(0.0, 1.0 - multiplier), 3),
            "fresh_sources": [d["source"] for d in details if d["status"] == "fresh"],
            "stale_sources": [d["source"] for d in details if d["status"] == "stale"],
            "expired_sources": [d["source"] for d in details if d["status"] == "expired"],
            "missing_sources": [d["source"] for d in details if d["status"] == "missing"],
        }

    # ------------------------------------------------------------------
    # Strategy inventory
    # ------------------------------------------------------------------
    def _collect_strategy_inventory(self, context: RuntimeContext) -> List[Dict[str, Any]]:
        inventory: List[Dict[str, Any]] = []
        strategy_catalog = self.config.get("strategy_catalog") or {}
        recent_realized_pnl = safe_float(self.state.get("recent_realized_pnl"), 0.0)

        war_cfg = strategy_catalog.get("war_engine") or {}
        if war_cfg.get("enabled", True):
            war_ideas = self.strategy_engine.evaluate_entries(
                scorecard=context.scorecard,
                bridge_results=context.bridge_results,
                market_data=context.market_data,
            )
            for idea in war_ideas:
                inventory.append(
                    self._normalize_idea(
                        idea=idea,
                        family="war_engine",
                        strategy_name=str(idea.get("strategy") or "war_engine"),
                        market_data=context.market_data,
                        source_timestamps=context.source_timestamps,
                        source_keys=list(war_cfg.get("source_keys") or []),
                    )
                )

        for strategy_name, evaluator in self.strategy_evaluators.items():
            strat_cfg = strategy_catalog.get(strategy_name) or {}
            if not strat_cfg.get("enabled", True):
                continue
            ideas = self._invoke_strategy(
                evaluator,
                strategy_name=strategy_name,
                scorecard=context.scorecard,
                bridge_results=context.bridge_results,
                market_data=context.market_data,
                recent_realized_pnl=recent_realized_pnl,
                params=dict(strat_cfg.get("params") or {}),
            )
            for idea in ideas:
                inventory.append(
                    self._normalize_idea(
                        idea=idea,
                        family="instagram_strategies",
                        strategy_name=str(idea.get("strategy") or strategy_name),
                        market_data=context.market_data,
                        source_timestamps=context.source_timestamps,
                        source_keys=list(strat_cfg.get("source_keys") or ["market_data"]),
                    )
                )

        inventory.sort(key=lambda item: item.get("confidence_score", 0.0), reverse=True)
        return inventory

    @staticmethod
    def _build_market_microstructure_snapshot(
        bridge_results: Dict[str, Any],
        market_data: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        snapshot = dict(bridge_results.get("market_microstructure") or {})
        for symbol, sym_data in (market_data or {}).items():
            if not isinstance(sym_data, dict):
                continue
            sym = str(symbol or "").upper()
            if not sym:
                continue
            existing = dict(snapshot.get(sym) or {})
            adv_shares = safe_float(
                existing.get("adv_shares"),
                safe_float(sym_data.get("avg_volume"), safe_float(sym_data.get("volume"), 1_000_000.0)),
            )
            sigma_daily_pct = safe_float(
                existing.get("sigma_daily_pct"),
                max(
                    0.25,
                    safe_float(sym_data.get("sigma_daily_pct"), abs(safe_float(sym_data.get("change_pct"), 2.0))),
                ),
            )
            last_price = safe_float(
                existing.get("last_price"),
                safe_float(sym_data.get("price"), safe_float(sym_data.get("last_price"), 0.0)),
            )
            snapshot[sym] = {
                **existing,
                "adv_shares": adv_shares,
                "sigma_daily_pct": sigma_daily_pct,
                "last_price": last_price,
                "source": existing.get("source") or "paper_training_synthetic",
                "fresh": existing.get("fresh", True),
            }
        return snapshot

    @staticmethod
    def _invoke_strategy(
        evaluator: Callable[..., List[Dict[str, Any]]],
        *,
        strategy_name: str,
        scorecard: Dict[str, Any],
        bridge_results: Dict[str, Any],
        market_data: Dict[str, Dict[str, Any]],
        recent_realized_pnl: float,
        params: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        kwargs: Dict[str, Any] = {}
        signature = inspect.signature(evaluator)
        for name in signature.parameters:
            if name == "strat":
                kwargs[name] = {"params": params}
            elif name == "scorecard":
                kwargs[name] = scorecard
            elif name == "bridge_results":
                kwargs[name] = bridge_results
            elif name == "market_data":
                kwargs[name] = market_data
            elif name == "osint_data":
                kwargs[name] = bridge_results.get("maritime") or bridge_results
            elif name == "recent_pnl":
                kwargs[name] = recent_realized_pnl
        try:
            return list(evaluator(**kwargs) or [])
        except Exception as exc:  # pragma: no cover - defensive guard
            return [{
                "strategy": strategy_name,
                "symbol": "",
                "direction": "long",
                "confidence_score": 0.0,
                "metadata": {"error": str(exc), "strategy_invocation_failed": True},
                "entry_signal": f"{strategy_name} invocation failed",
            }]

    def _normalize_idea(
        self,
        *,
        idea: Dict[str, Any],
        family: str,
        strategy_name: str,
        market_data: Dict[str, Dict[str, Any]],
        source_timestamps: Dict[str, str],
        source_keys: List[str],
    ) -> Dict[str, Any]:
        symbol = str(idea.get("symbol") or "").upper().strip()
        sym_data = market_data.get(symbol) or {}
        freshness = self._evaluate_freshness(source_keys, source_timestamps)
        raw_direction = str(idea.get("direction") or idea.get("side") or "long").lower()
        routeability = self._routeability(raw_direction)
        raw_confidence = safe_float(
            idea.get("confidence_score", idea.get("confidence", 0.0)),
            0.0,
        )
        confidence_score = round(min(1.0, raw_confidence * freshness["multiplier"]), 3)
        size_multiplier = safe_float(
            idea.get("tier_size_multiplier", idea.get("size_multiplier_suggestion", 1.0)),
            1.0,
        )
        size_multiplier = round(max(0.1, size_multiplier * freshness["multiplier"]), 3)

        decision_price = self._resolve_market_price(symbol, market_data)
        if decision_price <= 0:
            decision_price = safe_float(idea.get("entry"), 0.0)

        metadata = dict(idea.get("metadata") or {})
        metadata.update({
            "strategy_name": strategy_name,
            "strategy_family": family,
            "entry_signal": idea.get("entry_signal"),
            "rationale": idea.get("rationale"),
            "stop_loss_pct": idea.get("stop_loss_pct"),
            "take_profit_pct": idea.get("take_profit_pct"),
            "account": idea.get("account"),
            "raw_confidence": round(raw_confidence, 3),
            "confidence_multiplier": freshness["multiplier"],
            "freshness": freshness,
            "stale_candidate": bool(
                freshness["stale_sources"]
                or freshness["expired_sources"]
                or freshness["missing_sources"]
            ),
        })
        block_reasons: List[str] = []
        if routeability["block_reason"]:
            block_reasons.append(str(routeability["block_reason"]))
        if not symbol:
            block_reasons.append("missing_symbol")

        return {
            "candidate_id": f"paper-{symbol.lower() or 'missing'}-{uuid.uuid4().hex[:8]}",
            "symbol": symbol,
            "side": routeability["side"],
            "direction": routeability["direction"],
            "strategy_style": strategy_name,
            "template_key": f"{strategy_name}_{routeability['direction']}_{symbol.lower() or 'missing'}",
            "instrument_types": ["equity"],
            "confidence_score": confidence_score,
            "size_multiplier_suggestion": size_multiplier,
            "status": "blocked" if block_reasons else "eligible",
            "block_reasons": block_reasons,
            "price_hints": {
                "decision_price": decision_price if decision_price > 0 else None,
                "last_price": decision_price if decision_price > 0 else None,
                "prior_close": safe_float(sym_data.get("prior_close"), 0.0) or None,
            },
            "execution_constraints": {
                "limit_price_fallback": decision_price if decision_price > 0 else None,
                "manual_review_required": False,
            },
            "fill_sim_assessment": {
                "fill_feasibility_score": self._fill_feasibility(sym_data, decision_price),
                "expected_slippage_bps": self._expected_slippage_bps(sym_data),
                "reject_risk_probability": 0.03 if decision_price > 0 else 0.20,
                "do_not_route_even_in_shadow": bool(routeability["block_reason"]),
            },
            "holding_period": str(idea.get("holding_period") or "day"),
            "entry_signal": idea.get("entry_signal"),
            "rationale": idea.get("rationale"),
            "metadata": metadata,
        }

    @staticmethod
    def _routeability(raw_direction: str) -> Dict[str, Optional[str]]:
        mapping = {
            "long": {"side": "buy", "direction": "bullish", "block_reason": None},
            "bullish": {"side": "buy", "direction": "bullish", "block_reason": None},
            "buy": {"side": "buy", "direction": "bullish", "block_reason": None},
            "short": {"side": "sell", "direction": "bearish", "block_reason": None},
            "bearish": {"side": "sell", "direction": "bearish", "block_reason": None},
            "sell": {"side": "sell", "direction": "bearish", "block_reason": None},
            "short_vol": {
                "side": "sell",
                "direction": "short_vol",
                "block_reason": "unsupported_expression_short_vol",
            },
            "long_vol": {
                "side": "buy",
                "direction": "long_vol",
                "block_reason": "unsupported_expression_long_vol",
            },
        }
        return mapping.get(
            raw_direction,
            {"side": "buy", "direction": raw_direction or "bullish", "block_reason": None},
        )

    @staticmethod
    def _fill_feasibility(sym_data: Dict[str, Any], decision_price: float) -> float:
        if decision_price <= 0:
            return 0.35
        rel_vol = safe_float(sym_data.get("relative_volume", sym_data.get("rvol")), 1.0)
        return round(min(0.95, 0.65 + min(rel_vol, 2.0) * 0.10), 3)

    @staticmethod
    def _expected_slippage_bps(sym_data: Dict[str, Any]) -> float:
        sigma = safe_float(sym_data.get("sigma_daily_pct", sym_data.get("volatility_pct")), 2.0)
        return round(max(5.0, min(80.0, sigma * 2.5)), 2)

    # ------------------------------------------------------------------
    # Position tracking / mock simulation
    # ------------------------------------------------------------------
    def _simulate_mock_entry_fills(
        self,
        route_summary: Dict[str, Any],
        market_data: Dict[str, Dict[str, Any]],
    ) -> int:
        loop_cfg = self.config.get("loop") or {}
        if self.broker_name != "mock" or not loop_cfg.get("simulate_fills_on_route", True):
            return 0
        adapter = self.router.adapter
        simulated = 0
        for row in route_summary.get("bound_order_attempts", []) or []:
            order_id = row.get("broker_order_id")
            qty = safe_float(row.get("qty"), 0.0)
            symbol = str(row.get("symbol") or "").upper()
            fill_price = safe_float(row.get("decision_price"), 0.0)
            if fill_price <= 0:
                fill_price = safe_float(row.get("limit_price"), 0.0)
            if fill_price <= 0:
                fill_price = self._resolve_market_price(symbol, market_data)
            if not order_id or qty <= 0 or fill_price <= 0:
                continue
            adapter.simulate_fill(str(order_id), qty, fill_price)
            self._sync_intent_reconciliation(str(order_id), "mock_auto_fill")
            simulated += 1
        return simulated

    def _mock_auto_close_positions(
        self,
        previous_positions: Dict[str, Dict[str, Any]],
        market_data: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        loop_cfg = self.config.get("loop") or {}
        if self.broker_name != "mock" or not loop_cfg.get("simulate_exits_using_targets", True):
            return []

        adapter = self.router.adapter
        current_positions = {
            str(pos.get("symbol") or "").upper(): pos
            for pos in adapter.list_positions()
            if pos.get("symbol")
        }

        exit_events: List[Dict[str, Any]] = []
        for symbol, state_pos in previous_positions.items():
            live_pos = current_positions.get(symbol)
            if not live_pos:
                continue
            qty = safe_float(live_pos.get("qty"), 0.0)
            if abs(qty) <= 1e-9:
                continue
            entry_price = safe_float(state_pos.get("avg_entry_price"), 0.0)
            current_price = self._resolve_market_price(symbol, market_data)
            if entry_price <= 0 or current_price <= 0:
                continue

            stop_loss_pct = abs(safe_float(state_pos.get("stop_loss_pct"), 0.0))
            take_profit_pct = abs(safe_float(state_pos.get("take_profit_pct"), 0.0))
            signed_return_pct = ((current_price - entry_price) / entry_price) * 100.0
            if qty < 0:
                signed_return_pct *= -1.0

            reason = None
            if stop_loss_pct > 0 and signed_return_pct <= -stop_loss_pct:
                reason = "stop_loss_trigger"
            elif take_profit_pct > 0 and signed_return_pct >= take_profit_pct:
                reason = "take_profit_trigger"
            if reason is None:
                continue

            close_side = "sell" if qty > 0 else "buy"
            close_order = adapter.submit_order({
                "symbol": symbol,
                "side": close_side,
                "type": "market",
                "qty": abs(qty),
                "shadow_mode": True,
                "client_order_id": f"auto-close-{uuid.uuid4().hex[:12]}",
            })
            adapter.simulate_fill(str(close_order["order_id"]), abs(qty), current_price)
            exit_events.append({
                "symbol": symbol,
                "qty": abs(qty),
                "exit_price": round(current_price, 4),
                "reason": reason,
            })
        return exit_events

    def _sync_intent_reconciliation(self, broker_order_id: str, note: str) -> None:
        intent = self.intent_registry.get_by_broker_order_id(broker_order_id)
        if not intent:
            return
        broker_order = self.router.adapter.get_order(broker_order_id)
        broker_trades = self.router.adapter.get_trades(broker_order_id)
        self.intent_registry.record_broker_reconciliation(
            intent_id=intent["intent_id"],
            broker_order=broker_order,
            broker_trades=broker_trades,
            reconciler_status="ok",
            notes=[note],
        )

    def _collect_positions(
        self,
        market_data: Dict[str, Dict[str, Any]],
        candidate_map: Optional[Dict[str, Dict[str, Any]]] = None,
        *,
        now_iso: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        if now_iso is None:
            now_iso = iso_now()
        candidate_map = candidate_map or {}
        positions: Dict[str, Dict[str, Any]] = {}
        previous_positions = self.state.get("previous_positions") or {}

        for raw in self.router.adapter.list_positions():
            symbol = str(raw.get("symbol") or "").upper()
            qty = safe_float(raw.get("qty"), 0.0)
            if not symbol or abs(qty) <= 1e-9:
                continue

            current_price = self._current_position_price(raw, market_data)
            avg_entry_price = safe_float(raw.get("avg_entry_price"), 0.0)
            current_context = candidate_map.get(symbol) or previous_positions.get(symbol) or self._lookup_position_context(symbol)
            metadata = dict(current_context.get("metadata") or {})
            positions[symbol] = {
                "symbol": symbol,
                "qty": qty,
                "avg_entry_price": avg_entry_price,
                "current_price": current_price,
                "unrealized_pl": self._calc_unrealized_pl(qty, avg_entry_price, current_price),
                "strategy": current_context.get("strategy") or metadata.get("strategy_name") or "paper_training",
                "metadata": metadata,
                "order_metadata": dict(current_context.get("order_metadata") or {}),
                "opened_at_utc": current_context.get("opened_at_utc") or now_iso,
                "stop_loss_pct": current_context.get("stop_loss_pct") or metadata.get("stop_loss_pct"),
                "take_profit_pct": current_context.get("take_profit_pct") or metadata.get("take_profit_pct"),
            }
        return positions

    def _lookup_position_context(self, symbol: str) -> Dict[str, Any]:
        matches = []
        for intent in self.intent_registry.list_intents():
            cand = intent.get("candidate_context") or {}
            if str(cand.get("symbol") or "").upper() != symbol:
                continue
            matches.append(intent)
        if not matches:
            return {}
        intent = sorted(matches, key=lambda row: row.get("timestamp_utc") or "", reverse=True)[0]
        cand = intent.get("candidate_context") or {}
        metadata = dict(cand.get("metadata") or {})
        return {
            "strategy": metadata.get("strategy_name") or cand.get("strategy_style"),
            "metadata": metadata,
            "order_metadata": {
                "signal_boost_detail": metadata.get("signal_boost_detail", {}),
                "freshness": metadata.get("freshness"),
            },
            "opened_at_utc": intent.get("timestamp_utc"),
            "stop_loss_pct": metadata.get("stop_loss_pct"),
            "take_profit_pct": metadata.get("take_profit_pct"),
        }

    @staticmethod
    def _current_position_price(position: Dict[str, Any], market_data: Dict[str, Dict[str, Any]]) -> float:
        symbol = str(position.get("symbol") or "").upper()
        current_price = safe_float(position.get("current_price"), 0.0)
        if current_price > 0:
            return current_price
        market_value = safe_float(position.get("market_value"), 0.0)
        qty = safe_float(position.get("qty"), 0.0)
        if market_value and qty:
            return abs(market_value / qty)
        return ContinuousPaperTrainingOrchestrator._resolve_market_price(symbol, market_data)

    @staticmethod
    def _calc_unrealized_pl(qty: float, avg_entry_price: float, current_price: float) -> float:
        if avg_entry_price <= 0 or current_price <= 0 or abs(qty) <= 1e-9:
            return 0.0
        return round((current_price - avg_entry_price) * qty, 2)

    @staticmethod
    def _resolve_market_price(symbol: str, market_data: Dict[str, Dict[str, Any]]) -> float:
        if not symbol:
            return 0.0
        sym_data = market_data.get(symbol) or market_data.get(symbol.upper()) or market_data.get(symbol.lower()) or {}
        for field in ("price", "last_price", "last", "mark_price", "close", "prior_close"):
            price = safe_float(sym_data.get(field), 0.0)
            if price > 0:
                return price
        return 0.0

    def _reconcile_closed_positions(
        self,
        previous_positions: Dict[str, Dict[str, Any]],
        current_positions: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        closures: List[Dict[str, Any]] = []
        now_iso = iso_now()

        for symbol, previous in previous_positions.items():
            prev_qty = safe_float(previous.get("qty"), 0.0)
            if abs(prev_qty) <= 1e-9:
                continue
            current = current_positions.get(symbol)
            cur_qty = safe_float((current or {}).get("qty"), 0.0)

            full_close = current is None or prev_qty * cur_qty < 0
            reduced = current is not None and abs(cur_qty) < abs(prev_qty) and prev_qty * cur_qty >= 0
            if not full_close and not reduced:
                continue

            closed_qty = abs(prev_qty) if full_close else abs(prev_qty) - abs(cur_qty)
            exit_price = safe_float((current or {}).get("current_price"), 0.0)
            if exit_price <= 0:
                exit_price = safe_float(previous.get("current_price"), 0.0)
            entry_price = safe_float(previous.get("avg_entry_price"), 0.0)
            if entry_price <= 0 or exit_price <= 0 or closed_qty <= 0:
                continue

            side = "long" if prev_qty > 0 else "short"
            metadata = dict(previous.get("metadata") or {})
            order_metadata = dict(previous.get("order_metadata") or {})
            order_metadata.setdefault(
                "signal_boost_detail",
                metadata.get("signal_boost_detail", {}),
            )
            closure = self.performance_tracker.record_closed_trade(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                exit_price=exit_price,
                qty=closed_qty,
                entry_time=previous.get("opened_at_utc") or previous.get("updated_at_utc") or now_iso,
                exit_time=now_iso,
                reason="position_closed" if full_close else "position_reduced",
                strategy=previous.get("strategy") or metadata.get("strategy_name") or "paper_training",
                metadata=metadata,
                order_metadata=order_metadata,
            )
            closures.append(closure)

        return closures

    # ------------------------------------------------------------------
    # Cycle execution
    # ------------------------------------------------------------------
    def run_once(
        self,
        *,
        scorecard_json: Optional[Path] = None,
        bridge_results_json: Optional[Path] = None,
        market_data_json: Optional[Path] = None,
        source_timestamps_json: Optional[Path] = None,
        context: Optional[Dict[str, Any]] = None,
        route_orders: bool = True,
    ) -> Dict[str, Any]:
        runtime = self._load_runtime_context(
            scorecard_json=scorecard_json,
            bridge_results_json=bridge_results_json,
            market_data_json=market_data_json,
            source_timestamps_json=source_timestamps_json,
            context=context,
        )
        loop_cfg = self.config.get("loop") or {}
        cycle_id = f"paper-cycle-{uuid.uuid4().hex[:10]}"

        previous_positions = dict(self.state.get("previous_positions") or {})
        auto_exits = self._mock_auto_close_positions(previous_positions, runtime.market_data)
        post_exit_positions = self._collect_positions(runtime.market_data)
        closures = self._reconcile_closed_positions(previous_positions, post_exit_positions)

        inventory = self._collect_strategy_inventory(runtime)
        routeable_candidates = [row for row in inventory if row.get("status") != "blocked"]
        blocked_candidates = [row for row in inventory if row.get("status") == "blocked"]
        market_microstructure = self._build_market_microstructure_snapshot(
            runtime.bridge_results,
            runtime.market_data,
        )

        package = {
            "schema_version": "continuous_paper_package.v1",
            "package_id": f"paper-pack-{uuid.uuid4().hex[:12]}",
            "package_type": "continuous_paper_training",
            "timestamp_utc": iso_now(),
            "effective_mode": "paper_training",
            "candidates": routeable_candidates,
            "blocked_candidates": blocked_candidates,
            "global_blocks": [],
            "window_context": {
                "time_window_name": "continuous_paper_training",
                "watchlist_only_window": False,
            },
            "macro_context": {
                "regime_shift_probability": runtime.scorecard.get("regime_shift_probability"),
                "confidence": runtime.scorecard.get("confidence"),
                "macro_event_quorum_pass": not runtime.scorecard.get("fallback_mode_status", False),
            },
            "snapshot": {
                "market_microstructure": market_microstructure,
            },
        }

        route_summary: Dict[str, Any] = {
            "submit_attempt_count": 0,
            "bound_order_attempts": [],
            "skipped_candidates": [],
            "selected_candidates": [],
            "broker_rejected_count": 0,
            "submitted_open_or_ack_count": 0,
        }
        if route_orders:
            route_summary = self.router.route_package(
                package,
                max_orders=int(loop_cfg.get("max_orders_per_cycle", 12)),
                min_confidence=safe_float(loop_cfg.get("min_route_confidence"), 0.18),
            )
            route_summary["mock_simulated_fill_count"] = self._simulate_mock_entry_fills(
                route_summary,
                runtime.market_data,
            )

        candidate_map = {
            row["symbol"]: {
                "strategy": (row.get("metadata") or {}).get("strategy_name") or row.get("strategy_style"),
                "metadata": dict(row.get("metadata") or {}),
                "order_metadata": {
                    "signal_boost_detail": (row.get("metadata") or {}).get("signal_boost_detail", {}),
                    "freshness": (row.get("metadata") or {}).get("freshness"),
                },
                "opened_at_utc": iso_now(),
                "stop_loss_pct": (row.get("metadata") or {}).get("stop_loss_pct"),
                "take_profit_pct": (row.get("metadata") or {}).get("take_profit_pct"),
            }
            for row in routeable_candidates
            if row.get("symbol")
        }
        final_positions = self._collect_positions(runtime.market_data, candidate_map)
        self.performance_tracker.snapshot_open_positions([
            {
                "symbol": position["symbol"],
                "qty": position["qty"],
                "avg_entry_price": position["avg_entry_price"],
                "current_price": position["current_price"],
                "unrealized_pl": position["unrealized_pl"],
            }
            for position in final_positions.values()
        ])

        self.state["recent_realized_pnl"] = round(
            sum(safe_float(row.get("pnl"), 0.0) for row in closures),
            2,
        )
        feedback = self.feedback_loop.analyze_and_adjust()
        performance_summary = self.performance_tracker.generate_summary()

        cycle_summary = {
            "timestamp_utc": iso_now(),
            "cycle_id": cycle_id,
            "broker_name": self.broker_name,
            "scorecard_path": runtime.scorecard_path,
            "bridge_results_path": runtime.bridge_results_path,
            "market_data_path": runtime.market_data_path,
            "candidate_inventory_count": len(inventory),
            "routeable_candidate_count": len(routeable_candidates),
            "blocked_candidate_count": len(blocked_candidates),
            "stale_candidate_count": sum(
                1 for row in inventory if (row.get("metadata") or {}).get("stale_candidate")
            ),
            "strategy_counts": self._strategy_counts(inventory),
            "auto_exit_count": len(auto_exits),
            "closure_count": len(closures),
            "closure_pnl_usd": round(sum(safe_float(row.get("pnl"), 0.0) for row in closures), 2),
            "open_position_count": len(final_positions),
            "route_summary": route_summary,
            "feedback_status": feedback.get("status"),
            "feedback_trades_analyzed": feedback.get("trades_analyzed"),
            "performance_summary": performance_summary,
        }

        self._append_jsonl(self.inventory_log_path, {
            "timestamp_utc": cycle_summary["timestamp_utc"],
            "cycle_id": cycle_id,
            "candidates": inventory,
        })
        self._append_jsonl(self.cycle_log_path, cycle_summary)
        self.status_report_path.write_text(
            json.dumps(cycle_summary, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        self.state["cycle_count"] = int(self.state.get("cycle_count", 0)) + 1
        self.state["previous_positions"] = final_positions
        self.state["last_cycle_summary"] = cycle_summary
        self._save_state()
        return cycle_summary

    @staticmethod
    def _strategy_counts(inventory: List[Dict[str, Any]]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for row in inventory:
            name = str((row.get("metadata") or {}).get("strategy_name") or row.get("strategy_style") or "unknown")
            counts[name] = counts.get(name, 0) + 1
        return dict(sorted(counts.items()))

    def run_loop(
        self,
        *,
        cycles: Optional[int] = None,
        sleep_seconds: Optional[float] = None,
        route_orders: bool = True,
        scorecard_json: Optional[Path] = None,
        bridge_results_json: Optional[Path] = None,
        market_data_json: Optional[Path] = None,
        source_timestamps_json: Optional[Path] = None,
    ) -> Dict[str, Any]:
        loop_cfg = self.config.get("loop") or {}
        delay = sleep_seconds if sleep_seconds is not None else safe_float(loop_cfg.get("sleep_seconds"), 300.0)
        completed = 0
        last_summary: Dict[str, Any] = {}

        while cycles is None or completed < cycles:
            last_summary = self.run_once(
                scorecard_json=scorecard_json,
                bridge_results_json=bridge_results_json,
                market_data_json=market_data_json,
                source_timestamps_json=source_timestamps_json,
                route_orders=route_orders,
            )
            completed += 1
            if cycles is not None and completed >= cycles:
                break
            time.sleep(delay)

        return {
            "status": "success",
            "iterations_completed": completed,
            "last_cycle_summary": last_summary,
        }

    def run_replay(
        self,
        contexts: List[Dict[str, Any]],
        *,
        route_orders: bool = True,
    ) -> Dict[str, Any]:
        results = []
        for context in contexts:
            results.append(self.run_once(context=context, route_orders=route_orders))
        report = {
            "timestamp_utc": iso_now(),
            "status": "success",
            "iterations_completed": len(results),
            "results": results,
        }
        self.replay_report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return report


def load_context_sequence(path: Path) -> List[Dict[str, Any]]:
    payload = load_json(path)
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    contexts = payload.get("contexts") or []
    return [dict(item) for item in contexts if isinstance(item, dict)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--config", default=None)
    parser.add_argument("--broker", default=None)
    parser.add_argument("--scorecard-json", default=None)
    parser.add_argument("--bridge-results-json", default=None)
    parser.add_argument("--market-data-json", default=None)
    parser.add_argument("--source-timestamps-json", default=None)
    parser.add_argument("--context-sequence-json", default=None)
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--sleep-seconds", type=float, default=None)
    parser.add_argument("--no-route", action="store_true")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    orchestrator = ContinuousPaperTrainingOrchestrator(
        repo_root=repo_root,
        config_path=Path(args.config).resolve() if args.config else None,
        broker_name=args.broker,
    )

    if args.context_sequence_json:
        result = orchestrator.run_replay(
            load_context_sequence(Path(args.context_sequence_json).resolve()),
            route_orders=not args.no_route,
        )
    else:
        result = orchestrator.run_loop(
            cycles=args.cycles,
            sleep_seconds=args.sleep_seconds,
            route_orders=not args.no_route,
            scorecard_json=Path(args.scorecard_json).resolve() if args.scorecard_json else None,
            bridge_results_json=Path(args.bridge_results_json).resolve() if args.bridge_results_json else None,
            market_data_json=Path(args.market_data_json).resolve() if args.market_data_json else None,
            source_timestamps_json=Path(args.source_timestamps_json).resolve() if args.source_timestamps_json else None,
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
