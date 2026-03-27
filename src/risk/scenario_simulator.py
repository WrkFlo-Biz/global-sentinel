"""Scenario simulator for stress-testing portfolio against geopolitical tail events."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Sector betas for PnL estimation
SECTOR_BETAS = {
    "energy": {"oil": 0.5, "market": 0.3},
    "shipping": {"oil": 0.3, "market": 0.2},
    "airlines": {"oil": -0.4, "market": 0.6},
    "defense": {"oil": 0.0, "market": 0.1},
    "gold": {"oil": 0.3, "market": -0.2},
    "em_short": {"em": -1.0, "market": -0.5},
    "vol": {"vix": 1.0, "market": -0.8},
}


class ScenarioSimulator:
    """Simulate predefined geopolitical / market shock scenarios against a portfolio."""

    SCENARIOS: dict[str, dict[str, Any]] = {
        "ceasefire": {
            "oil": -20,
            "short_squeeze": 0.10,
            "vol_crush": -0.40,
            "description": "War ends suddenly",
        },
        "hormuz_mines": {
            "oil": +30,
            "energy": 0.15,
            "airlines": -0.10,
            "description": "Hormuz fully mined",
        },
        "triple_chokepoint": {
            "oil": +50,
            "vix_to": 60,
            "em": -0.15,
            "description": "All 3 chokepoints disrupted",
        },
        "short_squeeze": {
            "all_shorts": 0.08,
            "description": "All shorts gap up simultaneously",
        },
        "flash_crash": {
            "market": -0.05,
            "description": "Market drops 5% in 30 minutes",
        },
        "information_blackout": {
            "bridges_stale": 5,
            "duration_hours": 1,
            "description": "5 bridges go dark",
        },
    }

    def __init__(self) -> None:
        self._last_results: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # Core simulation
    # ------------------------------------------------------------------

    def simulate(
        self, scenario_name: str, portfolio_state: dict[str, Any]
    ) -> dict[str, Any]:
        """Run a single scenario against the current portfolio.

        Args:
            scenario_name: Key into ``SCENARIOS``.
            portfolio_state: Must contain at minimum:
                - ``positions``: list of dicts with ``symbol``, ``sector``,
                  ``market_value``, ``side`` (long/short).
                - ``equity``: total account equity (float).
                - ``margin_used``: current margin usage (float).
                - ``margin_limit``: max margin allowed (float).

        Returns:
            Dict with pnl_impact_usd, margin_impact, liquidation_risk,
            recommended_actions, and positions_at_risk.
        """
        if scenario_name not in self.SCENARIOS:
            raise ValueError(
                f"Unknown scenario '{scenario_name}'. "
                f"Available: {list(self.SCENARIOS)}"
            )

        scenario = self.SCENARIOS[scenario_name]
        positions = portfolio_state.get("positions", [])
        equity = portfolio_state.get("equity", 0.0)
        margin_used = portfolio_state.get("margin_used", 0.0)
        margin_limit = portfolio_state.get("margin_limit", equity * 2)

        total_pnl = 0.0
        positions_at_risk: list[dict[str, Any]] = []
        recommended_actions: list[str] = []

        for pos in positions:
            symbol = pos.get("symbol", "???")
            sector = pos.get("sector", "unknown")
            value = pos.get("market_value", 0.0)
            side = pos.get("side", "long")
            sign = 1.0 if side == "long" else -1.0

            estimated_loss = self._estimate_position_impact(
                scenario, sector, value, sign
            )
            total_pnl += estimated_loss

            if estimated_loss < 0:
                positions_at_risk.append(
                    {
                        "symbol": symbol,
                        "current_value": value,
                        "estimated_loss": round(estimated_loss, 2),
                    }
                )

        # Handle short-squeeze scenario hitting all shorts
        if "all_shorts" in scenario:
            squeeze_pct = scenario["all_shorts"]
            for pos in positions:
                if pos.get("side") == "short":
                    loss = -abs(pos.get("market_value", 0.0)) * squeeze_pct
                    total_pnl += loss
                    positions_at_risk.append(
                        {
                            "symbol": pos.get("symbol", "???"),
                            "current_value": pos.get("market_value", 0.0),
                            "estimated_loss": round(loss, 2),
                        }
                    )

        # Margin impact — losses increase margin usage
        margin_impact = abs(min(total_pnl, 0.0))
        new_margin = margin_used + margin_impact
        liquidation_risk = new_margin > margin_limit or (equity + total_pnl) < 0

        # Build recommended actions
        if liquidation_risk:
            recommended_actions.append(
                "URGENT: Reduce positions immediately — liquidation risk detected"
            )
        if total_pnl < -equity * 0.10:
            recommended_actions.append(
                f"Hedge exposure: scenario causes >{abs(total_pnl / equity) * 100:.0f}% drawdown"
            )
        if scenario.get("oil", 0) > 20:
            recommended_actions.append("Consider adding oil calls as tail hedge")
        if scenario.get("oil", 0) < -10:
            recommended_actions.append(
                "Reduce energy longs or buy puts ahead of ceasefire risk"
            )
        if scenario.get("vix_to", 0) > 40:
            recommended_actions.append("Buy VIX puts to hedge vol spike")
        if "bridges_stale" in scenario:
            recommended_actions.append(
                "Tighten stops — operating with degraded intelligence"
            )
        if not recommended_actions:
            recommended_actions.append("Portfolio resilient to this scenario")

        # Sort positions at risk by severity
        positions_at_risk.sort(key=lambda p: p["estimated_loss"])

        result = {
            "scenario": scenario_name,
            "description": scenario.get("description", ""),
            "pnl_impact_usd": round(total_pnl, 2),
            "margin_impact": round(margin_impact, 2),
            "liquidation_risk": liquidation_risk,
            "recommended_actions": recommended_actions,
            "positions_at_risk": positions_at_risk,
        }

        self._last_results[scenario_name] = result
        return result

    def simulate_all(
        self, portfolio_state: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Run every predefined scenario and return a mapping of name → result."""
        results: dict[str, dict[str, Any]] = {}
        for name in self.SCENARIOS:
            try:
                results[name] = self.simulate(name, portfolio_state)
            except Exception:
                logger.exception("Scenario '%s' failed", name)
        self._last_results = results
        return results

    def format_telegram(self) -> str:
        """Compact summary of the worst-case scenario from the last run."""
        if not self._last_results:
            return "No scenarios simulated yet."

        worst_name = min(
            self._last_results,
            key=lambda k: self._last_results[k]["pnl_impact_usd"],
        )
        worst = self._last_results[worst_name]
        pnl = worst["pnl_impact_usd"]
        liq = " LIQUIDATION RISK" if worst["liquidation_risk"] else ""

        at_risk_str = ""
        if worst["positions_at_risk"]:
            top3 = worst["positions_at_risk"][:3]
            parts = [
                f"{p['symbol']} ${p['estimated_loss']:+,.0f}" for p in top3
            ]
            at_risk_str = " | At risk: " + ", ".join(parts)

        actions_str = ""
        if worst["recommended_actions"]:
            actions_str = " | " + worst["recommended_actions"][0]

        return (
            f"Worst-case: {worst_name} ({worst['description']}) "
            f"PnL ${pnl:+,.0f}{liq}{at_risk_str}{actions_str}"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_position_impact(
        scenario: dict[str, Any],
        sector: str,
        value: float,
        sign: float,
    ) -> float:
        """Estimate dollar impact on a single position using sector betas.

        Uses the ``SECTOR_BETAS`` mapping to translate scenario shocks
        (oil price change, market move, VIX level, EM move) into an
        estimated dollar P&L for the position.
        """
        betas = SECTOR_BETAS.get(sector, {"market": 0.3})
        impact = 0.0

        # Oil shock — scenario["oil"] is absolute $ move on crude
        if "oil" in scenario and "oil" in betas:
            oil_move_pct = scenario["oil"] / 80.0  # normalise to ~$80 base
            impact += oil_move_pct * betas["oil"] * value * sign

        # Broad market move
        if "market" in scenario and "market" in betas:
            impact += scenario["market"] * betas["market"] * value * sign

        # EM move (affects em_short positions)
        if "em" in scenario and "em" in betas:
            impact += scenario["em"] * betas["em"] * value * sign

        # VIX spike — translate absolute VIX target into move vs assumed 18
        if "vix_to" in scenario and "vix" in betas:
            vix_move = (scenario["vix_to"] - 18) / 18
            impact += vix_move * betas["vix"] * value * sign

        # Vol crush (ceasefire)
        if "vol_crush" in scenario and "vix" in betas:
            impact += scenario["vol_crush"] * betas["vix"] * value * sign

        # Direct sector percentage move (e.g. energy: +15%)
        if sector in scenario and isinstance(scenario[sector], float):
            impact += scenario[sector] * value * sign

        return impact
