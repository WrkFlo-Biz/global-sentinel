from __future__ import annotations

import os
import urllib.request
import json
from typing import Any, Dict, List

from src.packets.physical_flow_event import make_physical_flow_event


EIA_API_KEY = os.environ.get("EIA_API_KEY", "")
EIA_BASE = "https://api.eia.gov/v2"


class EIABridge:
    source = "eia"
    source_tier = "tier_1_official"
    trust_weight = 1.0

    def fetch(self) -> List[Dict[str, Any]]:
        return self.fetch_petroleum_inventory()

    def fetch_petroleum_inventory(self) -> List[Dict[str, Any]]:
        if not EIA_API_KEY:
            return []
        url = (
            f"{EIA_BASE}/petroleum/stoc/wstk/data/"
            f"?api_key={EIA_API_KEY}"
            f"&frequency=weekly&data[0]=value"
            f"&sort[0][column]=period&sort[0][direction]=desc"
            f"&offset=0&length=1"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "GlobalSentinel/5.1"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            return []
        rows = (data.get("response") or {}).get("data") or []

        out: List[Dict[str, Any]] = []
        for row in rows:
            value = float(row.get("value", 0.0))
            pkt = make_physical_flow_event(
                source=self.source,
                source_tier=self.source_tier,
                trust_weight=self.trust_weight,
                region="US",
                flow_type="petroleum_inventory",
                disruption_score=0.0,
                measured_value=value,
                unit="barrels",
                related_assets=["CL", "USO", "XLE", "XOM", "CVX"],
                summary=f"Weekly EIA petroleum inventory value: {value}",
                confidence=0.99,
                provenance={"period": row.get("period"), "series_name": row.get("series-name")},
            )
            out.append(pkt.to_dict())
        return out
