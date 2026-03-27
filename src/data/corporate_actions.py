"""Corporate actions tracker for splits, dividends, mergers, and spinoffs."""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CorporateActionsTracker:
    """Tracks and caches upcoming corporate actions for watched symbols."""

    def __init__(self, repo_root: str | Path | None = None) -> None:
        if repo_root is None:
            repo_root = Path(__file__).resolve().parents[2]
        self.repo_root = Path(repo_root)
        self.cache_path = self.repo_root / "data" / "corporate_actions_cache.json"
        self._cache: list[dict[str, Any]] = []
        self.load_cache()

    # ── Cache persistence ────────────────────────────────────────────

    def load_cache(self) -> None:
        """Load cached corporate actions from disk."""
        if self.cache_path.exists():
            try:
                with open(self.cache_path, "r") as fh:
                    self._cache = json.load(fh)
                logger.info("Loaded %d cached corporate actions", len(self._cache))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load corporate actions cache: %s", exc)
                self._cache = []
        else:
            self._cache = []

    def save_cache(self) -> None:
        """Persist cached corporate actions to disk."""
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.cache_path, "w") as fh:
            json.dump(self._cache, fh, indent=2, default=str)
        logger.info("Saved %d corporate actions to cache", len(self._cache))

    # ── Core API ─────────────────────────────────────────────────────

    def check_pending(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        """Return pending corporate actions, optionally filtered by symbols.

        Each item: {symbol, action_type, ex_date, details}
        action_type is one of: split, dividend, merger, spinoff

        Will be wired to a live data source later; currently returns from cache.
        """
        today = datetime.utcnow().strftime("%Y-%m-%d")
        pending = [
            a for a in self._cache
            if a.get("ex_date", "") >= today
        ]
        if symbols:
            symbol_set = {s.upper() for s in symbols}
            pending = [a for a in pending if a.get("symbol", "").upper() in symbol_set]
        return pending

    def adjust_historical(
        self,
        symbol: str,
        price_data: list[dict[str, Any]],
        actions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Adjust historical price data for corporate actions (splits, dividends).

        Returns a new list with adjusted OHLCV values.
        """
        adjusted = [dict(row) for row in price_data]

        for action in sorted(actions, key=lambda a: a.get("ex_date", ""), reverse=True):
            action_type = action.get("action_type", "")
            ex_date = action.get("ex_date", "")
            details = action.get("details", {})

            if action_type == "split":
                ratio_str = details.get("ratio", "1:1")
                parts = ratio_str.split(":")
                try:
                    ratio = float(parts[0]) / float(parts[1])
                except (ValueError, ZeroDivisionError, IndexError):
                    logger.warning("Invalid split ratio %s for %s", ratio_str, symbol)
                    continue

                for row in adjusted:
                    if row.get("date", "") < ex_date:
                        for field in ("open", "high", "low", "close"):
                            if field in row:
                                row[field] = round(row[field] / ratio, 4)
                        if "volume" in row:
                            row["volume"] = int(row["volume"] * ratio)

            elif action_type == "dividend":
                div_amount = float(details.get("amount", 0))
                if div_amount > 0:
                    for row in adjusted:
                        if row.get("date", "") < ex_date:
                            for field in ("open", "high", "low", "close"):
                                if field in row:
                                    row[field] = round(row[field] - div_amount, 4)

            # merger and spinoff adjustments are complex; placeholder for future
            elif action_type in ("merger", "spinoff"):
                logger.info(
                    "Skipping %s adjustment for %s (not yet implemented)",
                    action_type,
                    symbol,
                )

        return adjusted

    def alert_on_action(self, watchlist_symbols: list[str]) -> list[dict[str, Any]]:
        """Return alerts for watchlist symbols with upcoming corporate actions."""
        pending = self.check_pending(symbols=watchlist_symbols)
        alerts: list[dict[str, Any]] = []
        for action in pending:
            alerts.append({
                "symbol": action["symbol"],
                "action_type": action["action_type"],
                "ex_date": action["ex_date"],
                "details": action.get("details", {}),
                "message": (
                    f"{action['symbol']} has upcoming {action['action_type']} "
                    f"on {action['ex_date']}"
                ),
            })
        return alerts

    def add_known_action(
        self,
        symbol: str,
        action_type: str,
        ex_date: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Manually add a known corporate action to the cache."""
        if action_type not in ("split", "dividend", "merger", "spinoff"):
            raise ValueError(
                f"Invalid action_type '{action_type}'; "
                "must be split, dividend, merger, or spinoff"
            )
        entry = {
            "symbol": symbol.upper(),
            "action_type": action_type,
            "ex_date": ex_date,
            "details": details or {},
            "added_at": datetime.utcnow().isoformat(),
        }
        self._cache.append(entry)
        self.save_cache()
        logger.info("Added %s %s for %s on %s", action_type, symbol, ex_date, details)

    # ── Formatting ───────────────────────────────────────────────────

    def format_telegram(self) -> str:
        """Format pending actions for Telegram digest."""
        pending = self.check_pending()
        if not pending:
            return "\U0001f4cb Corp Actions: None pending"

        parts: list[str] = []
        for a in pending:
            sym = a["symbol"]
            atype = a["action_type"]
            ex = a["ex_date"]
            details = a.get("details", {})

            # Format the ex_date as M/D
            try:
                dt = datetime.strptime(ex, "%Y-%m-%d")
                ex_short = f"{dt.month}/{dt.day}"
            except ValueError:
                ex_short = ex

            if atype == "dividend":
                amount = details.get("amount", "?")
                parts.append(f"{sym} ex-div {ex_short} ${amount}")
            elif atype == "split":
                ratio = details.get("ratio", "?")
                parts.append(f"{sym} split {ex_short} {ratio}")
            elif atype == "merger":
                target = details.get("target", "")
                parts.append(f"{sym} merger {ex_short} {target}".strip())
            elif atype == "spinoff":
                new_sym = details.get("new_symbol", "")
                parts.append(f"{sym} spinoff {ex_short} {new_sym}".strip())
            else:
                parts.append(f"{sym} {atype} {ex_short}")

        return "\U0001f4cb Corp Actions: " + " | ".join(parts)
