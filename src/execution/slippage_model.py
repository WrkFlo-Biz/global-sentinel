#!/usr/bin/env python3
"""Pre-trade slippage and impact estimation."""
from __future__ import annotations

from typing import Any, Dict, List


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _clip_01(value: Any, default: float) -> float:
    raw = _safe_optional_float(value)
    if raw is None:
        return default
    return max(0.0, min(raw, 1.0))


def compute_global_net_ev_ranking(
    *,
    expected_edge_bps: Any = None,
    expected_cost_bps: Any = None,
    confidence_score: Any = None,
    size_multiplier: Any = 1.0,
    fill_feasibility_score: Any = None,
    fill_quality_score: Any = None,
    session_liquidity_score: Any = None,
    reject_risk_probability: Any = None,
    do_not_route: Any = False,
) -> Dict[str, float]:
    """Canonical net-EV ranking profile used by optimizer/router paths."""
    edge_bps = _safe_optional_float(expected_edge_bps)
    if edge_bps is None:
        edge_bps = _safe_float(confidence_score, 0.0) * 100.0

    cost_bps = _safe_float(expected_cost_bps, 0.0)
    net_bps = edge_bps - cost_bps

    size_mult = max(_safe_float(size_multiplier, 1.0), 0.01)
    fill_feas = _clip_01(fill_feasibility_score, 0.5)
    fill_quality = _clip_01(fill_quality_score, 0.6)
    session_liquidity = _clip_01(session_liquidity_score, 0.6)
    reject_risk = _clip_01(reject_risk_probability, 0.0)

    quality_multiplier = (
        fill_feas
        * fill_quality
        * session_liquidity
        * (1.0 - min(reject_risk, 0.8) * 0.5)
    )
    if bool(do_not_route):
        quality_multiplier *= 0.2

    ranking_score = (net_bps / 100.0) * size_mult * quality_multiplier
    return {
        "expected_edge_bps": round(edge_bps, 2),
        "expected_cost_bps": round(cost_bps, 2),
        "net_expected_value_bps": round(net_bps, 2),
        "quality_multiplier": round(quality_multiplier, 6),
        "ranking_score": round(ranking_score, 6),
        "fill_feasibility_score": round(fill_feas, 4),
        "fill_quality_score": round(fill_quality, 4),
        "session_liquidity_score": round(session_liquidity, 4),
        "reject_risk_probability": round(reject_risk, 4),
        "size_multiplier": round(size_mult, 4),
    }


class SlippageModel:
    """Estimate execution cost from spread, volume, volatility, and war stress."""

    def __init__(
        self,
        spread_weight: float = 0.5,
        volume_impact_coeff: float = 15.0,
        volatility_kappa: float = 120.0,
        war_vix_surcharge_bps: float = 8.0,
        liquidity_penalty_coeff: float = 14.0,
        fill_quality_penalty_coeff: float = 18.0,
        short_borrow_default_bps: float = 7.0,
        hard_to_borrow_surcharge_bps: float = 18.0,
    ):
        self.spread_weight = spread_weight
        self.volume_impact_coeff = volume_impact_coeff
        self.volatility_kappa = volatility_kappa
        self.war_vix_surcharge_bps = war_vix_surcharge_bps
        self.liquidity_penalty_coeff = liquidity_penalty_coeff
        self.fill_quality_penalty_coeff = fill_quality_penalty_coeff
        self.short_borrow_default_bps = short_borrow_default_bps
        self.hard_to_borrow_surcharge_bps = hard_to_borrow_surcharge_bps

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
        spread_bps = _safe_float(market_data.get("spread_bps"), 0.0)
        if spread_bps <= 0.0:
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
        session_liquidity_score = self._session_liquidity_score(market_data, adv=adv, spread_bps=spread_bps)
        fill_quality_score = self._fill_quality_score(market_data)
        liquidity_penalty_bps = max(0.0, (1.0 - session_liquidity_score) * self.liquidity_penalty_coeff)
        fill_quality_penalty_bps = max(0.0, (1.0 - fill_quality_score) * self.fill_quality_penalty_coeff)
        borrow_cost_bps = self._borrow_cost_bps(direction=direction, market_data=market_data)
        expected_slippage_bps = spread_cost_bps + volatility_cost_bps + liquidity_penalty_bps
        total_cost_bps = (
            expected_slippage_bps
            + volume_cost_bps
            + war_premium_bps
            + borrow_cost_bps
            + fill_quality_penalty_bps
        )
        return {
            "symbol": symbol,
            "direction": direction,
            "expected_spread_cost_bps": spread_cost_bps,
            "expected_slippage_bps": expected_slippage_bps,
            "expected_market_impact_bps": volume_cost_bps,
            "expected_borrow_cost_bps": borrow_cost_bps,
            "expected_fill_quality_cost_bps": fill_quality_penalty_bps,
            "expected_liquidity_cost_bps": liquidity_penalty_bps,
            "war_premium_bps": war_premium_bps,
            "session_liquidity_score": round(session_liquidity_score, 4),
            "fill_quality_score": round(fill_quality_score, 4),
            "total_expected_cost_bps": total_cost_bps,
            "total_expected_cost_usd": notional * total_cost_bps / 10000.0,
            "confidence": self._confidence_score(
                bid=bid,
                ask=ask,
                adv=adv,
                fill_quality_score=fill_quality_score,
                session_liquidity_score=session_liquidity_score,
            ),
            "model_used": "spread+volume+volatility+borrow+fill_quality+session_liquidity+war_premium",
        }

    def _session_liquidity_score(
        self,
        market_data: Dict[str, Any],
        *,
        adv: float,
        spread_bps: float,
    ) -> float:
        explicit = _safe_float(market_data.get("session_liquidity_score"), -1.0)
        if explicit >= 0.0:
            return max(0.0, min(explicit, 1.0))

        bucket = str(market_data.get("session_bucket") or market_data.get("bucket") or "").lower()
        bucket_score = 0.65
        if "opening" in bucket:
            bucket_score = 0.72
        elif "power" in bucket:
            bucket_score = 0.69
        elif "lunch" in bucket:
            bucket_score = 0.46
        elif "close_exhaustion" in bucket:
            bucket_score = 0.38

        rvol = _safe_float(market_data.get("rvol"), 1.0)
        liquidity_score = _safe_float(market_data.get("liquidity_score"), -1.0)
        if liquidity_score >= 0.0:
            bucket_score = 0.55 * bucket_score + 0.45 * max(0.0, min(liquidity_score, 1.0))

        if rvol > 0:
            bucket_score += min(max(rvol - 1.0, -1.0), 1.0) * 0.12
        if spread_bps > 0:
            bucket_score -= min(spread_bps / 150.0, 0.20)
        if adv <= 5_000:
            bucket_score -= 0.12
        elif adv <= 50_000:
            bucket_score -= 0.06
        return max(0.0, min(bucket_score, 1.0))

    def _fill_quality_score(self, market_data: Dict[str, Any]) -> float:
        explicit = _safe_float(market_data.get("fill_quality_score"), -1.0)
        if explicit >= 0.0:
            return max(0.0, min(explicit, 1.0))

        feasibility = _safe_float(market_data.get("fill_feasibility_score"), 0.6)
        completion = _safe_float(market_data.get("fill_completion_probability"), feasibility)
        reject_risk = _safe_float(market_data.get("reject_risk_probability"), 0.08)
        partial_fill_probability = _safe_float(market_data.get("partial_fill_probability"), 0.15)
        score = (
            feasibility * 0.4
            + completion * 0.35
            + (1.0 - min(reject_risk, 1.0)) * 0.15
            + (1.0 - min(partial_fill_probability, 1.0)) * 0.10
        )
        return max(0.0, min(score, 1.0))

    def _borrow_cost_bps(self, direction: str, market_data: Dict[str, Any]) -> float:
        side = str(direction or "").lower()
        if "short" not in side and "sell" not in side:
            return 0.0

        borrow_fee_bps = _safe_float(market_data.get("borrow_fee_bps"), 0.0)
        if borrow_fee_bps <= 0.0:
            borrow_fee_bps = self.short_borrow_default_bps
        if bool(market_data.get("hard_to_borrow")):
            borrow_fee_bps += self.hard_to_borrow_surcharge_bps
        return max(0.0, borrow_fee_bps)

    @staticmethod
    def _confidence_score(
        *,
        bid: float,
        ask: float,
        adv: float,
        fill_quality_score: float,
        session_liquidity_score: float,
    ) -> float:
        baseline = 0.55
        if bid > 0 and ask > 0 and adv > 1:
            baseline = 0.72
        return round(
            max(
                0.35,
                min(
                    baseline + fill_quality_score * 0.12 + session_liquidity_score * 0.08,
                    0.97,
                ),
            ),
            4,
        )

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
