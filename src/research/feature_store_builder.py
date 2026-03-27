#!/usr/bin/env python3
"""Global Sentinel Feature Store Builder.

Core data spine that:
- Ingests packets from all bridges (fed, fred, eia, gdelt, whitehouse, sec, cftc, noaa, maritime, sentiment, etc.)
- Scores by trust tier using weights from config/data_trust_hierarchy.yaml
- Deduplicates by packet_id
- Merges into a canonical research snapshot
- Feeds QuantumOptimizationRequest packets
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bridge registry: name -> (module_path, class_name)
# ---------------------------------------------------------------------------
_BRIDGE_REGISTRY: List[Dict[str, str]] = [
    {"name": "fed",        "module": "src.bridges.fed_board_bridge",        "class": "FedBoardBridge"},
    {"name": "fred",       "module": "src.bridges.fred_bridge",             "class": "FREDBridge"},
    {"name": "eia",        "module": "src.bridges.eia_bridge",              "class": "EIABridge"},
    {"name": "gdelt",      "module": "src.bridges.gdelt_bridge",           "class": "GDELTBridge"},
    {"name": "whitehouse", "module": "src.bridges.whitehouse_policy_bridge","class": "WhiteHousePolicyBridge"},
    {"name": "bls",        "module": "src.bridges.bls_release_bridge",     "class": "BLSReleaseBridge"},
    {"name": "treasury",   "module": "src.bridges.treasury_ofac_bridge",   "class": "TreasuryOFACBridge"},
    {"name": "finnhub",    "module": "src.bridges.finnhub_bridge",         "class": "FinnhubBridge"},
    # DISABLED 2026-03-21: zero trade value &  {"name": "aviation",   "module": "src.bridges.aviation_disruption_bridge", "class": "AviationDisruptionBridge"},
    {"name": "exa",        "module": "src.bridges.exa_search_bridge",      "class": "ExaSearchBridge"},
    {"name": "narrative",  "module": "src.bridges.narrative_velocity_bridge","class": "NarrativeVelocityBridge"},
    {"name": "market_microstructure", "module": "src.bridges.market_microstructure_bridge", "class": "MarketMicrostructureBridge"},
    {"name": "options",    "module": "src.bridges.options_greeks_bridge",   "class": "OptionsGreeksBridge"},
    {"name": "politician", "module": "src.bridges.politician_alpha_bridge", "class": "PoliticianAlphaBridge"},
    # DISABLED 2026-03-21: zero trade value & {"name": "gcp",        "module": "src.bridges.gcp_consciousness_bridge","class": "GCPConsciousnessBridge"},
    {"name": "maritime_v2","module": "src.bridges.maritime_bridge_v2",       "class": "MaritimeBridgeV2"},
    {"name": "cds_sovereign","module":"src.bridges.cds_sovereign_bridge",    "class": "CDSSovereignBridge"},
    {"name": "gpr_index",  "module": "src.bridges.gpr_index_bridge",        "class": "GPRIndexBridge"},
    {"name": "semiconductor","module":"src.bridges.semiconductor_supply_bridge","class":"SemiconductorSupplyBridge"},
]


def _load_yaml(path: Path) -> Dict[str, Any]:
    """Load YAML config; falls back to empty dict if unavailable."""
    try:
        import yaml  # type: ignore[import-untyped]
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        # Fallback: parse simple YAML subset via json if pyyaml missing
        logger.warning("PyYAML not installed; attempting JSON fallback for %s", path)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    except FileNotFoundError:
        logger.warning("Config not found: %s", path)
        return {}
    except Exception as exc:
        logger.error("Failed to load %s: %s", path, exc)
        return {}


class FeatureStoreBuilder:
    """Ingests, deduplicates, trust-weights, and snapshots bridge data."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self._packets: Dict[str, Dict[str, Any]] = {}  # packet_id -> packet dict
        self._trust_config = self._load_trust_config()
        self._source_freshness: Dict[str, str] = {}  # source -> latest timestamp_utc
        self._bridge_errors: Dict[str, str] = {}  # bridge_name -> error message

    # ------------------------------------------------------------------
    # Trust config
    # ------------------------------------------------------------------

    def _load_trust_config(self) -> Dict[str, Any]:
        """Load config/data_trust_hierarchy.yaml."""
        return _load_yaml(self.repo_root / "config" / "data_trust_hierarchy.yaml")

    def _trust_weight_for_source(self, source: str) -> float:
        """Look up trust weight for a source name."""
        tiers = self._trust_config.get("tiers", {})
        for tier_cfg in tiers.values():
            sources = tier_cfg.get("sources", [])
            if source in sources:
                return float(tier_cfg.get("weight", 0.5))
        # Unknown source gets a conservative default
        return 0.3

    # ------------------------------------------------------------------
    # Bridge instantiation and polling
    # ------------------------------------------------------------------

    @staticmethod
    def _import_bridge(module_path: str, class_name: str) -> Optional[type]:
        """Dynamically import a bridge class."""
        import importlib
        try:
            mod = importlib.import_module(module_path)
            return getattr(mod, class_name)
        except (ImportError, AttributeError) as exc:
            logger.debug("Could not import %s.%s: %s", module_path, class_name, exc)
            return None

    def _poll_bridge(self, entry: Dict[str, str]) -> List[Dict[str, Any]]:
        """Instantiate and poll a single bridge, returning packets."""
        name = entry["name"]
        bridge_cls = self._import_bridge(entry["module"], entry["class"])
        if bridge_cls is None:
            self._bridge_errors[name] = f"import_failed:{entry['module']}.{entry['class']}"
            return []

        try:
            bridge = bridge_cls(self.repo_root)
            raw = bridge.poll()
        except Exception as exc:
            self._bridge_errors[name] = str(exc)
            logger.error("Bridge %s poll failed: %s", name, exc, exc_info=True)
            return []

        # Normalize: poll() may return List[Dict], Dict (single/summary), or Dict[str, Dict]
        packets: List[Dict[str, Any]] = []
        if isinstance(raw, list):
            packets = raw
        elif isinstance(raw, dict):
            # Some bridges return a summary dict or keyed dict
            if "packet_id" in raw:
                packets = [raw]
            else:
                # Possibly a dict-of-dicts (market_microstructure, options, etc.)
                # Wrap as a single summary packet
                packets = [{"packet_type": f"{name}_summary", "packet_id": f"{name}_summary", "source": name, "data": raw}]
        return packets

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest_all(self) -> int:
        """Run all bridges and collect packets. Returns count ingested."""
        total = 0
        for entry in _BRIDGE_REGISTRY:
            name = entry["name"]
            try:
                packets = self._poll_bridge(entry)
            except Exception as exc:
                self._bridge_errors[name] = f"unexpected:{exc}"
                logger.error("Unexpected error polling bridge %s: %s", name, exc, exc_info=True)
                continue

            for pkt in packets:
                pid = pkt.get("packet_id")
                if not pid:
                    logger.debug("Dropping packet without packet_id from bridge %s", name)
                    continue

                # Deduplicate
                if pid in self._packets:
                    continue

                # Apply trust weight
                source = pkt.get("source", name)
                trust_w = self._trust_weight_for_source(source)
                pkt.setdefault("trust_weight", trust_w)

                # Adjust confidence by trust weight
                raw_conf = pkt.get("confidence", 1.0)
                pkt["weighted_confidence"] = raw_conf * trust_w

                self._packets[pid] = pkt
                total += 1

                # Track freshness
                ts = pkt.get("timestamp_utc", "")
                if ts and (source not in self._source_freshness or ts > self._source_freshness[source]):
                    self._source_freshness[source] = ts

            if name not in self._bridge_errors:
                logger.info("Bridge %s: ingested %d packets", name, len(packets))

        logger.info("Total ingested: %d packets from %d bridges (%d errors)",
                     total, len(_BRIDGE_REGISTRY), len(self._bridge_errors))
        return total

    def ingest_packets(self, packets: List[Dict[str, Any]]) -> int:
        """Ingest pre-fetched packets (for testing or external feeds)."""
        added = 0
        for pkt in packets:
            pid = pkt.get("packet_id")
            if not pid or pid in self._packets:
                continue
            source = pkt.get("source", "unknown")
            trust_w = self._trust_weight_for_source(source)
            pkt.setdefault("trust_weight", trust_w)
            pkt["weighted_confidence"] = pkt.get("confidence", 1.0) * trust_w
            self._packets[pid] = pkt
            added += 1
            ts = pkt.get("timestamp_utc", "")
            if ts and (source not in self._source_freshness or ts > self._source_freshness[source]):
                self._source_freshness[source] = ts
        return added

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def build_snapshot(self) -> Dict[str, Any]:
        """Build canonical research snapshot from collected packets."""
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for pkt in self._packets.values():
            ptype = pkt.get("packet_type", "unknown")
            grouped.setdefault(ptype, []).append(pkt)

        # Compute aggregate scores where applicable
        macro_packets = grouped.get("macro_policy_event", [])
        hawkish_scores = [p.get("hawkish_dovish_score", 0) for p in macro_packets if "hawkish_dovish_score" in p]
        growth_scores = [p.get("growth_inflation_score", 0) for p in macro_packets if "growth_inflation_score" in p]

        snapshot: Dict[str, Any] = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "packet_count": len(self._packets),
            "source_freshness": dict(self._source_freshness),
            "bridge_errors": dict(self._bridge_errors),
            "packets_by_type": {ptype: len(pkts) for ptype, pkts in grouped.items()},
            "aggregates": {
                "hawkish_dovish": {
                    "mean": sum(hawkish_scores) / len(hawkish_scores) if hawkish_scores else 0.0,
                    "count": len(hawkish_scores),
                },
                "growth_inflation": {
                    "mean": sum(growth_scores) / len(growth_scores) if growth_scores else 0.0,
                    "count": len(growth_scores),
                },
            },
            "grouped_packets": {ptype: pkts for ptype, pkts in grouped.items()},
        }

        # V4: Annotate snapshot with feature freshness compliance
        try:
            from src.core.feature_freshness_enforcer import FeatureFreshnessEnforcer
            ffe = FeatureFreshnessEnforcer(config_dir=self.repo_root / "config")
            if ffe.is_loaded:
                now = datetime.now(timezone.utc)
                feature_timestamps: Dict[str, Optional[datetime]] = {}
                for feat_name in self._source_freshness:
                    try:
                        ts_str = self._source_freshness[feat_name]
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        feature_timestamps[feat_name] = ts
                    except (ValueError, AttributeError):
                        feature_timestamps[feat_name] = None
                snapshot["_freshness_compliance"] = ffe.summary(feature_timestamps, now)
        except Exception as e:
            logger.debug("Freshness enforcement unavailable: %s", e)

        return snapshot

    def build_quantum_request(
        self,
        candidate_universe: List[Dict[str, Any]],
        constraints: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build a QuantumOptimizationRequest from current snapshot."""
        from src.research.quantum_optimization_request_builder import build_request

        snapshot = self.build_snapshot()
        return build_request(snapshot, candidate_universe, constraints=constraints)

    def save_snapshot(self, output_dir: Path) -> Path:
        """Persist snapshot to JSON."""
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = output_dir / f"feature_snapshot_{ts}.json"
        snapshot = self.build_snapshot()
        out_path.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
        logger.info("Snapshot saved: %s", out_path)
        return out_path

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def packet_count(self) -> int:
        return len(self._packets)

    @property
    def source_freshness(self) -> Dict[str, str]:
        return dict(self._source_freshness)

    @property
    def bridge_errors(self) -> Dict[str, str]:
        return dict(self._bridge_errors)
