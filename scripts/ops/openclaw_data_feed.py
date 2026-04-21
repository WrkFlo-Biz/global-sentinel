#!/usr/bin/env python3
"""
Global Sentinel — OpenClaw Bot Data Feed

Compiles a compact JSON + human-readable summary from GS data
for the OpenClaw Telegram bot to read and relay to users.
Runs every 5 minutes during market hours.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone, date
from pathlib import Path
from typing import Any, Dict, List, Optional

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("global_sentinel.openclaw_data_feed")

DOTENV = os.environ.get("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel") + "/.env"


def load_env():
    if os.path.exists(DOTENV):
        with open(DOTENV) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


load_env()


class OpenClawDataFeed:
    def __init__(self):
        self.repo_root = Path(os.environ.get("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
        self.qf = self.repo_root / "data" / "quantum_feed"
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        self.broker_url = "https://paper-api.alpaca.markets"
        self.output_json = self.qf / "openclaw_live_feed.json"
        self.output_txt = self.qf / "openclaw_summary.txt"

    def _api_get(self, url: str) -> Optional[Any]:
        req = urllib.request.Request(url)
        req.add_header("APCA-API-KEY-ID", self.api_key)
        req.add_header("APCA-API-SECRET-KEY", self.api_secret)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.warning(f"API error {url[:80]}: {e}")
            return None

    def _load_json(self, filename: str) -> Optional[Dict]:
        p = self.qf / filename
        try:
            with open(p) as f:
                return json.load(f)
        except Exception:
            return None

    def _get_account(self) -> Dict:
        data = self._api_get(f"{self.broker_url}/v2/account")
        if not data:
            return {"error": "unavailable"}
        equity = float(data.get("equity", 0))
        last_equity = float(data.get("last_equity", 0))
        daily_pnl = equity - last_equity
        return {
            "equity": round(equity, 2),
            "last_equity": round(last_equity, 2),
            "daily_pnl": round(daily_pnl, 2),
            "daily_pnl_pct": round(daily_pnl / last_equity * 100, 2) if last_equity else 0,
            "buying_power": round(float(data.get("buying_power", 0)), 2),
            "cash": round(float(data.get("cash", 0)), 2),
        }

    def _get_positions(self) -> List[Dict]:
        data = self._api_get(f"{self.broker_url}/v2/positions")
        if not data:
            return []
        positions = []
        for p in data:
            positions.append({
                "symbol": p.get("symbol", ""),
                "qty": float(p.get("qty", 0)),
                "side": p.get("side", ""),
                "avg_entry": round(float(p.get("avg_entry_price", 0)), 2),
                "current_price": round(float(p.get("current_price", 0)), 2),
                "unrealized_pnl": round(float(p.get("unrealized_pl", 0)), 2),
                "unrealized_pnl_pct": round(float(p.get("unrealized_plpc", 0)) * 100, 2),
                "market_value": round(float(p.get("market_value", 0)), 2),
            })
        return sorted(positions, key=lambda x: abs(x["unrealized_pnl"]), reverse=True)

    def _get_recent_trades(self, limit: int = 3) -> List[Dict]:
        data = self._api_get(
            f"{self.broker_url}/v2/orders?status=closed&limit={limit}&direction=desc"
        )
        if not data:
            return []
        trades = []
        for o in data[:limit]:
            filled_avg = float(o.get("filled_avg_price", 0) or 0)
            trades.append({
                "symbol": o.get("symbol", ""),
                "side": o.get("side", ""),
                "qty": o.get("filled_qty", "0"),
                "filled_price": round(filled_avg, 2),
                "filled_at": o.get("filled_at", ""),
                "type": o.get("type", ""),
            })
        return trades

    def _get_signals(self) -> Dict:
        result = {"leaders": [], "regime": "unknown", "alerts": []}

        # Momentum leaders
        heatmap = self._load_json("momentum_heatmap.json")
        if heatmap:
            result["leaders"] = heatmap.get("leaders_top5", [])[:5]

        # Regime
        regime = self._load_json("hmm_regime.json")
        if regime:
            result["regime"] = regime.get("current_regime", "unknown")
            result["regime_duration"] = regime.get("regime_duration_days", 0)

        # Ensemble signals
        ensemble = self._load_json("ensemble_signals.json")
        if ensemble and "signals" in ensemble:
            for s in ensemble["signals"][:10]:
                if abs(s.get("ensemble_signal", 0)) > 0.3:
                    result["alerts"].append({
                        "symbol": s["symbol"],
                        "signal": round(s["ensemble_signal"], 3),
                        "direction": s.get("direction", ""),
                    })

        return result

    def _get_key_levels(self) -> Dict:
        """Extract SPY/QQQ support/resistance from available data."""
        levels = {}
        for sym in ["SPY", "QQQ"]:
            # Try ict_smc_signals for structure levels
            smc = self._load_json("ict_smc_signals.json")
            if smc and "signals" in smc:
                for s in smc["signals"]:
                    if s.get("symbol") == sym:
                        levels[sym] = {
                            "bias": s.get("bias", ""),
                            "support": s.get("demand_zone", {}).get("low", 0),
                            "resistance": s.get("supply_zone", {}).get("high", 0),
                        }
                        break
            if sym not in levels:
                levels[sym] = {"bias": "N/A", "support": 0, "resistance": 0}
        return levels

    def _get_options_unusual(self, limit: int = 3) -> List[Dict]:
        flow = self._load_json("options_flow.json")
        if not flow:
            return []
        unusual = flow.get("top_unusual", flow.get("summary", {}).get("most_active_strikes", []))
        if isinstance(unusual, list):
            return unusual[:limit]
        return []

    def run(self):
        logger.info("OpenClaw Data Feed compiling...")
        now = datetime.now(timezone.utc)

        account = self._get_account()
        positions = self._get_positions()
        trades = self._get_recent_trades(3)
        signals = self._get_signals()
        key_levels = self._get_key_levels()
        options_unusual = self._get_options_unusual(3)

        # Volatility surface
        vol_surface = self._load_json("volatility_surface.json")
        vol_flags = {}
        if vol_surface and "symbols" in vol_surface:
            for sym, data in vol_surface["symbols"].items():
                if data.get("flags"):
                    vol_flags[sym] = data["flags"]

        feed = {
            "timestamp": now.isoformat(),
            "source": "global_sentinel",
            "account": account,
            "positions": positions,
            "recent_trades": trades,
            "signals": signals,
            "key_levels": key_levels,
            "options_unusual": options_unusual,
            "volatility_flags": vol_flags,
        }

        with open(self.output_json, "w") as f:
            json.dump(feed, f, indent=2)
        logger.info(f"JSON feed written to {self.output_json}")

        # Human-readable summary
        lines = []
        lines.append(f"Global Sentinel Live Summary — {now.strftime('%Y-%m-%d %H:%M UTC')}")
        lines.append("=" * 55)

        # Account
        if "error" not in account:
            pnl_sign = "+" if account["daily_pnl"] >= 0 else ""
            lines.append(
                f"\nACCOUNT: ${account['equity']:,.0f} equity | "
                f"Day P&L: {pnl_sign}${account['daily_pnl']:,.2f} ({pnl_sign}{account['daily_pnl_pct']}%)"
            )
        else:
            lines.append("\nACCOUNT: unavailable")

        # Positions
        if positions:
            lines.append(f"\nOPEN POSITIONS ({len(positions)}):")
            for p in positions[:8]:
                pnl_sign = "+" if p["unrealized_pnl"] >= 0 else ""
                lines.append(
                    f"  {p['symbol']:6s} {p['qty']:>6.0f} @ ${p['avg_entry']:.2f} → "
                    f"${p['current_price']:.2f} | {pnl_sign}${p['unrealized_pnl']:.2f} ({pnl_sign}{p['unrealized_pnl_pct']}%)"
                )
        else:
            lines.append("\nNO OPEN POSITIONS")

        # Signals
        lines.append(f"\nMARKET REGIME: {signals['regime'].upper()}")
        if signals["leaders"]:
            leaders_str = ", ".join(
                f"{l['symbol']}({l.get('pct_chg', 0):+.1f}%)" for l in signals["leaders"][:5]
            )
            lines.append(f"MOMENTUM LEADERS: {leaders_str}")

        if signals["alerts"]:
            lines.append("ACTIVE SIGNALS:")
            for a in signals["alerts"][:5]:
                lines.append(f"  {a['symbol']}: {a['direction']} (strength {a['signal']})")

        # Key levels
        for sym in ["SPY", "QQQ"]:
            if sym in key_levels and key_levels[sym]["support"]:
                kl = key_levels[sym]
                lines.append(f"{sym} LEVELS: S={kl['support']:.1f} R={kl['resistance']:.1f} bias={kl['bias']}")

        # Recent trades
        if trades:
            lines.append("\nLAST TRADES:")
            for t in trades:
                lines.append(f"  {t['side'].upper()} {t['qty']} {t['symbol']} @ ${t['filled_price']:.2f}")

        # Volatility flags
        if vol_flags:
            flags_str = ", ".join(f"{sym}: {'/'.join(f)}" for sym, f in vol_flags.items())
            lines.append(f"\nVOL FLAGS: {flags_str}")

        summary = "\n".join(lines)
        with open(self.output_txt, "w") as f:
            f.write(summary)
        logger.info(f"Summary written to {self.output_txt}")

        logger.info("OpenClaw Data Feed complete.")


if __name__ == "__main__":
    feed = OpenClawDataFeed()
    feed.run()
