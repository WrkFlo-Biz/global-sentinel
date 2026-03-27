"""Real-time correlation monitoring and concentration risk detection."""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger(__name__)


class CorrelationMonitor:
    """Track rolling correlations across positions and flag concentration risk.

    Designed for standard-library-only operation so it runs on the VM without
    heavy dependencies.
    """

    def __init__(self, window: int = 30):
        self._window = window
        # symbol -> list of recent prices (most recent last)
        self._history: dict[str, list[float]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Data ingestion
    # ------------------------------------------------------------------

    def update(self, market_data: dict[str, float]) -> None:
        """Append latest prices.  *market_data* maps symbol -> price."""
        for sym, price in market_data.items():
            series = self._history[sym]
            series.append(float(price))
            if len(series) > self._window:
                self._history[sym] = series[-self._window :]

    # ------------------------------------------------------------------
    # Correlation math
    # ------------------------------------------------------------------

    def compute_matrix(self, symbols: list[str] | None = None) -> dict[str, dict[str, float]]:
        """Return pairwise Pearson correlations as ``{sym_a: {sym_b: rho}}``."""
        syms = symbols or list(self._history.keys())
        # Compute returns
        returns: dict[str, list[float]] = {}
        for s in syms:
            prices = self._history.get(s, [])
            if len(prices) < 3:
                continue
            returns[s] = [
                (prices[i] - prices[i - 1]) / prices[i - 1]
                for i in range(1, len(prices))
                if prices[i - 1] != 0
            ]
        valid = [s for s in syms if s in returns and len(returns[s]) >= 2]

        matrix: dict[str, dict[str, float]] = {}
        for a in valid:
            matrix[a] = {}
            for b in valid:
                matrix[a][b] = self._pearson(returns[a], returns[b])
        return matrix

    def compute_effective_bets(self, positions: list[dict]) -> int:
        """Estimate how many truly independent bets the portfolio contains.

        Uses simple single-linkage clustering: two positions are in the same
        cluster when their absolute correlation exceeds 0.70.  Fifteen
        oil-correlated names collapse into one or two clusters.
        """
        syms = [p["symbol"] for p in positions if p.get("symbol") in self._history]
        if len(syms) <= 1:
            return len(syms)

        corr = self.compute_matrix(syms)
        clusters = self._cluster(syms, corr, threshold=0.70)
        return len(clusters)

    # ------------------------------------------------------------------
    # Risk checks
    # ------------------------------------------------------------------

    def check_concentration(self, positions: list[dict]) -> list[dict]:
        """Return a list of warning dicts when concentration is dangerous."""
        warnings: list[dict] = []
        if not positions:
            return warnings

        # Direction check — fraction moving same way
        longs = sum(1 for p in positions if p.get("side", "long") == "long")
        shorts = len(positions) - longs
        dominant = max(longs, shorts)
        direction_pct = dominant / len(positions) if positions else 0
        if direction_pct > 0.60:
            side = "long" if longs >= shorts else "short"
            warnings.append({
                "level": "warn",
                "msg": f"{dominant}/{len(positions)} positions are {side} ({direction_pct:.0%})",
            })

        # Effective bets
        eff = self.compute_effective_bets(positions)
        if eff < 3:
            warnings.append({
                "level": "critical",
                "msg": f"Effective independent bets: {eff} (need >= 3)",
            })

        # Cluster notional concentration
        syms = [p["symbol"] for p in positions if p.get("symbol") in self._history]
        if syms:
            corr = self.compute_matrix(syms)
            clusters = self._cluster(syms, corr, threshold=0.70)
            notional_by_sym = {p["symbol"]: abs(float(p.get("notional", 0))) for p in positions}
            total_notional = sum(notional_by_sym.values()) or 1.0
            for cluster in clusters:
                cluster_notional = sum(notional_by_sym.get(s, 0) for s in cluster)
                pct = cluster_notional / total_notional
                if pct > 0.40:
                    warnings.append({
                        "level": "warn",
                        "msg": (
                            f"Correlation cluster {cluster[:3]}{'…' if len(cluster) > 3 else ''} "
                            f"= {pct:.0%} of notional"
                        ),
                    })

        return warnings

    # ------------------------------------------------------------------
    # Recommendations
    # ------------------------------------------------------------------

    def recommend(
        self,
        positions: list[dict],
        correlation_matrix: dict[str, dict[str, float]] | None = None,
    ) -> list[str]:
        """Generate actionable suggestions based on correlation structure."""
        suggestions: list[str] = []
        if not positions:
            return suggestions

        corr = correlation_matrix or self.compute_matrix(
            [p["symbol"] for p in positions if p.get("symbol") in self._history]
        )
        syms = [p["symbol"] for p in positions if p["symbol"] in corr]
        clusters = self._cluster(syms, corr, threshold=0.70)
        eff = len(clusters)

        # Identify dominant cluster theme (use first symbol as label)
        if clusters:
            largest = max(clusters, key=len)
            label = largest[0] if largest else "unknown"

            if len(largest) >= 3:
                # Find weakest member (smallest notional)
                notional_by_sym = {p["symbol"]: abs(float(p.get("notional", 0))) for p in positions}
                weakest = min(largest, key=lambda s: notional_by_sym.get(s, 0))
                suggestions.append(f"Trim weakest {label}-correlated position ({weakest})")

        if eff < 3:
            suggestions.append(f"Portfolio is effectively a {eff}-bet portfolio")

        if eff < 5:
            suggestions.append("Add uncorrelated hedge")

        return suggestions

    # ------------------------------------------------------------------
    # Telegram output
    # ------------------------------------------------------------------

    def format_telegram(self, positions: list[dict] | None = None) -> str:
        """One-line Telegram summary."""
        positions = positions or []
        syms = [p["symbol"] for p in positions if p.get("symbol") in self._history]
        corr = self.compute_matrix(syms) if syms else {}
        clusters = self._cluster(syms, corr, threshold=0.70) if syms else []
        eff = len(clusters) if clusters else len(syms)

        # Count positions in largest cluster
        largest_size = max((len(c) for c in clusters), default=0)
        largest_label = clusters[0][0] if clusters and clusters[0] else "N/A"

        recs = self.recommend(positions, corr)
        rec_text = recs[0] if recs else "balanced"

        return (
            f"\U0001f4ca Correlation: {largest_size}/{len(positions)} positions "
            f"{largest_label}-linked | Effective bets: {eff} | "
            f"Recommendation: {rec_text}"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pearson(xs: list[float], ys: list[float]) -> float:
        n = min(len(xs), len(ys))
        if n < 2:
            return 0.0
        xs, ys = xs[-n:], ys[-n:]
        mx = sum(xs) / n
        my = sum(ys) / n
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        sy = math.sqrt(sum((y - my) ** 2 for y in ys))
        if sx == 0 or sy == 0:
            return 0.0
        return cov / (sx * sy)

    @staticmethod
    def _cluster(
        symbols: list[str],
        corr: dict[str, dict[str, float]],
        threshold: float = 0.70,
    ) -> list[list[str]]:
        """Single-linkage clustering by absolute correlation."""
        parent: dict[str, str] = {s: s for s in symbols}

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i, a in enumerate(symbols):
            for b in symbols[i + 1 :]:
                rho = corr.get(a, {}).get(b, 0.0)
                if abs(rho) >= threshold:
                    union(a, b)

        groups: dict[str, list[str]] = defaultdict(list)
        for s in symbols:
            groups[find(s)].append(s)
        return list(groups.values())
