"""Profit optimizer — dynamic sizing and idea prioritisation across all alpha sources."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default sizing parameters (fraction of equity)
DEFAULT_BASE_ALLOCATION = 0.02  # 2% per strategy
MIN_ALLOCATION = 0.005  # 0.5%
MAX_ALLOCATION = 0.06  # 6%
EXPLORATORY_SIZE = 0.005  # 0.5% for scanner discoveries


class ProfitOptimizer:
    """Aggregate signals from strategies, edge detectors, and scanners to
    recommend sizing changes and new positions."""

    def __init__(self, repo_root: str | Path | None = None) -> None:
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
        self._last_result: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def optimize(
        self,
        portfolio_state: dict[str, Any] | None = None,
        strategy_results: list[dict[str, Any]] | None = None,
        edge_findings: list[dict[str, Any]] | None = None,
        scanner_discoveries: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Produce a unified optimisation recommendation.

        Args:
            portfolio_state: Current positions, equity, etc.
            strategy_results: Per-strategy output with keys like
                ``strategy``, ``signal_strength`` (0-1), ``pnl_trailing``
                (positive = winning), ``current_allocation``.
            edge_findings: Edge-detector output with ``symbol``,
                ``edge_type``, ``confidence`` (0-1), ``direction``
                (long/short), ``recommended_notional``.
            scanner_discoveries: War-opportunity / idiosyncratic scanner
                output with ``symbol``, ``source``, ``confidence`` (0-1),
                ``action`` (buy/sell), ``reason``.

        Returns:
            Dict with increase_exposure, decrease_exposure, new_positions,
            priority_ranked_ideas.
        """
        portfolio_state = portfolio_state or {}
        strategy_results = strategy_results or []
        edge_findings = edge_findings or []
        scanner_discoveries = scanner_discoveries or []

        equity = portfolio_state.get("equity", 100_000.0)

        increase_exposure: list[dict[str, Any]] = []
        decrease_exposure: list[dict[str, Any]] = []
        new_positions: list[dict[str, Any]] = []
        all_ideas: list[dict[str, Any]] = []

        # ----- 1. Strategy sizing adjustments -----
        for sr in strategy_results:
            strategy = sr.get("strategy", "unknown")
            signal = sr.get("signal_strength", 0.5)
            pnl = sr.get("pnl_trailing", 0.0)
            current = sr.get("current_allocation", DEFAULT_BASE_ALLOCATION)

            recommended = self._compute_new_allocation(signal, pnl, current)

            entry: dict[str, Any] = {
                "strategy": strategy,
                "current_size": round(current, 4),
                "recommended_size": round(recommended, 4),
            }

            if recommended > current * 1.05:
                entry["reason"] = self._increase_reason(signal, pnl)
                increase_exposure.append(entry)
                all_ideas.append(
                    {
                        "source": "strategy",
                        "strategy": strategy,
                        "action": "increase",
                        "expected_edge": signal * max(pnl, 0.01),
                        **entry,
                    }
                )
            elif recommended < current * 0.95:
                entry["reason"] = self._decrease_reason(signal, pnl)
                decrease_exposure.append(entry)

        # ----- 2. Edge detector findings -----
        for ef in edge_findings:
            symbol = ef.get("symbol", "???")
            confidence = ef.get("confidence", 0.5)
            direction = ef.get("direction", "long")
            notional = ef.get("recommended_notional", equity * DEFAULT_BASE_ALLOCATION)

            # Leading signal → size up immediately
            if confidence >= 0.7:
                notional = min(notional * 1.5, equity * MAX_ALLOCATION)

            new_positions.append(
                {
                    "symbol": symbol,
                    "source": "edge",
                    "action": "buy" if direction == "long" else "sell",
                    "notional": round(notional, 2),
                    "reason": f"Edge detector: {ef.get('edge_type', 'unknown')} "
                              f"(conf {confidence:.0%})",
                }
            )
            all_ideas.append(
                {
                    "source": "edge",
                    "symbol": symbol,
                    "action": direction,
                    "expected_edge": confidence,
                    "notional": round(notional, 2),
                }
            )

        # ----- 3. Scanner discoveries -----
        for sd in scanner_discoveries:
            symbol = sd.get("symbol", "???")
            confidence = sd.get("confidence", 0.5)
            action = sd.get("action", "buy")
            reason = sd.get("reason", "scanner discovery")

            # High confidence → small exploratory; low confidence → skip
            if confidence < 0.4:
                logger.debug("Skipping low-confidence scanner hit: %s", symbol)
                continue

            notional = equity * EXPLORATORY_SIZE
            if confidence >= 0.8:
                notional = equity * DEFAULT_BASE_ALLOCATION

            new_positions.append(
                {
                    "symbol": symbol,
                    "source": "scanner",
                    "action": action,
                    "notional": round(notional, 2),
                    "reason": reason,
                }
            )
            all_ideas.append(
                {
                    "source": "scanner",
                    "symbol": symbol,
                    "action": action,
                    "expected_edge": confidence * 0.8,  # discount scanner vs edge
                    "notional": round(notional, 2),
                }
            )

        # ----- 4. Priority ranking -----
        all_ideas.sort(key=lambda i: i.get("expected_edge", 0), reverse=True)

        self._last_result = {
            "increase_exposure": increase_exposure,
            "decrease_exposure": decrease_exposure,
            "new_positions": new_positions,
            "priority_ranked_ideas": all_ideas,
        }
        return self._last_result

    # ------------------------------------------------------------------
    # Telegram formatting
    # ------------------------------------------------------------------

    def format_telegram(self) -> str:
        """Compact one-liner for Telegram digest."""
        if not self._last_result:
            return "No optimisation run yet."

        parts: list[str] = []

        for inc in self._last_result.get("increase_exposure", [])[:2]:
            strategy = inc.get("strategy", "?")
            reason_short = inc.get("reason", "").split(":")[0] if inc.get("reason") else "signal"
            parts.append(f"Increase {strategy} ({reason_short})")

        for dec in self._last_result.get("decrease_exposure", [])[:2]:
            strategy = dec.get("strategy", "?")
            reason_short = dec.get("reason", "").split(":")[0] if dec.get("reason") else "weak"
            parts.append(f"Reduce {strategy} ({reason_short})")

        for np_ in self._last_result.get("new_positions", [])[:2]:
            symbol = np_.get("symbol", "?")
            notional = np_.get("notional", 0)
            reason_short = np_.get("reason", "")[:30]
            parts.append(f"New: {symbol} ${notional:,.0f} ({reason_short})")

        if not parts:
            return "Optimizer: No changes recommended."

        return "Optimizer: " + " | ".join(parts)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_new_allocation(
        signal: float, pnl: float, current: float
    ) -> float:
        """Dynamic sizing rules.

        - Winning strategy with strong signal → increase 50%
        - Losing strategy with weak signal → decrease 50%
        - Otherwise interpolate linearly
        """
        if pnl > 0 and signal >= 0.7:
            # Winning + strong signal → increase 50%
            new = current * 1.5
        elif pnl <= 0 and signal < 0.3:
            # Losing + weak signal → decrease 50%
            new = current * 0.5
        elif pnl > 0 and signal >= 0.5:
            # Winning + moderate signal → slight increase
            new = current * 1.2
        elif pnl <= 0 and signal < 0.5:
            # Losing + moderate signal → slight decrease
            new = current * 0.8
        else:
            # Neutral
            new = current

        return max(MIN_ALLOCATION, min(MAX_ALLOCATION, new))

    @staticmethod
    def _increase_reason(signal: float, pnl: float) -> str:
        if signal >= 0.7 and pnl > 0:
            return "Strong signal + winning streak: size up 50%"
        if signal >= 0.5:
            return "Moderate signal + positive PnL: size up 20%"
        return "Signal improving"

    @staticmethod
    def _decrease_reason(signal: float, pnl: float) -> str:
        if signal < 0.3 and pnl <= 0:
            return "Weak signal + losing streak: cut 50%"
        if signal < 0.5:
            return "Fading signal + negative PnL: reduce 20%"
        return "Signal weakening"
