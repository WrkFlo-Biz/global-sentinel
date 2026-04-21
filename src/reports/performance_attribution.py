"""Performance attribution: decompose daily PnL into alpha, beta, and factor components."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class PerformanceAttribution:
    """Decompose daily PnL into meaningful components.

    Key insight: if beta dominates, you're just riding oil, not adding intelligence.
    If alpha dominates, the system is working.
    """

    def __init__(self) -> None:
        self._last_decomposition: dict | None = None
        self._history: list[dict] = []

    def decompose(
        self,
        daily_pnl: float,
        portfolio_state: dict | None = None,
        market_data: dict | None = None,
    ) -> dict:
        """Decompose daily PnL into attribution components.

        Args:
            daily_pnl: Total daily profit/loss in dollars.
            portfolio_state: Optional dict with positions, exposures, sector weights.
            market_data: Optional dict with benchmark returns, oil prices, sector indices.

        Returns:
            Dict with alpha, beta, oil_factor, sector_rotation, timing,
            information_edge, and residual components.
        """
        portfolio_state = portfolio_state or {}
        market_data = market_data or {}

        # Extract market returns for factor estimation
        benchmark_return = market_data.get("benchmark_return", 0.0)
        oil_return = market_data.get("oil_return", 0.0)
        sector_returns = market_data.get("sector_returns", {})

        # Portfolio characteristics
        portfolio_beta = portfolio_state.get("beta", 1.0)
        portfolio_value = portfolio_state.get("portfolio_value", 600_000.0)
        sector_weights = portfolio_state.get("sector_weights", {})
        signal_times = portfolio_state.get("signal_timestamps", [])
        move_times = market_data.get("move_timestamps", [])

        # Beta component: PnL attributable to market exposure
        beta_pnl = portfolio_beta * benchmark_return * portfolio_value
        beta = round(beta_pnl, 2)

        # Oil factor: sensitivity to oil price moves
        oil_exposure = portfolio_state.get("oil_exposure", 0.0)
        oil_pnl = oil_exposure * oil_return * portfolio_value
        oil_factor = round(oil_pnl, 2)

        # Sector rotation: value from sector timing vs equal-weight
        sector_pnl = 0.0
        if sector_weights and sector_returns:
            equal_weight = 1.0 / max(len(sector_returns), 1)
            for sector, weight in sector_weights.items():
                sector_ret = sector_returns.get(sector, 0.0)
                sector_pnl += (weight - equal_weight) * sector_ret * portfolio_value
        sector_rotation = round(sector_pnl, 2)

        # Timing: entry/exit quality relative to intraday range
        timing_score = portfolio_state.get("timing_score", 0.0)
        timing_pnl = timing_score * abs(daily_pnl) if daily_pnl != 0 else 0.0
        timing = round(timing_pnl, 2)

        # Information edge: trades where GS signal preceded market move
        info_edge_pnl = 0.0
        leading_trade_count = 0
        if signal_times and move_times:
            for sig_t, move_t in zip(signal_times, move_times):
                if sig_t < move_t:
                    leading_trade_count += 1
            trade_pnl_list = portfolio_state.get("trade_pnls", [])
            for i, (sig_t, move_t) in enumerate(zip(signal_times, move_times)):
                if sig_t < move_t and i < len(trade_pnl_list):
                    info_edge_pnl += trade_pnl_list[i]
        information_edge = round(info_edge_pnl, 2)

        # Alpha: strategy-specific edge (what's left after known factors)
        explained = beta + oil_factor + sector_rotation + timing + information_edge
        residual_raw = daily_pnl - explained

        # Split residual: alpha gets the portion correlated with signal quality,
        # true residual is unexplained noise
        signal_quality = portfolio_state.get("signal_quality", 0.5)
        alpha = round(residual_raw * signal_quality, 2)
        residual = round(residual_raw - alpha, 2)

        result = {
            "daily_pnl": daily_pnl,
            "alpha": alpha,
            "beta": beta,
            "oil_factor": oil_factor,
            "sector_rotation": sector_rotation,
            "timing": timing,
            "information_edge": information_edge,
            "residual": residual,
            "leading_trade_count": leading_trade_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        self._last_decomposition = result
        self._history.append(result)
        return result

    def assess_quality(self) -> dict:
        """Assess the quality of strategy returns.

        Returns:
            Dict with alpha_ratio, information_ratio, and signal_value assessment.
        """
        if not self._last_decomposition:
            return {
                "alpha_ratio": 0.0,
                "information_ratio": 0.0,
                "signal_value": "no data",
            }

        d = self._last_decomposition
        total_pnl = d["daily_pnl"]
        alpha = d["alpha"]

        # Alpha ratio: alpha / total PnL (should be > 0.3 for system to be adding value)
        if total_pnl != 0:
            alpha_ratio = round(alpha / total_pnl, 4)
        else:
            alpha_ratio = 0.0

        # Information ratio: alpha / tracking error
        # Use history for tracking error calculation
        if len(self._history) >= 2:
            alpha_series = [h["alpha"] for h in self._history]
            mean_alpha = sum(alpha_series) / len(alpha_series)
            variance = sum((a - mean_alpha) ** 2 for a in alpha_series) / len(alpha_series)
            tracking_error = variance ** 0.5
            if tracking_error > 0:
                information_ratio = round(mean_alpha / tracking_error, 4)
            else:
                information_ratio = 0.0
        else:
            tracking_error = 0.0
            information_ratio = 0.0

        # Signal value: did signals precede or follow market moves?
        leading_count = d.get("leading_trade_count", 0)
        if leading_count >= 3:
            signal_value = f"strong — {leading_count} trades leading market"
        elif leading_count >= 1:
            signal_value = f"moderate — {leading_count} trades leading market"
        else:
            signal_value = "weak — signals not preceding moves"

        quality_flag = "adding value" if alpha_ratio > 0.3 else "review needed"

        return {
            "alpha_ratio": alpha_ratio,
            "information_ratio": information_ratio,
            "signal_value": signal_value,
            "quality_flag": quality_flag,
            "tracking_error": round(tracking_error, 2),
        }

    def format_telegram(self) -> str:
        """Format attribution summary for Telegram digest."""
        if not self._last_decomposition:
            return "No attribution data available."

        d = self._last_decomposition
        total = d["daily_pnl"]

        def _pct(val: float) -> int:
            if total == 0:
                return 0
            return round(abs(val) / abs(total) * 100)

        alpha_pct = _pct(d["alpha"])
        beta_pct = _pct(d["beta"])
        oil_pct = _pct(d["oil_factor"])
        leading = d.get("leading_trade_count", 0)

        return (
            f"Attribution: Alpha ${d['alpha']:.0f} ({alpha_pct}%) "
            f"| Beta ${d['beta']:.0f} ({beta_pct}%) "
            f"| Oil ${d['oil_factor']:.0f} ({oil_pct}%) "
            f"| Info-edge: {leading} trades leading"
        )
