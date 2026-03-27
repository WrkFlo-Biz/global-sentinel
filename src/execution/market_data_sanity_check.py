#!/usr/bin/env python3
"""Market data quality checks run before trade idea packaging."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


_REQUIRED_FIELDS = ("bid", "ask", "last", "volume")

_DEFAULTS = {
    "max_quote_age_seconds": 30.0,
    "max_spread_pct": 0.05,
    "max_price_jump_pct": 0.20,
    "min_volume": 100,
}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


@dataclass
class SanityCheckResult:
    passed: bool
    reason: str
    checks: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Spec requires top-level key "pass" (not "passed").
        d["pass"] = d.pop("passed")
        return d


class MarketDataSanityCheck:
    """Validate market-data quality before trade idea packaging."""

    def __init__(
        self,
        max_quote_age_seconds: float = _DEFAULTS["max_quote_age_seconds"],
        max_spread_pct: float = _DEFAULTS["max_spread_pct"],
        max_price_jump_pct: float = _DEFAULTS["max_price_jump_pct"],
        min_volume: int = int(_DEFAULTS["min_volume"]),
    ):
        self.max_quote_age_seconds = max_quote_age_seconds
        self.max_spread_pct = max_spread_pct
        self.max_price_jump_pct = max_price_jump_pct
        self.min_volume = min_volume

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------
    @classmethod
    def from_yaml_file(cls, path: Path) -> "MarketDataSanityCheck":
        """Load thresholds from a YAML config file, falling back to defaults."""
        try:
            import yaml

            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            raw = {}
        valid_keys = set(cls.__init__.__code__.co_varnames) & set(raw)
        return cls(**{k: raw[k] for k in valid_keys})

    @classmethod
    def from_default_config(cls) -> "MarketDataSanityCheck":
        """Try to load config/pre_trade_controls.yaml relative to project root."""
        candidates = [
            Path(__file__).resolve().parents[2] / "config" / "pre_trade_controls.yaml",
            Path("/opt/global-sentinel/config/pre_trade_controls.yaml"),
        ]
        for p in candidates:
            if p.is_file():
                return cls.from_yaml_file(p)
        return cls()

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------
    @staticmethod
    def _check_missing_fields(quote: Dict[str, Any]) -> Dict[str, Any]:
        missing = [f for f in _REQUIRED_FIELDS if f not in quote or quote[f] is None]
        return {
            "name": "missing_fields",
            "passed": len(missing) == 0,
            "severity": "fail",
            "value": missing,
            "threshold": list(_REQUIRED_FIELDS),
        }

    @staticmethod
    def _check_zero_price(quote: Dict[str, Any]) -> Dict[str, Any]:
        bad: List[str] = []
        for fld in ("bid", "ask", "last"):
            val = quote.get(fld)
            if val is not None and _safe_float(val) <= 0:
                bad.append(fld)
        return {
            "name": "zero_price",
            "passed": len(bad) == 0,
            "severity": "fail",
            "value": bad,
            "threshold": "> 0",
        }

    def _check_stale_quote(self, quote: Dict[str, Any]) -> Dict[str, Any]:
        age = _safe_float(quote.get("quote_age_seconds"), 0.0)
        return {
            "name": "stale_quote",
            "passed": age <= self.max_quote_age_seconds,
            "severity": "fail",
            "value": age,
            "threshold": self.max_quote_age_seconds,
        }

    @staticmethod
    def _check_crossed_market(quote: Dict[str, Any]) -> Dict[str, Any]:
        bid = _safe_float(quote.get("bid"), 0.0)
        ask = _safe_float(quote.get("ask"), 0.0)
        crossed = bid > 0 and ask > 0 and bid > ask
        return {
            "name": "crossed_market",
            "passed": not crossed,
            "severity": "fail",
            "value": {"bid": bid, "ask": ask},
            "threshold": "bid <= ask",
        }

    @staticmethod
    def _check_locked_market(quote: Dict[str, Any]) -> Dict[str, Any]:
        bid = _safe_float(quote.get("bid"), 0.0)
        ask = _safe_float(quote.get("ask"), 0.0)
        locked = bid > 0 and ask > 0 and bid == ask
        return {
            "name": "locked_market",
            "passed": True,  # warn only — does not block
            "severity": "warn" if locked else "ok",
            "value": {"bid": bid, "ask": ask},
            "threshold": "bid != ask",
        }

    def _check_impossible_jump(self, quote: Dict[str, Any]) -> Dict[str, Any]:
        last = _safe_float(quote.get("last"), 0.0)
        prev_close = _safe_float(quote.get("prev_close"), 0.0)
        if prev_close <= 0 or last <= 0:
            return {
                "name": "impossible_jump",
                "passed": True,
                "severity": "skip",
                "value": None,
                "threshold": self.max_price_jump_pct,
            }
        pct_change = abs(last - prev_close) / prev_close
        return {
            "name": "impossible_jump",
            "passed": pct_change <= self.max_price_jump_pct,
            "severity": "fail" if pct_change > self.max_price_jump_pct else "ok",
            "value": pct_change,
            "threshold": self.max_price_jump_pct,
        }

    def _check_spread_too_wide(self, quote: Dict[str, Any]) -> Dict[str, Any]:
        bid = _safe_float(quote.get("bid"), 0.0)
        ask = _safe_float(quote.get("ask"), 0.0)
        if bid <= 0 or ask <= 0 or bid > ask:
            return {
                "name": "spread_too_wide",
                "passed": True,
                "severity": "skip",
                "value": None,
                "threshold": self.max_spread_pct,
            }
        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid if mid > 0 else 0.0
        return {
            "name": "spread_too_wide",
            "passed": spread_pct <= self.max_spread_pct,
            "severity": "fail" if spread_pct > self.max_spread_pct else "ok",
            "value": spread_pct,
            "threshold": self.max_spread_pct,
        }

    def _check_volume_too_low(self, quote: Dict[str, Any]) -> Dict[str, Any]:
        volume = _safe_float(quote.get("volume"), 0.0)
        low = volume < self.min_volume
        return {
            "name": "volume_too_low",
            "passed": True,  # warn only — does not block
            "severity": "warn" if low else "ok",
            "value": volume,
            "threshold": self.min_volume,
        }

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------
    def check(self, quote: Dict[str, Any]) -> Dict[str, Any]:
        """Run all sanity checks against *quote* and return structured result.

        Returns
        -------
        dict with keys ``pass`` (bool), ``reason`` (str), ``checks`` (list).
        """
        checks: List[Dict[str, Any]] = [
            self._check_missing_fields(quote),
            self._check_zero_price(quote),
            self._check_stale_quote(quote),
            self._check_crossed_market(quote),
            self._check_locked_market(quote),
            self._check_impossible_jump(quote),
            self._check_spread_too_wide(quote),
            self._check_volume_too_low(quote),
        ]

        hard_fail = [c for c in checks if not c["passed"]]
        warnings = [c for c in checks if c["severity"] == "warn"]

        if hard_fail:
            reason = "; ".join(c["name"] for c in hard_fail)
        elif warnings:
            reason = "passed with warnings: " + ", ".join(c["name"] for c in warnings)
        else:
            reason = "all checks passed"

        return SanityCheckResult(
            passed=len(hard_fail) == 0,
            reason=reason,
            checks=checks,
        ).to_dict()
