#!/usr/bin/env python3
"""ACLED Conflict Data Bridge — real-time political violence events."""
import json, os, time, datetime, urllib.request

ACLED_BASE = "https://api.acleddata.com/acled/read"
REGIONS = {
    "middle_east": ["Iran", "Iraq", "Syria", "Yemen", "Israel", "Lebanon", "Saudi Arabia"],
    "europe_east": ["Russia", "Ukraine"],
    "asia_pacific": ["China", "Taiwan"],
}

def iso_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def poll():
    results = {"timestamp": iso_now(), "regions": {}, "total_events": 0, "total_fatalities": 0}
    end = datetime.date.today().isoformat()
    start = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()

    for region_name, countries in REGIONS.items():
        region_data = {"events": 0, "fatalities": 0, "battles": 0, "explosions": 0, "protests": 0, "countries": {}}
        for country in countries:
            try:
                url = f"{ACLED_BASE}?event_date={start}|{end}&event_date_where=BETWEEN&country={country}&limit=100"
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
