#!/usr/bin/env python3
"""Fertilizer Price Bridge — Nitrogen/Urea price ingestion for ag spread strategies.

Tracks nitrogen fertilizer costs which are critical for the corn/soybean spread
cascade trade. When oil rises above $90/bbl, ethanol demand lifts corn. But the
Strait of Hormuz also chokes nitrogen fertilizer supply, spiking costs from ~$480
to $700+/ton. This makes corn expensive to grow, causing farmers to rotate to
soybeans (which fix their own nitrogen). The corn/soybean spread reverses.

Data sources (priority order):
1. World Bank Commodity Prices (free, monthly)
2. FRED (Federal Reserve) — fertilizer price indices
3. CME Urea futures proxy via natural gas price * conversion factor

Emits PhysicalFlowEvent packets with fertilizer disruption scoring.
"""
from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any, Dict, List, Optional

from src.packets.physical_flow_event import make_physical_flow_event

logger = logging.getLogger(__name__)

# FRED series for fertilizer-related data
# PPI for Nitrogenous Fertilizer Manufacturing
FRED_FERTILIZER_SERIES = "PCU325311325311"
# Natural gas (Henry Hub) — primary input cost for nitrogen fertilizer
FRED_NATGAS_SERIES = "DHHNGSP"

# World Bank commodity price API (Pink Sheet)
WORLDBANK_COMMODITIES_URL = (
    "https://api.worldbank.org/v2/country/WLD/indicator/"
    "COMMODITY.FERTILIZERS.UREA?format=json&per_page=12&date=2024:2026"
)

# Baseline fertilizer cost thresholds (USD/ton for urea)
UREA_THRESHOLDS = {
    "normal_ceiling": 350.0,      # Below $350 = normal
    "elevated_ceiling": 500.0,    # $350-$500 = elevated
    "shock_ceiling": 650.0,       # $500-$650 = shock
    # Above $650 = crisis (2022 saw $700+)
}

# Natural gas to fertilizer cost conversion
# ~33 MMBtu of natural gas per ton of ammonia, ammonia is ~80% of urea cost
NATGAS_TO_UREA_FACTOR = 33.0 * 0.80  # rough $/ton contribution from gas


class FertilizerBridge:
    """Ingests fertilizer price data for agricultural spread strategies."""

    source = "fertilizer_bridge"
    source_tier = "tier_b_institutional"
    trust_weight = 0.85

    def __init__(self, fred_api_key: Optional[str] = None) -> None:
        self._fred_key = fred_api_key
        self._cache: Dict[str, Any] = {}
        self._last_urea_price: Optional[float] = None
        self._last_natgas_price: Optional[float] = None

    def fetch(self, market_data: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """Fetch fertilizer price data from available sources.

        Args:
            market_data: Optional dict with natural gas price data from other bridges.

        Returns:
            List of PhysicalFlowEvent packet dicts.
        """
        packets: List[Dict[str, Any]] = []

        # Source 1: Try FRED for PPI fertilizer index
        fred_data = self._fetch_fred_fertilizer()
        if fred_data is not None:
            pkt = self._build_packet(
                price=fred_data["value"],
                unit="index_ppi",
                summary=f"FRED PPI Nitrogenous Fertilizer: {fred_data['value']:.1f} (date: {fred_data.get('date', 'unknown')})",
                provenance={"series": FRED_FERTILIZER_SERIES, "source": "fred", "date": fred_data.get("date")},
                disruption=self._ppi_to_disruption(fred_data["value"]),
            )
            packets.append(pkt)

        # Source 2: Natural gas price → estimated urea cost
        natgas_price = self._get_natgas_price(market_data)
        if natgas_price is not None:
            self._last_natgas_price = natgas_price
            estimated_urea = self._estimate_urea_from_natgas(natgas_price)
            disruption = self._urea_disruption_score(estimated_urea)
            pkt = self._build_packet(
                price=estimated_urea,
                unit="usd_per_ton_estimated",
                summary=(
                    f"Fertilizer (urea) estimated ${estimated_urea:.0f}/ton "
                    f"from natgas ${natgas_price:.2f}/MMBtu"
                ),
                provenance={
                    "natgas_price": natgas_price,
                    "conversion_factor": NATGAS_TO_UREA_FACTOR,
                    "source": "natgas_derived",
                },
                disruption=disruption,
                flow_type="fertilizer_cost_estimate",
            )
            packets.append(pkt)
            self._last_urea_price = estimated_urea

        # Source 3: World Bank commodity data (monthly, may be stale)
        wb_data = self._fetch_worldbank_urea()
        if wb_data is not None:
            disruption = self._urea_disruption_score(wb_data["value"])
            pkt = self._build_packet(
                price=wb_data["value"],
                unit="usd_per_ton",
                summary=f"World Bank Urea: ${wb_data['value']:.0f}/ton ({wb_data.get('date', 'unknown')})",
                provenance={"source": "worldbank", "date": wb_data.get("date")},
                disruption=disruption,
                flow_type="fertilizer_spot_price",
            )
            packets.append(pkt)
            self._last_urea_price = wb_data["value"]

        return packets

    def get_fertilizer_state(self) -> Dict[str, Any]:
        """Return current fertilizer market state for strategy engine consumption."""
        urea = self._last_urea_price
        regime = "UNKNOWN"
        if urea is not None:
            if urea > UREA_THRESHOLDS["shock_ceiling"]:
                regime = "CRISIS"
            elif urea > UREA_THRESHOLDS["elevated_ceiling"]:
                regime = "SHOCK"
            elif urea > UREA_THRESHOLDS["normal_ceiling"]:
                regime = "ELEVATED"
            else:
                regime = "NORMAL"

        return {
            "urea_price_estimated": urea,
            "natgas_price": self._last_natgas_price,
            "fertilizer_regime": regime,
            "disruption_score": self._urea_disruption_score(urea) if urea else 0.3,
            "thresholds": UREA_THRESHOLDS,
        }

    # ------------------------------------------------------------------
    # Data fetching helpers
    # ------------------------------------------------------------------

    def _fetch_fred_fertilizer(self) -> Optional[Dict[str, Any]]:
        """Fetch PPI for nitrogenous fertilizer from FRED."""
        if not self._fred_key:
            return self._cache.get("fred_fertilizer")
        url = (
            f"https://api.stlouisfed.org/fred/series/observations"
            f"?series_id={FRED_FERTILIZER_SERIES}"
            f"&api_key={self._fred_key}"
            f"&file_type=json&sort_order=desc&limit=1"
        )
        try:
            data = self._http_get(url, "fred_fertilizer_raw")
            obs = data.get("observations", [])
            if obs and obs[0].get("value") != ".":
                result = {"value": float(obs[0]["value"]), "date": obs[0].get("date")}
                self._cache["fred_fertilizer"] = result
                return result
        except Exception as e:
            logger.warning("FRED fertilizer fetch failed: %s", e)
        return self._cache.get("fred_fertilizer")

    def _fetch_worldbank_urea(self) -> Optional[Dict[str, Any]]:
        """Fetch urea price from World Bank commodity data."""
        try:
            data = self._http_get(WORLDBANK_COMMODITIES_URL, "wb_urea_raw")
            if isinstance(data, list) and len(data) > 1:
                records = data[1]
                for rec in (records or []):
                    if rec.get("value") is not None:
                        result = {
                            "value": float(rec["value"]),
                            "date": rec.get("date", "unknown"),
                        }
                        self._cache["wb_urea"] = result
                        return result
        except Exception as e:
            logger.warning("World Bank urea fetch failed: %s", e)
        return self._cache.get("wb_urea")

    def _get_natgas_price(self, market_data: Optional[Dict[str, Any]]) -> Optional[float]:
        """Extract natural gas price from market data or fetch from FRED."""
        # Try market_data first (from other bridges)
        if market_data:
            ng = market_data.get("nat_gas", {})
            if isinstance(ng, dict) and ng.get("price"):
                return float(ng["price"])
            # Also check flat key
            if market_data.get("natgas_price"):
                return float(market_data["natgas_price"])

        # Fallback: FRED Henry Hub
        if self._fred_key:
            url = (
                f"https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={FRED_NATGAS_SERIES}"
                f"&api_key={self._fred_key}"
                f"&file_type=json&sort_order=desc&limit=1"
            )
            try:
                data = self._http_get(url, "fred_natgas_raw")
                obs = data.get("observations", [])
                if obs and obs[0].get("value") != ".":
                    return float(obs[0]["value"])
            except Exception as e:
                logger.warning("FRED natgas fetch failed: %s", e)

        return self._cache.get("natgas_price")

    # ------------------------------------------------------------------
    # Estimation & scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_urea_from_natgas(natgas_price: float) -> float:
        """Estimate urea cost from natural gas price.

        Base cost = gas input + fixed production costs (~$150/ton)
        At $3/MMBtu gas: ~$230/ton urea (normal)
        At $6/MMBtu gas: ~$310/ton urea (elevated)
        At $9/MMBtu gas: ~$390/ton urea (high)

        Supply disruptions (Hormuz) add 30-80% premium on top.
        """
        gas_component = natgas_price * NATGAS_TO_UREA_FACTOR
        fixed_costs = 150.0  # production, transport baseline
        return gas_component + fixed_costs

    @staticmethod
    def _urea_disruption_score(price: Optional[float]) -> float:
        """Score fertilizer disruption 0.0-1.0 based on urea price."""
        if price is None:
            return 0.3
        if price > UREA_THRESHOLDS["shock_ceiling"]:
            return min(0.85 + (price - UREA_THRESHOLDS["shock_ceiling"]) / 500, 1.0)
        if price > UREA_THRESHOLDS["elevated_ceiling"]:
            return 0.6 + (price - UREA_THRESHOLDS["elevated_ceiling"]) / 600
        if price > UREA_THRESHOLDS["normal_ceiling"]:
            return 0.4 + (price - UREA_THRESHOLDS["normal_ceiling"]) / 750
        return 0.2

    @staticmethod
    def _ppi_to_disruption(ppi_value: float) -> float:
        """Convert PPI index to disruption score.

        PPI baseline ~200 (2020), peaked ~450 in 2022.
        """
        if ppi_value > 400:
            return 0.9
        if ppi_value > 300:
            return 0.7
        if ppi_value > 250:
            return 0.5
        return 0.3

    def _build_packet(
        self,
        price: float,
        unit: str,
        summary: str,
        provenance: Dict[str, Any],
        disruption: float,
        flow_type: str = "fertilizer_price",
    ) -> Dict[str, Any]:
        pkt = make_physical_flow_event(
            source=self.source,
            source_tier=self.source_tier,
            trust_weight=self.trust_weight,
            region="GLOBAL",
            flow_type=flow_type,
            disruption_score=disruption,
            measured_value=price,
            unit=unit,
            related_assets=["ZC", "ZS", "NG", "MOS", "CF", "NTR"],
            summary=summary,
            confidence=0.80,
            provenance=provenance,
        )
        return pkt.to_dict()

    def _http_get(self, url: str, cache_key: str) -> Any:
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "GlobalSentinel/5.4"}
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self._cache[cache_key] = data
                return data
        except Exception:
            return self._cache.get(cache_key, {})
