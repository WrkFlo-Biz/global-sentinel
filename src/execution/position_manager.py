#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Position Manager

Monitors open positions and identifies when they would normally be closed:
1. Profit target is hit (default 2%)
2. Stop loss is hit (default 1%)
3. Trailing stop: if position is up >1.5%, stop moves to breakeven
4. End-of-day flatten: all day-trade positions closed after 3:45 PM ET

These are approval-required proposals only. Auto-close is hard-blocked in
code so no position can be closed without manual approval.

Runs every cycle from crisis_monitor._run_cycle().

Safety:
- Paper/shadow mode only (uses Alpaca paper API)
- Close signals are logged as proposals unless auto close is explicitly enabled
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from src.execution.strategy_learning import infer_strategy_family

try:
    import yaml
except ImportError:
    yaml = None

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore

# Import TelegramNotifier for instant close notifications
try:
    from src.monitoring.telegram_notifier import TelegramNotifier
except ImportError:
    try:
        from monitoring.telegram_notifier import TelegramNotifier
    except ImportError:
        TelegramNotifier = None  # type: ignore


GUARDED_CLOSE_PROJECT = "global-sentinel"
GUARDED_CLOSE_KIND = "gs.trade.execute_shadow"
GUARDED_CLOSE_REQUESTER = "position_manager"
GUARDED_CLOSE_REQUESTER_KIND = "scheduler"
GUARDED_CLOSE_REQUESTER_ID = "position_manager"
GUARDED_CLOSE_REQUESTER_CHANNEL = "position_manager"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


class PositionManager:
    """Monitors open positions and proposes closes at profit targets or stop losses."""

    def __init__(
        self,
        repo_root: Path,
        profit_target_pct: float = 2.0,
        stop_loss_pct: float = 1.0,
        trailing_stop_activation_pct: float = 1.5,
        eod_flatten_time: str = "15:45",  # ET, 24h format
        auto_close_enabled: bool = False,
    ):
        self.repo_root = repo_root
        self.profit_target_pct = profit_target_pct
        self.stop_loss_pct = stop_loss_pct
        self.trailing_stop_activation_pct = trailing_stop_activation_pct
        self.eod_flatten_time = eod_flatten_time
        self.live_guardrails = self._load_live_guardrails()
        # Hard safety policy: this monitor is proposal-only unless the code is
        # intentionally changed in a future review. Constructor overrides do not
        # enable automatic closes.
        self.require_human_approval_for_closures = True
        self.auto_close_enabled = False

        # Alpaca API credentials
        self.base_url = os.getenv(
            "ALPACA_PAPER_BASE_URL",
            os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        ).rstrip("/")
        self.api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
        self.api_secret = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

        self.session = requests.Session()
        if self.api_key and self.api_secret:
            self.session.headers.update({
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
                "Content-Type": "application/json",
                "Accept": "application/json",
            })

        # Rate limiter for Alpaca API calls
        try:
            from src.utils.rate_limiter import get_limiter
            self._rate_limiter = get_limiter(self.api_key or "", max_rpm=180) if self.api_key else None
        except ImportError:
            self._rate_limiter = None

        # Log paths
        self.log_dir = repo_root / "logs" / "execution"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.position_log_path = self.log_dir / "position_manager.jsonl"
        self.shadow_order_log_path = self.log_dir / "shadow_order_router.jsonl"

        # Load dual-strategy config from execution_mode.yaml
        self.strategy_config = self._load_strategy_config()

        # TelegramNotifier for instant close notifications
        self._notifier: Optional[Any] = None
        if TelegramNotifier is not None:
            try:
                self._notifier = TelegramNotifier(repo_root)
            except Exception:
                pass

        # Trailing stop state: {symbol: adjusted_stop_pct}
        # Persisted in memory per cycle; resets on restart (conservative)
        self._trailing_stops: Dict[str, float] = {}

    def run_check(self) -> Dict[str, Any]:
        """
        Main entry point. Check all open positions against targets.
        Returns a summary of proposal signals and any executed actions.
        """
        result = {
            "timestamp_utc": iso_now(),
            "actions_taken": 0,
            "actions_executed": 0,
            "proposed_close_count": 0,
            "approval_required": False,
            "manual_approval_required": True,
            "profits_taken": 0,
            "stops_hit": 0,
            "eod_flattened": 0,
            "close_details": [],
            "errors": [],
            "positions_checked": 0,
        }

        if not self.api_key or not self.api_secret:
            result["errors"].append("Missing Alpaca API credentials")
            return result

        try:
            positions = self._get_open_positions()
        except Exception as e:
            result["errors"].append(f"Failed to fetch positions: {e}")
            return result

        result["positions_checked"] = len(positions)

        if not positions:
            return result

        # Load order history before any guarded handoff so close proposals can
        # carry strategy/account provenance into the orchestrator boundary.
        order_history = self._load_order_history()

        # Portfolio-level drawdown protection (25% margin-call auto-liquidation)
        portfolio_drawdown = self._check_portfolio_drawdown(positions)
        result["portfolio_drawdown_pct"] = portfolio_drawdown.get("drawdown_pct", 0)
        if portfolio_drawdown.get("emergency_liquidate"):
            result["emergency_liquidation"] = True
            result["emergency_liquidation_blocked"] = not self.auto_close_enabled
            for pos in positions:
                symbol = pos.get("symbol", "")
                try:
                    entry = order_history.get(symbol, {})
                    detail = {
                        "symbol": symbol,
                        "reason": "emergency_portfolio_drawdown_25pct",
                        "strategy": entry.get("strategy") or "portfolio_protection",
                        "unrealized_pl": safe_float(pos.get("unrealized_pl"), 0),
                        "unrealized_plpc": safe_float(pos.get("unrealized_plpc"), 0),
                        "qty": abs(safe_float(pos.get("qty"), 0)),
                        "status": "pending_manual_approval",
                        "auto_close_blocked": not self.auto_close_enabled,
                        "close_result": None,
                    }
                    if not self.auto_close_enabled:
                        detail.update(
                            self._build_guarded_close_handoff(
                                position=pos,
                                close_reason="emergency_portfolio_drawdown_25pct",
                                strategy_name=str(entry.get("strategy") or "portfolio_protection"),
                                order_history_entry=entry,
                            )
                        )
                    result["close_details"].append(detail)
                    result["proposed_close_count"] += 1
                    if self.auto_close_enabled:
                        close_result = self._close_position(
                            symbol=symbol,
                            qty=abs(safe_float(pos.get("qty"), 0)),
                            side=pos.get("side", "long"),
                            reason="emergency_portfolio_drawdown_25pct",
                        )
                        detail["close_result"] = close_result
                        detail["status"] = "executed"
                        result["actions_taken"] += 1
                        result["actions_executed"] += 1
                except Exception as e:
                    result["errors"].append(f"Emergency close {symbol}: {e}")
            # Notify
            if self._notifier:
                try:
                    if self.auto_close_enabled:
                        self._notifier.send_message(
                            f"EMERGENCY LIQUIDATION: Portfolio drawdown {portfolio_drawdown['drawdown_pct']:.1f}% "
                            f"exceeded 25% threshold. All positions closed.",
                            bot_name="mo2darkbot",
                        )
                    else:
                        self._notifier.send_message(
                            f"EMERGENCY LIQUIDATION REVIEW REQUIRED: Portfolio drawdown {portfolio_drawdown['drawdown_pct']:.1f}% "
                            f"exceeded 25% threshold. Close-out is blocked until manual approval.",
                            bot_name="mo2darkbot",
                        )
                except Exception:
                    pass
            self._log_close({
                "event": "emergency_portfolio_liquidation",
                "drawdown_pct": portfolio_drawdown["drawdown_pct"],
                "positions_affected": len(positions),
                "auto_close_blocked": not self.auto_close_enabled,
            })
            result["manual_approval_required"] = result["proposed_close_count"] > 0
            result["approval_required"] = result["manual_approval_required"]
            return result

        for pos in positions:
            symbol = pos.get("symbol", "")
            try:
                entry = order_history.get(symbol, {})
                action = self._evaluate_position(pos, order_history)
                if action:
                    strategy_name = action.get("strategy", "day_trade")
                    pnl_pct = safe_float(pos.get("unrealized_plpc"), 0) * 100
                    pnl_usd = safe_float(pos.get("unrealized_pl"), 0)

                    detail = {
                        "symbol": symbol,
                        "reason": action["reason"],
                        "strategy": strategy_name,
                        "unrealized_plpc": safe_float(pos.get("unrealized_plpc"), 0),
                        "unrealized_pl": pnl_usd,
                        "qty": safe_float(pos.get("qty"), 0),
                        "avg_entry_price": safe_float(pos.get("avg_entry_price"), 0),
                        "current_price": safe_float(pos.get("current_price"), 0),
                        "status": "pending_manual_approval" if not self.auto_close_enabled else "executed",
                        "auto_close_blocked": not self.auto_close_enabled,
                    }
                    if not self.auto_close_enabled:
                        detail.update(
                            self._build_guarded_close_handoff(
                                position=pos,
                                close_reason=action["reason"],
                                strategy_name=str(strategy_name),
                                order_history_entry=entry,
                            )
                        )
                    if self.auto_close_enabled:
                        close_result = self._close_position(
                            symbol=symbol,
                            qty=abs(safe_float(pos.get("qty"), 0)),
                            side=pos.get("side", "long"),
                            reason=action["reason"],
                        )
                        detail["close_result"] = close_result
                        result["actions_taken"] += 1
                        result["actions_executed"] += 1
                    else:
                        detail["close_result"] = None
                        result["proposed_close_count"] += 1
                    result["close_details"].append(detail)

                    if action["reason"] == "take_profit":
                        result["profits_taken"] += 1
                    elif action["reason"] == "stop_loss":
                        result["stops_hit"] += 1
                    elif action["reason"] == "eod_flatten":
                        result["eod_flattened"] += 1

                    self._log_close(detail)

                    # Send instant Telegram notification
                    if self._notifier:
                        try:
                            if self.auto_close_enabled:
                                self._notifier.notify_position_closed(
                                    symbol=symbol,
                                    reason=action["reason"],
                                    pnl_pct=pnl_pct,
                                    pnl_usd=pnl_usd,
                                    strategy_name=strategy_name,
                                )
                            else:
                                self._notifier.send_message(
                                    (
                                        f"POSITION CLOSE REVIEW REQUIRED: {symbol} "
                                        f"would trigger {action['reason']} for {strategy_name}. "
                                        f"P&L {pnl_pct:+.2f}% / ${pnl_usd:+,.2f}. "
                                        "No auto-close was sent."
                                    ),
                                    bot_name="mo2darkbot",
                                )
                        except Exception:
                            pass  # Don't let notification failure block position management

            except Exception as e:
                result["errors"].append(f"{symbol}: {e}")

        if result["proposed_close_count"] > 0:
            result["manual_approval_required"] = True
        else:
            result["manual_approval_required"] = False
        result["approval_required"] = result["manual_approval_required"]
        return result

    def _rate_limited_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make a rate-limited request to Alpaca API with retry on 429."""
        if self._rate_limiter:
            self._rate_limiter.acquire(timeout=30.0)
        resp = self.session.request(method, url, **kwargs)
        if resp.status_code == 429:
            # Retry with backoff
            try:
                from src.utils.rate_limiter import retry_with_backoff
                def _retry():
                    if self._rate_limiter:
                        self._rate_limiter.acquire(timeout=30.0)
                    r = self.session.request(method, url, **kwargs)
                    r.raise_for_status()
                    return r
                return retry_with_backoff(_retry, max_retries=3, base_delay=2.0)
            except Exception:
                pass
        return resp

    def _check_portfolio_drawdown(self, positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Check portfolio-level drawdown. If total unrealized loss exceeds 25%
        of portfolio value, trigger emergency liquidation of all positions.
        """
        try:
            url = f"{self.base_url}/v2/account"
            resp = self._rate_limited_request("GET", url, timeout=10)
            resp.raise_for_status()
            acct = resp.json()

            equity = safe_float(acct.get("equity"), 0)
            last_equity = safe_float(acct.get("last_equity"), 0)

            if last_equity <= 0:
                return {"drawdown_pct": 0, "emergency_liquidate": False}

            # Drawdown from last close
            drawdown_pct = ((last_equity - equity) / last_equity) * 100

            # Also check total unrealized P&L vs portfolio
            total_unrealized = sum(safe_float(p.get("unrealized_pl"), 0) for p in positions)
            unrealized_pct = (abs(total_unrealized) / equity * 100) if equity > 0 else 0

            return {
                "drawdown_pct": round(drawdown_pct, 2),
                "unrealized_loss_pct": round(unrealized_pct, 2) if total_unrealized < 0 else 0,
                "equity": equity,
                "last_equity": last_equity,
                "total_unrealized_pl": round(total_unrealized, 2),
                "emergency_liquidate": drawdown_pct >= 25.0,
            }
        except Exception:
            return {"drawdown_pct": 0, "emergency_liquidate": False}

    def _get_open_positions(self) -> List[Dict[str, Any]]:
        """Fetch all open positions from Alpaca (raw, with all fields)."""
        url = f"{self.base_url}/v2/positions"
        resp = self._rate_limited_request("GET", url, timeout=15)
        resp.raise_for_status()
        positions = resp.json()
        if not isinstance(positions, list):
            return []
        return positions

    def _load_strategy_config(self) -> Dict[str, Any]:
        """Load strategy definitions from config/execution_mode.yaml."""
        config_path = self.repo_root / "config" / "execution_mode.yaml"
        if not config_path.exists() or yaml is None:
            return {}
        try:
            return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def _load_live_guardrails(self) -> Dict[str, Any]:
        """Load live trading guardrails for close approval policy."""
        guardrails_path = self.repo_root / "config" / "live_trading_guardrails.yaml"
        if not guardrails_path.exists() or yaml is None:
            return {}
        try:
            return yaml.safe_load(guardrails_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    def _approval_command(self, kind: str, target: str) -> str:
        return f'wrkflo-orchestrator approve --kind {kind} --target {target} --reason "<reason>"'

    def _build_guarded_close_handoff(
        self,
        *,
        position: Dict[str, Any],
        close_reason: str,
        strategy_name: str,
        order_history_entry: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        requested_at = iso_now()
        symbol = str(position.get("symbol") or "").strip().upper()
        qty = abs(safe_float(position.get("qty"), 0))
        position_side = self._position_side(position)
        close_side = self._close_side(position)
        asset_class = str(position.get("asset_class") or "equity").strip().lower() or "equity"
        account = self._close_account_label(strategy_name, order_history_entry or {})

        ticket_basis = {
            "account": account,
            "asset_class": asset_class,
            "close_reason": close_reason,
            "position_side": position_side,
            "qty": qty,
            "side": close_side,
            "source_surface": GUARDED_CLOSE_REQUESTER_CHANNEL,
            "strategy": strategy_name,
            "symbol": symbol,
        }
        ticket_hash = hashlib.sha256(
            json.dumps(ticket_basis, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        ticket_id = f"pm-{self._ticket_slug(symbol)}-{ticket_hash[:16]}"
        target = f"{GUARDED_CLOSE_PROJECT}/trade-ticket/{ticket_id}"
        request_reason = f"position_manager close proposal for {symbol}: {close_reason}"
        handoff = {
            "project": GUARDED_CLOSE_PROJECT,
            "kind": GUARDED_CLOSE_KIND,
            "target": target,
            "requester": GUARDED_CLOSE_REQUESTER,
            "requester_kind": GUARDED_CLOSE_REQUESTER_KIND,
            "requester_id": GUARDED_CLOSE_REQUESTER_ID,
            "requester_channel": GUARDED_CLOSE_REQUESTER_CHANNEL,
            "reason": request_reason,
            "requested_at": requested_at,
            "ticket_id": ticket_id,
            "ticket_hash": ticket_hash,
            "strategy": strategy_name,
            "account": account,
            "symbol": symbol,
            "side": close_side,
            "qty": qty,
            "asset_class": asset_class,
            "order_type": "market",
            "time_in_force": "day",
            "source_surface": GUARDED_CLOSE_REQUESTER_CHANNEL,
            "close_reason": close_reason,
            "position_side": position_side,
        }
        return {
            "approval_required": True,
            "project": GUARDED_CLOSE_PROJECT,
            "kind": GUARDED_CLOSE_KIND,
            "target": target,
            "ticket_id": ticket_id,
            "orchestrator_command": self._approval_command(GUARDED_CLOSE_KIND, target),
            "orchestrator_handoff": handoff,
        }

    def _position_side(self, position: Dict[str, Any]) -> str:
        side = str(position.get("side") or "").strip().lower()
        if side in {"long", "short"}:
            return side
        return "short" if safe_float(position.get("qty"), 0) < 0 else "long"

    def _close_side(self, position: Dict[str, Any]) -> str:
        return "buy" if self._position_side(position) == "short" else "sell"

    def _close_account_label(self, strategy_name: str, order_history_entry: Dict[str, Any]) -> str:
        account = str(
            order_history_entry.get("account")
            or order_history_entry.get("account_name")
            or ""
        ).strip()
        if account:
            return account

        holding_period = str(order_history_entry.get("holding_period") or "").strip().lower()
        if strategy_name == "medium_long" or holding_period in {
            "swing",
            "medium",
            "long",
            "macro",
            "weekly",
            "monthly",
            "multi_day",
            "overnight",
        }:
            return "medium_long"
        return "day_trade"

    def _ticket_slug(self, symbol: str) -> str:
        slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in symbol).strip("-")
        return slug or "position"

    def _get_strategy_params(
        self,
        holding_period: str,
        strategy_family: Optional[str] = None,
        strategy_style: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Return strategy parameters based on holding_period.
        If swing/medium/long/macro → medium_long config.
        Otherwise → day_trade config (default).
        """
        strategies = self.strategy_config.get("strategies", {})
        normalized = str(holding_period or "").strip().lower()
        inferred_family = infer_strategy_family(
            {
                "holding_period": normalized,
                "strategy_family": strategy_family,
                "strategy_style": strategy_style,
            }
        )

        if normalized == "intraday_scalp":
            # Opening range / amateur hour: tight stops, quick profits, always flat EOD
            return {
                "strategy_name": "intraday_scalp",
                "profit_target_pct": 1.0,
                "stop_loss_pct": 0.5,
                "trailing_stop_activation_pct": 0.8,
                "eod_flatten": True,
            }
        elif normalized == "intraday_momentum":
            # Power hour: slightly wider stops, ride momentum, flat EOD
            return {
                "strategy_name": "intraday_momentum",
                "profit_target_pct": 2.0,
                "stop_loss_pct": 1.0,
                "trailing_stop_activation_pct": 1.5,
                "eod_flatten": True,
            }
        elif inferred_family == "medium_long" or normalized in (
            "swing",
            "medium",
            "long",
            "macro",
            "weekly",
            "monthly",
            "multi_day",
            "overnight",
        ):
            cfg = strategies.get("medium_long", {})
            return {
                "strategy_name": "medium_long",
                "profit_target_pct": cfg.get("profit_target_pct", 8.0),
                "stop_loss_pct": cfg.get("stop_loss_pct", 4.0),
                "trailing_stop_activation_pct": cfg.get("trailing_stop_activation_pct", 5.0),
                "eod_flatten": cfg.get("eod_flatten", False),
            }
        else:
            cfg = strategies.get("day_trade", {})
            return {
                "strategy_name": "day_trade",
                "profit_target_pct": cfg.get("profit_target_pct", self.profit_target_pct),
                "stop_loss_pct": cfg.get("stop_loss_pct", self.stop_loss_pct),
                "trailing_stop_activation_pct": cfg.get("trailing_stop_activation_pct", self.trailing_stop_activation_pct),
                "trailing_stop_distance_pct": cfg.get("trailing_stop_distance_pct", 0.4),
                "eod_flatten": cfg.get("eod_flatten", True),
            }

    def _evaluate_position(
        self,
        position: Dict[str, Any],
        order_history: Dict[str, Dict],
    ) -> Optional[Dict[str, str]]:
        """
        Evaluate a position and return action if needed.
        Returns {"reason": "take_profit"|"stop_loss"|"eod_flatten", "strategy": ...} or None.
        """
        symbol = position.get("symbol", "")

        # Determine strategy from order history
        entry = order_history.get(symbol, {})
        holding_period = entry.get("holding_period", "day")  # default to day (conservative)
        params = self._get_strategy_params(
            holding_period,
            strategy_family=entry.get("strategy_family"),
            strategy_style=entry.get("strategy_style"),
        )
        strategy_name = params["strategy_name"]

        # Get unrealized P&L percentage from Alpaca raw response
        unrealized_plpc = safe_float(position.get("unrealized_plpc"), 0) * 100  # Convert to %

        # Check profit target (strategy-specific)
        if unrealized_plpc >= params["profit_target_pct"]:
            return {"reason": "take_profit", "strategy": strategy_name}

        # Trailing stop logic: once activated, trail behind peak P&L
        effective_stop = params["stop_loss_pct"]
        trail_distance = params.get("trailing_stop_distance_pct", 0.4)  # Default 40bps trail
        trailing_activation = params["trailing_stop_activation_pct"]

        if unrealized_plpc >= trailing_activation:
            # Trail the stop behind current P&L (lock in gains)
            new_stop = unrealized_plpc - trail_distance
            prev_stop = self._trailing_stops.get(symbol, 0.0)
            # Only ratchet up, never down
            trailing_stop_level = max(new_stop, prev_stop, 0.0)
            self._trailing_stops[symbol] = trailing_stop_level
            effective_stop = trailing_stop_level
        elif symbol in self._trailing_stops:
            # Already activated previously, use last trailing level
            effective_stop = self._trailing_stops[symbol]

        # Check stop loss (with trailing adjustment)
        if self._should_stop_loss(unrealized_plpc, effective_stop):
            return {"reason": "stop_loss", "strategy": strategy_name}

        # Check EOD flatten — only for strategies with eod_flatten enabled
        if params["eod_flatten"] and self._should_eod_flatten():
            return {"reason": "eod_flatten", "strategy": strategy_name}

        return None

    def _should_take_profit(self, unrealized_plpc: float) -> bool:
        """Check if position has hit profit target."""
        return unrealized_plpc >= self.profit_target_pct

    def _should_stop_loss(self, unrealized_plpc: float, effective_stop: float = None) -> bool:
        """Check if position has hit stop loss."""
        stop = effective_stop if effective_stop is not None else self.stop_loss_pct
        return unrealized_plpc <= -stop

    def _should_eod_flatten(self) -> bool:
        """Check if it's after 3:45 PM ET (15 minutes before market close)."""
        et = ZoneInfo("America/New_York")
        now_et = datetime.now(et)

        # Only on weekdays
        if now_et.weekday() >= 5:
            return False

        hour, minute = map(int, self.eod_flatten_time.split(":"))
        flatten_time = now_et.replace(hour=hour, minute=minute, second=0, microsecond=0)
        market_close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)

        return flatten_time <= now_et <= market_close

    def _close_position(
        self,
        symbol: str,
        qty: float,
        side: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        Close a position via Alpaca API.
        Uses DELETE /v2/positions/{symbol} which closes the entire position.
        """
        url = f"{self.base_url}/v2/positions/{symbol}"
        try:
            resp = self._rate_limited_request("DELETE", url, timeout=15)
            if resp.status_code == 204:
                return {
                    "status": "closed",
                    "symbol": symbol,
                    "reason": reason,
                    "timestamp_utc": iso_now(),
                }
            resp.raise_for_status()
            data = resp.json()
            return {
                "status": "closed",
                "symbol": symbol,
                "reason": reason,
                "order_id": data.get("id"),
                "timestamp_utc": iso_now(),
            }
        except requests.HTTPError as e:
            return {
                "status": "error",
                "symbol": symbol,
                "reason": reason,
                "error": str(e),
                "http_status": getattr(e.response, "status_code", None),
                "timestamp_utc": iso_now(),
            }
        except Exception as e:
            return {
                "status": "error",
                "symbol": symbol,
                "reason": reason,
                "error": str(e),
                "timestamp_utc": iso_now(),
            }

    def _load_order_history(self) -> Dict[str, Dict]:
        """
        Load order history from shadow_order_router.jsonl.
        Returns {symbol: {entry_price, holding_period, ...}} for latest entry per symbol.
        """
        history: Dict[str, Dict] = {}
        if not self.shadow_order_log_path.exists():
            return history

        try:
            with self.shadow_order_log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                        payload = row.get("payload", row)

                        # Extract selected candidates from route events
                        for cand in payload.get("selected_candidates", []):
                            sym = cand.get("symbol")
                            if sym:
                                history[sym] = {
                                    "strategy": cand.get("strategy"),
                                    "strategy_style": cand.get("strategy_style"),
                                    "strategy_family": cand.get("strategy_family"),
                                    "underlying_strategy": cand.get("underlying_strategy"),
                                    "account": cand.get("account") or cand.get("account_name"),
                                    "direction": cand.get("direction"),
                                    "confidence_score": cand.get("confidence_score"),
                                    "template_key": cand.get("template_key"),
                                    "timestamp_utc": row.get("timestamp_utc"),
                                    # Infer holding period from time_in_force
                                    "holding_period": "day" if payload.get("time_window_name") == "us_regular_hours" else "swing",
                                }
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

        return history

    def _log_close(self, detail: Dict[str, Any]):
        """Append close proposal or executed close to position_manager.jsonl."""
        event_type = detail.get("event") or (
            "position_closed" if detail.get("status") == "executed" else "position_close_proposed"
        )
        row = {
            "schema_version": "position_manager_close.v1",
            "timestamp_utc": iso_now(),
            "event_type": event_type,
            **detail,
        }
        try:
            with self.position_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as e:
            print(f"[{iso_now()}] Failed to log position close: {e}", file=sys.stderr)


# --- CLI ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Position Manager - check and propose position closes")
    parser.add_argument("--repo-root", default=".", help="Repository root path")
    parser.add_argument("--profit-target", type=float, default=2.0, help="Profit target %% (default 2)")
    parser.add_argument("--stop-loss", type=float, default=1.0, help="Stop loss %% (default 1)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without closing")
    args = parser.parse_args()

    pm = PositionManager(
        repo_root=Path(args.repo_root).resolve(),
        profit_target_pct=args.profit_target,
        stop_loss_pct=args.stop_loss,
    )

    if args.dry_run:
        positions = pm._get_open_positions()
        order_history = pm._load_order_history()
        print(f"Open positions: {len(positions)}")
        for pos in positions:
            symbol = pos.get("symbol", "?")
            plpc = safe_float(pos.get("unrealized_plpc"), 0) * 100
            action = pm._evaluate_position(pos, order_history)
            status = action["reason"] if action else "hold"
            print(f"  {symbol}: P&L={plpc:+.2f}% -> {status}")
    else:
        result = pm.run_check()
        print(json.dumps(result, indent=2))
