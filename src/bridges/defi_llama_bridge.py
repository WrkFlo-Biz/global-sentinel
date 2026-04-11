#!/usr/bin/env python3
"""
Global Sentinel — DeFi Llama Bridge

Fetches DeFi ecosystem data: total TVL, stablecoin market cap, DeFi yields.
Tracks TVL trends, stablecoin flows (risk-on/off indicator), yield compression.

No API key needed.

Output: data/quantum_feed/defi_data.json
Tier 3, trust 0.5, TTL 60 min
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("global_sentinel.defi_llama_bridge")


def _fetch_json(url: str, timeout: int = 20) -> Optional[Any]:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (GlobalSentinel/1.0)",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.warning("Fetch failed %s: %s", url, exc)
        return None


class DeFiLlamaBridge:
    """Fetch DeFi ecosystem data from DeFi Llama APIs."""

    DISPLAY_NAME = "defi_llama"
    CATEGORY = "crypto_defi"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.output_path = self.repo_root / "data" / "quantum_feed" / "defi_data.json"

    def _get_historical_tvl(self) -> Dict[str, Any]:
        """Fetch total DeFi TVL history."""
        data = _fetch_json("https://api.llama.fi/v2/historicalChainTvl")
        if not data or not isinstance(data, list):
            return {"error": "no_data", "current_tvl": None, "trend": "unknown"}

        recent = data[-30:] if len(data) > 30 else data
        current_tvl = recent[-1].get("tvl", 0) if recent else 0
        tvl_7d_ago = recent[-7].get("tvl", 0) if len(recent) >= 7 else current_tvl
        tvl_30d_ago = recent[0].get("tvl", 0) if recent else current_tvl

        pct_7d = round(((current_tvl - tvl_7d_ago) / tvl_7d_ago) * 100, 2) if tvl_7d_ago > 0 else 0
        pct_30d = round(((current_tvl - tvl_30d_ago) / tvl_30d_ago) * 100, 2) if tvl_30d_ago > 0 else 0

        trend = "expanding" if pct_7d > 2 else "contracting" if pct_7d < -2 else "stable"

        return {
            "current_tvl_usd": current_tvl,
            "current_tvl_billions": round(current_tvl / 1e9, 2),
            "change_7d_pct": pct_7d,
            "change_30d_pct": pct_30d,
            "trend": trend,
            "signal": "risk_on" if pct_7d > 5 else "risk_off" if pct_7d < -5 else "neutral",
        }

    def _get_stablecoins(self) -> Dict[str, Any]:
        """Fetch stablecoin market cap and flows."""
        data = _fetch_json("https://stablecoins.llama.fi/stablecoins?includePrices=true")
        if not data or "peggedAssets" not in data:
            return {"error": "no_data"}

        stables = data["peggedAssets"]
        total_mcap = 0.0
        top_stables = []

        for stable in stables[:10]:
            name = stable.get("name", "Unknown")
            symbol = stable.get("symbol", "")
            chains = stable.get("chainCirculating", {})
            total_circ = 0.0
            for chain_data in chains.values():
                if isinstance(chain_data, dict):
                    total_circ += chain_data.get("current", {}).get("peggedUSD", 0)

            total_mcap += total_circ
            top_stables.append(
                {
                    "name": name,
                    "symbol": symbol,
                    "circulating_usd": round(total_circ, 0),
                    "circulating_billions": round(total_circ / 1e9, 2),
                }
            )

        return {
            "total_stablecoin_mcap_billions": round(total_mcap / 1e9, 2),
            "top_stablecoins": top_stables,
            "signal": "liquidity_expansion" if total_mcap > 150e9 else "normal",
        }

    def _get_yields(self) -> Dict[str, Any]:
        """Fetch DeFi yield data for yield compression analysis."""
        data = _fetch_json("https://yields.llama.fi/pools")
        if not data or "data" not in data:
            return {"error": "no_data"}

        pools = data["data"]
        significant_pools = [
            pool
            for pool in pools
            if pool.get("tvlUsd", 0) > 10_000_000
            and pool.get("apy", 0) > 0
            and pool.get("apy", 0) < 1000
        ]

        if not significant_pools:
            return {"error": "no_significant_pools"}

        apys = [pool["apy"] for pool in significant_pools]
        avg_apy = round(sum(apys) / len(apys), 2)
        median_apy = round(sorted(apys)[len(apys) // 2], 2)

        top_yield = sorted(
            significant_pools,
            key=lambda pool: pool.get("tvlUsd", 0),
            reverse=True,
        )[:10]
        top_protocols = [
            {
                "project": pool.get("project", ""),
                "chain": pool.get("chain", ""),
                "symbol": pool.get("symbol", ""),
                "apy": round(pool.get("apy", 0), 2),
                "tvl_millions": round(pool.get("tvlUsd", 0) / 1e6, 1),
            }
            for pool in top_yield
        ]

        yield_regime = "compressed" if median_apy < 3 else "elevated" if median_apy > 10 else "normal"

        return {
            "total_pools_analyzed": len(significant_pools),
            "avg_apy": avg_apy,
            "median_apy": median_apy,
            "yield_regime": yield_regime,
            "top_protocols_by_tvl": top_protocols,
            "signal": "risk_on" if median_apy > 8 else "risk_off" if median_apy < 2 else "neutral",
        }

    def _composite_signal(self, tvl: Dict[str, Any], stables: Dict[str, Any], yields: Dict[str, Any]) -> Dict[str, Any]:
        """Derive composite risk signal from DeFi data."""
        signals = []
        if tvl.get("signal") == "risk_on":
            signals.append(1)
        elif tvl.get("signal") == "risk_off":
            signals.append(-1)
        else:
            signals.append(0)

        if yields.get("signal") == "risk_on":
            signals.append(1)
        elif yields.get("signal") == "risk_off":
            signals.append(-1)
        else:
            signals.append(0)

        avg = sum(signals) / max(len(signals), 1)
        if avg > 0.3:
            composite = "risk_on"
        elif avg < -0.3:
            composite = "risk_off"
        else:
            composite = "neutral"

        return {
            "signal": composite,
            "score": round(avg, 2),
            "components": {
                "tvl_trend": tvl.get("signal", "unknown"),
                "yield_regime": yields.get("signal", "unknown"),
            },
        }

    def poll(self) -> Dict[str, Any]:
        """Poll DeFi Llama for ecosystem data."""
        tvl = self._get_historical_tvl()
        stables = self._get_stablecoins()
        yields = self._get_yields()

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "defi_llama",
            "data": {
                "tvl": tvl,
                "stablecoins": stables,
                "yields": yields,
                "composite_signal": self._composite_signal(tvl, stables, yields),
            },
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        logger.info(
            "[DeFiLlamaBridge] TVL: $%sB, Yield regime: %s",
            tvl.get("current_tvl_billions", "?"),
            yields.get("yield_regime", "?"),
        )

        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh the DeFi Llama bridge output.")
    parser.add_argument(
        "--repo-root",
        default=os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"),
        help="Global Sentinel repository root",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("GS_LOG_LEVEL", "INFO"),
        help="Python logging level",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    result = DeFiLlamaBridge(repo_root=args.repo_root).poll()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
