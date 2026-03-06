#!/usr/bin/env python3
"""
Global Sentinel V5.2 — GCP 2.0 Consciousness Coherence Bridge

Integrates data from the Global Consciousness Project (GCP 2.0) Random Number
Generator network. High Max[Z] scores indicate global collective attention and
emotional synchronization — a statistically validated leading indicator for
market volatility events.

Evidence basis:
- Max[Z] significantly covaries with VIX (volatility index)
- RNG deviations preceded 9/11 by ~2 hours
- RNG deviations preceded March 2020 liquidation by ~48 hours
- Regional RNG spikes in Asia precede USD/JPY and Nikkei moves

Data source: GCP 2.0 real-time network (gcp2.net)
Fallback: Princeton GCP Dot (gcpdot.com) for aggregate coherence

No API key required — public data feeds.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_get(url: str, timeout: int = 15, retries: int = 2) -> Optional[str]:
    """Fetch URL with retry logic."""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "GlobalSentinel-GCPBridge/1.0"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", errors="ignore")
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries:
                time.sleep(3 * (attempt + 1))
                continue
            return None
        except Exception:
            if attempt < retries:
                time.sleep(2)
                continue
            return None


def safe_get_json(url: str, timeout: int = 15) -> Any:
    text = safe_get(url, timeout)
    if text:
        try:
            return json.loads(text)
        except Exception:
            pass
    return None


class GCPConsciousnessBridge:
    """
    Polls GCP 2.0 RNG coherence data and computes consciousness
    coherence metrics for regime scoring.

    The "Field Layer" — a leading indicator that precedes narrative
    and execution layers.
    """

    # GCP 2.0 endpoints (public)
    GCP2_STATUS_URL = "https://www.gcp2.net/api/status"
    GCP2_DATA_URL = "https://www.gcp2.net/api/data"
    # Fallback: GCP Dot aggregate (simpler, always available)
    GCPDOT_URL = "https://gcpdot.com/gcpindex.php"

    # Z-score thresholds for consciousness coherence
    Z_THRESHOLD_ELEVATED = 2.0    # Moderate coherence — attention focusing
    Z_THRESHOLD_HIGH = 2.5        # High coherence — significant global event
    Z_THRESHOLD_EXTREME = 3.5     # Extreme — black swan territory

    # Regional node multipliers — D.C. and Silicon Valley nodes carry
    # higher weight for tech/policy-correlated trades
    REGIONAL_MULTIPLIERS = {
        "north_america_east": 1.2,   # D.C., NYC — policy/finance center
        "north_america_west": 1.2,   # Silicon Valley — tech center
        "americas_east": 1.2,
        "americas_west": 1.2,
        "us_east": 1.2,
        "us_west": 1.2,
        "dc": 1.3,
        "silicon_valley": 1.3,
        "europe": 1.0,
        "asia": 1.1,                 # TSMC/semiconductor supply chain
        "oceania": 0.9,
        "africa": 0.8,
    }

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.cache_dir = repo_root / "logs" / "bridge_cache" / "gcp_consciousness"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def poll(self) -> Dict[str, Any]:
        """
        Poll GCP 2.0 network for current consciousness coherence.

        Returns:
            Dict with max_z, regional_z, coherence_level, node_count,
            regional_spikes, and evidence signals.
        """
        result = {
            "timestamp_utc": iso_now(),
            "source": "gcp2",
            "max_z": 0.0,
            "mean_z": 0.0,
            "node_count": 0,
            "coherence_level": "random",  # random, low, moderate, high, extreme
            "regional_z": {},
            "regional_spikes": [],
            "evidence": [],
            "fresh": False,
        }

        # Try GCP 2.0 API first
        gcp2_data = self._poll_gcp2()
        if gcp2_data:
            result.update(gcp2_data)
            result["fresh"] = True
        else:
            # Fallback to GCP Dot aggregate
            dot_data = self._poll_gcpdot()
            if dot_data:
                result.update(dot_data)
                result["source"] = "gcpdot_fallback"
                result["fresh"] = True

        # Compute regionally-weighted z-score (D.C./Silicon Valley 1.2-1.3x)
        regional_z = result.get("regional_z", {})
        if regional_z:
            weighted_sum = 0.0
            weight_total = 0.0
            for region, z in regional_z.items():
                mult = self._region_multiplier(region)
                weighted_sum += z * mult
                weight_total += mult
            if weight_total > 0:
                result["coherence_z"] = round(weighted_sum / weight_total, 4)
            else:
                result["coherence_z"] = result["max_z"]
        else:
            result["coherence_z"] = result["max_z"]

        # Classify coherence level
        result["coherence_level"] = self._classify_coherence(result["max_z"])

        # Generate evidence signals
        result["evidence"] = self._generate_evidence(result)

        # Detect regional spikes (Asia/Europe/Americas)
        result["regional_spikes"] = self._detect_regional_spikes(result.get("regional_z", {}))

        # Cache result
        self._cache_result(result)

        return result

    def build_snapshot_section(self) -> Dict[str, Any]:
        """Build snapshot section for regime scoring."""
        data = self.poll()
        return {
            "max_z": data.get("max_z", 0.0),
            "mean_z": data.get("mean_z", 0.0),
            "coherence_level": data.get("coherence_level", "random"),
            "node_count": data.get("node_count", 0),
            "regional_z": data.get("regional_z", {}),
            "regional_spikes": data.get("regional_spikes", []),
            "evidence": data.get("evidence", []),
            "fresh": data.get("fresh", False),
        }

    def _poll_gcp2(self) -> Optional[Dict[str, Any]]:
        """Poll GCP 2.0 real-time API."""
        # Try status endpoint for current network state
        status = safe_get_json(self.GCP2_STATUS_URL)
        if not status:
            # Try data endpoint
            data = safe_get_json(self.GCP2_DATA_URL)
            if not data:
                return None
            status = data

        # GCP 2.0 API returns varying formats — extract what we can
        max_z = 0.0
        mean_z = 0.0
        node_count = 0
        regional_z = {}

        # Handle different response formats
        if isinstance(status, dict):
            max_z = float(status.get("max_z", status.get("maxZ", status.get("z_score", 0))))
            mean_z = float(status.get("mean_z", status.get("meanZ", max_z * 0.6)))
            node_count = int(status.get("node_count", status.get("nodes", status.get("active_nodes", 0))))

            # Regional breakdown if available
            regions = status.get("regions", status.get("regional", {}))
            if isinstance(regions, dict):
                for region, data in regions.items():
                    if isinstance(data, dict):
                        regional_z[region] = float(data.get("z", data.get("z_score", 0)))
                    elif isinstance(data, (int, float)):
                        regional_z[region] = float(data)
            elif isinstance(regions, list):
                for r in regions:
                    if isinstance(r, dict) and "region" in r:
                        regional_z[r["region"]] = float(r.get("z", 0))

        elif isinstance(status, list) and status:
            # Array of node readings
            z_values = []
            for node in status:
                if isinstance(node, dict):
                    z = float(node.get("z", node.get("z_score", 0)))
                    z_values.append(z)
                    region = node.get("region", "unknown")
                    if region not in regional_z:
                        regional_z[region] = z
                    else:
                        regional_z[region] = max(regional_z[region], z)
            if z_values:
                max_z = max(z_values)
                mean_z = sum(z_values) / len(z_values)
                node_count = len(z_values)

        # Sanity check: Z-scores above ~7 are physically impossible
        if abs(max_z) > 10.0:
            max_z = 0.0
        if abs(mean_z) > 10.0:
            mean_z = 0.0
        regional_z = {k: (v if abs(v) <= 10.0 else 0.0) for k, v in regional_z.items()}

        return {
            "max_z": round(max_z, 3),
            "mean_z": round(mean_z, 3),
            "node_count": node_count,
            "regional_z": {k: round(v, 3) for k, v in regional_z.items()},
        }

    @staticmethod
    def _prob_to_zscore(p: float) -> float:
        """
        Convert probability (0-1) to Z-score using rational approximation
        of the inverse normal CDF (Abramowitz & Stegun 26.2.23).
        p=0.5 → z=0, p>0.5 → positive z (network coherence above chance).
        """
        if p <= 0.0 or p >= 1.0:
            return 0.0
        # Work with upper tail
        if p <= 0.5:
            t_p = 1.0 - p
            sign = -1.0
        else:
            t_p = p
            sign = 1.0
        # Map to upper-tail probability
        q = 1.0 - t_p
        if q <= 0.0:
            return 0.0
        import math
        t = math.sqrt(-2.0 * math.log(q))
        # Abramowitz & Stegun constants
        c0, c1, c2 = 2.515517, 0.802853, 0.010328
        d1, d2, d3 = 1.432788, 0.189269, 0.001308
        z = t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t * t * t)
        return round(sign * z, 4)

    def _poll_gcpdot(self) -> Optional[Dict[str, Any]]:
        """
        Fallback: scrape GCP Dot for aggregate coherence.
        GCPDot returns XML like:
          <gcpstats><serverTime>...</serverTime>
            <ss><s t='1772762460'>0.5683089</s>...</ss>
          </gcpstats>
        Values are probabilities (0.0-1.0) where 0.5 = chance.
        We convert to Z-scores using inverse normal CDF.
        """
        text = safe_get(self.GCPDOT_URL, timeout=10)
        if not text:
            return None

        try:
            import re

            # Handle JSON response (unlikely but possible)
            if text.strip().startswith("{"):
                data = json.loads(text)
                dot_value = float(data.get("dot", data.get("value", data.get("index", 0))))
                if abs(dot_value) > 10.0:
                    return None
                return {
                    "max_z": round(dot_value, 3),
                    "mean_z": round(dot_value * 0.8, 3),
                    "node_count": 1,
                    "regional_z": {"global_aggregate": round(dot_value, 3)},
                }

            # Parse XML: extract probability values from <s t='timestamp'>value</s>
            prob_values = re.findall(r"<s\s+t='[^']*'>\s*([\d.]+)\s*</s>", text)
            if not prob_values:
                return None

            # Convert probabilities to Z-scores
            z_scores = []
            for pv in prob_values:
                p = float(pv)
                if 0.0 < p < 1.0:
                    z_scores.append(self._prob_to_zscore(p))

            if not z_scores:
                return None

            max_z = max(z_scores, key=abs)
            mean_z = sum(z_scores) / len(z_scores)

            return {
                "max_z": round(max_z, 3),
                "mean_z": round(mean_z, 3),
                "node_count": len(z_scores),
                "regional_z": {"global_aggregate": round(max_z, 3)},
            }
        except Exception:
            return None

    def _classify_coherence(self, max_z: float) -> str:
        """Classify consciousness coherence level from Max[Z]."""
        abs_z = abs(max_z)
        if abs_z >= self.Z_THRESHOLD_EXTREME:
            return "extreme"
        elif abs_z >= self.Z_THRESHOLD_HIGH:
            return "high"
        elif abs_z >= self.Z_THRESHOLD_ELEVATED:
            return "moderate"
        elif abs_z >= 1.0:
            return "low"
        return "random"

    def _region_multiplier(self, region: str) -> float:
        """Get regional weight multiplier for a GCP node region."""
        lower = region.lower().replace(" ", "_").replace("-", "_")
        for key, mult in self.REGIONAL_MULTIPLIERS.items():
            if key in lower:
                return mult
        return 1.0

    def _detect_regional_spikes(self, regional_z: Dict[str, float]) -> List[Dict[str, Any]]:
        """
        Detect regional consciousness spikes.
        Asian spikes predict USD/JPY and Nikkei moves.
        European spikes predict DAX/Euro moves.
        """
        spikes = []
        asia_regions = {"asia", "east_asia", "japan", "china", "india", "pacific"}
        europe_regions = {"europe", "western_europe", "eu", "uk"}
        americas_regions = {"americas", "north_america", "us", "south_america"}

        for region, z in regional_z.items():
            region_lower = region.lower()
            if abs(z) >= self.Z_THRESHOLD_ELEVATED:
                spike = {
                    "region": region,
                    "z_score": z,
                    "level": self._classify_coherence(z),
                }

                # Predict affected markets based on region
                if any(r in region_lower for r in asia_regions):
                    spike["predicted_markets"] = ["USD/JPY", "Nikkei", "Hang Seng"]
                    spike["market_zone"] = "asia"
                elif any(r in region_lower for r in europe_regions):
                    spike["predicted_markets"] = ["DAX", "FTSE", "EUR/USD"]
                    spike["market_zone"] = "europe"
                elif any(r in region_lower for r in americas_regions):
                    spike["predicted_markets"] = ["S&P 500", "VIX", "DXY"]
                    spike["market_zone"] = "americas"
                else:
                    spike["predicted_markets"] = []
                    spike["market_zone"] = "other"

                spikes.append(spike)

        return spikes

    def _generate_evidence(self, result: Dict[str, Any]) -> List[str]:
        """Generate human-readable evidence signals."""
        evidence = []
        max_z = result.get("max_z", 0)
        coherence = result.get("coherence_level", "random")
        nodes = result.get("node_count", 0)

        if coherence == "extreme":
            evidence.append(f"GCP EXTREME coherence: Max[Z]={max_z:.2f} — black swan signal")
        elif coherence == "high":
            evidence.append(f"GCP HIGH coherence: Max[Z]={max_z:.2f} — systemic event likely")
        elif coherence == "moderate":
            evidence.append(f"GCP moderate coherence: Max[Z]={max_z:.2f} — attention focusing")
        elif coherence == "low":
            evidence.append(f"GCP low coherence: Z={max_z:.2f} — marginal field activity")

        if nodes > 0:
            evidence.append(f"GCP network: {nodes} active RNG nodes")

        # Regional spike evidence
        for spike in result.get("regional_spikes", []):
            markets = ", ".join(spike.get("predicted_markets", []))
            evidence.append(
                f"GCP regional spike: {spike['region']} Z={spike['z_score']:.2f} "
                f"— watch {markets}"
            )

        return evidence

    def _cache_result(self, result: Dict[str, Any]):
        """Cache poll result for historical analysis."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        cache_file = self.cache_dir / f"gcp_{ts}.json"
        try:
            cache_file.write_text(
                json.dumps(result, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            # Keep only last 200 cache files
            files = sorted(self.cache_dir.glob("gcp_*.json"))
            for f in files[:-200]:
                f.unlink(missing_ok=True)
        except Exception:
            pass


class SentinelLogicEngine:
    """
    The consciousness-market bridge decision engine.

    Combines GCP coherence (Field Layer) with narrative velocity
    (Narrative Layer) and market data (Execution Layer) to generate
    trading signals.
    """

    def __init__(self, z_threshold: float = 2.5, sentiment_floor: float = -0.4):
        self.Z_THRESHOLD = z_threshold
        self.SENTIMENT_FLOOR = sentiment_floor

    def analyze_signal(
        self,
        gcp_z: float,
        narrative_velocity: float,
        current_vix: float = 0,
    ) -> Dict[str, Any]:
        """
        Three-layer signal analysis.

        Returns signal type, action, and confidence.
        """
        # Scenario 1: Black Swan Shield
        # Field coherent + news spreading fast = systemic shock incoming
        if gcp_z > self.Z_THRESHOLD and narrative_velocity > 10:
            return {
                "signal": "SYSTEMIC_SHOCK",
                "action": "hedge_longs_exit_margin",
                "description": "High consciousness coherence + rapid news spread — systemic shock likely",
                "confidence": min(0.95, 0.5 + (gcp_z - self.Z_THRESHOLD) * 0.15),
                "urgency": "immediate",
            }

        # Scenario 2: Fake News Filter (Divergence)
        # News panicking but GCP field remains random = noise, not signal
        if narrative_velocity > 15 and gcp_z < 1.0:
            return {
                "signal": "NOISE_DIVERGENCE",
                "action": "stay_neutral_potential_bear_trap",
                "description": "News panic without consciousness coherence — likely noise/bear trap",
                "confidence": 0.70,
                "urgency": "monitor",
            }

        # Scenario 3: Synchronized Euphoria
        # High coherence + low/positive narrative = accumulation phase
        if gcp_z > self.Z_THRESHOLD and narrative_velocity < 5:
            return {
                "signal": "COHERENT_ACCUMULATION",
                "action": "leveraged_trend_follow",
                "description": "Consciousness coherent with stable positive trend — accumulation signal",
                "confidence": min(0.85, 0.4 + gcp_z * 0.12),
                "urgency": "opportunistic",
            }

        # Scenario 4: Observer Effect Check
        # High VIX but no GCP coherence = algorithmic/synthetic volatility
        if current_vix > 25 and gcp_z < 1.5:
            return {
                "signal": "SYNTHETIC_VOLATILITY",
                "action": "fade_vix_spike",
                "description": "VIX elevated without organic consciousness shift — synthetic bot-driven",
                "confidence": 0.60,
                "urgency": "tactical",
            }

        # Scenario 5: Pre-Pulse Detection
        # Moderate coherence building = potential pre-event accumulation
        if 1.5 < gcp_z <= self.Z_THRESHOLD and narrative_velocity < 3:
            return {
                "signal": "PRE_PULSE",
                "action": "increase_monitoring_tighten_stops",
                "description": "Rising coherence without narrative trigger — pre-pulse pattern forming",
                "confidence": 0.50,
                "urgency": "heightened_awareness",
            }

        return {
            "signal": "NEUTRAL",
            "action": "follow_standard_technicals",
            "description": "No consciousness-market divergence detected",
            "confidence": 0.40,
            "urgency": "normal",
        }
