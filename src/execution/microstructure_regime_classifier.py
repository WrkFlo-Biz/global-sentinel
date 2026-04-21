#!/usr/bin/env python3
"""Classify current market microstructure conditions."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List


@dataclass
class MicrostructureRegime:
    state: str
    confidence: float
    contributing_factors: List[str]
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()


NORMAL_LIQUIDITY = "NORMAL_LIQUIDITY"
SPREAD_WIDENING = "SPREAD_WIDENING"
PARTIAL_FILL_HEAVY = "PARTIAL_FILL_HEAVY"
DELAYED_ACK = "DELAYED_ACK"
STALE_BROKER_STATE = "STALE_BROKER_STATE"
QUEUE_DECAY = "QUEUE_DECAY"
VOLATILITY_AUCTION_RISK = "VOLATILITY_AUCTION_RISK"


class MicrostructureRegimeClassifier:
    """Infer a routing regime from recent fills, quotes, and broker health."""

    def __init__(self, spread_widening_threshold: float = 2.0, fill_rate_threshold: float = 0.70, ack_latency_threshold_ms: float = 500.0, queue_age_threshold_seconds: float = 300.0):
        self.spread_widening_threshold = spread_widening_threshold
        self.fill_rate_threshold = fill_rate_threshold
        self.ack_latency_threshold_ms = ack_latency_threshold_ms
        self.queue_age_threshold_seconds = queue_age_threshold_seconds

    def classify(self, recent_orders: List[Dict[str, Any]], market_data: Dict[str, Any], broker_state: Dict[str, Any]) -> MicrostructureRegime:
        factors: List[str] = []
        regime = NORMAL_LIQUIDITY
        confidence = 0.80

        spread_ratio = float(market_data.get("spread_ratio_vs_normal", 1.0) or 1.0)
        if spread_ratio >= self.spread_widening_threshold:
            regime = SPREAD_WIDENING
            confidence = min(0.99, spread_ratio / max(self.spread_widening_threshold, 1.0) / 2.0)
            factors.append(f"spread_ratio={spread_ratio:.2f}")

        if recent_orders:
            fill_rates = [float(order.get("fill_rate", 1.0) or 1.0) for order in recent_orders]
            avg_fill_rate = sum(fill_rates) / len(fill_rates)
            if avg_fill_rate < self.fill_rate_threshold and regime == NORMAL_LIQUIDITY:
                regime = PARTIAL_FILL_HEAVY
                confidence = max(0.5, 1.0 - avg_fill_rate)
            if avg_fill_rate < self.fill_rate_threshold:
                factors.append(f"fill_rate={avg_fill_rate:.2f}")

        ack_latency = float(broker_state.get("avg_ack_latency_ms", 0.0) or 0.0)
        if ack_latency > self.ack_latency_threshold_ms and regime == NORMAL_LIQUIDITY:
            regime = DELAYED_ACK
            confidence = min(0.95, ack_latency / max(self.ack_latency_threshold_ms, 1.0) / 3.0)
        if ack_latency > self.ack_latency_threshold_ms:
            factors.append(f"ack_latency={ack_latency:.0f}ms")

        if broker_state.get("reconciliation_drift", False):
            if regime == NORMAL_LIQUIDITY:
                regime = STALE_BROKER_STATE
                confidence = 0.85
            factors.append("reconciliation_drift")

        queue_age = float(broker_state.get("max_pending_order_age_seconds", 0.0) or 0.0)
        if queue_age > self.queue_age_threshold_seconds and regime == NORMAL_LIQUIDITY:
            regime = QUEUE_DECAY
            confidence = min(0.90, queue_age / max(self.queue_age_threshold_seconds, 1.0) / 4.0)
        if queue_age > self.queue_age_threshold_seconds:
            factors.append(f"queue_age={queue_age:.0f}s")

        luld_risk = bool(market_data.get("near_luld_band") or market_data.get("halt_risk"))
        if luld_risk:
            regime = VOLATILITY_AUCTION_RISK
            confidence = max(confidence, 0.90)
            factors.append("volatility_auction_risk")

        if not factors:
            factors.append("all_normal")
        return MicrostructureRegime(state=regime, confidence=confidence, contributing_factors=factors)

    def get_execution_parameters(self, regime: MicrostructureRegime) -> Dict[str, Any]:
        defaults = {
            "max_order_size_pct": 0.12,
            "urgency_adjustment": 1.0,
            "spread_tolerance_multiplier": 1.0,
            "partial_fill_patience_seconds": 30,
            "should_pause_new_orders": False,
        }
        if regime.state == SPREAD_WIDENING:
            return {**defaults, "max_order_size_pct": 0.08, "spread_tolerance_multiplier": 1.5, "urgency_adjustment": 0.7}
        if regime.state == PARTIAL_FILL_HEAVY:
            return {**defaults, "max_order_size_pct": 0.06, "partial_fill_patience_seconds": 60, "urgency_adjustment": 0.5}
        if regime.state == DELAYED_ACK:
            return {**defaults, "max_order_size_pct": 0.06, "urgency_adjustment": 0.3}
        if regime.state == STALE_BROKER_STATE:
            return {**defaults, "should_pause_new_orders": True, "urgency_adjustment": 0.0}
        if regime.state == QUEUE_DECAY:
            return {**defaults, "max_order_size_pct": 0.04, "urgency_adjustment": 0.5}
        if regime.state == VOLATILITY_AUCTION_RISK:
            return {**defaults, "should_pause_new_orders": True, "urgency_adjustment": 0.0}
        return defaults
