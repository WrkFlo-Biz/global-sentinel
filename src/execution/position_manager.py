#!/usr/bin/env python3
"""
Global Sentinel V5.1 - Position Manager

Monitors open positions and closes them when:
1. Profit target is hit (default 2%)
2. Stop loss is hit (default 1%)
3. Trailing stop: if position is up >1.5%, stop moves to breakeven
4. End-of-day flatten: all day-trade positions closed after 3:45 PM ET

Runs every cycle from crisis_monitor._run_cycle().

Safety:
- Paper/shadow mode only (uses Alpaca paper API)
- All close actions logged with reason, P&L, hold duration
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

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


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


# Maximum number of close retries per position per session before giving up
_MAX_CLOSE_RETRIES = 3

# Exponential backoff delays in seconds: 1 min, 5 min, 15 min
_RETRY_BACKOFF_SECONDS = [60, 300, 900]


class PositionManager:
    """Monitors open positions and closes at profit targets or stop losses."""

    def __init__(
        self,
        repo_root: Path,
        profit_target_pct: float = 2.0,
        stop_loss_pct: float = 1.0,
        trailing_stop_activation_pct: float = 1.5,
        eod_flatten_time: str = "15:45",  # ET, 24h format
    ):
        self.repo_root = repo_root
        self.profit_target_pct = profit_target_pct
        self.stop_loss_pct = stop_loss_pct
        self.trailing_stop_activation_pct = trailing_stop_activation_pct
        self.eod_flatten_time = eod_flatten_time

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

        # Take-profit tier state: tracks which trim tiers have fired per symbol
        # {symbol: {"tier1_done": bool, "tier2_done": bool, "tier3_trailing": bool,
        #           "original_qty": float, "stop_at_breakeven": bool}}
        self._tp_state: Dict[str, Dict[str, Any]] = {}

        # Close retry tracking: {(symbol, reason): {"count": int, "last_attempt": float,
        #   "last_error": str, "alerted": bool}}
        # Persists across cycles within a single process session.
        # Keyed by (symbol, reason) tuple so each close-reason is tracked independently.
        self._close_retries: Dict[Tuple[str, str], Dict[str, Any]] = {}

        # Log dedup tracker for current cycle: set of (symbol, reason, error_string)
        # Reset at the start of each run_check() call to allow one log per unique event per cycle
        self._logged_this_cycle: set = set()

    def run_check(self) -> Dict[str, Any]:
        """
        Main entry point. Check all open positions against targets.
        Returns summary of actions taken.
        """
        # Reset per-cycle log dedup tracker
        self._logged_this_cycle = set()

        result = {
            "timestamp_utc": iso_now(),
            "actions_taken": 0,
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

        # Portfolio-level drawdown protection (25% margin-call auto-liquidation)
        portfolio_drawdown = self._check_portfolio_drawdown(positions)
        result["portfolio_drawdown_pct"] = portfolio_drawdown.get("drawdown_pct", 0)
        if portfolio_drawdown.get("emergency_liquidate"):
            result["emergency_liquidation"] = True
            for pos in positions:
                symbol = pos.get("symbol", "")
                try:
                    close_result = self._close_position(
                        symbol=symbol,
                        qty=abs(safe_float(pos.get("qty"), 0)),
                        side=pos.get("side", "long"),
                        reason="emergency_portfolio_drawdown_25pct",
                    )
                    result["close_details"].append({
                        "symbol": symbol,
                        "reason": "emergency_portfolio_drawdown_25pct",
                        "unrealized_pl": safe_float(pos.get("unrealized_pl"), 0),
                        "close_result": close_result,
                    })
                    result["actions_taken"] += 1
                except Exception as e:
                    result["errors"].append(f"Emergency close {symbol}: {e}")
            # Notify
            if self._notifier:
                try:
                    self._notifier.send_message(
                        f"EMERGENCY LIQUIDATION: Portfolio drawdown {portfolio_drawdown['drawdown_pct']:.1f}% "
                        f"exceeded 25% threshold. All positions closed.",
                        bot_name="mo2darkbot",
                    )
                except Exception:
                    pass
            self._log_close({
                "event": "emergency_portfolio_liquidation",
                "drawdown_pct": portfolio_drawdown["drawdown_pct"],
                "positions_closed": len(positions),
            })
            return result

        # Load order history for entry context
        order_history = self._load_order_history()

        for pos in positions:
            symbol = pos.get("symbol", "")
            try:
                # --- Tiered take-profit check (runs before full-close evaluation) ---
                tp_action = self._evaluate_take_profit_tiers(pos, order_history)
                if tp_action and tp_action.get("trim_qty", 0) > 0:
                    trim_result = self._reduce_position(
                        symbol=symbol,
                        qty_to_sell=tp_action["trim_qty"],
                        side=tp_action.get("side", "long"),
                        reason=tp_action["reason"],
                    )
                    pnl_usd = safe_float(pos.get("unrealized_pl"), 0)
                    trim_detail = {
                        "symbol": symbol,
                        "reason": tp_action["reason"],
                        "strategy": tp_action.get("strategy", "day_trade"),
                        "trim_qty": tp_action["trim_qty"],
                        "remaining_qty": abs(safe_float(pos.get("qty"), 0)) - tp_action["trim_qty"],
                        "pct_gain_at_trim": tp_action.get("pct_gain", 0),
                        "unrealized_pl": pnl_usd,
                        "avg_entry_price": safe_float(pos.get("avg_entry_price"), 0),
                        "current_price": safe_float(pos.get("current_price"), 0),
                        "stop_moved_to_breakeven": tp_action.get("stop_moved_to_breakeven", False),
                        "trailing_stop_set": tp_action.get("trailing_stop_set", False),
                        "close_result": trim_result,
                    }
                    result["close_details"].append(trim_detail)
                    result["actions_taken"] += 1
                    result["profits_taken"] += 1
                    self._log_trim(trim_detail)
                    if self._notifier:
                        try:
                            self._notifier.send_message(
                                f"TRIM {symbol}: {tp_action['reason']} | "
                                f"Sold {tp_action['trim_qty']} at +{tp_action.get('pct_gain', 0):.1f}%",
                                bot_name="mo2darkbot",
                            )
                        except Exception:
                            pass
                    continue  # Let next cycle re-evaluate the reduced position
                elif tp_action and tp_action.get("action") == "trailing_activated":
                    self._log_trim({
                        "symbol": symbol,
                        "reason": tp_action["reason"],
                        "strategy": tp_action.get("strategy", "day_trade"),
                        "trim_qty": 0,
                        "pct_gain_at_activation": tp_action.get("pct_gain", 0),
                        "trailing_stop_set": True,
                        "trail_distance_pct": tp_action.get("trail_distance_pct", 2.0),
                    })
                # --- End tiered take-profit check ---

                action = self._evaluate_position(pos, order_history)
                if action:
                    reason = action["reason"]

                    # --- Retry gate: check if we should skip this close attempt ---
                    retry_key = (symbol, reason)
                    retry_state = self._close_retries.get(retry_key)

                    if retry_state:
                        # Already exhausted max retries -- skip (alert already sent)
                        if retry_state["count"] >= _MAX_CLOSE_RETRIES:
                            continue

                        # Check exponential backoff: don't retry until enough time has passed
                        backoff_idx = min(retry_state["count"], len(_RETRY_BACKOFF_SECONDS) - 1)
                        required_wait = _RETRY_BACKOFF_SECONDS[backoff_idx]
                        elapsed = time.monotonic() - retry_state["last_attempt"]
                        if elapsed < required_wait:
                            continue

                    close_result = self._close_position(
                        symbol=symbol,
                        qty=abs(safe_float(pos.get("qty"), 0)),
                        side=pos.get("side", "long"),
                        reason=reason,
                    )
                    strategy_name = action.get("strategy", "day_trade")
                    pnl_pct = safe_float(pos.get("unrealized_plpc"), 0) * 100
                    pnl_usd = safe_float(pos.get("unrealized_pl"), 0)

                    detail = {
                        "symbol": symbol,
                        "reason": reason,
                        "strategy": strategy_name,
                        "unrealized_plpc": safe_float(pos.get("unrealized_plpc"), 0),
                        "unrealized_pl": pnl_usd,
                        "qty": safe_float(pos.get("qty"), 0),
                        "avg_entry_price": safe_float(pos.get("avg_entry_price"), 0),
                        "current_price": safe_float(pos.get("current_price"), 0),
                        "close_result": close_result,
                    }

                    # --- Handle close failure with retry tracking and dedup ---
                    if close_result.get("status") == "error":
                        error_str = close_result.get("error", "unknown")
                        retry_count = (retry_state["count"] if retry_state else 0) + 1

                        # Update retry tracker
                        self._close_retries[retry_key] = {
                            "count": retry_count,
                            "last_attempt": time.monotonic(),
                            "last_error": error_str,
                            "alerted": retry_state["alerted"] if retry_state else False,
                        }

                        detail["retry_count"] = retry_count

                        # Dedup: only log if we haven't logged this exact event this cycle
                        dedup_key = (symbol, reason, error_str)
                        if dedup_key not in self._logged_this_cycle:
                            self._logged_this_cycle.add(dedup_key)
                            self._log_close(detail)

                        # If max retries reached, send a one-time Telegram alert
                        if retry_count >= _MAX_CLOSE_RETRIES and not self._close_retries[retry_key]["alerted"]:
                            self._close_retries[retry_key]["alerted"] = True
                            if self._notifier:
                                try:
                                    self._notifier.send_message(
                                        f"CLOSE FAILED after {retry_count} retries: {symbol} "
                                        f"(reason={reason}, error={error_str}). "
                                        f"Manual intervention required.",
                                        bot_name="mo2darkbot",
                                    )
                                except Exception:
                                    pass

                        result["errors"].append(
                            f"{symbol}: close failed (retry {retry_count}/{_MAX_CLOSE_RETRIES}): {error_str}"
                        )
                        continue  # Don't count failed close as action taken

                    # --- Success path: clear retry state and proceed normally ---
                    self._close_retries.pop(retry_key, None)

                    result["close_details"].append(detail)
                    result["actions_taken"] += 1

                    if reason == "take_profit":
                        result["profits_taken"] += 1
                    elif reason == "stop_loss":
                        result["stops_hit"] += 1
                    elif reason == "eod_flatten":
                        result["eod_flattened"] += 1

                    self._log_close(detail)

                    # Send instant Telegram notification
                    if self._notifier:
                        try:
                            self._notifier.notify_position_closed(
                                symbol=symbol,
                                reason=reason,
                                pnl_pct=pnl_pct,
                                pnl_usd=pnl_usd,
                                strategy_name=strategy_name,
                            )
                        except Exception:
                            pass  # Don't let notification failure block position management

            except Exception as e:
                result["errors"].append(f"{symbol}: {e}")

        # Clean up take-profit state for symbols no longer in open positions
        open_symbols = {pos.get("symbol", "") for pos in positions}
        stale_tp = [s for s in self._tp_state if s not in open_symbols]
        for s in stale_tp:
            del self._tp_state[s]
        # Clean up close retry state for symbols no longer in open positions
        stale_retries = [k for k in self._close_retries if k[0] not in open_symbols]
        for k in stale_retries:
            del self._close_retries[k]

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

    def _get_strategy_params(self, holding_period: str) -> Dict[str, Any]:
        """
        Return strategy parameters based on holding_period.
        If swing/medium/long/macro -> medium_long config.
        Otherwise -> day_trade config (default).
        """
        strategies = self.strategy_config.get("strategies", {})

        if holding_period in ("swing", "medium", "long", "macro"):
            cfg = strategies.get("medium_long", {})
            return {
                "strategy_name": "medium_long",
                "profit_target_pct": cfg.get("profit_target_pct", 8.0),
                "stop_loss_pct": cfg.get("stop_loss_pct", 4.0),
                "trailing_stop_activation_pct": cfg.get("trailing_stop_activation_pct", 5.0),
                "trailing_stop_distance_pct": cfg.get("trailing_stop_distance_pct", 1.5),
                "eod_flatten": cfg.get("eod_flatten", False),
            }
        elif holding_period == "intraday_scalp":
            # Opening range / amateur hour: tight stops, quick profits, always flat EOD
            return {
                "strategy_name": "intraday_scalp",
                "profit_target_pct": 1.0,
                "stop_loss_pct": 0.5,
                "trailing_stop_activation_pct": 0.8,
                "eod_flatten": True,
            }
        elif holding_period == "intraday_momentum":
            # Power hour: slightly wider stops, ride momentum, flat EOD
            return {
                "strategy_name": "intraday_momentum",
                "profit_target_pct": 2.0,
                "stop_loss_pct": 1.0,
                "trailing_stop_activation_pct": 1.5,
                "eod_flatten": True,
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
        params = self._get_strategy_params(holding_period)
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

        # Check EOD flatten -- only for strategies with eod_flatten enabled
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
        market_close_ext = now_et.replace(hour=16, minute=15, second=0, microsecond=0)

        return flatten_time <= now_et <= market_close_ext

    def _close_position(
        self,
        symbol: str,
        qty: float,
        side: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        Close a position via Alpaca API with retry limits (max 3 per symbol).
        Uses DELETE /v2/positions/{symbol} which closes the entire position.
        """
        # Retry guard: max 3 attempts per symbol per session
        retry_info = self._close_retries.get(symbol, {"count": 0, "last_attempt": ""})
        if retry_info["count"] >= 3:
            if self._notifier and retry_info["count"] == 3:
                try:
                    self._notifier.send_message(
                        f"ALERT: Failed to close {symbol} after 3 attempts ({reason}). Manual intervention needed.",
                        bot_name="mo2darkbot",
                    )
                except Exception:
                    pass
            retry_info["count"] += 1
            self._close_retries[symbol] = retry_info
            return {"status": "max_retries_exceeded", "symbol": symbol, "reason": reason,
                    "retry_count": retry_info["count"], "timestamp_utc": iso_now()}

        retry_info["count"] += 1
        retry_info["last_attempt"] = iso_now()
        self._close_retries[symbol] = retry_info

        url = f"{self.base_url}/v2/positions/{symbol}"
        try:
            resp = self._rate_limited_request("DELETE", url, timeout=15)
            if resp.status_code == 204:
                return {"status": "closed", "symbol": symbol, "reason": reason,
                        "retry_count": retry_info["count"], "timestamp_utc": iso_now()}
            resp.raise_for_status()
            data = resp.json()
            return {"status": "closed", "symbol": symbol, "reason": reason,
                    "order_id": data.get("id"), "retry_count": retry_info["count"], "timestamp_utc": iso_now()}
        except requests.HTTPError as e:
            return {"status": "error", "symbol": symbol, "reason": reason,
                    "error": str(e), "http_status": getattr(e.response, "status_code", None),
                    "retry_count": retry_info["count"], "timestamp_utc": iso_now()}
        except Exception as e:
            return {"status": "error", "symbol": symbol, "reason": reason,
                    "error": str(e), "retry_count": retry_info["count"], "timestamp_utc": iso_now()}

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
                                    "strategy_style": cand.get("strategy_style"),
                                    "direction": cand.get("direction"),
                                    "confidence_score": cand.get("confidence_score"),
                                    "template_key": cand.get("template_key"),
                                    "timestamp_utc": row.get("timestamp_utc"),
                                    # Infer holding period from time_in_force
                                    "holding_period": "swing" if payload.get("time_window_name", "") in ("asia_session", "europe_premarket", "after_hours_global", "premarket_signal_prep") else "day",
                                }
                    except json.JSONDecodeError:
                        continue
        except Exception:
            pass

        return history

    def _reduce_position(
        self, symbol: str, qty_to_sell: float, side: str, reason: str,
    ) -> Dict[str, Any]:
        """Partially close a position by submitting a market order for qty_to_sell."""
        if qty_to_sell <= 0:
            return {"status": "skipped", "symbol": symbol, "reason": "zero_qty"}
        order_side = "sell" if side == "long" else "buy"
        url = f"{self.base_url}/v2/orders"
        payload = {
            "symbol": symbol,
            "qty": str(round(qty_to_sell, 6)),
            "side": order_side,
            "type": "market",
            "time_in_force": "day",
        }
        try:
            resp = self._rate_limited_request("POST", url, json=payload, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            return {
                "status": "trimmed", "symbol": symbol, "qty_sold": qty_to_sell,
                "reason": reason, "order_id": data.get("id"), "timestamp_utc": iso_now(),
            }
        except Exception as e:
            return {
                "status": "error", "symbol": symbol, "reason": reason,
                "error": str(e), "timestamp_utc": iso_now(),
            }

    def _evaluate_take_profit_tiers(
        self, position: Dict[str, Any], order_history: Dict[str, Dict],
    ) -> Optional[Dict[str, Any]]:
        """
        Tiered take-profit logic.
        Medium/long: Tier1 +3% trim 50% + breakeven stop, Tier2 +5% trim 25%, Tier3 +8% 2% trail.
        Day trade: Tier1 +2% trim 50% + 1% trail on rest.
        """
        symbol = position.get("symbol", "")
        unrealized_plpc = safe_float(position.get("unrealized_plpc"), 0) * 100
        total_qty = abs(safe_float(position.get("qty"), 0))
        side = position.get("side", "long")

        if total_qty <= 0 or unrealized_plpc <= 0:
            return None

        entry = order_history.get(symbol, {})
        holding_period = entry.get("holding_period", "day")
        params = self._get_strategy_params(holding_period)
        strategy_name = params["strategy_name"]

        if symbol not in self._tp_state:
            self._tp_state[symbol] = {
                "tier1_done": False, "tier2_done": False, "tier3_trailing": False,
                "original_qty": total_qty, "stop_at_breakeven": False,
            }
        state = self._tp_state[symbol]
        original_qty = state["original_qty"]

        # Day trade: Tier1 +50% -> trim 50%, Tier2 +75% -> trim 25%, Tier3 +100% -> close all
        if strategy_name in ("day_trade", "intraday_scalp", "intraday_momentum"):
            if not state["tier1_done"] and unrealized_plpc >= 50.0:
                trim_qty = round(total_qty * 0.5, 6)
                if trim_qty >= 1:
                    state["tier1_done"] = True
                    self._trailing_stops[symbol] = max(
                        unrealized_plpc * 0.5, self._trailing_stops.get(symbol, 0.0), 0.0)
                    return {
                        "action": "trim", "reason": "take_profit_50pct",
                        "strategy": strategy_name, "trim_qty": trim_qty, "side": side,
                        "pct_gain": unrealized_plpc, "trailing_stop_set": True, "trail_distance_pct": 10.0,
                    }
            if state["tier1_done"] and not state["tier2_done"] and unrealized_plpc >= 75.0:
                trim_qty = min(round(original_qty * 0.25, 6), total_qty)
                if trim_qty >= 1:
                    state["tier2_done"] = True
                    return {
                        "action": "trim", "reason": "take_profit_75pct",
                        "strategy": strategy_name, "trim_qty": trim_qty, "side": side,
                        "pct_gain": unrealized_plpc,
                    }
            if state["tier2_done"] and not state["tier3_trailing"] and unrealized_plpc >= 100.0:
                state["tier3_trailing"] = True
                # Close remaining -- 100%+ gain = strong sell signal
                return {
                    "action": "trim", "reason": "take_profit_100pct_close_all",
                    "strategy": strategy_name, "trim_qty": total_qty, "side": side,
                    "pct_gain": unrealized_plpc,
                }
            return None

        # Medium/long: Tier1 +50% -> trim 50%, Tier2 +75% -> trim 25%, Tier3 +100% -> close all
        if not state["tier1_done"] and unrealized_plpc >= 50.0:
            trim_qty = round(total_qty * 0.5, 6)
            if trim_qty >= 1:
                state["tier1_done"] = True
                state["stop_at_breakeven"] = True
                self._trailing_stops[symbol] = max(0.0, self._trailing_stops.get(symbol, 0.0))
                return {
                    "action": "trim", "reason": "take_profit_50pct",
                    "strategy": strategy_name, "trim_qty": trim_qty, "side": side,
                    "pct_gain": unrealized_plpc, "stop_moved_to_breakeven": True,
                }

        # Tier2 +75% -> trim 25% of original
        if state["tier1_done"] and not state["tier2_done"] and unrealized_plpc >= 75.0:
            trim_qty = min(round(original_qty * 0.25, 6), total_qty)
            if trim_qty >= 1:
                state["tier2_done"] = True
                return {
                    "action": "trim", "reason": "take_profit_75pct",
                    "strategy": strategy_name, "trim_qty": trim_qty, "side": side,
                    "pct_gain": unrealized_plpc,
                }

        # Tier3 +100% -> close all remaining -- strong sell signal
        if state["tier2_done"] and not state["tier3_trailing"] and unrealized_plpc >= 100.0:
            state["tier3_trailing"] = True
            return {
                "action": "trim", "reason": "take_profit_100pct_close_all",
                "strategy": strategy_name, "trim_qty": total_qty, "side": side,
                "pct_gain": unrealized_plpc,
            }

        # If past tier2 but not 100% yet, enable trailing stop at 15% distance
        if state["tier2_done"] and unrealized_plpc >= 75.0:
            new_stop = unrealized_plpc - 15.0
            self._trailing_stops[symbol] = max(new_stop, self._trailing_stops.get(symbol, 0.0), 0.0)

        return None

    def _log_trim(self, detail: Dict[str, Any]):
        """Append trim/take-profit action to position_manager.jsonl."""
        event_type = "position_trimmed" if detail.get("trim_qty", 0) > 0 else "take_profit_triggered"
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
            print(f"[{iso_now()}] Failed to log position trim: {e}", file=sys.stderr)

    def _log_close(self, detail: Dict[str, Any]):
        """Append close action to position_manager.jsonl."""
        row = {
            "schema_version": "position_manager_close.v1",
            "timestamp_utc": iso_now(),
            "event_type": "position_closed",
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

    parser = argparse.ArgumentParser(description="Position Manager - check and close positions")
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
