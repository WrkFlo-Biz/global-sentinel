"""Build derivative/hedge research candidates from underlying equities.

Turns equity candidates into option-style research candidates
based on regime state. This is a research builder, not an order generator.
"""
from __future__ import annotations

from typing import Any, Dict, List


class DerivativeCandidateBuilder:

    def build(
        self,
        *,
        underlyings: List[Dict[str, Any]],
        regime_state: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []

        geo_state = str(regime_state.get("geopolitical_state", "monitoring")).lower()

        for row in underlyings:
            symbol = str(row.get("symbol", ""))
            theme = str(row.get("theme", "")).lower()
            px = float(row.get("underlying_price", row.get("price", 100.0)))

            if theme in {"ai", "tech", "semis", "growth"}:
                out.append({
                    "symbol": symbol,
                    "instrument_type": "option",
                    "option_type": "put",
                    "target_moneyness": "atm_to_5pct_otm",
                    "score": float(row.get("score", 0.0)) + 0.10,
                    "theme": theme,
                    "sector": row.get("sector", "unknown"),
                    "underlying_price": px,
                    "direction": "long",
                })

            if geo_state in {"heightened", "crisis"} and theme in {"energy", "defense", "gold"}:
                out.append({
                    "symbol": symbol,
                    "instrument_type": "option",
                    "option_type": "call",
                    "target_moneyness": "atm_to_5pct_otm",
                    "score": float(row.get("score", 0.0)) + 0.15,
                    "theme": theme,
                    "sector": row.get("sector", "unknown"),
                    "underlying_price": px,
                    "direction": "long",
                })

        out.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
        return out
