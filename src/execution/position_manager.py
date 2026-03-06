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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        Returns summary of actions taken.
        """
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
                action = self._evaluate_position(pos, order_history)
                if action:
                    close_result = self._close_position(
                        symbol=symbol,
                        qty=abs(safe_float(pos.get("qty"), 0)),
                        side=pos.get("side", "long"),
                        reason=action["reason"],
                    )
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
                        "close_result": close_result,
                    }
                    result["close_details"].append(detail)
                    result["actions_taken"] += 1

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
                            self._notifier.notify_position_closed(
                                symbol=symbol,
                                reason=action["reason"],
                                pnl_pct=pnl_pct,
                                pnl_usd=pnl_usd,
                                strategy_name=strategy_name,
                            )
                        except Exception:
                            pass  # Don't let notification failure block position management

            except Exception as e:
                result["errors"].append(f"{symbol}: {e}")

        return result

    def _check_portfolio_drawdown(self, positions: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Check portfolio-level drawdown. If total unrealized loss exceeds 25%
        of portfolio value, trigger emergency liquidation of all positions.
        """
        try:
            url = f"{self.base_url}/v2/account"
            resp = self.session.get(url, timeout=10)
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
        resp = self.session.get(url, timeout=15)
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
        If swing/medium/long/macro → medium_long config.
        Otherwise → day_trade config (default).
        """
        strategies = self.strategy_config.get("strategies", {})

        if holding_period in ("swing", "medium", "long", "macro"):
            cfg = strategies.get("medium_long", {})
            return {
                "strategy_name": "medium_long",
                "profit_target_pct": cfg.get("profit_target_pct", 8.0),
                "stop_loss_pct": cfg.get("stop_loss_pct", 4.0),
                "trailing_stop_activation_pct": cfg.get("trailing_stop_activation_pct", 5.0),
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

        # Trailing stop logic: if up > trailing activation, move stop to breakeven (0%)
        effective_stop = params["stop_loss_pct"]
        if unrealized_plpc >= params["trailing_stop_activation_pct"]:
            # Trailing stop activated: stop at breakeven (0% loss)
            self._trailing_stops[symbol] = 0.0
            effective_stop = 0.0
        elif symbol in self._trailing_stops:
            # Already activated previously, use breakeven stop
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
            resp = self.session.delete(url, timeout=15)
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
                                    "strategy_style": cand.get("strategy_style"),
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
