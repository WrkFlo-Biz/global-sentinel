#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Politician Alpha Executor

Purpose:
- Convert high-conviction Politician Alpha signals into shadow orders
  via the existing ShadowOrderRouter
- Enforce strict safety rules: paper-only, rate-limited, position-sized
- Log all orders for audit trail

Safety:
1. ALL orders are paper/shadow mode only (hard-enforced)
2. Requires politician_alpha_score > threshold AND GSS field confirmation
3. Position sizing: max 5% of portfolio per politician signal
4. Rate limit: max 1 politician-triggered order per ticker per day
5. Logged to logs/execution/politician_alpha_orders.jsonl
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from src.core.control_state_snapshot import read_control_state_snapshot


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Confidence buckets for Telegram formatting
# ---------------------------------------------------------------------------
_CONFIDENCE_LABELS = {
    (9.0, float("inf")): "VERY HIGH",
    (7.0, 9.0): "HIGH",
    (5.0, 7.0): "MODERATE",
    (0.0, 5.0): "LOW",
}


def _confidence_label(score: float) -> str:
    for (lo, hi), label in _CONFIDENCE_LABELS.items():
        if lo <= score < hi:
            return label
    return "LOW"


class PoliticianAlphaExecutor:
    """
    Converts high-conviction Politician Alpha signals into shadow orders
    via the existing ShadowOrderRouter.

    Safety rules:
    1. ALL orders are paper/shadow mode only
    2. Requires politician_alpha_score > threshold AND GSS field confirmation
    3. Position sizing: max 5% of portfolio per politician signal
    4. Rate limit: max 1 politician-triggered order per ticker per day
    5. Logged to logs/execution/politician_alpha_orders.jsonl
    """

    # "Golden Cross" thresholds
    POLITICAL_SCORE_THRESHOLD = 5.0
    Z_SCORE_THRESHOLD = 2.0

    # Position sizing tiers (political_score -> portfolio %)
    SIZING_TIERS = [
        (9.0, 5.0),   # score >= 9  -> 5% max
        (7.0, 3.0),   # score >= 7  -> 3%
        (5.0, 2.0),   # score >= 5  -> 2%
    ]

    # Risk parameters for politician plays
    STOP_LOSS_PCT = 3.0   # 3% stop loss
    TAKE_PROFIT_PCT = 8.0  # 8% take profit
    DEFAULT_TIF = "gtc"    # politician signals are medium-term

    def __init__(self, repo_root: Path, broker_name: Optional[str] = None):
        self.repo_root = repo_root
        self.log_dir = repo_root / "logs" / "execution"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.order_log_path = self.log_dir / "politician_alpha_orders.jsonl"

        # Control file paths
        self.kill_switch_path = repo_root / "control" / "kill_switch.json"
        self.manual_veto_path = repo_root / "control" / "manual_veto.json"

        # Lazy-loaded collaborators (avoid import failures at init)
        self._broker_name = broker_name
        self._router: Optional[Any] = None
        self._strategy_manager: Optional[Any] = None

    # ------------------------------------------------------------------
    # Lazy loaders
    # ------------------------------------------------------------------
    def _get_router(self):
        if self._router is None:
            from src.execution.shadow_order_router import ShadowOrderRouter
            self._router = ShadowOrderRouter(self.repo_root, broker_name=self._broker_name)
        return self._router

    def _get_strategy_manager(self):
        if self._strategy_manager is None:
            from src.execution.strategy_manager import StrategyManager
            self._strategy_manager = StrategyManager(self.repo_root)
        return self._strategy_manager

    # ------------------------------------------------------------------
    # 1. Signal evaluation
    # ------------------------------------------------------------------
    def evaluate_signal(
        self,
        ticker: str,
        political_score: float,
        committee_weight: float,
        gss_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        The "Golden Cross" check:
        - political_score > 5.0 AND z_score > 2.0

        Returns:
            {
                action: "BUY" | "SELL" | "HOLD",
                confidence: str,
                reason: str,
                sizing_pct: float,
                political_score: float,
                z_score: float,
                committee_weight: float,
                advisory_only: True,
            }
        """
        z_score = safe_float(gss_snapshot.get("z_score"), 0.0)
        regime = str(gss_snapshot.get("mode", "NORMAL")).upper()

        result: Dict[str, Any] = {
            "ticker": ticker,
            "action": "HOLD",
            "confidence": "LOW",
            "reason": "",
            "sizing_pct": 0.0,
            "political_score": political_score,
            "z_score": z_score,
            "committee_weight": committee_weight,
            "regime": regime,
            "advisory_only": True,
        }

        # Gate: reject during CRISIS mode
        if regime == "CRISIS":
            result["reason"] = "CRISIS regime active - politician alpha signals suspended"
            return result

        # Gate: political score below threshold
        if political_score < self.POLITICAL_SCORE_THRESHOLD:
            result["reason"] = (
                f"political_score {political_score:.1f} below threshold "
                f"{self.POLITICAL_SCORE_THRESHOLD}"
            )
            return result

        # Gate: z_score below threshold (GSS field confirmation)
        if z_score < self.Z_SCORE_THRESHOLD:
            result["reason"] = (
                f"z_score {z_score:.2f} below threshold {self.Z_SCORE_THRESHOLD} "
                f"- no GSS field confirmation"
            )
            return result

        # Golden Cross achieved
        sizing_pct = self._compute_sizing_pct(political_score)
        confidence = _confidence_label(political_score)

        # Determine direction from committee weight sign
        if committee_weight >= 0:
            action = "BUY"
            direction_reason = "bullish committee activity"
        else:
            action = "SELL"
            direction_reason = "bearish committee activity"

        reason = (
            f"Golden Cross: political_score={political_score:.1f} "
            f"(>{self.POLITICAL_SCORE_THRESHOLD}), "
            f"z_score={z_score:.2f} (>{self.Z_SCORE_THRESHOLD}), "
            f"committee_weight={committee_weight:+.2f} ({direction_reason})"
        )

        result.update({
            "action": action,
            "confidence": confidence,
            "reason": reason,
            "sizing_pct": sizing_pct,
        })
        return result

    # ------------------------------------------------------------------
    # 2. Order generation
    # ------------------------------------------------------------------
    def generate_order(
        self,
        ticker: str,
        signal: Dict[str, Any],
        portfolio_equity: float,
    ) -> Dict[str, Any]:
        """
        Creates an order request compatible with ShadowOrderRouter.

        Position sizing:
          - Score 5-7:  2% of portfolio
          - Score 7-9:  3% of portfolio
          - Score 9+:   5% of portfolio (max)

        Sets stop loss (3%) and take profit (8%).
        TIF: GTC (politician signals are medium-term).
        """
        action = signal.get("action", "HOLD")
        if action == "HOLD":
            raise ValueError(f"Cannot generate order for HOLD signal on {ticker}")

        sizing_pct = signal.get("sizing_pct", 2.0)
        order_value = portfolio_equity * (sizing_pct / 100.0)

        side = "buy" if action == "BUY" else "sell"

        order_request = {
            "symbol": ticker,
            "side": side,
            "type": "market",
            "time_in_force": self.DEFAULT_TIF,
            "notional": round(order_value, 2),
            "extended_hours": False,
            "shadow_mode": True,
            "advisory_only": True,
            # Politician alpha metadata (not sent to broker, used for logging)
            "politician_alpha_meta": {
                "political_score": signal.get("political_score"),
                "committee_weight": signal.get("committee_weight"),
                "z_score": signal.get("z_score"),
                "confidence": signal.get("confidence"),
                "sizing_pct": sizing_pct,
                "stop_loss_pct": self.STOP_LOSS_PCT,
                "take_profit_pct": self.TAKE_PROFIT_PCT,
                "signal_reason": signal.get("reason", ""),
            },
            # Strategy classification: politician plays -> medium_long
            "strategy_context": {
                "strategy_name": "medium_long",
                "holding_period": "swing",
                "source": "politician_alpha",
            },
        }

        return order_request

    # ------------------------------------------------------------------
    # 3. Shadow order submission
    # ------------------------------------------------------------------
    def submit_shadow_order(self, order_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Submit via the existing ShadowOrderRouter.route_package().
        Checks kill_switch and manual_veto before submitting.
        Logs the order with politician_alpha metadata.

        Returns order result dict.
        """
        # Safety: check kill switch
        if self._is_kill_switch_active():
            return {
                "status": "blocked",
                "reason": "kill_switch_active",
                "shadow_only": True,
                "advisory_only": True,
                "timestamp_utc": iso_now(),
            }

        # Safety: check manual veto
        if self._is_manual_veto_active():
            return {
                "status": "blocked",
                "reason": "manual_veto_active",
                "shadow_only": True,
                "advisory_only": True,
                "timestamp_utc": iso_now(),
            }

        ticker = order_request.get("symbol", "UNKNOWN")

        # Safety: rate limit check
        if not self.check_rate_limit(ticker):
            return {
                "status": "rate_limited",
                "reason": f"politician alpha order already placed for {ticker} today",
                "shadow_only": True,
                "advisory_only": True,
                "timestamp_utc": iso_now(),
            }

        meta = order_request.get("politician_alpha_meta", {})

        # Build a minimal package that ShadowOrderRouter expects
        candidate = {
            "symbol": ticker,
            "candidate_id": f"pol-alpha-{uuid.uuid4().hex[:10]}",
            "direction": "bullish" if order_request.get("side") == "buy" else "bearish",
            "confidence_score": 0.8,  # politician signals are pre-vetted
            "strategy_style": "politician_alpha",
            "template_key": "politician_alpha_signal",
            "holding_period": "swing",
            "status": "active",
            "instrument_types": ["equity"],
            "size_multiplier_suggestion": 1.0,
            "price_hints": {},
            "execution_constraints": {},
            "fill_sim_assessment": {},
            "metadata": {
                "political_score": meta.get("political_score"),
                "committee_weight": meta.get("committee_weight"),
                "z_score": meta.get("z_score"),
            },
        }

        strategy_config = {
            "name": "medium_long",
            "holding_period": "swing",
            "time_in_force": self.DEFAULT_TIF,
            "extended_hours": False,
        }

        package = {
            "package_id": f"pol-alpha-pkg-{uuid.uuid4().hex[:10]}",
            "package_type": "politician_alpha",
            "timestamp_utc": iso_now(),
            "effective_mode": "NORMAL",
            "candidates": [candidate],
            "blocked_candidates": [],
            "window_context": {
                "time_window_name": "politician_alpha",
                "watchlist_only_window": False,
            },
            "global_blocks": [],
            "snapshot": {},
        }

        try:
            router = self._get_router()
            route_result = router.route_package(
                package=package,
                max_orders=1,
                min_confidence=0.0,
                strategy_config=strategy_config,
            )

            order_result = {
                "status": "submitted" if route_result.get("submitted_open_or_ack_count", 0) > 0 else "failed",
                "router_run_id": route_result.get("router_run_id"),
                "submitted_count": route_result.get("submitted_open_or_ack_count", 0),
                "errors": route_result.get("errors", []),
                "shadow_only": True,
                "advisory_only": True,
                "timestamp_utc": iso_now(),
            }

        except Exception as e:
            order_result = {
                "status": "error",
                "reason": str(e),
                "shadow_only": True,
                "advisory_only": True,
                "timestamp_utc": iso_now(),
            }

        # Log with full politician alpha metadata
        self._log_order(ticker, order_request, order_result)

        return order_result

    # ------------------------------------------------------------------
    # 4. Rate limiting
    # ------------------------------------------------------------------
    def check_rate_limit(self, ticker: str) -> bool:
        """
        Check if a politician alpha order was already placed for this
        ticker today. Returns True if order is allowed, False if blocked.
        """
        today = date.today().isoformat()

        if not self.order_log_path.exists():
            return True

        try:
            with self.order_log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        if (
                            row.get("ticker") == ticker
                            and row.get("timestamp_utc", "")[:10] == today
                            and row.get("action") in ("BUY", "SELL")
                        ):
                            return False  # already placed today
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

        return True

    # ------------------------------------------------------------------
    # 5. Telegram alert formatting
    # ------------------------------------------------------------------
    def format_telegram_alert(
        self,
        ticker: str,
        signal: Dict[str, Any],
        order_result: Dict[str, Any],
    ) -> str:
        """
        Formats a Telegram notification for a politician alpha order.

        Includes: ticker, political score, committee, action, size,
        confidence, order status.
        """
        action = signal.get("action", "HOLD")
        pol_score = signal.get("political_score", 0)
        committee_wt = signal.get("committee_weight", 0)
        z_score = signal.get("z_score", 0)
        confidence = signal.get("confidence", "LOW")
        sizing = signal.get("sizing_pct", 0)
        reason = signal.get("reason", "")
        regime = signal.get("regime", "NORMAL")

        status = order_result.get("status", "unknown")
        router_id = order_result.get("router_run_id", "n/a")

        lines = [
            "=" * 40,
            "POLITICIAN ALPHA SIGNAL",
            "=" * 40,
            f"Ticker:           {ticker}",
            f"Action:           {action}",
            f"Confidence:       {confidence}",
            f"Political Score:  {pol_score:.1f}",
            f"Committee Weight: {committee_wt:+.2f}",
            f"GSS Z-Score:      {z_score:.2f}",
            f"Regime:           {regime}",
            "",
            f"Position Size:    {sizing:.1f}% of portfolio",
            f"Stop Loss:        {self.STOP_LOSS_PCT}%",
            f"Take Profit:      {self.TAKE_PROFIT_PCT}%",
            f"Time in Force:    {self.DEFAULT_TIF.upper()}",
            "",
            f"Order Status:     {status.upper()}",
            f"Router Run:       {router_id}",
            "",
            f"Reason: {reason}",
            "",
            "[SHADOW/PAPER MODE ONLY - Advisory]",
            "=" * 40,
        ]

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Convenience: full pipeline
    # ------------------------------------------------------------------
    def process_signal(
        self,
        ticker: str,
        political_score: float,
        committee_weight: float,
        gss_snapshot: Dict[str, Any],
        portfolio_equity: float,
    ) -> Dict[str, Any]:
        """
        End-to-end: evaluate -> generate order -> submit -> format alert.

        Returns:
            {
                signal: dict,
                order_request: dict | None,
                order_result: dict | None,
                telegram_alert: str,
                advisory_only: True,
            }
        """
        signal = self.evaluate_signal(
            ticker=ticker,
            political_score=political_score,
            committee_weight=committee_weight,
            gss_snapshot=gss_snapshot,
        )

        result: Dict[str, Any] = {
            "signal": signal,
            "order_request": None,
            "order_result": None,
            "telegram_alert": "",
            "advisory_only": True,
        }

        if signal["action"] == "HOLD":
            result["telegram_alert"] = (
                f"[Politician Alpha] {ticker}: HOLD - {signal['reason']}"
            )
            return result

        order_request = self.generate_order(
            ticker=ticker,
            signal=signal,
            portfolio_equity=portfolio_equity,
        )
        result["order_request"] = order_request

        order_result = self.submit_shadow_order(order_request)
        result["order_result"] = order_result

        result["telegram_alert"] = self.format_telegram_alert(
            ticker=ticker,
            signal=signal,
            order_result=order_result,
        )

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _compute_sizing_pct(self, political_score: float) -> float:
        """Map political score to portfolio sizing percentage."""
        for threshold, pct in self.SIZING_TIERS:
            if political_score >= threshold:
                return pct
        return 0.0

    def _control_flags(self) -> Dict[str, bool]:
        return read_control_state_snapshot(self.repo_root)

    def _is_kill_switch_active(self) -> bool:
        try:
            return self._control_flags()["kill_switch"]
        except Exception:
            pass
        return False

    def _is_manual_veto_active(self) -> bool:
        try:
            return self._control_flags()["manual_veto"]
        except Exception:
            pass
        return False

    def _log_order(
        self,
        ticker: str,
        order_request: Dict[str, Any],
        order_result: Dict[str, Any],
    ):
        """Append to politician_alpha_orders.jsonl."""
        meta = order_request.get("politician_alpha_meta", {})

        row = {
            "schema_version": "politician_alpha_order.v1",
            "timestamp_utc": iso_now(),
            "ticker": ticker,
            "political_score": meta.get("political_score"),
            "committee_weight": meta.get("committee_weight"),
            "gss_z_score": meta.get("z_score"),
            "action": "BUY" if order_request.get("side") == "buy" else "SELL",
            "order_notional": order_request.get("notional"),
            "portfolio_pct": meta.get("sizing_pct"),
            "confidence": meta.get("confidence"),
            "stop_loss_pct": meta.get("stop_loss_pct"),
            "take_profit_pct": meta.get("take_profit_pct"),
            "shadow_only": True,
            "advisory_only": True,
            "order_status": order_result.get("status"),
            "router_run_id": order_result.get("router_run_id"),
            "errors": order_result.get("errors") or [],
        }

        try:
            with self.order_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception:
            pass  # Never let logging failure break order flow


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Politician Alpha Executor - evaluate and submit shadow orders"
    )
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    parser.add_argument("--ticker", required=True, help="Stock ticker symbol")
    parser.add_argument("--political-score", type=float, required=True, help="Politician alpha score")
    parser.add_argument("--committee-weight", type=float, default=1.0, help="Committee weight")
    parser.add_argument("--z-score", type=float, default=2.5, help="GSS z-score")
    parser.add_argument("--portfolio-equity", type=float, default=100000.0, help="Portfolio equity ($)")
    parser.add_argument("--broker", default=None, choices=["mock", "alpaca_paper", "tradier_sandbox"])
    parser.add_argument("--dry-run", action="store_true", help="Evaluate only, do not submit")
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    executor = PoliticianAlphaExecutor(repo_root, broker_name=args.broker)

    gss_snapshot = {
        "z_score": args.z_score,
        "mode": "NORMAL",
    }

    signal = executor.evaluate_signal(
        ticker=args.ticker,
        political_score=args.political_score,
        committee_weight=args.committee_weight,
        gss_snapshot=gss_snapshot,
    )

    print(json.dumps(signal, indent=2))

    if signal["action"] == "HOLD":
        print(f"\nHOLD - {signal['reason']}")
        return

    if args.dry_run:
        order_req = executor.generate_order(
            ticker=args.ticker,
            signal=signal,
            portfolio_equity=args.portfolio_equity,
        )
        print("\n--- Order Request (dry run) ---")
        print(json.dumps(order_req, indent=2))
        return

    result = executor.process_signal(
        ticker=args.ticker,
        political_score=args.political_score,
        committee_weight=args.committee_weight,
        gss_snapshot=gss_snapshot,
        portfolio_equity=args.portfolio_equity,
    )

    print("\n--- Telegram Alert ---")
    print(result["telegram_alert"])
    print("\n--- Order Result ---")
    print(json.dumps(result["order_result"], indent=2))


if __name__ == "__main__":
    main()
