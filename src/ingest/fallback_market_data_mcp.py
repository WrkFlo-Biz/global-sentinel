#!/usr/bin/env python3
"""Global Sentinel V4 — Fallback Market Data MCP

Public/free market data sources as fallback when primary APIs are unavailable.
Uses FRED (Federal Reserve Economic Data) and Yahoo Finance.
Lower confidence than premium sources.
"""

import json
import os
import sys
from datetime import datetime, timezone

try:
    import requests
except ImportError:
    requests = None

FRED_API_KEY = os.getenv("FRED_API_KEY", "")

FRED_SERIES = {
    "vix": "VIXCLS",
    "treasury_10y": "DGS10",
    "treasury_2y": "DGS2",
    "fed_funds": "FEDFUNDS",
    "crude_oil": "DCOILWTICO",
    "gold": "GOLDAMGBD228NLBM",
    "unemployment": "UNRATE",
    "cpi": "CPIAUCSL",
}


class FallbackMarketDataIngester:
    """Fallback market data from FRED and public sources."""

    def fetch_fred(self, series_id: str, limit: int = 5) -> dict:
        if not requests or not FRED_API_KEY:
            return {"error": "FRED API unavailable", "fresh": False}

        url = "https://api.stlouisfed.org/fred/series/observations"
        params = {
            "series_id": series_id,
            "api_key": FRED_API_KEY,
            "file_type": "json",
            "sort_order": "desc",
            "limit": limit,
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            observations = data.get("observations", [])
            return {
                "series_id": series_id,
                "observations": observations,
                "fresh": len(observations) > 0,
                "source": "FRED",
            }
        except Exception as e:
            return {"error": str(e), "series_id": series_id, "fresh": False}

    def fetch_all(self) -> dict:
        results = {}
        for name, series_id in FRED_SERIES.items():
            results[name] = self.fetch_fred(series_id)

        fresh_count = sum(1 for v in results.values() if v.get("fresh", False))
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": results,
            "fresh_count": fresh_count,
            "total_series": len(FRED_SERIES),
            "is_fallback": True,
        }


def serve_mcp():
    ingester = FallbackMarketDataIngester()
    for line in sys.stdin:
        try:
            request = json.loads(line.strip())
            method = request.get("method", "")
            if method == "fetch_all":
                result = ingester.fetch_all()
            elif method == "fetch":
                series = request.get("params", {}).get("series_id", "VIXCLS")
                result = ingester.fetch_fred(series)
            elif method == "status":
                result = {"status": "ok", "service": "fallback-market-data-mcp", "has_api_key": bool(FRED_API_KEY)}
            else:
                result = {"error": f"Unknown method: {method}"}
            print(json.dumps({"id": request.get("id"), "result": result}), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)


if __name__ == "__main__":
    serve_mcp()
