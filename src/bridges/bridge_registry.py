#!/usr/bin/env python3
"""Central registry exposing a uniform bridge contract."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - production dependency
    yaml = None

from src.bridges.adapters import ExistingBridgeAdapter, SECFilingAdapter
from src.bridges.adapters.generic_adapter import ExistingBridgeSpec


@dataclass(frozen=True)
class BridgeRegistrySpec:
    name: str
    aliases: tuple[str, ...]
    module_path: Optional[str]
    class_name: Optional[str]
    preferred_methods: tuple[str, ...]
    default_source_tier: str
    default_trust_weight: float
    default_freshness_ttl_minutes: int
    adapter_kind: str = "existing"


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


class BridgeRegistry:
    """Load the full source fleet through a consistent wrapper contract."""

    DEFAULT_SPECS: Dict[str, BridgeRegistrySpec] = {
        "fed_bridge": BridgeRegistrySpec(
            name="fed_bridge",
            aliases=("fed_bridge", "fed_board_bridge", "fed"),
            module_path="src.bridges.fed_board_bridge",
            class_name="FedBoardBridge",
            preferred_methods=("poll",),
            default_source_tier="tier_1_official",
            default_trust_weight=1.0,
            default_freshness_ttl_minutes=60,
        ),
        "fred_bridge": BridgeRegistrySpec(
            name="fred_bridge",
            aliases=("fred_bridge", "fred"),
            module_path="src.bridges.fred_bridge",
            class_name="FREDBridge",
            preferred_methods=("poll",),
            default_source_tier="tier_1_official",
            default_trust_weight=1.0,
            default_freshness_ttl_minutes=1440,
        ),
        "bls_bridge": BridgeRegistrySpec(
            name="bls_bridge",
            aliases=("bls_bridge", "bls_release_bridge", "bls"),
            module_path="src.bridges.bls_release_bridge",
            class_name="BLSReleaseBridge",
            preferred_methods=("poll",),
            default_source_tier="tier_1_official",
            default_trust_weight=1.0,
            default_freshness_ttl_minutes=1440,
        ),
        "eia_bridge": BridgeRegistrySpec(
            name="eia_bridge",
            aliases=("eia_bridge", "eia"),
            module_path="src.bridges.eia_bridge",
            class_name="EIABridge",
            preferred_methods=("poll",),
            default_source_tier="tier_1_official",
            default_trust_weight=1.0,
            default_freshness_ttl_minutes=1440,
        ),
        "sec_edgar_bridge": BridgeRegistrySpec(
            name="sec_edgar_bridge",
            aliases=("sec_edgar_bridge", "sec_edgar", "sec"),
            module_path="src.ingestion.sec_edgar_bridge",
            class_name="SECEdgarBridge",
            preferred_methods=("fetch",),
            default_source_tier="tier_1_official",
            default_trust_weight=1.0,
            default_freshness_ttl_minutes=1440,
        ),
        "cftc_bridge": BridgeRegistrySpec(
            name="cftc_bridge",
            aliases=("cftc_bridge", "cftc"),
            module_path="src.ingestion.cftc_bridge",
            class_name="CFTCBridge",
            preferred_methods=("fetch",),
            default_source_tier="tier_1_official",
            default_trust_weight=1.0,
            default_freshness_ttl_minutes=10080,
        ),
        "whitehouse_policy_bridge": BridgeRegistrySpec(
            name="whitehouse_policy_bridge",
            aliases=("whitehouse_policy_bridge", "whitehouse_policy", "whitehouse"),
            module_path="src.bridges.whitehouse_policy_bridge",
            class_name="WhiteHousePolicyBridge",
            preferred_methods=("poll",),
            default_source_tier="tier_1_official",
            default_trust_weight=1.0,
            default_freshness_ttl_minutes=1440,
        ),
        "noaa_bridge": BridgeRegistrySpec(
            name="noaa_bridge",
            aliases=("noaa_bridge", "noaa"),
            module_path="src.ingestion.noaa_bridge",
            class_name="NOAABridge",
            preferred_methods=("fetch",),
            default_source_tier="tier_1_official",
            default_trust_weight=1.0,
            default_freshness_ttl_minutes=360,
        ),
        "gdelt_bridge": BridgeRegistrySpec(
            name="gdelt_bridge",
            aliases=("gdelt_bridge", "gdelt"),
            module_path="src.bridges.gdelt_bridge",
            class_name="GDELTBridge",
            preferred_methods=("build_snapshot_section", "poll"),
            default_source_tier="tier_2_operational",
            default_trust_weight=0.8,
            default_freshness_ttl_minutes=30,
        ),
        "maritime_bridge": BridgeRegistrySpec(
            name="maritime_bridge",
            aliases=("maritime_bridge", "maritime"),
            module_path="src.bridges.maritime_bridge",
            class_name="MaritimeBridge",
            preferred_methods=("poll",),
            default_source_tier="tier_2_operational",
            default_trust_weight=0.8,
            default_freshness_ttl_minutes=120,
        ),
        "sentiment_bridge": BridgeRegistrySpec(
            name="sentiment_bridge",
            aliases=("sentiment_bridge", "sentiment", "finnhub"),
            module_path="src.ingestion.sentiment_bridge",
            class_name="SentimentBridge",
            preferred_methods=("fetch",),
            default_source_tier="tier_2_operational",
            default_trust_weight=0.8,
            default_freshness_ttl_minutes=60,
        ),
        "exa_ai_bridge": BridgeRegistrySpec(
            name="exa_ai_bridge",
            aliases=("exa_ai_bridge", "exa_search_bridge", "exa_search", "exa_ai"),
            module_path="src.bridges.exa_search_bridge",
            class_name="ExaSearchBridge",
            preferred_methods=("build_snapshot_section", "poll"),
            default_source_tier="tier_2_operational",
            default_trust_weight=0.8,
            default_freshness_ttl_minutes=60,
        ),
        "options_greeks_bridge": BridgeRegistrySpec(
            name="options_greeks_bridge",
            aliases=("options_greeks_bridge", "options_greeks"),
            module_path="src.bridges.options_greeks_bridge",
            class_name="OptionsGreeksBridge",
            preferred_methods=("fetch", "build_snapshot_section", "poll"),
            default_source_tier="tier_3_research",
            default_trust_weight=0.5,
            default_freshness_ttl_minutes=15,
        ),
        "aviation_bridge": BridgeRegistrySpec(
            name="aviation_bridge",
            aliases=("aviation_bridge", "aviation_disruption_bridge", "aviation"),
            module_path="src.bridges.aviation_disruption_bridge",
            class_name="AviationDisruptionBridge",
            preferred_methods=("poll",),
            default_source_tier="tier_2_operational",
            default_trust_weight=0.8,
            default_freshness_ttl_minutes=60,
        ),
        "policy_uncertainty_bridge": BridgeRegistrySpec(
            name="policy_uncertainty_bridge",
            aliases=("policy_uncertainty_bridge", "policy_uncertainty"),
            module_path="src.ingestion.policy_uncertainty_bridge",
            class_name="PolicyUncertaintyBridge",
            preferred_methods=("fetch",),
            default_source_tier="tier_3_research",
            default_trust_weight=0.5,
            default_freshness_ttl_minutes=1440,
        ),
        "political_disclosure_research_monitor": BridgeRegistrySpec(
            name="political_disclosure_research_monitor",
            aliases=("political_disclosure_research_monitor", "politician_alpha_bridge", "politician_alpha", "congressional_disclosures"),
            module_path="src.bridges.politician_alpha_bridge",
            class_name="PoliticianAlphaBridge",
            preferred_methods=("poll",),
            default_source_tier="tier_3_research",
            default_trust_weight=0.5,
            default_freshness_ttl_minutes=1440,
        ),
        "sec_filing_event_scorer": BridgeRegistrySpec(
            name="sec_filing_event_scorer",
            aliases=("sec_filing_event_scorer", "sec_filing_scorer"),
            module_path=None,
            class_name=None,
            preferred_methods=("fetch",),
            default_source_tier="tier_3_research",
            default_trust_weight=0.5,
            default_freshness_ttl_minutes=1440,
            adapter_kind="sec_filing_scorer",
        ),
        "market_microstructure_bridge": BridgeRegistrySpec(
            name="market_microstructure_bridge",
            aliases=("market_microstructure_bridge", "market_microstructure"),
            module_path="src.bridges.market_microstructure_bridge",
            class_name="MarketMicrostructureBridge",
            preferred_methods=("build_snapshot_section", "poll"),
            default_source_tier="tier_2_operational",
            default_trust_weight=0.8,
            default_freshness_ttl_minutes=15,
        ),
        "treasury_ofac_bridge": BridgeRegistrySpec(
            name="treasury_ofac_bridge",
            aliases=("treasury_ofac_bridge", "ofac_bridge", "sanctions"),
            module_path="src.bridges.treasury_ofac_bridge",
            class_name="TreasuryOFACBridge",
            preferred_methods=("poll",),
            default_source_tier="tier_1_official",
            default_trust_weight=1.0,
            default_freshness_ttl_minutes=1440,
        ),
        "gpr_index_bridge": BridgeRegistrySpec(
            name="gpr_index_bridge",
            aliases=("gpr_index_bridge", "gpr_index"),
            module_path="src.bridges.gpr_index_bridge",
            class_name="GPRIndexBridge",
            preferred_methods=("poll",),
            default_source_tier="tier_3_research",
            default_trust_weight=0.5,
            default_freshness_ttl_minutes=1440,
        ),
        "semiconductor_supply_bridge": BridgeRegistrySpec(
            name="semiconductor_supply_bridge",
            aliases=("semiconductor_supply_bridge", "semiconductor_supply"),
            module_path="src.bridges.semiconductor_supply_bridge",
            class_name="SemiconductorSupplyBridge",
            preferred_methods=("poll",),
            default_source_tier="tier_3_research",
            default_trust_weight=0.5,
            default_freshness_ttl_minutes=1440,
        ),
    }

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or Path(__file__).resolve().parents[2]
        self._trust_cfg = _load_yaml(self.repo_root / "config" / "data_trust_hierarchy.yaml")
        self._freshness_cfg = _load_yaml(self.repo_root / "config" / "freshness_policy.yaml")
        self._tier_lookup = self._build_tier_lookup(self._trust_cfg)
        self._bridges: Dict[str, Any] = {}
        self._load_all()

    def _build_tier_lookup(self, cfg: Dict[str, Any]) -> Dict[str, tuple[str, float]]:
        lookup: Dict[str, tuple[str, float]] = {}
        for tier_name, tier_data in (cfg.get("tiers") or {}).items():
            weight = float((tier_data or {}).get("weight", 0.0))
            for source in (tier_data or {}).get("sources", []) or []:
                lookup[str(source)] = (tier_name, weight)
        return lookup

    def _resolve_tier(self, spec: BridgeRegistrySpec) -> tuple[str, float]:
        for alias in spec.aliases:
            if alias in self._tier_lookup:
                return self._tier_lookup[alias]
        return spec.default_source_tier, spec.default_trust_weight

    def _resolve_ttl(self, spec: BridgeRegistrySpec) -> int:
        freshness = (self._freshness_cfg.get("sources") or {})
        for alias in spec.aliases:
            row = freshness.get(alias)
            if isinstance(row, dict) and row.get("freshness_ttl_minutes") is not None:
                return int(row["freshness_ttl_minutes"])
        return spec.default_freshness_ttl_minutes

    def _load_all(self) -> None:
        for name, spec in self.DEFAULT_SPECS.items():
            source_tier, trust_weight = self._resolve_tier(spec)
            ttl = self._resolve_ttl(spec)
            try:
                if spec.adapter_kind == "sec_filing_scorer":
                    bridge = SECFilingAdapter(repo_root=self.repo_root)
                    bridge.source_tier = source_tier
                    bridge.trust_weight = trust_weight
                    bridge.freshness_ttl_minutes = ttl
                    self._bridges[name] = bridge
                else:
                    wrapped_spec = ExistingBridgeSpec(
                        name=spec.name,
                        module_path=str(spec.module_path),
                        class_name=str(spec.class_name),
                        aliases=spec.aliases,
                        preferred_methods=spec.preferred_methods,
                        source_tier=source_tier,
                        trust_weight=trust_weight,
                        freshness_ttl_minutes=ttl,
                    )
                    self._bridges[name] = ExistingBridgeAdapter(wrapped_spec, repo_root=self.repo_root)
            except Exception as exc:
                self._bridges[name] = {
                    "status": "load_error",
                    "error": str(exc),
                    "source": name,
                    "source_tier": source_tier,
                    "trust_weight": trust_weight,
                    "fresh": False,
                    "last_fetch": None,
                    "consecutive_failures": 0,
                }

    def names(self) -> Iterable[str]:
        return self.DEFAULT_SPECS.keys()

    def specs(self) -> Dict[str, BridgeRegistrySpec]:
        return dict(self.DEFAULT_SPECS)

    def get(self, name: str) -> Any:
        return self._bridges.get(name)

    def fetch_all(self, names: Optional[Iterable[str]] = None) -> Dict[str, Dict[str, Any]]:
        wanted = list(names) if names is not None else list(self.names())
        results: Dict[str, Dict[str, Any]] = {}
        for name in wanted:
            bridge = self._bridges.get(name)
            if bridge is None:
                continue
            if isinstance(bridge, dict):
                results[name] = {
                    "source": bridge.get("source", name),
                    "source_tier": bridge.get("source_tier", ""),
                    "trust_weight": bridge.get("trust_weight", 0.0),
                    "timestamp_utc": None,
                    "fresh": False,
                    "error": bridge.get("error", "load_error"),
                    "data": None,
                    "status": "load_error",
                }
                continue
            results[name] = bridge.fetch()
        return results

    def health_all(self) -> Dict[str, Dict[str, Any]]:
        health: Dict[str, Dict[str, Any]] = {}
        for name, bridge in self._bridges.items():
            if isinstance(bridge, dict):
                health[name] = dict(bridge)
            else:
                health[name] = bridge.health()
        return health
