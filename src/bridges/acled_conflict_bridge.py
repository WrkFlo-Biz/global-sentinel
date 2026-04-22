#!/usr/bin/env python3
"""ACLED Conflict Data Bridge — real-time political violence events."""
import json
import logging
import os
import time
import datetime

import requests

logger = logging.getLogger(__name__)

ACLED_TOKEN_URL = "https://acleddata.com/oauth/token"
ACLED_API_URL = "https://acleddata.com/api/acled/read"

ACLED_EMAIL = os.getenv("ACLED_EMAIL", "")
ACLED_PASSWORD = os.getenv("ACLED_PASSWORD", "")

# Module-level token cache: avoids re-authenticating every poll cycle (tokens last 24h).
_token_cache = {"access_token": None, "expires_at": 0.0}
REGIONS = {
    "middle_east": ["Iran", "Iraq", "Syria", "Yemen", "Israel", "Lebanon", "Saudi Arabia"],
    "europe_east": ["Russia", "Ukraine"],
    "asia_pacific": ["China", "Taiwan"],
}

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def _get_access_token():
    """Obtain an OAuth access token, returning cached value if still valid."""
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    try:
        resp = requests.post(
            ACLED_TOKEN_URL,
            data={
                "username": ACLED_EMAIL,
                "password": ACLED_PASSWORD,
                "grant_type": "password",
                "client_id": "acled",
            },
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        token = payload["access_token"]
        # Tokens last 24h; refresh 1h early to be safe.
        expires_in = int(payload.get("expires_in", 86400))
        _token_cache["access_token"] = token
        _token_cache["expires_at"] = now + expires_in - 3600
        logger.info("ACLED OAuth token acquired (expires_in=%ss)", expires_in)
        return token
    except Exception as exc:
        logger.error("ACLED OAuth token request failed: %s", exc)
        _token_cache["access_token"] = None
        _token_cache["expires_at"] = 0.0
        return None


def _degraded_results(reason):
    """Return a degraded-mode result set with zeroed region data."""
    results = {"timestamp": iso_now(), "regions": {}, "total_events": 0, "total_fatalities": 0}
    results["status"] = "degraded"
    results["reason"] = reason
    for region_name in REGIONS:
        results["regions"][region_name] = {
            "events": 0, "fatalities": 0, "battles": 0,
            "explosions": 0, "protests": 0, "conflict_intensity": 0.0,
            "countries": {},
        }
    return results


def poll():
    results = {"timestamp": iso_now(), "regions": {}, "total_events": 0, "total_fatalities": 0}

    if not ACLED_EMAIL or not ACLED_PASSWORD:
        logger.warning("ACLED_EMAIL / ACLED_PASSWORD not set — returning empty conflict data")
        return _degraded_results("acled_credentials_missing")

    token = _get_access_token()
    if not token:
        logger.warning("ACLED OAuth failed — returning degraded conflict data")
        return _degraded_results("acled_auth_failed")

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    end = datetime.date.today().isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    for region_name, countries in REGIONS.items():
        region_data = {"events": 0, "fatalities": 0, "battles": 0, "explosions": 0, "protests": 0, "countries": {}}
        for country in countries:
            try:
                resp = requests.get(
                    ACLED_API_URL,
                    headers=headers,
                    params={
                        "event_date": f"{start}|{end}",
                        "event_date_where": "BETWEEN",
                        "country": country,
                        "limit": 100,
                    },
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                events = data.get("data", [])
                fatalities = sum(int(e.get("fatalities", 0)) for e in events)
                battles = sum(1 for e in events if "battle" in e.get("event_type", "").lower())
                explosions = sum(1 for e in events if "explosion" in e.get("event_type", "").lower())
                protests = sum(1 for e in events if "protest" in e.get("event_type", "").lower())
                region_data["events"] += len(events)
                region_data["fatalities"] += fatalities
                region_data["battles"] += battles
                region_data["explosions"] += explosions
                region_data["protests"] += protests
                region_data["countries"][country] = {"events": len(events), "fatalities": fatalities}
            except Exception as e:
                region_data["countries"][country] = {"error": str(e)[:100]}
            time.sleep(0.5)

        intensity = min(10, region_data["events"] / 10 + region_data["fatalities"] / 50)
        region_data["conflict_intensity"] = round(intensity, 2)
        results["regions"][region_name] = region_data
        results["total_events"] += region_data["events"]
        results["total_fatalities"] += region_data["fatalities"]

    return results

if __name__ == "__main__":
    print(json.dumps(poll(), indent=2))
