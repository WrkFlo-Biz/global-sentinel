#!/usr/bin/env python3
"""Pre-trade slippage and impact estimation."""
from __future__ import annotations

from typing import Any, Dict, List


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


class SlippageModel:
    """Estimate execution cost from spread, volume, volatility, and war stress."""

    def __init__(
        self,
        spread_weight: float = 0.5,
        volume_impact_coeff: float = 15.0,
        volatility_kappa: float = 120.0,
        war_vix_surcharge_bps: float = 8.0,
    ):
        self.spread_weight = spread_weight
        self.volume_impact_coeff = volume_impact_coeff
        self.volatility_kappa = volatility_kappa
        self.war_vix_surcharge_bps = war_vix_surcharge_bps

    def estimate(
        self,
        symbol: str,
        direction: str,
        quantity: int,
        order_type: str,
        market_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        bid = _safe_float(market_data.get("bid"), 0.0)
        ask = _safe_float(market_data.get("ask"), 0.0)
        last_price = _safe_float(market_data.get("last_price"), ask or bid or 0.0)
        spread = max(ask - bid, 0.0) if bid > 0 and ask > 0 else 0.0
        spread_bps = (spread / last_price * 10000.0) if last_price else 0.0
        spread_cost_bps = spread_bps * self.spread_weight if str(order_type).lower() == "market" else spread_bps * 0.2

        adv = max(_safe_float(market_data.get("avg_daily_volume"), 0.0), 1.0)
        notional = abs(quantity) * max(last_price, 0.01)
        order_share = min(abs(quantity) / adv, 1.0)
        volume_cost_bps = order_share * self.volume_impact_coeff * 100.0

        realized_vol = _safe_float(market_data.get("realized_vol"), _safe_float(market_data.get("volatility"), 0.0))
        volatility_cost_bps = (realized_vol * (order_share ** 0.5) * self.volatility_kappa) if realized_vol > 0 else 0.0

        vix = _safe_float(market_data.get("vix"), 0.0)
        war_premium_bps = self.war_vix_surcharge_bps if vix > 25.0 else 0.0
        total_cost_bps = spread_cost_bps + volume_cost_bps + volatility_cost_bps + war_premium_bps
        return {
            "symbol": symbol,
            "direction": direction,
            "expected_slippage_bps": spread_cost_bps + volatility_cost_bps,
            "expected_market_impact_bps": volume_cost_bps,
            "war_premium_bps": war_premium_bps,
            "total_expected_cost_bps": total_cost_bps,
            "total_expected_cost_usd": notional * total_cost_bps / 10000.0,
            "confidence": 0.85 if bid > 0 and ask > 0 and adv > 1 else 0.55,
            "model_used": "spread+volume+volatility+war_premium",
        }

    def calibrate(self, recent_fills: List[Dict[str, Any]]) -> Dict[str, float]:
        if not recent_fills:
            return {
                "spread_weight": self.spread_weight,
                "volume_impact_coeff": self.volume_impact_coeff,
                "volatility_kappa": self.volatility_kappa,
                "war_vix_surcharge_bps": self.war_vix_surcharge_bps,
            }
        observed_slippage = [_safe_float(fill.get("slippage_bps"), 0.0) for fill in recent_fills if fill.get("slippage_bps") is not None]
        if observed_slippage:
            avg = sum(observed_slippage) / len(observed_slippage)
            self.volatility_kappa = max(40.0, avg * 10.0)
            self.war_vix_surcharge_bps = max(2.0, avg * 0.2)
        return {
            "spread_weight": self.spread_weight,
            "volume_impact_coeff": self.volume_impact_coeff,
            "volatility_kappa": self.volatility_kappa,
            "war_vix_surcharge_bps": self.war_vix_surcharge_bps,
        }
