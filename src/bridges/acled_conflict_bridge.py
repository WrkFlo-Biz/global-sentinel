#!/usr/bin/env python3
"""ACLED Conflict Data Bridge — real-time political violence events."""
import json
import logging
import os
import time
import datetime
import urllib.request

logger = logging.getLogger(__name__)

ACLED_BASE = os.getenv(
    "ACLED_API_URL",
    "https://api.acleddata.com/acled/read",
)
ACLED_KEY = os.getenv("ACLED_API_KEY", "")
ACLED_EMAIL = os.getenv("ACLED_EMAIL", "")
REGIONS = {
    "middle_east": ["Iran", "Iraq", "Syria", "Yemen", "Israel", "Lebanon", "Saudi Arabia"],
    "europe_east": ["Russia", "Ukraine"],
    "asia_pacific": ["China", "Taiwan"],
}

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def poll():
    results = {"timestamp": iso_now(), "regions": {}, "total_events": 0, "total_fatalities": 0}
    if not ACLED_KEY:
        logger.warning("ACLED_API_KEY not set — returning empty conflict data")
        results["status"] = "degraded"
        results["reason"] = "acled_api_key_missing"
        for region_name in REGIONS:
            results["regions"][region_name] = {
                "events": 0, "fatalities": 0, "battles": 0,
                "explosions": 0, "protests": 0, "conflict_intensity": 0.0,
                "countries": {},
            }
        return results

    end = datetime.date.today().isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    for region_name, countries in REGIONS.items():
        region_data = {"events": 0, "fatalities": 0, "battles": 0, "explosions": 0, "protests": 0, "countries": {}}
        for country in countries:
            try:
                url = (
                    f"{ACLED_BASE}?key={ACLED_KEY}&email={ACLED_EMAIL}"
                    f"&event_date={start}|{end}&event_date_where=BETWEEN"
                    f"&country={country}&limit=100"
                )
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
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
