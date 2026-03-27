"""Baseline benchmark comparisons for Global Sentinel.

Compares GS trading performance against seven naive/simple baselines
to quantify whether the intelligence layer adds genuine alpha.

Key insight: if GS cannot beat baseline #7 (simple_oil_long when Hormuz
is elevated), the system is not adding value beyond the obvious trade.
"""
from __future__ import annotations

import json
import logging
import math
import pathlib
import datetime
import importlib
from typing import Any

logger = logging.getLogger(__name__)


class BaselineBenchmarks:
    """Compare Global Sentinel performance against naive baselines."""

    BASELINE_NAMES = [
        "buy_hold_spy",
        "inverse_spy",
        "equal_weight_xle",
        "sixty_forty",
        "random_selection",
        "momentum_top5_bottom5",
        "simple_oil_long",
    ]

    def __init__(self, repo_root: str | pathlib.Path | None = None) -> None:
        if repo_root is None:
            repo_root = pathlib.Path(__file__).resolve().parents[2]
        self.repo_root = pathlib.Path(repo_root)
        self._last_results: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _try_import(self, module_path: str) -> Any:
        try:
            return importlib.import_module(module_path)
        except (ImportError, ModuleNotFoundError):
            logger.debug("Module %s not available", module_path)
            return None

    @staticmethod
    def _return_pct(prices: list[float]) -> float:
        """Simple return from first to last price."""
        if not prices or len(prices) < 2 or prices[0] == 0:
            return 0.0
        return (prices[-1] / prices[0] - 1.0) * 100.0

    @staticmethod
    def _sharpe(daily_returns: list[float], risk_free_annual: float = 0.05) -> float:
        """Annualised Sharpe ratio from daily returns."""
        if not daily_returns or len(daily_returns) < 2:
            return 0.0
        rf_daily = risk_free_annual / 252.0
        excess = [r - rf_daily for r in daily_returns]
        mean_e = sum(excess) / len(excess)
        var = sum((x - mean_e) ** 2 for x in excess) / (len(excess) - 1)
        std = math.sqrt(var) if var > 0 else 1e-9
        return (mean_e / std) * math.sqrt(252)

    @staticmethod
    def _max_drawdown(prices: list[float]) -> float:
        """Maximum drawdown percentage from a price series."""
        if not prices:
            return 0.0
        peak = prices[0]
        max_dd = 0.0
        for p in prices:
            if p > peak:
                peak = p
            dd = (peak - p) / peak * 100.0 if peak != 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @staticmethod
    def _daily_returns(prices: list[float]) -> list[float]:
        if len(prices) < 2:
            return []
        return [(prices[i] / prices[i - 1] - 1.0) for i in range(1, len(prices))]

    def _stats(self, prices: list[float]) -> dict[str, float]:
        """Compute return_pct, sharpe, max_dd from a price series."""
        dr = self._daily_returns(prices)
        return {
            "return_pct": round(self._return_pct(prices), 4),
            "sharpe": round(self._sharpe(dr), 4),
            "max_dd": round(self._max_drawdown(prices), 4),
        }

    # ------------------------------------------------------------------
    # Individual baselines
    # ------------------------------------------------------------------

    def _baseline_buy_hold_spy(self, market_data: dict[str, Any], window_days: int) -> dict[str, Any]:
        prices = market_data.get("SPY", {}).get("prices", [])[-window_days:]
        stats = self._stats(prices)
        return {"name": "buy_hold_spy", **stats}

    def _baseline_inverse_spy(self, market_data: dict[str, Any], window_days: int) -> dict[str, Any]:
        prices = market_data.get("SPY", {}).get("prices", [])[-window_days:]
        if prices and prices[0] != 0:
            inverse = [prices[0] + (prices[0] - p) for p in prices]
        else:
            inverse = prices
        stats = self._stats(inverse)
        return {"name": "inverse_spy", **stats}

    def _baseline_equal_weight_xle(self, market_data: dict[str, Any], window_days: int) -> dict[str, Any]:
        prices = market_data.get("XLE", {}).get("prices", [])[-window_days:]
        stats = self._stats(prices)
        return {"name": "equal_weight_xle", **stats}

    def _baseline_sixty_forty(self, market_data: dict[str, Any], window_days: int) -> dict[str, Any]:
        spy_prices = market_data.get("SPY", {}).get("prices", [])[-window_days:]
        tlt_prices = market_data.get("TLT", {}).get("prices", [])[-window_days:]
        if not spy_prices or not tlt_prices:
            return {"name": "sixty_forty", "return_pct": 0.0, "sharpe": 0.0, "max_dd": 0.0}
        min_len = min(len(spy_prices), len(tlt_prices))
        spy_prices = spy_prices[:min_len]
        tlt_prices = tlt_prices[:min_len]
        if spy_prices[0] == 0 or tlt_prices[0] == 0:
            return {"name": "sixty_forty", "return_pct": 0.0, "sharpe": 0.0, "max_dd": 0.0}
        blend = [
            0.6 * (s / spy_prices[0]) + 0.4 * (t / tlt_prices[0])
            for s, t in zip(spy_prices, tlt_prices)
        ]
        stats = self._stats(blend)
        return {"name": "sixty_forty", **stats}

    def _baseline_random_selection(
        self, market_data: dict[str, Any], window_days: int
    ) -> dict[str, Any]:
        """Equal-weight random basket from the same symbol universe."""
        import random

        symbols = [s for s in market_data if s not in {"SPY", "TLT", "XLE", "USO"}]
        if not symbols:
            return {"name": "random_selection", "return_pct": 0.0, "sharpe": 0.0, "max_dd": 0.0}
        picked = random.sample(symbols, min(10, len(symbols)))
        series_list: list[list[float]] = []
        for sym in picked:
            p = market_data.get(sym, {}).get("prices", [])[-window_days:]
            if p and p[0] != 0:
                series_list.append([v / p[0] for v in p])
        if not series_list:
            return {"name": "random_selection", "return_pct": 0.0, "sharpe": 0.0, "max_dd": 0.0}
        min_len = min(len(s) for s in series_list)
        avg = [sum(s[i] for s in series_list) / len(series_list) for i in range(min_len)]
        stats = self._stats(avg)
        return {"name": "random_selection", **stats}

    def _baseline_momentum_top5_bottom5(
        self, market_data: dict[str, Any], window_days: int
    ) -> dict[str, Any]:
        """Long top-5 momentum, short bottom-5 momentum."""
        mom: list[tuple[str, float]] = []
        for sym, data in market_data.items():
            prices = data.get("prices", [])
            if len(prices) >= window_days and prices[-window_days] != 0:
                ret = prices[-1] / prices[-window_days] - 1.0
                mom.append((sym, ret))
        if len(mom) < 10:
            return {"name": "momentum_top5_bottom5", "return_pct": 0.0, "sharpe": 0.0, "max_dd": 0.0}
        mom.sort(key=lambda x: x[1], reverse=True)
        longs = [s for s, _ in mom[:5]]
        shorts = [s for s, _ in mom[-5:]]

        long_series: list[list[float]] = []
        short_series: list[list[float]] = []
        for sym in longs:
            p = market_data[sym].get("prices", [])[-window_days:]
            if p and p[0] != 0:
                long_series.append([v / p[0] for v in p])
        for sym in shorts:
            p = market_data[sym].get("prices", [])[-window_days:]
            if p and p[0] != 0:
                short_series.append([2.0 - v / p[0] for v in p])

        all_legs = long_series + short_series
        if not all_legs:
            return {"name": "momentum_top5_bottom5", "return_pct": 0.0, "sharpe": 0.0, "max_dd": 0.0}
        min_len = min(len(s) for s in all_legs)
        combined = [sum(s[i] for s in all_legs) / len(all_legs) for i in range(min_len)]
        stats = self._stats(combined)
        return {"name": "momentum_top5_bottom5", **stats}

    def _baseline_simple_oil_long(
        self, market_data: dict[str, Any], window_days: int
    ) -> dict[str, Any]:
        """Buy USO whenever Hormuz chokepoint risk is elevated (dumb version)."""
        prices = market_data.get("USO", {}).get("prices", [])[-window_days:]
        # In production this would check chokepoint state per bar; here we
        # just use USO buy-and-hold as the dumb proxy.
        stats = self._stats(prices)
        return {"name": "simple_oil_long", **stats}

    # ------------------------------------------------------------------
    # GS performance
    # ------------------------------------------------------------------

    def _gs_return(self, trade_results: list[dict[str, Any]] | None) -> float:
        """Calculate GS total return from trade results."""
        if not trade_results:
            return 0.0
        total = 0.0
        for t in trade_results:
            total += float(t.get("pnl_pct", 0.0))
        return round(total, 4)

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def benchmark(
        self,
        trade_results: list[dict[str, Any]] | None = None,
        market_data: dict[str, Any] | None = None,
        window_days: int = 5,
    ) -> dict[str, Any]:
        """Compare GS performance against seven baselines.

        Parameters
        ----------
        trade_results : list of trade dicts with at least ``pnl_pct``
        market_data : dict mapping symbol -> {prices: [float]}
        window_days : lookback window for baselines

        Returns
        -------
        dict with ``gs_return_pct`` and per-baseline metrics including
        ``vs_gs_return_bps`` (positive means GS outperformed).
        """
        if market_data is None:
            market_data = {}

        gs_ret = self._gs_return(trade_results)

        baseline_runners = [
            self._baseline_buy_hold_spy,
            self._baseline_inverse_spy,
            self._baseline_equal_weight_xle,
            self._baseline_sixty_forty,
            self._baseline_random_selection,
            self._baseline_momentum_top5_bottom5,
            self._baseline_simple_oil_long,
        ]

        baselines: list[dict[str, Any]] = []
        for runner in baseline_runners:
            b = runner(market_data, window_days)
            b["vs_gs_return_bps"] = round((gs_ret - b.get("return_pct", 0.0)) * 100, 2)
            baselines.append(b)

        result = {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "window_days": window_days,
            "gs_return_pct": gs_ret,
            "baselines": baselines,
        }
        self._last_results = result
        return result

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def generate_report(
        self,
        benchmark_results: dict[str, Any],
        date: str | None = None,
    ) -> pathlib.Path:
        """Save benchmark results to reports/operational/baseline_{date}.json."""
        if date is None:
            date = datetime.date.today().isoformat()
        report_dir = self.repo_root / "reports" / "operational"
        report_dir.mkdir(parents=True, exist_ok=True)
        out_path = report_dir / f"baseline_{date}.json"
        with open(out_path, "w") as f:
            json.dump(benchmark_results, f, indent=2, default=str)
        logger.info("Baseline report saved to %s", out_path)
        return out_path

    def format_telegram(self, results: dict[str, Any] | None = None) -> str:
        """Format benchmark results into a compact Telegram-friendly line."""
        r = results or self._last_results
        if r is None:
            return "No benchmark data available. Run benchmark() first."

        gs_ret = r.get("gs_return_pct", 0.0)
        baselines = r.get("baselines", [])

        spy_ret = 0.0
        oil_ret = 0.0
        for b in baselines:
            if b["name"] == "buy_hold_spy":
                spy_ret = b.get("return_pct", 0.0)
            elif b["name"] == "simple_oil_long":
                oil_ret = b.get("return_pct", 0.0)

        alpha_bps = round((gs_ret - spy_ret) * 100, 0)
        sign = "+" if alpha_bps >= 0 else ""

        return (
            f"\U0001f4ca Baselines: GS {'+' if gs_ret >= 0 else ''}{gs_ret:.1f}% "
            f"vs SPY {'+' if spy_ret >= 0 else ''}{spy_ret:.1f}% "
            f"vs OilDumb {'+' if oil_ret >= 0 else ''}{oil_ret:.1f}% "
            f"\u2014 Alpha: {sign}{alpha_bps:.0f}bps"
        )
