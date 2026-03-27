#!/usr/bin/env python3
"""Economic Surprise Index Bridge for Global Sentinel.

Tracks key economic releases (NFP, CPI, GDP, ISM PMI, Retail Sales, Housing Starts),
compares actual vs consensus expectations, and computes a rolling economic surprise index.

Positive index = economy beating expectations → bullish catalyst
Negative index = economy missing expectations → bearish catalyst
Strong surprises in either direction = market-moving events
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(REPO_ROOT / ".env")

from src.bridges.base_bridge import BaseBridge, utc_now_iso

FINNHUB_KEY = os.getenv("FINNHUB_API_KEY", os.getenv("FINNHUB_KEY", ""))
FMP_KEY = os.getenv("FMP_API_KEY", "")

OUTPUT_PATH = REPO_ROOT / "data" / "quantum_feed" / "economic_surprise.json"
HISTORY_PATH = REPO_ROOT / "data" / "cache" / "economic_releases_history.json"

# ── Economic indicator definitions ─────────────────────────────────────
INDICATORS = {
    "nonfarm_payrolls": {
        "name": "Nonfarm Payrolls",
        "frequency": "monthly",
        "importance": "high",
        "weight": 2.0,
        "finnhub_event": "NFP",
        "description": "Monthly change in non-farm employment",
    },
    "cpi_yoy": {
        "name": "CPI Year-over-Year",
        "frequency": "monthly",
        "importance": "high",
        "weight": 2.0,
        "description": "Consumer Price Index annual change",
    },
    "gdp_qoq": {
        "name": "GDP Quarter-over-Quarter",
        "frequency": "quarterly",
        "importance": "high",
        "weight": 1.5,
        "description": "Real GDP quarterly annualized growth rate",
    },
    "ism_manufacturing": {
        "name": "ISM Manufacturing PMI",
        "frequency": "monthly",
        "importance": "high",
        "weight": 1.5,
        "description": "ISM Manufacturing Purchasing Managers Index",
    },
    "retail_sales_mom": {
        "name": "Retail Sales Month-over-Month",
        "frequency": "monthly",
        "importance": "medium",
        "weight": 1.0,
        "description": "Monthly change in retail sales",
    },
    "housing_starts": {
        "name": "Housing Starts",
        "frequency": "monthly",
        "importance": "medium",
        "weight": 1.0,
        "description": "New residential construction starts (thousands)",
    },
}


FRED_API_KEY = os.getenv("FRED_API_KEY", "")

# FRED series for each indicator (actual values)
FRED_SERIES = {
    "nonfarm_payrolls": "PAYEMS",        # All Employees, Total Nonfarm
    "cpi_yoy": "CPIAUCSL",              # CPI for All Urban Consumers
    "gdp_qoq": "GDP",                    # Gross Domestic Product
    "ism_manufacturing": "MANEMP",        # Manufacturing Employment (proxy)
    "retail_sales_mom": "RSAFS",          # Advance Retail Sales
    "housing_starts": "HOUST",            # Housing Starts
}


def fetch_fred_series(series_id: str, observation_start: str, observation_end: str) -> List[Dict]:
    """Fetch observations from FRED API."""
    api_key = FRED_API_KEY
    if not api_key:
        return []
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": observation_start,
        "observation_end": observation_end,
        "sort_order": "desc",
        "limit": "12",
    }
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("observations", [])
    except Exception as e:
        logger.warning(f"FRED fetch for {series_id} failed: {e}")
        return []


def seed_from_fred() -> List[Dict[str, Any]]:
    """Seed economic release history from FRED data when calendar APIs return nothing."""
    if not FRED_API_KEY:
        logger.warning("FRED_API_KEY not set, cannot seed economic data")
        return []

    releases = []
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")

    for indicator_key, series_id in FRED_SERIES.items():
        obs = fetch_fred_series(series_id, start_date, end_date)
        if len(obs) < 2:
            continue

        # Use consecutive observations to compute surprise (actual vs prior as estimate proxy)
        valid_obs = [(o["date"], float(o["value"])) for o in obs if o.get("value", ".") != "."]
        if len(valid_obs) < 2:
            continue

        for i in range(len(valid_obs) - 1):
            date_str, actual = valid_obs[i]
            _, prior = valid_obs[i + 1]

            # Use prior value as naive "consensus estimate"
            estimate = prior
            surprise = _compute_surprise_score(actual, estimate, indicator_key)

            releases.append({
                "date": date_str,
                "indicator": indicator_key,
                "indicator_name": INDICATORS[indicator_key]["name"],
                "event_raw": f"FRED:{series_id}",
                **surprise,
            })

    logger.info(f"Seeded {len(releases)} economic releases from FRED")
    return releases


def fetch_economic_calendar_finnhub(from_date: str, to_date: str) -> List[Dict[str, Any]]:
    """Fetch economic calendar from Finnhub."""
    if not FINNHUB_KEY:
        logger.warning("FINNHUB_API_KEY not set")
        return []

    url = "https://finnhub.io/api/v1/calendar/economic"
    params = {"from": from_date, "to": to_date, "token": FINNHUB_KEY}
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("economicCalendar", [])
    except Exception as e:
        logger.error(f"Finnhub economic calendar error: {e}")
        return []


def fetch_economic_calendar_fmp(from_date: str, to_date: str) -> List[Dict[str, Any]]:
    """Fetch economic calendar from Financial Modeling Prep as fallback."""
    if not FMP_KEY:
        return []
    url = f"https://financialmodelingprep.com/api/v3/economic_calendar"
    params = {"from": from_date, "to": to_date, "apikey": FMP_KEY}
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"FMP economic calendar error: {e}")
        return []


def _classify_event(event_name: str) -> Optional[str]:
    """Map a calendar event name to our tracked indicator key."""
    name_lower = event_name.lower() if event_name else ""

    mappings = [
        ("nonfarm_payrolls", ["nonfarm payroll", "non-farm payroll", "nfp", "employment change",
                              "payrolls", "jobs report", "employment situation"]),
        ("cpi_yoy", ["consumer price index", "cpi y/y", "cpi yoy", "cpi annual", "cpi m/m",
                      "cpi mom", "core cpi", "inflation rate"]),
        ("gdp_qoq", ["gdp q/q", "gdp qoq", "gross domestic product", "gdp growth",
                      "gdp annualized", "gdp preliminary", "gdp advance", "gdp final"]),
        ("ism_manufacturing", ["ism manufacturing", "ism pmi", "manufacturing pmi",
                                "ism non-manufacturing", "ism services", "pmi composite"]),
        ("retail_sales_mom", ["retail sales m/m", "retail sales mom", "core retail",
                              "retail sales", "advance retail"]),
        ("housing_starts", ["housing starts", "building permits", "new home sales",
                            "existing home sales", "pending home sales", "housing"]),
    ]

    for key, patterns in mappings:
        for pattern in patterns:
            if pattern in name_lower:
                return key
    return None


def _compute_surprise_score(actual: float, estimate: float, indicator_key: str) -> Dict[str, Any]:
    """Compute normalized surprise score for an economic release."""
    if estimate == 0:
        raw_surprise = actual - estimate
        normalized = raw_surprise
    else:
        raw_surprise = actual - estimate
        normalized = raw_surprise / abs(estimate)

    weight = INDICATORS.get(indicator_key, {}).get("weight", 1.0)
    weighted_score = normalized * weight

    # Classify magnitude
    abs_norm = abs(normalized)
    if abs_norm > 0.10:
        magnitude = "major_surprise"
    elif abs_norm > 0.05:
        magnitude = "moderate_surprise"
    elif abs_norm > 0.02:
        magnitude = "mild_surprise"
    else:
        magnitude = "in_line"

    direction = "beat" if raw_surprise > 0 else "miss" if raw_surprise < 0 else "inline"

    return {
        "actual": actual,
        "estimate": estimate,
        "raw_surprise": round(raw_surprise, 4),
        "normalized_surprise": round(normalized, 4),
        "weighted_score": round(weighted_score, 4),
        "magnitude": magnitude,
        "direction": direction,
    }


def load_release_history() -> List[Dict[str, Any]]:
    """Load previous release history from cache."""
    if HISTORY_PATH.exists():
        try:
            return json.loads(HISTORY_PATH.read_text())
        except Exception:
            pass
    return []


def save_release_history(history: List[Dict[str, Any]]) -> None:
    """Persist release history."""
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Keep last 365 days of releases
    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    history = [r for r in history if r.get("date", "") >= cutoff[:10]]
    HISTORY_PATH.write_text(json.dumps(history, indent=2, default=str))


def compute_rolling_surprise_index(history: List[Dict[str, Any]], window_days: int = 90) -> Dict[str, Any]:
    """Compute rolling economic surprise index from release history."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    recent = [r for r in history if r.get("date", "") >= cutoff_str and r.get("weighted_score") is not None]

    if not recent:
        return {
            "index_value": 0.0,
            "num_releases": 0,
            "window_days": window_days,
            "interpretation": "No recent data",
            "trend": "stable",
            "beats": 0,
            "misses": 0,
            "inline": 0,
            "std_deviation": 0.0,
        }

    scores = [r["weighted_score"] for r in recent]
    index_value = np.mean(scores)
    std_dev = np.std(scores) if len(scores) > 1 else 0

    # Interpretation
    if index_value > 0.05:
        interpretation = "Economy significantly beating expectations — bullish macro"
    elif index_value > 0.02:
        interpretation = "Economy modestly beating expectations — mildly bullish"
    elif index_value > -0.02:
        interpretation = "Economy roughly in line with expectations — neutral"
    elif index_value > -0.05:
        interpretation = "Economy modestly missing expectations — mildly bearish"
    else:
        interpretation = "Economy significantly missing expectations — bearish macro"

    # Trend: compare last 30d vs prior 60d
    cutoff_30 = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
    last_30 = [r["weighted_score"] for r in recent if r.get("date", "") >= cutoff_30]
    prior = [r["weighted_score"] for r in recent if r.get("date", "") < cutoff_30]

    trend = "stable"
    if last_30 and prior:
        if np.mean(last_30) > np.mean(prior) + 0.02:
            trend = "improving"
        elif np.mean(last_30) < np.mean(prior) - 0.02:
            trend = "deteriorating"

    return {
        "index_value": round(float(index_value), 4),
        "std_deviation": round(float(std_dev), 4),
        "num_releases": len(recent),
        "window_days": window_days,
        "trend": trend,
        "interpretation": interpretation,
        "beats": sum(1 for r in recent if r.get("direction") == "beat"),
        "misses": sum(1 for r in recent if r.get("direction") == "miss"),
        "inline": sum(1 for r in recent if r.get("direction") == "inline"),
    }


class EconomicSurpriseBridge(BaseBridge):
    """Bridge that provides economic surprise index data to the system."""

    source = "economic_surprise"
    source_tier = "tier_1_official"
    trust_weight = 0.9
    freshness_ttl_minutes = 360  # 6 hours

    def fetch(self) -> Dict[str, Any]:
        try:
            result = run()
            return self._mark_success({
                "source": self.source,
                "source_tier": self.source_tier,
                "trust_weight": self.trust_weight,
                "timestamp_utc": utc_now_iso(),
                "fresh": True,
                "data": result,
            })
        except Exception as e:
            return self._mark_failure(e)


def run() -> Dict[str, Any]:
    """Fetch economic calendar, compute surprises, update rolling index."""
    timestamp = datetime.now(timezone.utc).isoformat()

    # Fetch last 90 days of economic calendar
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    events = fetch_economic_calendar_finnhub(start_date, end_date)
    if not events:
        events = fetch_economic_calendar_fmp(start_date, end_date)

    # Load existing history
    history = load_release_history()
    existing_keys = {(r.get("date"), r.get("indicator")) for r in history}

    # Process new events
    new_releases = []
    for ev in events:
        # Finnhub format
        event_name = ev.get("event", ev.get("eventName", ""))
        actual = ev.get("actual")
        estimate = ev.get("estimate", ev.get("consensus"))
        date = ev.get("date", ev.get("eventDate", ""))
        country = ev.get("country", ev.get("countryCode", ""))

        # Only US data
        if country and country.upper() not in ("US", "USA"):
            continue

        if actual is None or estimate is None:
            continue

        try:
            actual = float(actual)
            estimate = float(estimate)
        except (ValueError, TypeError):
            continue

        indicator_key = _classify_event(event_name)
        if indicator_key is None:
            continue

        if (date, indicator_key) in existing_keys:
            continue

        surprise = _compute_surprise_score(actual, estimate, indicator_key)

        release = {
            "date": date,
            "indicator": indicator_key,
            "indicator_name": INDICATORS[indicator_key]["name"],
            "event_raw": event_name,
            **surprise,
        }

        new_releases.append(release)
        history.append(release)
        existing_keys.add((date, indicator_key))

    # If no data from calendars, seed from FRED
    if not history:
        logger.info("No calendar data found, seeding from FRED...")
        fred_releases = seed_from_fred()
        history.extend(fred_releases)
        new_releases.extend(fred_releases)

    # Save updated history
    save_release_history(history)

    # Compute rolling indices at different windows
    surprise_30d = compute_rolling_surprise_index(history, 30)
    surprise_90d = compute_rolling_surprise_index(history, 90)

    # Recent major surprises
    recent_major = [
        r for r in history
        if r.get("magnitude") in ("major_surprise", "moderate_surprise")
        and r.get("date", "") >= (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    ]
    recent_major.sort(key=lambda x: abs(x.get("weighted_score", 0)), reverse=True)

    # Per-indicator summary
    indicator_summary = {}
    for key, info in INDICATORS.items():
        ind_releases = [r for r in history if r.get("indicator") == key]
        if ind_releases:
            last = max(ind_releases, key=lambda x: x.get("date", ""))
            recent_scores = [
                r["weighted_score"] for r in ind_releases
                if r.get("date", "") >= (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
            ]
            indicator_summary[key] = {
                "name": info["name"],
                "last_release_date": last.get("date"),
                "last_actual": last.get("actual"),
                "last_estimate": last.get("estimate"),
                "last_direction": last.get("direction"),
                "last_magnitude": last.get("magnitude"),
                "avg_surprise_6m": round(float(np.mean(recent_scores)), 4) if recent_scores else None,
                "num_releases_6m": len(recent_scores),
            }

    output = {
        "source": "economic_surprise",
        "timestamp_utc": timestamp,
        "rolling_surprise_index_30d": surprise_30d,
        "rolling_surprise_index_90d": surprise_90d,
        "recent_major_surprises": recent_major[:10],
        "new_releases_found": len(new_releases),
        "total_history_entries": len(history),
        "indicator_summary": indicator_summary,
        "signal": {
            "direction": "bullish" if surprise_30d["index_value"] > 0.02 else "bearish" if surprise_30d["index_value"] < -0.02 else "neutral",
            "strength": abs(surprise_30d["index_value"]),
            "trend": surprise_30d["trend"],
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, default=str))
    logger.info(f"Economic surprise index written to {OUTPUT_PATH}")
    return output


if __name__ == "__main__":
    results = run()
    idx_30 = results.get("rolling_surprise_index_30d", {})
    idx_90 = results.get("rolling_surprise_index_90d", {})
    logger.info(f"30-day surprise index: {idx_30.get('index_value', 0):.4f} ({idx_30.get('interpretation', '')})")
    logger.info(f"90-day surprise index: {idx_90.get('index_value', 0):.4f} ({idx_90.get('interpretation', '')})")
    logger.info(f"Signal: {results.get('signal', {})}")
    print(json.dumps(results, indent=2, default=str)[:500])
