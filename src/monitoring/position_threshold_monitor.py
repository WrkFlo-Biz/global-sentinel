from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from src.monitoring.telegram_notifier import TelegramNotifier


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def relation_to_threshold(value: Optional[float], threshold: float) -> Optional[str]:
    if value is None:
        return None
    return "above_or_equal" if float(value) >= float(threshold) else "below"


def threshold_crossed(previous_relation: Optional[str], current_relation: Optional[str], direction: str) -> bool:
    if previous_relation is None or current_relation is None:
        return False
    normalized = str(direction or "up").strip().lower()
    if normalized == "up":
        return previous_relation == "below" and current_relation == "above_or_equal"
    if normalized == "down":
        return previous_relation == "above_or_equal" and current_relation == "below"
    if normalized == "either":
        return previous_relation != current_relation
    raise ValueError(f"unsupported threshold direction: {direction}")


class PositionThresholdMonitor:
    def __init__(
        self,
        repo_root: Path,
        *,
        dashboard_base_url: str = "http://127.0.0.1:8501",
        config_path: Optional[Path] = None,
        state_path: Optional[Path] = None,
        event_log_path: Optional[Path] = None,
    ):
        self.repo_root = repo_root
        self.dashboard_base_url = dashboard_base_url.rstrip("/")
        self.config_path = config_path or (repo_root / "config" / "position_alerts.json")
        self.state_path = state_path or (repo_root / "logs" / "notifications" / "position_threshold_state.json")
        self.event_log_path = event_log_path or (repo_root / "logs" / "notifications" / "position_threshold_alerts.jsonl")
        self.notifier = TelegramNotifier(repo_root)

    def load_config(self) -> Dict[str, Any]:
        payload = load_json(self.config_path, {"poll_seconds": 15, "alerts": []})
        if not isinstance(payload, dict):
            return {"poll_seconds": 15, "alerts": []}
        payload.setdefault("poll_seconds", 15)
        payload.setdefault("alerts", [])
        return payload

    def load_state(self) -> Dict[str, Any]:
        payload = load_json(self.state_path, {"threshold_states": {}})
        if not isinstance(payload, dict):
            return {"threshold_states": {}}
        payload.setdefault("threshold_states", {})
        return payload

    def save_state(self, state: Dict[str, Any]) -> None:
        write_json(self.state_path, state)

    def append_event(self, payload: Dict[str, Any]) -> None:
        self.event_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.event_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")

    def fetch_json(self, url: str) -> Any:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode("utf-8"))

    def fetch_position_prices(self) -> List[Dict[str, Any]]:
        payload = self.fetch_json(f"{self.dashboard_base_url}/api/position-prices")
        positions = payload.get("positions") if isinstance(payload, dict) else []
        return [row for row in positions if isinstance(row, dict)]

    def fetch_latest_underlying_prices(self, symbols: Iterable[str]) -> Dict[str, Optional[float]]:
        result: Dict[str, Optional[float]] = {}
        for symbol in sorted({str(item).upper() for item in symbols if str(item).strip()}):
            encoded = urllib.parse.quote(symbol)
            try:
                payload = self.fetch_json(
                    f"{self.dashboard_base_url}/api/bars/{encoded}?timeframe=1Min&limit=1"
                )
                bars = payload.get("bars") if isinstance(payload, dict) else []
                last_bar = bars[-1] if isinstance(bars, list) and bars else {}
                result[symbol] = float(last_bar.get("c")) if last_bar and last_bar.get("c") is not None else None
            except Exception:
                result[symbol] = None
        return result

    def _find_position(self, positions: List[Dict[str, Any]], symbol: str, account: str) -> Optional[Dict[str, Any]]:
        wanted_symbol = str(symbol).upper()
        wanted_account = str(account or "").strip()
        for row in positions:
            row_symbol = str(row.get("symbol") or "").upper()
            row_account = str(row.get("account") or row.get("account_label") or "").strip()
            if row_symbol != wanted_symbol:
                continue
            if wanted_account and row_account != wanted_account:
                continue
            return row
        return None

    def _evaluate_level(
        self,
        *,
        state: Dict[str, Any],
        alert_id: str,
        scope: str,
        level: Dict[str, Any],
        current_value: Optional[float],
    ) -> Tuple[bool, Dict[str, Any]]:
        threshold = float(level["price"])
        state_key = f"{alert_id}:{scope}:{level['name']}"
        threshold_states = state.setdefault("threshold_states", {})
        previous = threshold_states.get(state_key) if isinstance(threshold_states, dict) else None
        previous_relation = previous.get("relation") if isinstance(previous, dict) else None
        current_relation = relation_to_threshold(current_value, threshold)
        fired = threshold_crossed(previous_relation, current_relation, str(level.get("direction") or "up"))
        threshold_states[state_key] = {
            "relation": current_relation,
            "last_value": current_value,
            "threshold": threshold,
            "updated_at_utc": iso_now(),
        }
        return fired, threshold_states[state_key]

    def _format_alert_message(
        self,
        *,
        alert: Dict[str, Any],
        scope: str,
        level: Dict[str, Any],
        position: Dict[str, Any],
        current_value: Optional[float],
        underlying_price: Optional[float],
    ) -> str:
        symbol = str(position.get("symbol") or alert.get("symbol") or "")
        account = str(position.get("account") or position.get("account_label") or alert.get("account") or "")
        option_price = position.get("current_price")
        bid = position.get("bid")
        ask = position.get("ask")
        pnl = position.get("pnl")
        pnl_pct = position.get("pnl_pct")
        underlying = str(position.get("underlying") or alert.get("underlying_symbol") or "")
        direction = str(level.get("direction") or "up").upper()
        trigger_kind = "UNDERLYING" if scope == "underlying" else "OPTION"

        lines = [
            f"Position Threshold Hit: {level['name']}",
            f"{symbol} ({account or 'unknown_account'})",
            f"Trigger: {trigger_kind} {direction} through ${float(level['price']):.2f}",
            f"Current trigger value: ${float(current_value):.3f}" if current_value is not None else "Current trigger value: unavailable",
        ]
        if option_price is not None:
            lines.append(f"Option mid: ${float(option_price):.3f}")
        if bid is not None and ask is not None:
            lines.append(f"Bid/ask: ${float(bid):.2f} / ${float(ask):.2f}")
        if pnl is not None and pnl_pct is not None:
            lines.append(f"P&L: ${float(pnl):+.2f} ({float(pnl_pct):+.2f}%)")
        if underlying:
            if underlying_price is not None:
                lines.append(f"Underlying {underlying}: ${float(underlying_price):.3f}")
            else:
                lines.append(f"Underlying {underlying}: unavailable")
        lines.append(f"Time: {iso_now()}")
        return "\n".join(lines)

    def poll_once(self) -> Dict[str, Any]:
        config = self.load_config()
        state = self.load_state()
        positions = self.fetch_position_prices()
        alerts_cfg = [item for item in config.get("alerts", []) if isinstance(item, dict) and item.get("enabled", True)]

        underlying_symbols = []
        for item in alerts_cfg:
            if item.get("underlying_symbol"):
                underlying_symbols.append(str(item.get("underlying_symbol")))
                continue
            position = self._find_position(positions, str(item.get("symbol") or ""), str(item.get("account") or ""))
            if position and position.get("underlying"):
                underlying_symbols.append(str(position.get("underlying")))

        underlying_prices = self.fetch_latest_underlying_prices(underlying_symbols)
        events: List[Dict[str, Any]] = []

        for alert in alerts_cfg:
            alert_id = str(alert.get("id") or alert.get("symbol") or "unnamed_alert")
            symbol = str(alert.get("symbol") or "")
            account = str(alert.get("account") or "")
            position = self._find_position(positions, symbol, account)
            if not position:
                continue

            underlying_symbol = str(
                alert.get("underlying_symbol")
                or position.get("underlying")
                or ""
            ).upper()
            underlying_price = underlying_prices.get(underlying_symbol) if underlying_symbol else None

            for level in alert.get("option_levels", []) or []:
                if not isinstance(level, dict) or level.get("price") is None or not level.get("name"):
                    continue
                current_value = float(position.get("current_price")) if position.get("current_price") is not None else None
                fired, state_entry = self._evaluate_level(
                    state=state,
                    alert_id=alert_id,
                    scope="option",
                    level=level,
                    current_value=current_value,
                )
                if not fired:
                    continue
                message = self._format_alert_message(
                    alert=alert,
                    scope="option",
                    level=level,
                    position=position,
                    current_value=current_value,
                    underlying_price=underlying_price,
                )
                self.notifier.send_message(
                    message,
                    bot_name=str(alert.get("bot_name") or "mo2darkbot"),
                    chat_id=str(alert.get("chat_id") or ""),
                )
                event = {
                    "timestamp_utc": iso_now(),
                    "event_type": "position_threshold_triggered",
                    "alert_id": alert_id,
                    "scope": "option",
                    "symbol": symbol,
                    "account": account,
                    "threshold_name": str(level["name"]),
                    "threshold_price": float(level["price"]),
                    "direction": str(level.get("direction") or "up"),
                    "current_value": current_value,
                    "relation": state_entry.get("relation"),
                    "message": message,
                }
                events.append(event)
                self.append_event(event)

            for level in alert.get("underlying_levels", []) or []:
                if not isinstance(level, dict) or level.get("price") is None or not level.get("name"):
                    continue
                fired, state_entry = self._evaluate_level(
                    state=state,
                    alert_id=alert_id,
                    scope="underlying",
                    level=level,
                    current_value=underlying_price,
                )
                if not fired:
                    continue
                message = self._format_alert_message(
                    alert=alert,
                    scope="underlying",
                    level=level,
                    position=position,
                    current_value=underlying_price,
                    underlying_price=underlying_price,
                )
                self.notifier.send_message(
                    message,
                    bot_name=str(alert.get("bot_name") or "mo2darkbot"),
                    chat_id=str(alert.get("chat_id") or ""),
                )
                event = {
                    "timestamp_utc": iso_now(),
                    "event_type": "position_threshold_triggered",
                    "alert_id": alert_id,
                    "scope": "underlying",
                    "symbol": symbol,
                    "underlying_symbol": underlying_symbol,
                    "account": account,
                    "threshold_name": str(level["name"]),
                    "threshold_price": float(level["price"]),
                    "direction": str(level.get("direction") or "up"),
                    "current_value": underlying_price,
                    "relation": state_entry.get("relation"),
                    "message": message,
                }
                events.append(event)
                self.append_event(event)

        self.save_state(state)
        return {
            "timestamp_utc": iso_now(),
            "monitored_alert_count": len(alerts_cfg),
            "triggered_alert_count": len(events),
            "events": events,
        }

    def run_forever(self) -> None:
        while True:
            try:
                result = self.poll_once()
                print(
                    f"[{result['timestamp_utc']}] position_threshold_monitor "
                    f"alerts={result['triggered_alert_count']}/{result['monitored_alert_count']}",
                    flush=True,
                )
            except urllib.error.HTTPError as exc:
                print(f"[{iso_now()}] dashboard http error: {exc}", flush=True)
            except urllib.error.URLError as exc:
                print(f"[{iso_now()}] dashboard url error: {exc}", flush=True)
            except Exception as exc:
                print(f"[{iso_now()}] position threshold monitor error: {exc}", flush=True)

            config = self.load_config()
            poll_seconds = max(int(config.get("poll_seconds") or 15), 5)
            time.sleep(poll_seconds)
