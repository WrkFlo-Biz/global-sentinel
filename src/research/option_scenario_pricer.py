"""Lightweight scenario pricer for option-style hedge research.

Not a full derivatives library — a practical research helper for
scenario ranking and stress-test payoff evaluation.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, Any, List


@dataclass
class OptionScenarioInput:
    symbol: str
    underlying_price: float
    strike: float
    premium: float
    option_type: str  # "call" | "put"
    contracts: int = 1
    contract_multiplier: int = 100


@dataclass
class OptionScenarioResult:
    symbol: str
    scenario_move_pct: float
    scenario_underlying_price: float
    intrinsic_value: float
    pnl: float
    option_type: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class OptionScenarioPricer:

    def price_scenario(
        self,
        inp: OptionScenarioInput,
        scenario_move_pct: float,
    ) -> OptionScenarioResult:
        new_px = inp.underlying_price * (1.0 + scenario_move_pct)

        if inp.option_type.lower() == "call":
            intrinsic = max(new_px - inp.strike, 0.0)
        elif inp.option_type.lower() == "put":
            intrinsic = max(inp.strike - new_px, 0.0)
        else:
            raise ValueError(f"Unsupported option_type: {inp.option_type}")

        pnl_per_share = intrinsic - inp.premium
        pnl = pnl_per_share * inp.contracts * inp.contract_multiplier

        return OptionScenarioResult(
            symbol=inp.symbol,
            scenario_move_pct=scenario_move_pct,
            scenario_underlying_price=new_px,
            intrinsic_value=intrinsic,
            pnl=pnl,
            option_type=inp.option_type.lower(),
        )

    def price_grid(
        self,
        inp: OptionScenarioInput,
        scenario_moves_pct: List[float],
    ) -> List[Dict[str, Any]]:
        return [self.price_scenario(inp, move).to_dict() for move in scenario_moves_pct]
