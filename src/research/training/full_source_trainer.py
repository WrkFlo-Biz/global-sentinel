#!/usr/bin/env python3
"""Full-source training session for Global Sentinel research.

This trainer builds a cached/live research snapshot from the full source fleet,
derives training features, retrains the anomaly detector, seeds the quantum
comparison lane, and writes artifact-only reports for later review.
"""

from __future__ import annotations

import glob
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


from src.research.training.crisis_training_dataset import CRISIS_EVENTS  # noqa: E402
from src.bridges.bridge_registry import BridgeRegistry  # noqa: E402


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _flatten_numeric(prefix: str, value: Any) -> List[Dict[str, Any]]:
    """Recursively flatten numeric values into feature rows."""
    rows: List[Dict[str, Any]] = []
    if isinstance(value, bool):
        rows.append({"name": prefix, "value": 1.0 if value else 0.0})
    elif isinstance(value, (int, float)):
        rows.append({"name": prefix, "value": float(value)})
    elif isinstance(value, dict):
        for key, subvalue in value.items():
            rows.extend(_flatten_numeric(f"{prefix}_{key}", subvalue))
    elif isinstance(value, list):
        numeric_items = [item for item in value if isinstance(item, (int, float)) and not isinstance(item, bool)]
        if numeric_items:
            rows.append({"name": f"{prefix}_count", "value": float(len(value))})
            rows.append({"name": f"{prefix}_mean", "value": float(sum(numeric_items) / len(numeric_items))})
            rows.append({"name": f"{prefix}_max", "value": float(max(numeric_items))})
        elif value:
            rows.append({"name": f"{prefix}_count", "value": float(len(value))})
    return rows


class FullSourceTrainer:
    """Build a comprehensive offline training set from all data sources."""

    def __init__(
        self,
        repo_root: str | Path = REPO_ROOT,
        *,
        attempt_live_fetch: bool = True,
    ) -> None:
        self.repo_root = Path(repo_root)
        self.attempt_live_fetch = attempt_live_fetch
        self.results: Dict[str, Any] = {
            "schema_version": "full_source_training_report.v1",
            "session_id": str(int(time.time())),
            "started_at": _utc_now(),
            "bridges_trained": {},
            "features_extracted": 0,
            "training_sets_built": {},
            "errors": [],
            "execution_metadata": {
                "not_for_direct_execution": True,
                "quantum_direct_execution_forbidden": True,
                "bounded_secondary_signal_only": True,
                "script": "full_source_trainer",
            },
        }

    def run_full_training(self) -> Dict[str, Any]:
        """Master training orchestrator."""
        print("=" * 60)
        print("GLOBAL SENTINEL — FULL SOURCE TRAINING SESSION")
        print("=" * 60)

        bridge_data = self._collect_all_bridge_data()
        fred_features = self._build_fred_training_features(bridge_data)
        sentiment_features = self._build_sentiment_training_features(bridge_data)
        geo_features = self._build_geopolitical_training_features(bridge_data)
        options_features = self._build_options_flow_training_features(bridge_data)
        political_features = self._build_political_disclosure_features(bridge_data)
        physical_features = self._build_physical_flow_training_features(bridge_data)
        regime_training_set = self._build_regime_training_set(
            fred_features,
            sentiment_features,
            geo_features,
            options_features,
            political_features,
            physical_features,
        )

        self._train_anomaly_detector(regime_training_set)
        self._calibrate_regime_scorer(regime_training_set)
        self._seed_quantum_backends(regime_training_set)

        self.results["completed_at"] = _utc_now()
        report_path = self.repo_root / "reports" / "research" / "full_source_training_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(self.results, indent=2, default=str), encoding="utf-8")
        print(f"\nFull training report: {report_path}")
        return self.results

    def _collect_all_bridge_data(self) -> Dict[str, Any]:
        """Collect from every source, preferring live fetch but falling back to cache."""
        print("\n--- Track A: Collecting All Bridge Data ---")
        collected: Dict[str, Any] = {}
        registry = BridgeRegistry(repo_root=self.repo_root)
        live_results = registry.fetch_all() if self.attempt_live_fetch else {}

        for source_name, spec in sorted(registry.specs().items()):
            live_payload = live_results.get(source_name, {})
            fetched = live_payload.get("data") if isinstance(live_payload, dict) else None
            status = "no_data"
            if fetched is not None:
                status = "live_fetch"
            else:
                fetched = self._load_cached_source_data(spec.aliases)
                if fetched is not None:
                    status = "cached"

            if fetched is not None:
                collected[source_name] = fetched

            self.results["bridges_trained"][source_name] = {
                "status": status,
                "record_count": self._record_count(fetched) if fetched is not None else 0,
                "source_tier": spec.default_source_tier,
                "trust_weight": spec.default_trust_weight,
                "fresh": bool(live_payload.get("fresh")) if isinstance(live_payload, dict) else False,
            }
            print(f"  [{source_name}] {status}")

        print(f"Sources with data: {len(collected)}/{len(registry.specs())}")
        return collected

    def _load_cached_source_data(self, aliases: Iterable[str]) -> Any:
        """Load recent cached data by searching scorecards and research artifacts."""
        alias_set = {alias for alias in aliases if alias}
        if not alias_set:
            return None

        patterns = [
            self.repo_root / "logs" / "scorecards" / "scorecard_*.json",
            self.repo_root / "reports" / "operational" / "*.json",
            self.repo_root / "reports" / "research" / "*.json",
            self.repo_root / "reports" / "research" / "**" / "*.json",
        ]

        seen: set[Path] = set()
        candidates: List[Path] = []
        for pattern in patterns:
            matches = sorted(Path(path) for path in glob.glob(str(pattern), recursive=True))
            for path in reversed(matches[-20:]):
                if path not in seen:
                    candidates.append(path)
                    seen.add(path)

        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            match = self._find_alias_match(payload, alias_set)
            if match is not None:
                return match
        return None

    def _find_alias_match(self, payload: Any, aliases: set[str]) -> Any:
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in aliases:
                    return value
                match = self._find_alias_match(value, aliases)
                if match is not None:
                    return match
        elif isinstance(payload, list):
            for item in payload:
                match = self._find_alias_match(item, aliases)
                if match is not None:
                    return match
        return None

    def _record_count(self, payload: Any) -> int:
        if isinstance(payload, dict):
            return len(payload)
        if isinstance(payload, list):
            return len(payload)
        return 1

    def _build_fred_training_features(self, bridge_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        print("\n--- Track B: FRED Macro Features ---")
        data = bridge_data.get("fred_bridge") or {}
        features = self._extract_named_numeric_features("fred", data, [
            "DFF", "T10Y2Y", "T10YIE", "BAMLH0A0HYM2", "DTWEXBGS", "UNRATE",
            "CPIAUCSL", "INDPRO", "UMCSENT", "VIXCLS", "DCOILWTICO",
            "GOLDAMGBD228NLBM", "ICSA", "MORTGAGE30US",
        ])
        if not features:
            features = [
                {"source": "fred", "name": row["name"], "value": row["value"]}
                for row in _flatten_numeric("fred_defaults", {
                    "fed_funds_rate": 4.5,
                    "yield_curve_10y2y": -0.3,
                    "high_yield_spread": 4.0,
                    "trade_weighted_usd": 105.0,
                    "unemployment": 4.1,
                    "vix": 25.0,
                    "wti_crude": 70.0,
                    "gold": 2900.0,
                })
            ]
        self.results["training_sets_built"]["fred_macro"] = len(features)
        print(f"  FRED features extracted: {len(features)}")
        return features

    def _build_sentiment_training_features(self, bridge_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        print("\n--- Track C: Sentiment Features ---")
        features: List[Dict[str, Any]] = []
        for source_name in ("sentiment_bridge", "exa_ai_bridge"):
            data = bridge_data.get(source_name) or {}
            source = "finnhub" if source_name == "sentiment_bridge" else "exa_ai"
            features.extend(self._generic_source_features(source, data, limit=40))
        self.results["training_sets_built"]["sentiment"] = len(features)
        print(f"  Sentiment features: {len(features)}")
        return features

    def _build_geopolitical_training_features(self, bridge_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        print("\n--- Track D: Geopolitical Features ---")
        features: List[Dict[str, Any]] = []
        for source_name, source in (
            ("gdelt_bridge", "gdelt"),
            ("policy_uncertainty_bridge", "policy_uncertainty"),
            ("whitehouse_policy_bridge", "white_house"),
            ("gpr_index_bridge", "gpr_index"),
        ):
            features.extend(self._generic_source_features(source, bridge_data.get(source_name) or {}, limit=30))
        self.results["training_sets_built"]["geopolitical"] = len(features)
        print(f"  Geopolitical features: {len(features)}")
        return features

    def _build_options_flow_training_features(self, bridge_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        print("\n--- Track E: Options Flow Features ---")
        data = bridge_data.get("options_greeks_bridge") or {}
        features = self._generic_source_features("options_greeks", data, limit=50)
        self.results["training_sets_built"]["options_flow"] = len(features)
        print(f"  Options flow features: {len(features)}")
        return features

    def _build_political_disclosure_features(self, bridge_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        print("\n--- Track F: Political Disclosure Features (RESEARCH ONLY) ---")
        data = bridge_data.get("political_disclosure_research_monitor") or {}
        features = self._generic_source_features("political_disclosure", data, limit=25)
        self.results["training_sets_built"]["political_disclosure"] = len(features)
        print(f"  Political disclosure features: {len(features)}")
        return features

    def _build_physical_flow_training_features(self, bridge_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        print("\n--- Track G: Physical Flow Features ---")
        features: List[Dict[str, Any]] = []
        for source_name, source in (
            ("eia_bridge", "eia"),
            ("cftc_bridge", "cftc"),
            ("noaa_bridge", "noaa"),
            ("maritime_bridge", "maritime"),
            ("aviation_bridge", "aviation"),
            ("sec_edgar_bridge", "sec_edgar"),
            ("bls_bridge", "bls"),
            ("fed_bridge", "fed"),
            ("treasury_ofac_bridge", "ofac"),
            ("sec_filing_event_scorer", "sec_filing"),
            ("market_microstructure_bridge", "market_microstructure"),
            ("semiconductor_supply_bridge", "semiconductor_supply"),
        ):
            features.extend(self._generic_source_features(source, bridge_data.get(source_name) or {}, limit=35))
        self.results["training_sets_built"]["physical_flow"] = len(features)
        print(f"  Physical-flow features: {len(features)}")
        return features

    def _extract_named_numeric_features(
        self,
        source: str,
        data: Any,
        keys: Iterable[str],
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            for key in keys:
                if isinstance(data.get(key), (int, float)):
                    rows.append({"source": source, "name": key, "value": float(data[key])})
        return rows

    def _generic_source_features(self, source: str, data: Any, *, limit: int = 30) -> List[Dict[str, Any]]:
        rows = [
            {"source": source, "name": row["name"], "value": row["value"]}
            for row in _flatten_numeric(source, data)
        ]
        return rows[:limit]

    def _build_regime_training_set(
        self,
        fred: List[Dict[str, Any]],
        sentiment: List[Dict[str, Any]],
        geo: List[Dict[str, Any]],
        options: List[Dict[str, Any]],
        political: List[Dict[str, Any]],
        physical: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        print("\n--- Track H: Building Combined Regime Training Set ---")
        all_features = fred + sentiment + geo + options + political + physical
        numeric_vector = [float(row["value"]) for row in all_features if isinstance(row.get("value"), (int, float))]
        feature_names = [f"{row['source']}_{row['name']}" for row in all_features if isinstance(row.get("value"), (int, float))]
        manifest = {
            "feature_count": len(numeric_vector),
            "feature_names": feature_names,
            "sources": sorted({row["source"] for row in all_features}),
            "timestamp_utc": _utc_now(),
        }
        manifest_path = self.repo_root / "config" / "training_feature_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        self.results["features_extracted"] = len(numeric_vector)
        self.results["training_sets_built"]["combined_regime_features"] = len(numeric_vector)
        print(f"  Total numeric features: {len(numeric_vector)}")
        print(f"  Feature manifest: {manifest_path}")
        return {
            "all_features": all_features,
            "numeric_vector": numeric_vector,
            "feature_names": feature_names,
            "manifest": manifest,
        }

    def _train_anomaly_detector(self, training_set: Dict[str, Any]) -> None:
        print("\n--- Track I: Training Anomaly Detector ---")
        numeric = training_set.get("numeric_vector", [])
        if not numeric:
            print("  No numeric features available")
            return
        try:
            from src.research.backends.pennylane_anomaly_detector import PennyLaneAnomalyDetector

            chunk_size = 16
            vectors: List[List[float]] = []
            for start in range(0, max(len(numeric) - chunk_size + 1, 1), max(chunk_size // 2, 1)):
                chunk = numeric[start:start + chunk_size]
                if len(chunk) < chunk_size:
                    chunk = chunk + [0.0] * (chunk_size - len(chunk))
                vectors.append(chunk[:chunk_size])
            vectors.append([float(value) for value in numeric[:chunk_size]] + [0.0] * max(0, chunk_size - len(numeric[:chunk_size])))

            flat = [value for vector in vectors for value in vector]
            min_v = min(flat)
            max_v = max(flat)
            span = max(max_v - min_v, 1.0)
            normalized = [[(value - min_v) / span for value in vector] for vector in vectors]

            detector = PennyLaneAnomalyDetector(
                {"n_qubits": 4, "n_layers": 2, "anomaly_threshold": 0.3}
            )
            train_result = detector.train(normalized, epochs=50, learning_rate=0.01)
            weights_path = self.repo_root / "config" / "anomaly_detector_weights.json"
            detector.save_weights(weights_path)
            print(f"  Weights saved: {weights_path}")
            self.results["training_sets_built"]["anomaly_detector"] = {
                "vectors": len(normalized),
                "epochs": 50,
                "weights_path": str(weights_path),
                "train_result": train_result,
            }
        except Exception as exc:
            self.results["errors"].append(f"Anomaly detector training: {exc}")
            print(f"  Error: {exc}")

    def _calibrate_regime_scorer(self, training_set: Dict[str, Any]) -> None:
        print("\n--- Track J: Regime Scorer Calibration ---")
        calibration_rows = [
            {
                "event": event["id"],
                "severity": event.get("severity", 0.0),
                "regime_signature_keys": sorted(list(event.get("regime_signature", {}).keys())),
            }
            for event in CRISIS_EVENTS[-10:]
        ]
        path = self.repo_root / "reports" / "research" / "regime_scorer_calibration.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(calibration_rows, indent=2), encoding="utf-8")
        self.results["training_sets_built"]["regime_calibration"] = len(calibration_rows)
        print(f"  Calibration report: {path}")

    def _seed_quantum_backends(self, training_set: Dict[str, Any]) -> None:
        print("\n--- Track K: Seeding Quantum Backends ---")
        try:
            from src.research.backends.multi_backend_orchestrator import MultiBackendOrchestrator
            from src.research.experiment_tracker import ExperimentTracker

            orchestrator = MultiBackendOrchestrator()
            available = orchestrator.available_backends()
            bridge_context = {
                row["name"]: row["value"]
                for row in training_set.get("all_features", [])
                if isinstance(row.get("value"), (int, float))
            }
            sample_candidates = [
                {"symbol": "XLE", "expected_return": 0.08, "volatility": 0.30, "sector": "energy", "bridge_context": bridge_context},
                {"symbol": "GLD", "expected_return": 0.05, "volatility": 0.15, "sector": "commodities", "bridge_context": bridge_context},
                {"symbol": "LMT", "expected_return": 0.06, "volatility": 0.20, "sector": "defense", "bridge_context": bridge_context},
                {"symbol": "TLT", "expected_return": 0.03, "volatility": 0.12, "sector": "bonds", "bridge_context": bridge_context},
                {"symbol": "UVXY", "expected_return": 0.10, "volatility": 0.80, "sector": "volatility", "bridge_context": bridge_context},
                {"symbol": "SPY", "expected_return": -0.02, "volatility": 0.25, "sector": "broad_market", "bridge_context": bridge_context},
            ]
            request = {
                "request_id": f"full-source-train-{int(time.time())}",
                "package_id": "full_source_training",
                "candidates": sample_candidates,
                "constraints": {"budget": 3, "max_sector_pct": 0.5},
                "regime_state": {"macro": "elevated", "geo": "conflict", "shift_probability": 0.49},
                "config": {"risk_factor": 0.5},
            }
            result = orchestrator.run_comparison(request, mode="full")
            ExperimentTracker(self.repo_root).log_result(result)
            self.results["training_sets_built"]["quantum_seeded"] = {
                "available_backends": available,
                "objective_values": result.get("comparison", {}).get("objective_values", {}),
            }
            print(f"  Available backends: {available}")
        except Exception as exc:
            self.results["errors"].append(f"Quantum seeding: {exc}")
            print(f"  Error: {exc}")


if __name__ == "__main__":
    trainer = FullSourceTrainer(repo_root=os.environ.get("GS_REPO_ROOT", REPO_ROOT))
    report = trainer.run_full_training()
    print("\n" + "=" * 60)
    print("TRAINING COMPLETE")
    print(f"Bridges: {len(report['bridges_trained'])}")
    print(f"Features: {report['features_extracted']}")
    print(f"Errors: {len(report['errors'])}")
    print(f"Report: {report.get('completed_at')}")
    print("=" * 60)
