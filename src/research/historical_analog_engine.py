from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


# Tagged historical scenarios for regime similarity matching
ANALOG_LIBRARY = [
    {
        "label": "Gulf War oil shock (1990-91)",
        "tags": ["oil_shock", "geopolitical", "middle_east", "energy"],
        "regime_markers": {"energy_disruption": 0.9, "flight_to_quality": 0.7, "vol_spike": 0.8},
        "asset_impacts": {"CL": "sharp_up", "GLD": "up", "SPX": "down", "UST10Y": "down"},
    },
    {
        "label": "Red Sea / Houthi shipping disruption (2024)",
        "tags": ["shipping", "middle_east", "supply_chain", "energy"],
        "regime_markers": {"energy_disruption": 0.6, "supply_chain_stress": 0.8, "vol_spike": 0.4},
        "asset_impacts": {"CL": "moderate_up", "shipping_rates": "sharp_up", "XLE": "up"},
    },
    {
        "label": "Strait of Hormuz tension escalation",
        "tags": ["oil_shock", "geopolitical", "middle_east", "energy", "hormuz"],
        "regime_markers": {"energy_disruption": 0.95, "flight_to_quality": 0.8, "vol_spike": 0.9},
        "asset_impacts": {"CL": "sharp_up", "GLD": "up", "SPX": "sharp_down", "DXY": "up"},
    },
    {
        "label": "Volcker inflation scare (1980-82)",
        "tags": ["inflation", "fed_hawkish", "rates", "recession"],
        "regime_markers": {"hawkish_fed": 0.95, "inflation_stress": 0.9, "recession_risk": 0.7},
        "asset_impacts": {"UST10Y": "sharp_up", "SPX": "down", "GLD": "volatile", "DXY": "up"},
    },
    {
        "label": "Fed pause-to-cut transition (2019)",
        "tags": ["fed_dovish", "rates", "easing"],
        "regime_markers": {"hawkish_fed": -0.5, "growth_concern": 0.5, "vol_spike": 0.2},
        "asset_impacts": {"UST10Y": "down", "SPX": "up", "GLD": "up", "XLF": "mixed"},
    },
    {
        "label": "Tariff escalation (US-China 2018-19)",
        "tags": ["tariff", "trade_war", "china", "supply_chain"],
        "regime_markers": {"trade_stress": 0.8, "supply_chain_stress": 0.6, "vol_spike": 0.5},
        "asset_impacts": {"SPX": "down", "EEM": "sharp_down", "DXY": "up", "GLD": "up"},
    },
    {
        "label": "Banking stress / SVB (2023)",
        "tags": ["banking_stress", "flight_to_quality", "contagion"],
        "regime_markers": {"banking_stress": 0.9, "flight_to_quality": 0.85, "vol_spike": 0.7},
        "asset_impacts": {"XLF": "sharp_down", "UST10Y": "sharp_down", "GLD": "up", "SPX": "down"},
    },
    {
        "label": "Labor shock + tariff combo (2025-26)",
        "tags": ["tariff", "labor", "inflation", "supply_chain"],
        "regime_markers": {"trade_stress": 0.7, "inflation_stress": 0.6, "labor_tightness": 0.7},
        "asset_impacts": {"SPX": "volatile", "XLI": "down", "CL": "up", "DXY": "mixed"},
    },
]


class HistoricalAnalogEngine:
    """Match current regime state against tagged historical scenarios."""

    def __init__(
        self,
        custom_library: Optional[List[Dict[str, Any]]] = None,
        repo_root: Optional[Path] = None,
    ):
        self.library = list(custom_library or ANALOG_LIBRARY)
        # Auto-load crisis analog library if available
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]
        crisis_path = repo_root / "config" / "crisis_analog_library.json"
        if crisis_path.exists():
            try:
                crisis_entries = json.loads(crisis_path.read_text(encoding="utf-8"))
                if isinstance(crisis_entries, list):
                    existing_ids = {
                        a.get("source_event_id") or a.get("label") for a in self.library
                    }
                    for entry in crisis_entries:
                        eid = entry.get("source_event_id") or entry.get("label")
                        if eid not in existing_ids:
                            self.library.append(entry)
                            existing_ids.add(eid)
            except Exception:
                pass  # never block on crisis library load failure

    def find_matches(
        self,
        regime_state: Dict[str, Any],
        top_n: int = 5,
        min_similarity: float = 0.3,
    ) -> List[Dict[str, Any]]:
        scored = []
        for analog in self.library:
            sim = self._compute_similarity(regime_state, analog)
            if sim >= min_similarity:
                match: Dict[str, Any] = {
                    "label": analog["label"],
                    "similarity": round(sim, 3),
                    "tags": analog.get("tags", []),
                    "asset_impacts": analog.get("asset_impacts", {}),
                }
                # Include crisis-library fields when present
                for extra_key in ("category", "severity", "source_event_id"):
                    if extra_key in analog:
                        match[extra_key] = analog[extra_key]
                scored.append(match)
        scored.sort(key=lambda x: x["similarity"], reverse=True)
        return scored[:top_n]

    def _compute_similarity(self, regime_state: Dict[str, Any], analog: Dict[str, Any]) -> float:
        markers = analog.get("regime_markers", {})
        if not markers:
            return 0.0

        total = 0.0
        matched = 0
        for key, analog_val in markers.items():
            current_val = regime_state.get(key, 0.0)
            if isinstance(current_val, (int, float)) and isinstance(analog_val, (int, float)):
                diff = abs(float(current_val) - float(analog_val))
                total += max(0, 1.0 - diff)
                matched += 1

        # Tag-based bonus: check if regime has matching tags
        regime_tags = set(regime_state.get("active_tags", []))
        analog_tags = set(analog.get("tags", []))
        tag_overlap = len(regime_tags & analog_tags) / max(len(analog_tags), 1)
        tag_bonus = tag_overlap * 0.2

        base_score = (total / max(matched, 1)) if matched > 0 else 0.0
        return min(base_score + tag_bonus, 1.0)

    def save_library(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.library, indent=2), encoding="utf-8")

    @classmethod
    def load_library(cls, path: Path) -> "HistoricalAnalogEngine":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(custom_library=data)
