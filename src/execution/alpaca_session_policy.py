#!/usr/bin/env python3
"""Alpaca session-policy helpers for overnight and extended-hours constraints."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from src.core.market_session_classifier import MarketSessionClassifier, SessionClassification


@dataclass(frozen=True)
class SessionConstraintDecision:
    """Decision returned for session-specific Alpaca order constraints."""

    allowed: bool
    session_context: Dict[str, Any]
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)
    asset_metadata_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AlpacaSessionPolicy:
    """Evaluate Alpaca 24/5 session constraints from order intent and asset metadata."""

    def __init__(self, classifier: Optional[MarketSessionClassifier] = None):
        self.classifier = classifier or MarketSessionClassifier()

    def evaluate_equity_order(
        self,
        *,
        symbol: str,
        order: Dict[str, Any],
        asset_metadata: Optional[Dict[str, Any]] = None,
        timestamp_utc: Optional[Any] = None,
    ) -> SessionConstraintDecision:
        asset_metadata = asset_metadata or {}
        session = self.classifier.classify(timestamp_utc, asset_class="equity")
        checks_passed: List[str] = []
        checks_failed: List[str] = []

        order_type = str(order.get("type", "")).lower()
        tif = str(order.get("time_in_force", "")).lower()

        if session.session == "overnight":
            if bool(asset_metadata.get("overnight_tradable")):
                checks_passed.append("overnight_tradable")
            else:
                checks_failed.append("overnight_tradable_required")

            if not bool(asset_metadata.get("overnight_halted")):
                checks_passed.append("not_overnight_halted")
            else:
                checks_failed.append("overnight_halted")

            if order_type == "limit":
                checks_passed.append("overnight_limit_only_satisfied")
            else:
                checks_failed.append("overnight_limit_only")

            if tif == "day":
                checks_passed.append("overnight_day_tif_satisfied")
            else:
                checks_failed.append("overnight_day_tif_required")
        elif session.session in {"pre_market", "after_hours"}:
            checks_passed.append("extended_hours_session_detected")
        elif session.session == "regular":
            checks_passed.append("regular_session_detected")
        else:
            checks_failed.append("market_closed")

        return SessionConstraintDecision(
            allowed=not checks_failed,
            session_context=session.to_dict(),
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            asset_metadata_summary={
                "symbol": str(symbol or "").upper(),
                "tradable": bool(asset_metadata.get("tradable")) if asset_metadata else None,
                "shortable": bool(asset_metadata.get("shortable")) if asset_metadata else None,
                "overnight_tradable": bool(asset_metadata.get("overnight_tradable")) if asset_metadata else False,
                "overnight_halted": bool(asset_metadata.get("overnight_halted")) if asset_metadata else False,
            },
        )
