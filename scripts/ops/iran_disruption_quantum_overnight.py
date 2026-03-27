#!/usr/bin/env python3
"""Research-only overnight Iran chokepoint + quantum comparison orchestrator.

This job is intentionally bounded to research artifacts:
- registers chokepoint scenarios and crisis analogs
- collects live bridge data and builds training features
- retrains the anomaly detector on fresh feature vectors
- runs multi-backend quantum/classical comparison on a widened disruption universe
- benchmarks outputs against historical analog scenarios and real market history
- periodically runs bounded retraining from experiment artifacts

It never touches execution paths or broker routing.
"""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Dict, Iterable, List, Mapping, Optional


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from src.research.backends.multi_backend_orchestrator import MultiBackendOrchestrator
from src.research.benchmark_quantum_vs_classical import (
    compute_multi_backend_metrics,
    load_multi_backend_comparisons,
)
from src.research.experiment_tracker import ExperimentTracker
from src.research.quantum_retraining_job import QuantumRetrainingJob
from src.research.training.chokepoint_scenarios import (
    CHOKEPOINTS,
    COMBINED_SCENARIOS,
    compute_chokepoint_risk_score,
)
from src.research.training.full_source_trainer import FullSourceTrainer


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log(message: str) -> None:
    print(f"[{_iso_now()}] {message}", flush=True)


# Market hours scheduling: pause training during 09:15-16:30 ET to free CPU
_ET = ZoneInfo("America/New_York")
_MARKET_OPEN_ET = (9, 15)
_MARKET_CLOSE_ET = (16, 30)
_OFFHOURS_SLEEP = 300


def _is_market_hours():
    now_et = datetime.now(_ET)
    if now_et.weekday() >= 5:
        return False
    t = (now_et.hour, now_et.minute)
    return _MARKET_OPEN_ET <= t < _MARKET_CLOSE_ET


def _seconds_until_market_close():
    now_et = datetime.now(_ET)
    close_today = now_et.replace(hour=_MARKET_CLOSE_ET[0], minute=_MARKET_CLOSE_ET[1], second=0, microsecond=0)
    delta = (close_today - now_et).total_seconds()
    return max(0, int(delta))



def _load_script_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


REGISTER_CHOKEPOINT_MODULE = _load_script_module(
    "register_chokepoint_scenarios_mod",
    REPO_ROOT / "scripts" / "ops" / "register_chokepoint_scenarios.py",
)
RUN_CRISIS_TRAINING_MODULE = _load_script_module(
    "run_crisis_training_mod",
    REPO_ROOT / "scripts" / "ops" / "run_crisis_training.py",
)
HISTORICAL_BACKTEST_MODULE = _load_script_module(
    "historical_backtest_mod",
    REPO_ROOT / "scripts" / "ops" / "historical_backtest.py",
)


BASE_CANDIDATES: List[Dict[str, Any]] = [
    {"symbol": "USO", "sector": "energy", "theme": "oil_direct", "direction": "long", "volatility": 0.38},
    {"symbol": "XLE", "sector": "energy", "theme": "energy_equity", "direction": "long", "volatility": 0.30},
    {"symbol": "XOP", "sector": "energy", "theme": "energy_beta", "direction": "long", "volatility": 0.36},
    {"symbol": "OXY", "sector": "energy", "theme": "upstream", "direction": "long", "volatility": 0.34},
    {"symbol": "XOM", "sector": "energy", "theme": "integrated_oil", "direction": "long", "volatility": 0.24},
    {"symbol": "CVX", "sector": "energy", "theme": "integrated_oil", "direction": "long", "volatility": 0.22},
    {"symbol": "MPC", "sector": "refining", "theme": "refiner", "direction": "long", "volatility": 0.31},
    {"symbol": "VLO", "sector": "refining", "theme": "refiner", "direction": "long", "volatility": 0.31},
    {"symbol": "PSX", "sector": "refining", "theme": "refiner", "direction": "long", "volatility": 0.29},
    {"symbol": "PBF", "sector": "refining", "theme": "refiner", "direction": "long", "volatility": 0.39},
    {"symbol": "UNG", "sector": "energy", "theme": "nat_gas", "direction": "long", "volatility": 0.45},
    {"symbol": "LNG", "sector": "energy", "theme": "lng_infrastructure", "direction": "long", "volatility": 0.25},
    {"symbol": "GLD", "sector": "safe_haven", "theme": "gold", "direction": "long", "volatility": 0.18},
    {"symbol": "GDX", "sector": "safe_haven", "theme": "gold_beta", "direction": "long", "volatility": 0.28},
    {"symbol": "TLT", "sector": "rates", "theme": "treasury", "direction": "long", "volatility": 0.14},
    {"symbol": "UUP", "sector": "fx", "theme": "usd_safe_haven", "direction": "long", "volatility": 0.10},
    {"symbol": "UVXY", "sector": "volatility", "theme": "vol_spike", "direction": "long", "volatility": 0.85},
    {"symbol": "LMT", "sector": "defense", "theme": "defense_prime", "direction": "long", "volatility": 0.20},
    {"symbol": "RTX", "sector": "defense", "theme": "missile_defense", "direction": "long", "volatility": 0.23},
    {"symbol": "NOC", "sector": "defense", "theme": "defense_prime", "direction": "long", "volatility": 0.19},
    {"symbol": "GD", "sector": "defense", "theme": "defense_prime", "direction": "long", "volatility": 0.19},
    {"symbol": "ITA", "sector": "defense", "theme": "defense_etf", "direction": "long", "volatility": 0.21},
    {"symbol": "PPA", "sector": "defense", "theme": "defense_etf", "direction": "long", "volatility": 0.20},
    {"symbol": "AVAV", "sector": "defense", "theme": "drone_systems", "direction": "long", "volatility": 0.34},
    {"symbol": "HII", "sector": "defense", "theme": "naval_shipbuilding", "direction": "long", "volatility": 0.22},
    {"symbol": "ZIM", "sector": "shipping", "theme": "container_shipping", "direction": "long", "volatility": 0.52},
    {"symbol": "SBLK", "sector": "shipping", "theme": "dry_bulk", "direction": "long", "volatility": 0.38},
    {"symbol": "INSW", "sector": "shipping", "theme": "tankers", "direction": "long", "volatility": 0.35},
    {"symbol": "FRO", "sector": "shipping", "theme": "tankers", "direction": "long", "volatility": 0.34},
    {"symbol": "MOS", "sector": "agriculture", "theme": "fertilizer", "direction": "long", "volatility": 0.29},
    {"symbol": "CF", "sector": "agriculture", "theme": "fertilizer", "direction": "long", "volatility": 0.28},
    {"symbol": "NTR", "sector": "agriculture", "theme": "fertilizer", "direction": "long", "volatility": 0.24},
    {"symbol": "HACK", "sector": "cyber", "theme": "cyber_defense", "direction": "long", "volatility": 0.20},
    {"symbol": "PANW", "sector": "cyber", "theme": "cyber_defense", "direction": "long", "volatility": 0.29},
    {"symbol": "SOXX", "sector": "semis", "theme": "semiconductor_supply_chain", "direction": "short", "volatility": 0.28},
    {"symbol": "JETS", "sector": "aviation", "theme": "airline_fuel_pressure", "direction": "short", "volatility": 0.32},
    {"symbol": "DAL", "sector": "aviation", "theme": "airline_fuel_pressure", "direction": "short", "volatility": 0.34},
    {"symbol": "UAL", "sector": "aviation", "theme": "airline_fuel_pressure", "direction": "short", "volatility": 0.38},
    {"symbol": "AAL", "sector": "aviation", "theme": "airline_fuel_pressure", "direction": "short", "volatility": 0.40},
    {"symbol": "CCL", "sector": "travel", "theme": "travel_discretionary", "direction": "short", "volatility": 0.36},
    {"symbol": "RCL", "sector": "travel", "theme": "travel_discretionary", "direction": "short", "volatility": 0.36},
    {"symbol": "EZU", "sector": "europe", "theme": "europe_energy_import", "direction": "short", "volatility": 0.22},
    {"symbol": "EUFN", "sector": "europe_banks", "theme": "europe_energy_financial_stress", "direction": "short", "volatility": 0.26},
    {"symbol": "EEM", "sector": "emerging_markets", "theme": "em_capital_flight", "direction": "short", "volatility": 0.23},
    {"symbol": "FXI", "sector": "china", "theme": "asia_import_pressure", "direction": "short", "volatility": 0.22},
    {"symbol": "INDA", "sector": "india", "theme": "asia_import_pressure", "direction": "short", "volatility": 0.21},
    {"symbol": "EWY", "sector": "korea", "theme": "asia_import_pressure", "direction": "short", "volatility": 0.22},
]


MARKET_BENCHMARK_SYMBOLS = [
    "SPY",
    "XLE",
    "USO",
    "UNG",
    "GLD",
    "TLT",
    "UUP",
    "JETS",
    "DAL",
    "UAL",
    "AAL",
    "CVX",
    "MPC",
    "VLO",
    "PSX",
    "LNG",
    "LMT",
    "RTX",
    "NOC",
    "GD",
    "ZIM",
    "FRO",
    "INSW",
    "SBLK",
    "EZU",
    "EUFN",
    "EEM",
    "FXI",
    "INDA",
    "EWY",
    "HACK",
    "PANW",
    "MOS",
    "CF",
    "NTR",
]


EXTRA_TRAINING_SCENARIOS = [
    "shipping_reroute_stress",
    "fertilizer_and_food_chain_inflation",
    "cyber_retaliation_on_energy_and_transport",
    "european_bank_energy_stress",
    "travel_and_cruise_demand_destruction",
    "semiconductor_supply_chain_secondary_shock",
    "safe_haven_fx_rotation",
]


BASKET_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "key": "consensus_core_cross_asset",
        "base_weight_pct": 35,
        "symbols": ["UUP", "TLT", "GLD", "NOC", "GD", "INDA", "EZU", "FXI", "EWY", "EEM"],
        "rationale": "Model consensus favors safe havens, defense, and import-stress shorts over pure oil chase.",
        "options_expression": "Call spreads on NOC/GD/GLD or put spreads on EZU/FXI/INDA.",
    },
    {
        "key": "refiners_lng_vs_airlines",
        "base_weight_pct": 25,
        "symbols": ["MPC", "VLO", "PSX", "LNG", "JETS", "DAL", "UAL", "AAL", "CVX"],
        "rationale": "Cleaner profit path than outright crude when oil is extended but route and fuel stress persist.",
        "options_expression": "Call spreads on MPC/VLO/PSX/LNG and put spreads on JETS/DAL/UAL/AAL.",
    },
    {
        "key": "shipping_reroute_stress",
        "base_weight_pct": 15,
        "symbols": ["INSW", "FRO", "ZIM", "SBLK"],
        "rationale": "Bab el-Mandeb stress keeps shipping and tanker convexity relevant even without full Hormuz panic.",
        "options_expression": "Calls or call spreads on tanker and shipping names where liquidity allows; otherwise stock only.",
    },
    {
        "key": "spillover_cyber_food_finance",
        "base_weight_pct": 15,
        "symbols": ["HACK", "PANW", "MOS", "CF", "NTR", "EUFN"],
        "rationale": "Second-order transmission to cyber, fertilizer, and European finance stays underpriced.",
        "options_expression": "Call spreads on HACK/PANW; put spreads on EUFN where liquid.",
    },
    {
        "key": "hedge_overlay",
        "base_weight_pct": 10,
        "symbols": ["UVXY", "GLD", "UUP"],
        "rationale": "Use vol and hedge overlays tactically, not as the dominant book.",
        "options_expression": "Small UVXY overlay or GLD/UUP calls only when shock acceleration resumes.",
    },
]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _candidate_expected_return(
    candidate: Mapping[str, Any],
    *,
    composite: float,
    active_chokepoints: int,
    aligned: bool,
) -> float:
    theme = str(candidate.get("theme", ""))
    direction = str(candidate.get("direction", "long"))

    base = 0.015
    if theme in {"oil_direct", "energy_equity", "energy_beta", "upstream", "integrated_oil", "nat_gas"}:
        base += 0.06 * composite
    elif theme in {"refiner", "lng_infrastructure", "container_shipping", "dry_bulk", "tankers"}:
        base += 0.04 * composite
    elif theme in {"gold", "gold_beta", "treasury", "usd_safe_haven", "vol_spike"}:
        base += 0.035 * composite
    elif theme in {"defense_prime", "missile_defense", "defense_etf", "drone_systems", "naval_shipbuilding"}:
        base += 0.03 * composite
    elif theme in {"fertilizer", "cyber_defense", "semiconductor_supply_chain"}:
        base += 0.02 * composite
    elif theme in {
        "airline_fuel_pressure",
        "travel_discretionary",
        "europe_energy_import",
        "europe_energy_financial_stress",
        "em_capital_flight",
        "asia_import_pressure",
    }:
        base += 0.045 * composite

    if active_chokepoints >= 2:
        base += 0.01
    if aligned:
        base += 0.02
    if direction == "short":
        base += 0.005

    return round(max(base, 0.005), 4)


def _candidate_score(expected_return: float, volatility: float, aligned: bool) -> float:
    score = expected_return / max(volatility, 0.05)
    if aligned:
        score += 0.05
    return round(score, 4)


def _dedupe_symbols(rows: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    deduped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        sym = str(row.get("symbol", "")).upper()
        if not sym:
            continue
        if sym not in deduped:
            deduped[sym] = dict(row)
    return list(deduped.values())


class IranDisruptionQuantumOvernight:
    def __init__(
        self,
        repo_root: str | Path = REPO_ROOT,
        *,
        iterations: int = 60,
        sleep_seconds: int = 900,
        mode: str = "full",
        retrain_every: int = 6,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.iterations = iterations
        self.sleep_seconds = sleep_seconds
        self.mode = mode
        self.retrain_every = max(retrain_every, 1)
        self.output_dir = self.repo_root / "reports" / "research" / "overnight_iran_quantum"
        self.comparison_dir = self.output_dir / "comparisons"
        self.status_path = self.output_dir / "service_status.json"
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.comparison_dir.mkdir(parents=True, exist_ok=True)
        self.selection_counter: Counter[str] = Counter()

    def run(self) -> Dict[str, Any]:
        started_at = _iso_now()
        self._update_status(
            phase="starting",
            started_at=started_at,
            completed_iterations=0,
            mode=self.mode,
            sleep_seconds=self.sleep_seconds,
        )
        _log(
            "starting overnight iran disruption runner "
            f"(iterations={self.iterations}, sleep_seconds={self.sleep_seconds}, mode={self.mode})"
        )
        static_prep = self._run_static_prep()
        self._update_status(
            phase="static_prep_complete",
            started_at=started_at,
            completed_iterations=0,
            static_prep_complete=True,
        )
        _log("static preparation complete")

        run_report: Dict[str, Any] = {
            "schema_version": "iran_disruption_quantum_overnight.v1",
            "started_at": started_at,
            "repo_root": str(self.repo_root),
            "iterations_requested": self.iterations,
            "sleep_seconds": self.sleep_seconds,
            "mode": self.mode,
            "static_prep": static_prep,
            "iterations": [],
            "extra_training_scenarios": EXTRA_TRAINING_SCENARIOS,
            "execution_metadata": {
                "not_for_direct_execution": True,
                "quantum_direct_execution_forbidden": True,
                "bounded_secondary_signal_only": True,
                "script": "iran_disruption_quantum_overnight",
            },
        }

        for iteration in self._iteration_numbers():
            self._update_status(
                phase="iteration_start",
                started_at=started_at,
                current_iteration=iteration,
                completed_iterations=run_report.get("completed_iterations", 0),
            )
            _log(f"starting iteration {iteration}/{self.iterations}")
            iteration_report = self._run_iteration(iteration)
            run_report["iterations"].append(iteration_report)
            run_report["selection_frequency"] = dict(self.selection_counter.most_common())
            run_report["completed_iterations"] = iteration
            run_report["last_iteration_completed_at"] = _iso_now()
            self._write_report("overnight_run_latest.json", run_report)
            self._update_status(
                phase="iteration_complete",
                started_at=started_at,
                current_iteration=iteration,
                completed_iterations=iteration,
                last_iteration_timestamp=iteration_report.get("timestamp_utc"),
                last_iteration_file="iteration_latest.json",
                selection_frequency=run_report["selection_frequency"],
            )
            _log(f"completed iteration {iteration}/{self.iterations}")

            if not self._is_last_iteration(iteration) and self.sleep_seconds > 0:
                # Run 24/7 with consistent sleep between iterations
                effective_sleep = min(self.sleep_seconds, _OFFHOURS_SLEEP)
                self._update_status(
                    phase="sleeping",
                    started_at=started_at,
                    current_iteration=iteration,
                    completed_iterations=iteration,
                    sleep_seconds=effective_sleep,
                    next_wake_eta=(datetime.now(timezone.utc) + timedelta(seconds=effective_sleep)).isoformat(),
                    reason="continuous training (24/7)",
                )
                _log(f"sleeping {effective_sleep}s before next iteration")
                time.sleep(effective_sleep)

        if self.iterations <= 0:
            return run_report

        run_report["completed_at"] = _iso_now()
        final_name = "overnight_run_%s.json" % datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._write_report(final_name, run_report)
        self._write_report("overnight_run_latest.json", run_report)
        self._update_status(
            phase="completed",
            started_at=started_at,
            completed_iterations=run_report.get("completed_iterations", 0),
            completed_at=run_report["completed_at"],
            final_report=final_name,
        )
        return run_report

    def _iteration_numbers(self):
        if self.iterations <= 0:
            return itertools.count(1)
        return range(1, self.iterations + 1)

    def _is_last_iteration(self, iteration: int) -> bool:
        return self.iterations > 0 and iteration >= self.iterations

    def _run_static_prep(self) -> Dict[str, Any]:
        self._update_status(phase="registering_chokepoints")
        _log("registering chokepoint scenarios")
        register_result = REGISTER_CHOKEPOINT_MODULE.register(self.repo_root)
        self._update_status(phase="crisis_training")
        _log("running crisis training")
        crisis_training = RUN_CRISIS_TRAINING_MODULE.run_training(self.repo_root)
        self._update_status(phase="historical_benchmarks")
        _log("running historical benchmark scenarios")
        historical = self._historical_benchmarks()
        return {
            "registered_chokepoints": register_result,
            "crisis_training": crisis_training,
            "historical_benchmarks": historical,
        }

    def _run_iteration(self, iteration: int) -> Dict[str, Any]:
        self._update_status(phase="collecting_bridge_data", current_iteration=iteration)
        _log("collecting live bridge data")
        trainer = FullSourceTrainer(repo_root=self.repo_root, attempt_live_fetch=True)
        bridge_data = trainer._collect_all_bridge_data()
        self._update_status(phase="building_features", current_iteration=iteration)
        _log("building training features")
        fred = trainer._build_fred_training_features(bridge_data)
        sentiment = trainer._build_sentiment_training_features(bridge_data)
        geo = trainer._build_geopolitical_training_features(bridge_data)
        options = trainer._build_options_flow_training_features(bridge_data)
        political = trainer._build_political_disclosure_features(bridge_data)
        physical = trainer._build_physical_flow_training_features(bridge_data)
        training_set = trainer._build_regime_training_set(
            fred,
            sentiment,
            geo,
            options,
            political,
            physical,
        )
        self._update_status(phase="training_models", current_iteration=iteration)
        _log("training anomaly detector and regime scorer")
        trainer._train_anomaly_detector(training_set)
        trainer._calibrate_regime_scorer(training_set)

        bridge_context = {
            row["name"]: row["value"]
            for row in training_set.get("all_features", [])
            if isinstance(row.get("value"), (int, float))
        }
        chokepoint_scores = compute_chokepoint_risk_score(bridge_data)
        request = self._build_request(
            iteration=iteration,
            bridge_context=bridge_context,
            chokepoint_scores=chokepoint_scores,
        )

        self._update_status(phase="running_comparison", current_iteration=iteration)
        _log("running quantum/classical comparison")
        orchestrator = MultiBackendOrchestrator(artifact_dir=self.comparison_dir)
        comparison = orchestrator.run_comparison(request, mode=self.mode)
        ExperimentTracker(self.repo_root).log_result(comparison)

        selection_summary = self._selection_summary(comparison)
        self.selection_counter.update(selection_summary.get("selection_counts", {}))
        market_snapshot = self._market_history_snapshot()
        comparison_metrics = compute_multi_backend_metrics(
            load_multi_backend_comparisons(self.comparison_dir)
        )

        retraining_result: Optional[Dict[str, Any]] = None
        if iteration % self.retrain_every == 0:
            self._update_status(phase="retraining", current_iteration=iteration)
            _log("running bounded retraining pass")
            try:
                retraining_result = QuantumRetrainingJob(str(self.repo_root)).run()
            except Exception as retrain_err:
                _log(f"retraining failed (non-fatal): {retrain_err}")
                retraining_result = {"error": str(retrain_err)}

        iteration_report = {
            "iteration": iteration,
            "timestamp_utc": _iso_now(),
            "bridge_status": trainer.results.get("bridges_trained", {}),
            "features_extracted": trainer.results.get("features_extracted"),
            "chokepoint_scores": chokepoint_scores,
            "request_summary": {
                "request_id": request["request_id"],
                "candidate_count": len(request["candidates"]),
                "scenario_match": request["regime_state"].get("scenario_match"),
                "themes": sorted({c.get("theme") for c in request["candidates"]}),
            },
            "selection_summary": selection_summary,
            "comparison": {
                "backends_succeeded": comparison.get("backends_succeeded", []),
                "backends_failed": comparison.get("backends_failed", []),
                "backends_unavailable": comparison.get("backends_unavailable", []),
                "objective_values": (comparison.get("comparison") or {}).get("objective_values", {}),
                "best_objective_backend": (comparison.get("comparison") or {}).get("best_objective_backend"),
                "quantum_vs_strong_classical_delta": (comparison.get("comparison") or {}).get(
                    "quantum_vs_strong_classical_delta"
                ),
            },
            "aggregate_comparison_metrics": comparison_metrics,
            "market_history_snapshot": market_snapshot,
            "extra_training_scenarios": EXTRA_TRAINING_SCENARIOS,
            "retraining_result": retraining_result,
            "trainer_errors": trainer.results.get("errors", []),
        }

        name = "iteration_%02d_%s.json" % (
            iteration,
            datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S"),
        )
        self._write_report(name, iteration_report)
        self._write_report("iteration_latest.json", iteration_report)
        self._write_consensus(request, selection_summary, iteration_report)
        advisory = self._write_live_advisory(request, selection_summary, iteration_report)
        self._write_live_briefing(advisory)
        return iteration_report

    def _build_request(
        self,
        *,
        iteration: int,
        bridge_context: Mapping[str, float],
        chokepoint_scores: Mapping[str, Any],
    ) -> Dict[str, Any]:
        playbook = chokepoint_scores.get("recommended_playbook") or {}
        aligned_longs = {
            str(row.get("symbol", "")).upper()
            for row in playbook.get("immediate_longs", [])
            if row.get("symbol")
        }
        aligned_shorts = {
            str(row.get("symbol", "")).upper()
            for row in playbook.get("immediate_shorts", [])
            if row.get("symbol")
        }

        candidates = _dedupe_symbols(BASE_CANDIDATES + playbook.get("immediate_longs", []) + playbook.get("immediate_shorts", []))
        composite = _safe_float(chokepoint_scores.get("composite"))
        active = int(_safe_float(chokepoint_scores.get("active_chokepoints")))
        scenario_match = chokepoint_scores.get("scenario_match") or "monitoring_only"

        enriched: List[Dict[str, Any]] = []
        for row in candidates:
            symbol = str(row.get("symbol", "")).upper()
            direction = str(row.get("direction", "long")).lower()
            aligned = (direction == "long" and symbol in aligned_longs) or (
                direction == "short" and symbol in aligned_shorts
            )
            volatility = _safe_float(row.get("volatility"), 0.25)
            expected_return = _candidate_expected_return(
                row,
                composite=composite,
                active_chokepoints=active,
                aligned=aligned,
            )
            enriched.append(
                {
                    "symbol": symbol,
                    "score": _candidate_score(expected_return, volatility, aligned),
                    "expected_return": expected_return,
                    "volatility": volatility,
                    "sector": row.get("sector", "unknown"),
                    "theme": row.get("theme", "unknown"),
                    "direction": direction,
                    "aligned_with_playbook": aligned,
                    "scenario_match": scenario_match,
                    "bridge_context": dict(bridge_context),
                    "metadata": {
                        "research_only": True,
                        "not_for_direct_execution": True,
                        "extra_training_scenarios": EXTRA_TRAINING_SCENARIOS,
                    },
                }
            )

        return {
            "request_id": "iran-disruption-%02d-%d" % (iteration, int(time.time())),
            "package_id": "iran_disruption_overnight",
            "objective": {"type": "scenario_allocation"},
            "constraints": {"budget": min(len(enriched), 12), "max_sector_pct": 0.25},
            "config": {"risk_factor": 0.65, "max_candidates": 12},
            "regime_state": {
                "macro_state": "geopolitical_energy_shock",
                "geopolitical_state": "iran_chokepoint_monitoring",
                "scenario_match": scenario_match,
                "chokepoint_scores": dict(chokepoint_scores),
            },
            "candidates": enriched,
        }

    def _selection_summary(self, comparison: Mapping[str, Any]) -> Dict[str, Any]:
        counts: Counter[str] = Counter()
        backend_top_picks: Dict[str, List[str]] = {}
        for backend, payload in (comparison.get("results") or {}).items():
            if not isinstance(payload, Mapping):
                continue
            picks = [str(sym).upper() for sym in (payload.get("selected_candidates") or []) if sym]
            if picks:
                backend_top_picks[backend] = picks
                counts.update(picks)
        return {
            "backend_top_picks": backend_top_picks,
            "selection_counts": dict(counts),
            "most_selected": counts.most_common(10),
        }

    def _historical_benchmarks(self) -> Dict[str, Any]:
        summary: Dict[str, Any] = {}
        try:
            backtest = HISTORICAL_BACKTEST_MODULE.HistoricalBacktest(self.repo_root)
            for scenario_key in (
                "ukraine_invasion_2022",
                "covid_march_2020",
                "tariff_shock_2025",
                "svb_collapse_2023",
            ):
                result = backtest.run_scenario(scenario_key)
                timeline = result.get("timeline", []) or []
                transitions = [row for row in timeline if row.get("transition")]
                summary[scenario_key] = {
                    "scenario_name": HISTORICAL_BACKTEST_MODULE.SCENARIOS[scenario_key]["name"],
                    "timeline_length": len(timeline),
                    "first_transition": transitions[0] if transitions else None,
                    "final_mode": timeline[-1]["mode"] if timeline else None,
                    "peak_regime_probability": max((_safe_float(row.get("regime_p")) for row in timeline), default=0.0),
                }
        except Exception as exc:
            summary["error"] = str(exc)
        return summary

    def _market_history_snapshot(self) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        period2 = int(now.timestamp())
        period1 = int((now - timedelta(days=120)).timestamp())
        snapshot: Dict[str, Any] = {"as_of_utc": _iso_now(), "symbols": {}}

        for symbol in MARKET_BENCHMARK_SYMBOLS:
            try:
                data = HISTORICAL_BACKTEST_MODULE.fetch_yahoo_history(symbol, period1, period2)
                closes = (
                    data["chart"]["result"][0]["indicators"]["quote"][0].get("close", [])
                    if data and data.get("chart", {}).get("result")
                    else []
                )
                closes = [float(value) for value in closes if value is not None]
                if len(closes) < 61:
                    snapshot["symbols"][symbol] = {"status": "insufficient_history"}
                    continue
                snapshot["symbols"][symbol] = {
                    "last_close": round(closes[-1], 4),
                    "return_5d_pct": self._window_return(closes, 5),
                    "return_20d_pct": self._window_return(closes, 20),
                    "return_60d_pct": self._window_return(closes, 60),
                }
            except Exception as exc:
                snapshot["symbols"][symbol] = {"status": "error", "reason": str(exc)}
        return snapshot

    def _window_return(self, closes: List[float], window: int) -> float:
        if len(closes) <= window or closes[-window - 1] == 0:
            return 0.0
        start = closes[-window - 1]
        end = closes[-1]
        return round((end / start - 1.0) * 100.0, 4)

    def _write_report(self, name: str, payload: Mapping[str, Any]) -> None:
        path = self.output_dir / name
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    def _write_consensus(
        self,
        request: Mapping[str, Any],
        selection_summary: Mapping[str, Any],
        iteration_report: Mapping[str, Any],
    ) -> None:
        candidates = {
            str(row.get("symbol", "")).upper(): row
            for row in request.get("candidates", [])
            if row.get("symbol")
        }
        long_rows: List[Dict[str, Any]] = []
        short_rows: List[Dict[str, Any]] = []
        for symbol, count in (selection_summary.get("selection_counts") or {}).items():
            row = candidates.get(str(symbol).upper())
            if not row:
                continue
            summary_row = {
                "symbol": str(symbol).upper(),
                "count": count,
                "direction": row.get("direction"),
                "theme": row.get("theme"),
                "sector": row.get("sector"),
                "expected_return": row.get("expected_return"),
                "score": row.get("score"),
            }
            if row.get("direction") == "short":
                short_rows.append(summary_row)
            else:
                long_rows.append(summary_row)

        long_rows.sort(key=lambda item: (-_safe_float(item.get("count")), -_safe_float(item.get("score"))))
        short_rows.sort(key=lambda item: (-_safe_float(item.get("count")), -_safe_float(item.get("score"))))

        payload = {
            "schema_version": "iran_disruption_quantum_consensus.v1",
            "timestamp_utc": iteration_report.get("timestamp_utc"),
            "iteration": iteration_report.get("iteration"),
            "scenario_match": (iteration_report.get("request_summary") or {}).get("scenario_match"),
            "long_consensus": long_rows,
            "short_consensus": short_rows,
            "backend_top_picks": selection_summary.get("backend_top_picks", {}),
        }
        self._write_report("current_basket_consensus.json", payload)

    def _write_live_advisory(
        self,
        request: Mapping[str, Any],
        selection_summary: Mapping[str, Any],
        iteration_report: Mapping[str, Any],
    ) -> Dict[str, Any]:
        candidates = {
            str(row.get("symbol", "")).upper(): row
            for row in request.get("candidates", [])
            if row.get("symbol")
        }
        counts = dict(selection_summary.get("selection_counts") or {})
        snapshot = (iteration_report.get("market_history_snapshot") or {}).get("symbols", {})
        max_count = max((_safe_float(value) for value in counts.values()), default=1.0)

        def _basket_rows(symbols: List[str]) -> List[Dict[str, Any]]:
            rows: List[Dict[str, Any]] = []
            for symbol in symbols:
                row = candidates.get(symbol)
                if not row:
                    continue
                rows.append(
                    {
                        "symbol": symbol,
                        "direction": row.get("direction"),
                        "theme": row.get("theme"),
                        "sector": row.get("sector"),
                        "selection_count": counts.get(symbol, 0),
                        "expected_return": row.get("expected_return"),
                        "score": row.get("score"),
                        "market_snapshot": snapshot.get(symbol),
                    }
                )
            return rows

        uso_20d = _safe_float(((snapshot.get("USO") or {}).get("return_20d_pct")))
        chokepoints = iteration_report.get("chokepoint_scores") or {}
        composite = _safe_float(chokepoints.get("composite"))
        bab_el_mandeb = _safe_float(chokepoints.get("bab_el_mandeb"))
        aviation_fresh = bool((((iteration_report.get("bridge_status") or {}).get("aviation_bridge")) or {}).get("fresh"))

        triggers = [
            {
                "name": "de_emphasize_direct_oil",
                "condition": "USO 20d > 25 and composite chokepoint < 0.10",
                "currently_true": uso_20d > 25.0 and composite < 0.10,
            },
            {
                "name": "promote_shipping_reroute",
                "condition": "bab_el_mandeb >= 0.20 or composite chokepoint >= 0.12",
                "currently_true": bab_el_mandeb >= 0.20 or composite >= 0.12,
            },
            {
                "name": "promote_airline_short",
                "condition": "aviation bridge fresh and JETS 20d <= -10",
                "currently_true": aviation_fresh and _safe_float(((snapshot.get("JETS") or {}).get("return_20d_pct"))) <= -10.0,
            },
            {
                "name": "promote_safe_haven_core",
                "condition": "GLD 60d >= 15 and SPY 20d <= 0",
                "currently_true": _safe_float(((snapshot.get("GLD") or {}).get("return_60d_pct"))) >= 15.0
                and _safe_float(((snapshot.get("SPY") or {}).get("return_20d_pct"))) <= 0.0,
            },
        ]
        trigger_map = {row["name"]: bool(row.get("currently_true")) for row in triggers}

        def _market_support(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
            signals: List[float] = []
            for row in rows:
                market_row = row.get("market_snapshot") or {}
                if "last_close" not in market_row:
                    continue
                ret_20 = _safe_float(market_row.get("return_20d_pct"))
                ret_60 = _safe_float(market_row.get("return_60d_pct"))
                signal = ret_20 + (0.35 * ret_60)
                if row.get("direction") == "short":
                    signal *= -1.0
                signals.append(signal)
            if not signals:
                return {"sample_size": 0, "score": 0.0}
            score = sum(signals) / len(signals)
            return {"sample_size": len(signals), "score": round(score, 4)}

        def _trigger_bonus(key: str) -> Dict[str, Any]:
            names: List[str] = []
            bonus = 0.0
            if key == "consensus_core_cross_asset":
                if trigger_map.get("promote_safe_haven_core"):
                    names.append("promote_safe_haven_core")
                    bonus += 1.3
            elif key == "refiners_lng_vs_airlines":
                if trigger_map.get("de_emphasize_direct_oil"):
                    names.append("de_emphasize_direct_oil")
                    bonus += 0.7
                if trigger_map.get("promote_airline_short"):
                    names.append("promote_airline_short")
                    bonus += 1.1
            elif key == "shipping_reroute_stress":
                if trigger_map.get("promote_shipping_reroute"):
                    names.append("promote_shipping_reroute")
                    bonus += 1.2
            elif key == "spillover_cyber_food_finance":
                if composite < 0.12:
                    names.append("second_order_spillovers_preferred")
                    bonus += 0.8
            elif key == "hedge_overlay":
                if trigger_map.get("promote_safe_haven_core"):
                    names.append("promote_safe_haven_core")
                    bonus += 0.5
            return {"active_triggers": names, "bonus": round(bonus, 4)}

        basket_payloads: List[Dict[str, Any]] = []
        weight_basis: List[float] = []
        for definition in BASKET_DEFINITIONS:
            rows = _basket_rows(definition["symbols"])
            selected = [row for row in rows if _safe_float(row.get("selection_count")) > 0.0]
            coverage = (len(selected) / len(rows)) if rows else 0.0
            intensity = (
                sum(_safe_float(row.get("selection_count")) / max_count for row in selected) / len(selected)
                if selected
                else 0.0
            )
            selection_strength = round((0.55 * coverage) + (0.45 * intensity), 4)
            market_support = _market_support(rows)
            market_bonus = max(min(market_support["score"] / 18.0, 1.4), -1.0)
            trigger_bonus = _trigger_bonus(definition["key"])
            priority = round(
                (definition["base_weight_pct"] / 10.0)
                + (selection_strength * 3.5)
                + market_bonus
                + _safe_float(trigger_bonus["bonus"]),
                4,
            )
            weight_basis.append(max(priority, 0.25))
            basket_payloads.append(
                {
                    "name": definition["key"],
                    "base_weight_pct": definition["base_weight_pct"],
                    "rationale": definition["rationale"],
                    "options_expression": definition["options_expression"],
                    "instruments": rows,
                    "selection_strength": {
                        "coverage_ratio": round(coverage, 4),
                        "intensity_ratio": round(intensity, 4),
                        "selected_symbols": len(selected),
                        "candidate_symbols": len(rows),
                    },
                    "market_support": market_support,
                    "active_triggers": trigger_bonus["active_triggers"],
                    "priority_score": priority,
                }
            )

        total_weight_basis = sum(weight_basis) or 1.0
        assigned_weights = [int(round((raw / total_weight_basis) * 100)) for raw in weight_basis]
        delta = 100 - sum(assigned_weights)
        if assigned_weights:
            anchor = max(range(len(assigned_weights)), key=lambda idx: weight_basis[idx])
            assigned_weights[anchor] = max(0, assigned_weights[anchor] + delta)

        for basket, assigned_weight in zip(basket_payloads, assigned_weights):
            basket["weight_pct"] = assigned_weight

        basket_payloads.sort(
            key=lambda item: (-_safe_float(item.get("priority_score")), -_safe_float(item.get("weight_pct")))
        )
        for index, basket in enumerate(basket_payloads, start=1):
            basket["rank"] = index

        best_backend = (
            (iteration_report.get("comparison") or {}).get("best_objective_backend")
            or ((iteration_report.get("aggregate_comparison_metrics") or {}).get("best_backend"))
            or "classical_greedy"
        )
        quantum_delta = _safe_float(
            ((iteration_report.get("comparison") or {}).get("quantum_vs_strong_classical_delta"))
        )
        direct_oil_emphasis = "reduced" if trigger_map.get("de_emphasize_direct_oil") else "balanced"
        if composite >= 0.16:
            direct_oil_emphasis = "elevated"

        payload = {
            "schema_version": "iran_disruption_live_advisory.v1",
            "timestamp_utc": iteration_report.get("timestamp_utc"),
            "iteration": iteration_report.get("iteration"),
            "title": "Current volatility advisory baskets",
            "advisory_only": True,
            "not_for_direct_execution": True,
            "bounded_secondary_signal_only": True,
            "execution_influence_forbidden": True,
            "scenario_match": (iteration_report.get("request_summary") or {}).get("scenario_match"),
            "chokepoint_scores": chokepoints,
            "aggregate_comparison_metrics": iteration_report.get("aggregate_comparison_metrics"),
            "allocator_guidance": {
                "primary_allocator": best_backend,
                "quantum_confirmation_preferred": quantum_delta > 0.0,
                "quantum_vs_strong_classical_delta": quantum_delta,
                "direct_oil_emphasis": direct_oil_emphasis,
            },
            "baskets": basket_payloads,
            "triggers": triggers,
            "notes": [
                "Prefer relative-value expressions over oversized naked crude.",
                "Promote shipping and airline stress when maritime/aviation bridges stay fresh.",
                "Quantum lane remains research-only; advisory artifacts must not auto-route to execution.",
            ],
        }
        self._write_report("live_advisory.json", payload)
        return payload

    def _write_live_briefing(self, advisory: Mapping[str, Any]) -> None:
        lines = [
            f"# Live Volatility Advisory - {advisory.get('timestamp_utc', 'unknown')}",
            "",
            "Research only. This file does not authorize or route execution.",
            "",
            "## Regime",
            "",
            f"- Scenario match: `{advisory.get('scenario_match') or 'monitoring_only'}`",
            f"- Primary allocator: `{((advisory.get('allocator_guidance') or {}).get('primary_allocator') or 'classical_greedy')}`",
            f"- Direct oil emphasis: `{((advisory.get('allocator_guidance') or {}).get('direct_oil_emphasis') or 'balanced')}`",
            f"- Quantum confirmation preferred: `{bool((advisory.get('allocator_guidance') or {}).get('quantum_confirmation_preferred'))}`",
            "",
            "## Active Triggers",
            "",
        ]

        for trigger in advisory.get("triggers", []):
            state = "on" if trigger.get("currently_true") else "off"
            lines.append(f"- `{trigger.get('name')}`: {state} ({trigger.get('condition')})")

        lines.extend(["", "## Ranked Baskets", ""])
        for basket in advisory.get("baskets", []):
            selection_strength = basket.get("selection_strength") or {}
            market_support = basket.get("market_support") or {}
            lines.append(
                f"### {basket.get('rank')}. {basket.get('name')} - {basket.get('weight_pct')}% "
                f"(priority {basket.get('priority_score')})"
            )
            lines.append("")
            lines.append(f"- Rationale: {basket.get('rationale')}")
            lines.append(
                "- Selection strength: coverage "
                f"{selection_strength.get('coverage_ratio')} / intensity {selection_strength.get('intensity_ratio')}"
            )
            lines.append(
                f"- Market support: {market_support.get('score')} across {market_support.get('sample_size')} symbols"
            )
            if basket.get("active_triggers"):
                lines.append(f"- Active triggers: {', '.join(str(item) for item in basket.get('active_triggers', []))}")
            lines.append(f"- Options expression: {basket.get('options_expression')}")
            instruments = basket.get("instruments") or []
            if instruments:
                symbol_summary = ", ".join(
                    f"{row.get('symbol')}({row.get('direction')}, c={row.get('selection_count')})"
                    for row in instruments
                )
                lines.append(f"- Instruments: {symbol_summary}")
            lines.append("")

        (self.output_dir / "live_advisory.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    def _update_status(self, phase: str, **fields: Any) -> None:
        payload: Dict[str, Any] = {
            "schema_version": "iran_disruption_quantum_status.v1",
            "timestamp_utc": _iso_now(),
            "phase": phase,
            "pid": os.getpid(),
            "mode": self.mode,
            "iterations_requested": self.iterations,
            "sleep_seconds": self.sleep_seconds,
        }
        payload.update(fields)
        self.status_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--iterations", type=int, default=60, help="0 or less runs continuously")
    parser.add_argument("--sleep", type=int, default=900, dest="sleep_seconds")
    parser.add_argument("--mode", choices=("quick", "full"), default="full")
    parser.add_argument("--retrain-every", type=int, default=6)
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    runner = IranDisruptionQuantumOvernight(
        repo_root=args.repo_root,
        iterations=args.iterations,
        sleep_seconds=args.sleep_seconds,
        mode=args.mode,
        retrain_every=args.retrain_every,
    )
    try:
        result = runner.run()
        print(json.dumps(result, indent=2, default=str))
    except Exception as exc:
        runner._update_status(phase="error", error=str(exc))
        raise


if __name__ == "__main__":
    main()
