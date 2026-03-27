"""Multi-timeframe momentum tracker for the full watchlist."""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MomentumTracker:
    """Tracks multi-timeframe momentum across the full watchlist.

    Maintains a rolling price history per symbol and computes momentum
    metrics at 1h, 1d, 3d, and 1w horizons.  The composite momentum
    score is a weighted blend used for ranking and signal generation.
    """

    # Timeframe look-back windows in seconds
    _WINDOWS: dict[str, int] = {
        "1h": 3600,
        "1d": 86400,
        "3d": 259200,
        "1w": 604800,
    }

    # Weights for the composite momentum score
    _WEIGHTS: dict[str, float] = {
        "1h": 0.15,
        "1d": 0.30,
        "3d": 0.30,
        "1w": 0.25,
    }

    # Acceleration threshold multiplier — 2x normal flags "momentum ignition"
    _ACCEL_THRESHOLD: float = 2.0

    # Minimum gap percentage for the gap scanner
    _GAP_MIN_PCT: float = 2.0

    # Minimum volume ratio to confirm a gap
    _GAP_VOL_RATIO_MIN: float = 1.5

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
        # symbol → list of {price: float, timestamp: float}
        self.price_history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # symbol → latest computed momentum dict
        self.momentum_cache: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Data ingestion
    # ------------------------------------------------------------------

    def update(self, market_data: dict[str, dict[str, Any]] | None = None) -> None:
        """Update internal price history with latest prices.

        Args:
            market_data: dict of symbol → {price, volume, timestamp}.
                         If *None*, this is a no-op (allows scheduled calls
                         where data may not yet be available).
        """
        if not market_data:
            return

        now = time.time()
        for symbol, data in market_data.items():
            price = data.get("price")
            if price is None:
                continue
            ts = data.get("timestamp", now)
            self.price_history[symbol].append({"price": float(price), "timestamp": float(ts)})

        # Prune entries older than 2 weeks to bound memory
        cutoff = now - 14 * 86400
        for symbol in list(self.price_history):
            self.price_history[symbol] = [
                p for p in self.price_history[symbol] if p["timestamp"] >= cutoff
            ]

        # Recompute momentum for every symbol that received new data
        for symbol in market_data:
            if symbol in self.price_history and self.price_history[symbol]:
                self.momentum_cache[symbol] = self._compute_momentum(symbol)

    # ------------------------------------------------------------------
    # Momentum computation
    # ------------------------------------------------------------------

    def compute_momentum(self, symbol: str) -> dict[str, Any]:
        """Public interface — returns cached momentum or computes fresh."""
        if symbol in self.momentum_cache:
            return self.momentum_cache[symbol]
        result = self._compute_momentum(symbol)
        self.momentum_cache[symbol] = result
        return result

    def _compute_momentum(self, symbol: str) -> dict[str, Any]:
        history = self.price_history.get(symbol, [])
        if not history:
            return self._empty_momentum()

        latest_price = history[-1]["price"]
        latest_ts = history[-1]["timestamp"]

        pct_changes: dict[str, float] = {}
        for label, window_sec in self._WINDOWS.items():
            target_ts = latest_ts - window_sec
            ref_price = self._price_at(history, target_ts)
            if ref_price and ref_price != 0:
                pct_changes[label] = ((latest_price - ref_price) / ref_price) * 100.0
            else:
                pct_changes[label] = 0.0

        # Composite weighted score
        momentum_score = sum(
            pct_changes.get(label, 0.0) * weight
            for label, weight in self._WEIGHTS.items()
        )

        # Acceleration: rate of change of the 1d momentum
        acceleration = self._compute_acceleration(history, latest_ts)

        return {
            "symbol": symbol,
            "momentum_1h": round(pct_changes["1h"], 4),
            "momentum_1d": round(pct_changes["1d"], 4),
            "momentum_3d": round(pct_changes["3d"], 4),
            "momentum_1w": round(pct_changes["1w"], 4),
            "acceleration": round(acceleration, 4),
            "momentum_score": round(momentum_score, 4),
        }

    def _empty_momentum(self) -> dict[str, Any]:
        return {
            "symbol": "",
            "momentum_1h": 0.0,
            "momentum_1d": 0.0,
            "momentum_3d": 0.0,
            "momentum_1w": 0.0,
            "acceleration": 0.0,
            "momentum_score": 0.0,
        }

    def _compute_acceleration(self, history: list[dict[str, Any]], latest_ts: float) -> float:
        """Acceleration = momentum-of-momentum over the 1d window.

        Compares the 1d momentum *now* vs the 1d momentum *24h ago*.
        """
        window = self._WINDOWS["1d"]

        # Current 1d momentum
        p_now = history[-1]["price"]
        p_1d_ago = self._price_at(history, latest_ts - window)
        if not p_1d_ago or p_1d_ago == 0:
            return 0.0
        mom_now = ((p_now - p_1d_ago) / p_1d_ago) * 100.0

        # 1d momentum as of 24h ago
        p_1d_ago_val = p_1d_ago
        p_2d_ago = self._price_at(history, latest_ts - 2 * window)
        if not p_2d_ago or p_2d_ago == 0:
            return 0.0
        mom_prev = ((p_1d_ago_val - p_2d_ago) / p_2d_ago) * 100.0

        return mom_now - mom_prev

    @staticmethod
    def _price_at(history: list[dict[str, Any]], target_ts: float) -> float | None:
        """Return the price closest to *target_ts* (but not after it)."""
        best: dict[str, Any] | None = None
        for entry in history:
            if entry["timestamp"] <= target_ts:
                if best is None or entry["timestamp"] > best["timestamp"]:
                    best = entry
        return best["price"] if best else None

    # ------------------------------------------------------------------
    # Ranking & signals
    # ------------------------------------------------------------------

    def rank_by_momentum(self, n: int = 10) -> list[dict[str, Any]]:
        """Return the top *n* symbols by composite momentum score."""
        scored = []
        for symbol in self.price_history:
            mom = self.compute_momentum(symbol)
            scored.append(mom)
        scored.sort(key=lambda m: m["momentum_score"], reverse=True)
        return scored[:n]

    def detect_acceleration(self) -> list[dict[str, Any]]:
        """Flag symbols with outsized acceleration (momentum ignition).

        Returns a list of dicts: {symbol, timeframe, acceleration_pct, action}.
        """
        signals: list[dict[str, Any]] = []
        accels = []
        for symbol in self.price_history:
            mom = self.compute_momentum(symbol)
            accels.append(abs(mom["acceleration"]))

        mean_accel = (sum(accels) / len(accels)) if accels else 0.0
        threshold = mean_accel * self._ACCEL_THRESHOLD if mean_accel > 0 else 0.5

        for symbol in self.price_history:
            mom = self.compute_momentum(symbol)
            accel = mom["acceleration"]
            if abs(accel) > threshold:
                action = "momentum_ignition_long" if accel > 0 else "momentum_ignition_short"
                signals.append({
                    "symbol": symbol,
                    "timeframe": "1d",
                    "acceleration_pct": round(accel, 4),
                    "action": action,
                })

        signals.sort(key=lambda s: abs(s["acceleration_pct"]), reverse=True)
        return signals

    def detect_momentum_regime(self) -> dict[str, Any]:
        """Analyse breadth and concentration of momentum across the watchlist.

        Returns:
            breadth: fraction of symbols with positive 1d momentum
            narrowing: momentum concentrating in fewer names (reversal signal)
            broadening: momentum spreading to more names (continuation signal)
            leading_sectors: placeholder list (requires sector mapping)
        """
        if not self.price_history:
            return {
                "breadth": 0.0,
                "narrowing": False,
                "broadening": False,
                "leading_sectors": [],
            }

        positive_count = 0
        scores: list[float] = []
        for symbol in self.price_history:
            mom = self.compute_momentum(symbol)
            if mom["momentum_1d"] > 0:
                positive_count += 1
            scores.append(mom["momentum_score"])

        total = len(self.price_history)
        breadth = positive_count / total if total else 0.0

        # Concentration: measure via the share of total absolute score in the top 20%
        abs_scores = sorted([abs(s) for s in scores], reverse=True)
        top_n = max(1, int(total * 0.2))
        top_share = sum(abs_scores[:top_n])
        total_abs = sum(abs_scores) or 1.0
        concentration = top_share / total_abs

        narrowing = concentration > 0.7
        broadening = concentration < 0.4

        # Leading sectors require a symbol→sector mapping; return placeholder
        leading_sectors: list[str] = []

        return {
            "breadth": round(breadth, 4),
            "narrowing": narrowing,
            "broadening": broadening,
            "leading_sectors": leading_sectors,
        }

    def gap_scanner(self, market_data: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
        """Identify symbols gapping >2% with volume confirmation.

        Args:
            market_data: dict of symbol → {price, volume, timestamp,
                         prev_close (optional), avg_volume (optional)}.

        Returns:
            List of {symbol, gap_pct, volume_ratio, aligns_with_strategy}.
        """
        gaps: list[dict[str, Any]] = []
        for symbol, data in market_data.items():
            price = data.get("price")
            prev_close = data.get("prev_close")
            if price is None or prev_close is None or prev_close == 0:
                continue

            gap_pct = ((price - prev_close) / prev_close) * 100.0
            if abs(gap_pct) < self._GAP_MIN_PCT:
                continue

            volume = data.get("volume", 0)
            avg_volume = data.get("avg_volume", 0)
            volume_ratio = (volume / avg_volume) if avg_volume else 0.0

            # Aligns with strategy: gap up with volume confirmation and
            # existing positive momentum (or gap down with negative momentum)
            mom = self.compute_momentum(symbol)
            if gap_pct > 0:
                aligns = (
                    volume_ratio >= self._GAP_VOL_RATIO_MIN
                    and mom["momentum_1d"] > 0
                )
            else:
                aligns = (
                    volume_ratio >= self._GAP_VOL_RATIO_MIN
                    and mom["momentum_1d"] < 0
                )

            gaps.append({
                "symbol": symbol,
                "gap_pct": round(gap_pct, 4),
                "volume_ratio": round(volume_ratio, 4),
                "aligns_with_strategy": aligns,
            })

        gaps.sort(key=lambda g: abs(g["gap_pct"]), reverse=True)
        return gaps

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

    def format_telegram(self) -> str:
        """One-liner Telegram summary of the momentum landscape."""
        parts: list[str] = []

        # Acceleration highlights
        accel_signals = self.detect_acceleration()
        if accel_signals:
            top_accel = accel_signals[0]
            sign = "+" if top_accel["acceleration_pct"] > 0 else ""
            parts.append(
                f"\U0001f4c8 Momentum: {top_accel['symbol']} accel "
                f"{sign}{top_accel['acceleration_pct']:.0f}%"
            )

        # Top ranked
        top = self.rank_by_momentum(3)
        if top:
            names = ", ".join(m["symbol"] for m in top)
            parts.append(f"Top: {names}")

        # Breadth
        regime = self.detect_momentum_regime()
        breadth_pct = regime["breadth"] * 100
        parts.append(f"Breadth: {breadth_pct:.0f}% bullish")

        if not parts:
            return "\U0001f4c8 Momentum: no data"

        return " | ".join(parts)
