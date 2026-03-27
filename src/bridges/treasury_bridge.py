#!/usr/bin/env python3
"""
Global Sentinel — Treasury Fiscal Data Bridge

Fetches US Treasury fiscal data: average interest rates by security type,
total federal debt, and daily Treasury yield curve.

No API key needed — uses api.fiscaldata.treasury.gov.

Output: data/quantum_feed/treasury_fiscal.json
Tier 1, trust 1.0, TTL 1440 min
"""
from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.treasury_bridge")

_BASE_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service"


def _fetch_json(url: str, timeout: int = 20) -> Optional[dict]:
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (GlobalSentinel/1.0)",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        logger.warning("Fetch failed %s: %s", url, exc)
        return None


class TreasuryFiscalBridge:
    """Fetch US Treasury fiscal data for macro analysis."""

    DISPLAY_NAME = "treasury_fiscal"
    CATEGORY = "macro_official"

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        self.output_path = self.repo_root / "data" / "quantum_feed" / "treasury_fiscal.json"
        self.fred_key = os.getenv("FRED_API_KEY", "")

    def _get_avg_interest_rates(self) -> Dict[str, Any]:
        """Fetch average interest rates on Treasury securities."""
        url = (
            f"{_BASE_URL}/v2/accounting/od/avg_interest_rates"
            "?sort=-record_date&page[size]=50"
        )
        data = _fetch_json(url)
        if not data or "data" not in data:
            return {"error": "no_data"}

        records = data["data"]
        if not records:
            return {"error": "empty_response"}

        # Group by security type for latest date
        latest_date = records[0].get("record_date", "")
        by_type = {}
        for rec in records:
            if rec.get("record_date") != latest_date:
                continue
            sec_type = rec.get("security_type_desc", "Unknown")
            avg_rate = rec.get("avg_interest_rate_amt", "0")
            try:
                rate = float(avg_rate)
            except (ValueError, TypeError):
                rate = 0.0
            by_type[sec_type] = rate

        return {
            "record_date": latest_date,
            "rates_by_type": by_type,
            "summary": {
                "treasury_bills_rate": by_type.get("Treasury Bills", 0),
                "treasury_notes_rate": by_type.get("Treasury Notes", 0),
                "treasury_bonds_rate": by_type.get("Treasury Bonds", 0),
                "tips_rate": by_type.get("Treasury Inflation-Protected Securities", 0),
            },
        }

    def _get_total_debt(self) -> Dict[str, Any]:
        """Fetch total federal debt outstanding."""
        url = (
            f"{_BASE_URL}/v2/accounting/od/debt_to_penny"
            "?sort=-record_date&page[size]=10"
        )
        data = _fetch_json(url)
        if not data or "data" not in data:
            return {"error": "no_data"}

        records = data["data"]
        if not records:
            return {"error": "empty_response"}

        latest = records[0]
        prev = records[1] if len(records) > 1 else None

        total_debt = float(latest.get("tot_pub_debt_out_amt", 0))
        debt_held_public = float(latest.get("debt_held_public_amt", 0))
        intragov_holdings = float(latest.get("intragov_hold_amt", 0))

        result = {
            "record_date": latest.get("record_date", ""),
            "total_public_debt": total_debt,
            "total_debt_trillions": round(total_debt / 1e12, 3),
            "debt_held_by_public": debt_held_public,
            "intragovernmental_holdings": intragov_holdings,
        }

        if prev:
            prev_debt = float(prev.get("tot_pub_debt_out_amt", 0))
            if prev_debt > 0:
                change = total_debt - prev_debt
                result["debt_change"] = change
                result["debt_change_billions"] = round(change / 1e9, 2)
                result["previous_date"] = prev.get("record_date", "")

        return result

    def _get_treasury_yields(self) -> Dict[str, Any]:
        """Fetch daily Treasury yield curve from FRED."""
        if not self.fred_key:
            return {"error": "no_fred_api_key", "note": "Set FRED_API_KEY for yield curve data"}

        series_map = {
            "1m": "DGS1MO",
            "3m": "DGS3MO",
            "6m": "DGS6MO",
            "1y": "DGS1",
            "2y": "DGS2",
            "5y": "DGS5",
            "10y": "DGS10",
            "30y": "DGS30",
        }

        yields = {}
        for tenor, series_id in series_map.items():
            url = (
                f"https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={series_id}&sort_order=desc&limit=1"
                f"&api_key={self.fred_key}&file_type=json"
            )
            data = _fetch_json(url)
            if data and data.get("observations"):
                val = data["observations"][0].get("value", ".")
                yields[tenor] = float(val) if val != "." else None
            else:
                yields[tenor] = None

        # Compute spreads
        spreads = {}
        y2 = yields.get("2y")
        y10 = yields.get("10y")
        y3m = yields.get("3m")
        y30 = yields.get("30y")

        if y10 is not None and y2 is not None:
            spread_2s10s = round(y10 - y2, 3)
            spreads["2s10s"] = spread_2s10s
            spreads["2s10s_signal"] = "inverted" if spread_2s10s < 0 else "steepening" if spread_2s10s > 0.5 else "flat"

        if y10 is not None and y3m is not None:
            spread_3m10y = round(y10 - y3m, 3)
            spreads["3m10y"] = spread_3m10y
            spreads["3m10y_signal"] = "inverted" if spread_3m10y < 0 else "steepening" if spread_3m10y > 0.5 else "flat"

        if y30 is not None and y2 is not None:
            spreads["2s30s"] = round(y30 - y2, 3)

        return {
            "yields": yields,
            "spreads": spreads,
            "curve_shape": "inverted" if spreads.get("2s10s", 1) < 0 else "normal",
        }

    def poll(self) -> Dict[str, Any]:
        """Poll Treasury fiscal data APIs."""
        rates = self._get_avg_interest_rates()
        debt = self._get_total_debt()
        yields = self._get_treasury_yields()

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "treasury_fiscal",
            "data": {
                "avg_interest_rates": rates,
                "total_debt": debt,
                "yield_curve": yields,
                "macro_signal": self._macro_signal(rates, debt, yields),
            },
        }

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(json.dumps(result, indent=2))
        logger.info(
            f"[TreasuryFiscalBridge] Debt: ${debt.get('total_debt_trillions', '?')}T, "
            f"Curve: {yields.get('curve_shape', '?')}"
        )

        return result

    def _macro_signal(self, rates: Dict, debt: Dict, yields: Dict) -> Dict[str, Any]:
        """Derive macro signal from Treasury data."""
        signals = []

        # Yield curve inversion = recession warning
        curve = yields.get("curve_shape", "normal")
        if curve == "inverted":
            signals.append({"indicator": "yield_curve", "signal": "recession_warning", "score": -1})
        else:
            signals.append({"indicator": "yield_curve", "signal": "normal", "score": 0})

        # Debt trajectory
        debt_change_b = debt.get("debt_change_billions", 0)
        if debt_change_b and abs(debt_change_b) > 50:
            signals.append({"indicator": "debt_growth", "signal": "rapid_expansion", "score": -0.5})
        else:
            signals.append({"indicator": "debt_growth", "signal": "normal", "score": 0})

        avg_score = sum(s["score"] for s in signals) / max(len(signals), 1)
        overall = "hawkish" if avg_score < -0.3 else "dovish" if avg_score > 0.3 else "neutral"

        return {
            "overall_signal": overall,
            "score": round(avg_score, 2),
            "components": signals,
        }
