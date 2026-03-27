"""War Opportunity Scanner — discovers alpha outside the current strategy universe."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

HIDDEN_OPPORTUNITIES: dict[str, list[str]] = {
    "insurance_reinsurance": ["RNR", "MKL", "AIG"],
    "satellite_isr": ["RKLB", "BKSY", "PL"],
    "petrochemical_short": ["DOW", "LYB", "EMN"],
    "ev_acceleration": ["TSLA", "RIVN"],
    "water_infrastructure": ["XYL", "AWK"],
    "aluminum": ["AA", "CENX"],
    "india_japan_pain": ["INDA", "EWJ"],
    "refining_margins": ["MPC", "VLO", "PSX", "PBF", "DINO"],
    "post_war_copper": ["COPX", "FCX"],
}

# Maps categories to the sector keywords that would trigger them in bridge signals.
_SECTOR_KEYWORDS: dict[str, list[str]] = {
    "insurance_reinsurance": ["insurance", "reinsurance", "underwriting", "premiums", "casualty"],
    "satellite_isr": ["satellite", "isr", "reconnaissance", "space", "imaging", "geospatial"],
    "petrochemical_short": ["petrochemical", "chemical", "ethylene", "plastics", "downstream"],
    "ev_acceleration": ["ev", "electric vehicle", "battery", "charging", "electrification"],
    "water_infrastructure": ["water", "infrastructure", "utilities", "treatment", "desalination"],
    "aluminum": ["aluminum", "aluminium", "smelting", "bauxite", "metal"],
    "india_japan_pain": ["india", "japan", "asia", "emerging market", "yen", "rupee"],
    "refining_margins": ["refining", "refinery", "crack spread", "gasoline", "diesel", "margins"],
    "post_war_copper": ["copper", "mining", "reconstruction", "rebuild", "wiring"],
}

# Maps categories to a default action bias.
_ACTION_BIAS: dict[str, str] = {
    "insurance_reinsurance": "long",
    "satellite_isr": "long",
    "petrochemical_short": "short",
    "ev_acceleration": "long",
    "water_infrastructure": "long",
    "aluminum": "long",
    "india_japan_pain": "short",
    "refining_margins": "long",
    "post_war_copper": "long",
}

# Supply-chain adjacency: if a winner category is detected, also scan these.
_SUPPLY_CHAIN_MAP: dict[str, list[str]] = {
    "energy": ["aluminum", "water_infrastructure", "refining_margins"],
    "defense": ["satellite_isr", "aluminum", "post_war_copper"],
    "reconstruction": ["post_war_copper", "water_infrastructure"],
    "shipping": ["insurance_reinsurance", "refining_margins"],
}


class WarOpportunityScanner:
    """Scans for opportunities that are NOT in the current strategy universe."""

    def __init__(self, repo_root: str | Path | None = None) -> None:
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]
        self.repo_root = Path(repo_root)
        self.known_universe: set[str] = self._load_known_universe()
        self.hidden_opportunities = HIDDEN_OPPORTUNITIES

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_known_universe(self) -> set[str]:
        """Load the set of symbols already tracked by war_strategies.yaml."""
        config_path = self.repo_root / "config" / "war_strategies.yaml"
        symbols: set[str] = set()
        if not config_path.exists():
            logger.warning("war_strategies.yaml not found at %s", config_path)
            return symbols
        try:
            with open(config_path, "r") as fh:
                data = yaml.safe_load(fh) or {}
            # Walk the YAML tree and collect anything that looks like a ticker list.
            self._extract_symbols(data, symbols)
        except Exception:
            logger.exception("Failed to parse war_strategies.yaml")
        return symbols

    def _extract_symbols(self, obj: Any, out: set[str]) -> None:
        """Recursively pull ticker-like strings from a nested structure."""
        if isinstance(obj, dict):
            for key, val in obj.items():
                if key in ("symbols", "tickers", "universe", "watchlist"):
                    if isinstance(val, list):
                        out.update(str(s).upper() for s in val)
                else:
                    self._extract_symbols(val, out)
        elif isinstance(obj, list):
            for item in obj:
                self._extract_symbols(item, out)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _make_discovery(
        self,
        symbol: str,
        source: str,
        signal_type: str,
        action: str,
        confidence: float,
        category: str,
    ) -> dict[str, Any]:
        return {
            "symbol": symbol,
            "source": source,
            "signal_type": signal_type,
            "action": action,
            "confidence": round(confidence, 3),
            "timestamp": self._now_iso(),
            "category": category,
        }

    # ------------------------------------------------------------------
    # Scanning logic
    # ------------------------------------------------------------------

    def scan(
        self,
        bridge_results: dict[str, Any] | None = None,
        scorecard: dict[str, Any] | None = None,
        market_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run all scanning heuristics and return discoveries.

        Returns a dict with key ``discoveries`` — a list of discovery dicts.
        """
        discoveries: list[dict[str, Any]] = []

        # 1. Check hidden opportunities against bridge signals mentioning their sectors.
        discoveries.extend(self._scan_bridge_sectors(bridge_results))

        # 2. Inverse correlations: market down but some symbols up → flag.
        discoveries.extend(self._scan_inverse_correlations(market_data))

        # 3. Supply chain cascades: winners in energy → check suppliers.
        discoveries.extend(self._scan_supply_chain(bridge_results))

        # 4. Extreme sector sentiment from sentiment bridge.
        discoveries.extend(self._scan_sentiment_extremes(bridge_results))

        # 5. Scorecard chokepoint risk → flag insurance / shipping adjacents.
        discoveries.extend(self._scan_chokepoint_risk(scorecard))

        # Deduplicate by symbol (keep highest confidence).
        seen: dict[str, dict[str, Any]] = {}
        for d in discoveries:
            sym = d["symbol"]
            if sym not in seen or d["confidence"] > seen[sym]["confidence"]:
                seen[sym] = d
        unique = list(seen.values())

        # Filter out symbols already in the known universe.
        filtered = [d for d in unique if d["symbol"] not in self.known_universe]

        return {"discoveries": filtered}

    # -- 1. Bridge sector keyword matching ------------------------------------

    def _scan_bridge_sectors(
        self, bridge_results: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        if not bridge_results:
            return []
        results: list[dict[str, Any]] = []
        text_blob = json.dumps(bridge_results).lower()

        for category, keywords in _SECTOR_KEYWORDS.items():
            hits = sum(1 for kw in keywords if kw in text_blob)
            if hits == 0:
                continue
            confidence = min(0.4 + hits * 0.1, 0.85)
            action = _ACTION_BIAS.get(category, "watch")
            for symbol in HIDDEN_OPPORTUNITIES[category]:
                results.append(
                    self._make_discovery(
                        symbol=symbol,
                        source="bridge_sector_scan",
                        signal_type="sector_mention",
                        action=action,
                        confidence=confidence,
                        category=category,
                    )
                )
        return results

    # -- 2. Inverse correlations ----------------------------------------------

    def _scan_inverse_correlations(
        self, market_data: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        if not market_data:
            return []
        results: list[dict[str, Any]] = []

        # Expect market_data to contain a broad index return and per-symbol returns.
        index_return = market_data.get("index_return")
        symbol_returns: dict[str, float] = market_data.get("symbol_returns", {})
        if index_return is None:
            return []

        # Only interesting when the broad market is down.
        if index_return >= 0:
            return []

        all_hidden_symbols = {
            sym: cat
            for cat, syms in HIDDEN_OPPORTUNITIES.items()
            for sym in syms
        }

        for symbol, ret in symbol_returns.items():
            symbol_upper = symbol.upper()
            if symbol_upper not in all_hidden_symbols:
                continue
            # Symbol is up while index is down — divergence signal.
            if ret > 0:
                category = all_hidden_symbols[symbol_upper]
                spread = ret - index_return
                confidence = min(0.35 + spread * 2, 0.90)
                results.append(
                    self._make_discovery(
                        symbol=symbol_upper,
                        source="inverse_correlation",
                        signal_type="divergence",
                        action="long",
                        confidence=confidence,
                        category=category,
                    )
                )
        return results

    # -- 3. Supply chain cascades ---------------------------------------------

    def _scan_supply_chain(
        self, bridge_results: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        if not bridge_results:
            return []
        results: list[dict[str, Any]] = []
        text_blob = json.dumps(bridge_results).lower()

        for trigger_sector, downstream_categories in _SUPPLY_CHAIN_MAP.items():
            if trigger_sector not in text_blob:
                continue
            for category in downstream_categories:
                action = _ACTION_BIAS.get(category, "watch")
                for symbol in HIDDEN_OPPORTUNITIES.get(category, []):
                    results.append(
                        self._make_discovery(
                            symbol=symbol,
                            source="supply_chain_cascade",
                            signal_type="upstream_trigger",
                            action=action,
                            confidence=0.45,
                            category=category,
                        )
                    )
        return results

    # -- 4. Extreme sentiment -------------------------------------------------

    def _scan_sentiment_extremes(
        self, bridge_results: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        if not bridge_results:
            return []
        results: list[dict[str, Any]] = []

        sentiment_data = bridge_results.get("sentiment_bridge", {})
        sector_sentiments: dict[str, float] = sentiment_data.get(
            "sector_sentiment", {}
        )

        for sector, score in sector_sentiments.items():
            sector_lower = sector.lower()
            # Extreme negative or positive sentiment.
            if abs(score) < 0.7:
                continue
            for category, keywords in _SECTOR_KEYWORDS.items():
                if not any(kw in sector_lower for kw in keywords):
                    continue
                action = _ACTION_BIAS.get(category, "watch")
                # Flip action on extreme negative sentiment for long-biased categories.
                if score < -0.7 and action == "long":
                    action = "watch"
                elif score > 0.7 and action == "short":
                    action = "watch"
                confidence = min(abs(score), 0.90)
                for symbol in HIDDEN_OPPORTUNITIES[category]:
                    results.append(
                        self._make_discovery(
                            symbol=symbol,
                            source="sentiment_extreme",
                            signal_type="extreme_sector_sentiment",
                            action=action,
                            confidence=confidence,
                            category=category,
                        )
                    )
        return results

    # -- 5. Chokepoint risk → insurance / shipping adjacents ------------------

    def _scan_chokepoint_risk(
        self, scorecard: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        if not scorecard:
            return []
        results: list[dict[str, Any]] = []

        chokepoint_data = scorecard.get("chokepoint_risk", {})
        if isinstance(chokepoint_data, dict):
            chokepoint_risk = float(chokepoint_data.get("composite_score", chokepoint_data.get("score", 0)) or 0)
        else:
            try:
                chokepoint_risk = float(chokepoint_data)
            except (TypeError, ValueError):
                chokepoint_risk = 0.0
        if chokepoint_risk < 0.6:
            return []

        confidence = min(0.40 + chokepoint_risk * 0.3, 0.85)
        adjacent_categories = ["insurance_reinsurance", "refining_margins"]
        for category in adjacent_categories:
            action = _ACTION_BIAS.get(category, "watch")
            for symbol in HIDDEN_OPPORTUNITIES[category]:
                results.append(
                    self._make_discovery(
                        symbol=symbol,
                        source="chokepoint_adjacency",
                        signal_type="chokepoint_risk",
                        action=action,
                        confidence=confidence,
                        category=category,
                    )
                )
        return results

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def format_telegram(self, scan_result: dict[str, Any] | None = None) -> str:
        """Format discoveries for Telegram notification."""
        if scan_result is None:
            scan_result = self.scan()

        discoveries = scan_result.get("discoveries", [])
        if not discoveries:
            return "\U0001f50d Scanner: 0 discoveries"

        # Sort by confidence descending, pick top details for summary.
        ranked = sorted(discoveries, key=lambda d: d["confidence"], reverse=True)
        detail_parts: list[str] = []
        for d in ranked[:5]:
            label = d.get("category", d.get("signal_type", ""))
            # Make label human-readable.
            label = label.replace("_", " ")
            detail_parts.append(f"{d['symbol']} ({label})")

        summary = ", ".join(detail_parts)
        return f"\U0001f50d Scanner: {len(discoveries)} discoveries \u2014 {summary}"

    def get_hidden_opportunities(self) -> dict[str, list[str]]:
        """Return the full hidden opportunities dictionary."""
        return dict(self.hidden_opportunities)
