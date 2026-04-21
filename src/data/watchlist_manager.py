"""Global Sentinel — WatchlistManager

Loads the expanded watchlist YAML and provides query, scan, and mutation
helpers used by the ingestion pipeline and strategy engines.
"""
from __future__ import annotations

import json
import logging
import statistics
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


class WatchlistManager:
    """Manages the expanded symbol watchlist across all asset categories."""

    def __init__(
        self,
        config_path: str = "config/expanded_watchlist.yaml",
        repo_root: str | Path | None = None,
    ) -> None:
        if repo_root is None:
            # Walk up from this file to find repo root (src/data/ -> repo root)
            repo_root = Path(__file__).resolve().parents[2]
        self._repo_root = Path(repo_root)
        self._config_path = self._repo_root / config_path

        self._categories: dict[str, list[str]] = {}
        self._symbol_to_category: dict[str, str] = {}

        self._load()

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Parse the YAML config and build internal indexes."""
        if not self._config_path.exists():
            raise FileNotFoundError(
                f"Watchlist config not found: {self._config_path}"
            )

        with open(self._config_path, "r") as fh:
            raw = yaml.safe_load(fh)

        categories_raw = raw.get("categories", {})
        for cat_name, cat_data in categories_raw.items():
            symbols: list[str] = cat_data.get("symbols", [])
            self._categories[cat_name] = list(symbols)
            for sym in symbols:
                # First category wins if a symbol appears in multiple
                if sym not in self._symbol_to_category:
                    self._symbol_to_category[sym] = cat_name

        logger.info(
            "WatchlistManager loaded %d symbols across %d categories from %s",
            len(self._symbol_to_category),
            len(self._categories),
            self._config_path,
        )

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_all_symbols(self) -> list[str]:
        """Return a flat, deduplicated list of every tracked symbol."""
        seen: set[str] = set()
        result: list[str] = []
        for symbols in self._categories.values():
            for sym in symbols:
                if sym not in seen:
                    seen.add(sym)
                    result.append(sym)
        return result

    def get_by_category(self, category: str) -> list[str]:
        """Return the list of symbols in *category*, or empty list."""
        return list(self._categories.get(category, []))

    def get_categories(self) -> list[str]:
        """Return all category names."""
        return list(self._categories.keys())

    def is_tracked(self, symbol: str) -> bool:
        """Check whether *symbol* is in the watchlist."""
        return symbol in self._symbol_to_category

    def get_category_for(self, symbol: str) -> str:
        """Return the category name for *symbol*, or ``'unknown'``."""
        return self._symbol_to_category.get(symbol, "unknown")

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def pre_market_scan(
        self, market_data: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Identify symbols gapping more than 2% in pre-market.

        Parameters
        ----------
        market_data:
            Mapping of symbol -> dict with at least ``prev_close`` and
            ``pre_market_price`` keys.

        Returns
        -------
        list of dicts with keys: symbol, gap_pct, volume_ratio, category
        """
        results: list[dict[str, Any]] = []
        for sym in self.get_all_symbols():
            data = market_data.get(sym)
            if data is None:
                continue
            prev_close = data.get("prev_close")
            pre_price = data.get("pre_market_price")
            if prev_close is None or pre_price is None or prev_close == 0:
                continue

            gap_pct = ((pre_price - prev_close) / prev_close) * 100.0
            if abs(gap_pct) > 2.0:
                results.append(
                    {
                        "symbol": sym,
                        "gap_pct": round(gap_pct, 2),
                        "volume_ratio": data.get("volume_ratio", 0.0),
                        "category": self.get_category_for(sym),
                    }
                )

        # Sort by absolute gap descending
        results.sort(key=lambda r: abs(r["gap_pct"]), reverse=True)
        return results

    def unusual_activity_scan(
        self, market_data: dict[str, dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Detect volume anomalies using z-score analysis.

        Parameters
        ----------
        market_data:
            Mapping of symbol -> dict with at least ``volume`` and
            ``avg_volume_20d`` keys.  Optionally ``volume_history``
            (list of recent daily volumes) for per-symbol z-score.

        Returns
        -------
        list of dicts with keys: symbol, metric, value, z_score, category
        """
        results: list[dict[str, Any]] = []
        for sym in self.get_all_symbols():
            data = market_data.get(sym)
            if data is None:
                continue

            volume = data.get("volume")
            avg_vol = data.get("avg_volume_20d")
            if volume is None or avg_vol is None or avg_vol == 0:
                continue

            volume_ratio = volume / avg_vol

            # Compute z-score from history if available, else approximate
            vol_history: list[float] | None = data.get("volume_history")
            if vol_history and len(vol_history) >= 5:
                mean_v = statistics.mean(vol_history)
                stdev_v = statistics.stdev(vol_history)
                z_score = (volume - mean_v) / stdev_v if stdev_v > 0 else 0.0
            else:
                # Rough approximation: assume stdev ~ 0.5 * avg
                z_score = (volume - avg_vol) / (0.5 * avg_vol)

            if abs(z_score) >= 2.0:
                results.append(
                    {
                        "symbol": sym,
                        "metric": "volume",
                        "value": volume,
                        "z_score": round(z_score, 2),
                        "category": self.get_category_for(sym),
                    }
                )

        results.sort(key=lambda r: abs(r["z_score"]), reverse=True)
        return results

    # ------------------------------------------------------------------
    # Mutation (runtime only — does not persist to YAML)
    # ------------------------------------------------------------------

    def add_symbol(self, symbol: str, category: str) -> None:
        """Add *symbol* to *category* at runtime."""
        if category not in self._categories:
            self._categories[category] = []
        if symbol not in self._categories[category]:
            self._categories[category].append(symbol)
        self._symbol_to_category[symbol] = category
        logger.info("Added %s to category %s (runtime)", symbol, category)

    def remove_symbol(self, symbol: str) -> None:
        """Remove *symbol* from the runtime watchlist."""
        cat = self._symbol_to_category.pop(symbol, None)
        if cat and cat in self._categories:
            try:
                self._categories[cat].remove(symbol)
            except ValueError:
                pass
        logger.info("Removed %s from watchlist (was in %s)", symbol, cat)

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return summary statistics about the watchlist."""
        by_cat = {cat: len(syms) for cat, syms in self._categories.items()}
        return {
            "total_symbols": len(self.get_all_symbols()),
            "categories": len(self._categories),
            "by_category_count": by_cat,
        }

    def __repr__(self) -> str:
        s = self.stats()
        return (
            f"<WatchlistManager: {s['total_symbols']} symbols, "
            f"{s['categories']} categories>"
        )
