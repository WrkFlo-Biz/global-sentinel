#!/usr/bin/env python3
"""
Global Sentinel — Volatility Surface Tracker

Runs every 30 min during market hours for top 10 symbols.
Computes IV rank, IV percentile, put/call skew, term structure slope.
Flags IV_CHEAP, IV_EXPENSIVE, SKEW_ALERT conditions.
Outputs to data/quantum_feed/volatility_surface.json.
"""
from __future__ import annotations

import json
import logging
import math
import os
import sys
import urllib.request
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# --- Telegram topic routing ---
sys.path.insert(0, "/opt/global-sentinel") if "/opt/global-sentinel" not in sys.path else None
try:
    from src.monitoring.telegram_router import send as _send_topic
except Exception:
    _send_topic = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("global_sentinel.volatility_surface")

SYMBOLS = ["SPY", "QQQ", "NVDA", "TSLA", "AMD", "META", "AAPL", "AMZN", "GOOGL", "MSFT"]

# ── Env / .env ─────────────────────────────────────────────────────
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


class VolatilitySurfaceTracker:
    def __init__(self):
        self.repo_root = Path(os.environ.get("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        self.data_url = "https://data.alpaca.markets"
        self.broker_url = "https://paper-api.alpaca.markets"
        self.output_path = self.repo_root / "data" / "quantum_feed" / "volatility_surface.json"
        self.history_path = self.repo_root / "data" / "quantum_feed" / "iv_history.json"
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "7091381625")

    def _api_get(self, url: str) -> Optional[Any]:
        req = urllib.request.Request(url)
        req.add_header("APCA-API-KEY-ID", self.api_key)
        req.add_header("APCA-API-SECRET-KEY", self.api_secret)
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception as e:
            logger.warning(f"API error for {url[:120]}: {e}")
            return None

    def _get_stock_price(self, symbol: str) -> Optional[float]:
        url = f"{self.data_url}/v2/stocks/{symbol}/quotes/latest"
        data = self._api_get(url)
        if data and "quote" in data:
            q = data["quote"]
            bid, ask = q.get("bp", 0), q.get("ap", 0)
            if bid and ask:
                return (bid + ask) / 2.0
        return None

    def _get_expirations(self) -> List[str]:
        """Generate target expirations: today, this Friday, next Friday, monthly (3rd Friday)."""
        today = date.today()
        exps = set()
        # Today
        exps.add(today.isoformat())
        # This Friday
        days_to_fri = (4 - today.weekday()) % 7
        this_fri = today + timedelta(days=days_to_fri if days_to_fri > 0 else 7)
        exps.add(this_fri.isoformat())
        # Next Friday
        next_fri = this_fri + timedelta(days=7)
        exps.add(next_fri.isoformat())
        # Monthly (3rd Friday of next month)
        if today.month == 12:
            nm_year, nm_month = today.year + 1, 1
        else:
            nm_year, nm_month = today.year, today.month + 1
        first_day = date(nm_year, nm_month, 1)
        first_fri_offset = (4 - first_day.weekday()) % 7
        third_fri = first_day + timedelta(days=first_fri_offset + 14)
        exps.add(third_fri.isoformat())
        return sorted(exps)

    def _get_all_snapshots(self, symbol: str) -> Dict[str, Any]:
        """Fetch all options snapshots for a symbol from Alpaca data API (v1beta1)."""
        import time
        all_snapshots = {}
        page_token = None
        for _ in range(5):
            url = f"{self.data_url}/v1beta1/options/snapshots/{symbol}?feed=indicative&limit=200"
            if page_token:
                url += f"&page_token={page_token}"
            data = self._api_get(url)
            if not data or not isinstance(data, dict):
                break
            snapshots = data.get("snapshots", data)
            if isinstance(snapshots, dict):
                all_snapshots.update(snapshots)
            page_token = data.get("next_page_token")
            if not page_token:
                break
            time.sleep(0.15)
        return all_snapshots

    def _parse_contract_id(self, contract_id: str, symbol: str) -> Dict:
        """Parse OCC-style contract ID: SYMBOLYYMMDDCSSSSSSSS"""
        remainder = contract_id[len(symbol):] if contract_id.startswith(symbol) else contract_id
        result = {"expiry": "", "type": "", "strike": 0.0}
        if len(remainder) >= 15:
            yy, mm, dd = remainder[0:2], remainder[2:4], remainder[4:6]
            result["expiry"] = f"20{yy}-{mm}-{dd}"
            result["type"] = "call" if remainder[6].upper() == "C" else "put"
            try:
                result["strike"] = int(remainder[7:15]) / 1000.0
            except ValueError:
                pass
        return result

    def _get_option_chain(self, symbol: str, expiration: str) -> List[Dict]:
        """Get option snapshots for a symbol, filtered to a specific expiration."""
        if not hasattr(self, "_snapshot_cache"):
            self._snapshot_cache = {}
        if symbol not in self._snapshot_cache:
            self._snapshot_cache[symbol] = self._get_all_snapshots(symbol)

        contracts = []
        for sym_key, snap in self._snapshot_cache[symbol].items():
            parsed = self._parse_contract_id(sym_key, symbol)
            if parsed["expiry"] != expiration:
                continue
            contracts.append(self._parse_snapshot(sym_key, snap, parsed["type"]))
        return contracts

    def _parse_snapshot(self, sym_key: str, snap: Dict, opt_type: str) -> Dict:
        quote = snap.get("latestQuote", {})
        greeks = snap.get("greeks", {})
        bid = quote.get("bp", 0) or 0
        ask = quote.get("ap", 0) or 0
        mid = (bid + ask) / 2.0 if (bid and ask) else 0
        # Extract strike from OCC symbol (last 8 chars / 1000)
        try:
            strike = int(sym_key[-8:]) / 1000.0
        except Exception:
            strike = 0.0
        return {
            "symbol": sym_key,
            "type": opt_type,
            "strike": strike,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "iv": greeks.get("implied_volatility", None),
            "delta": greeks.get("delta", None),
            "volume": snap.get("latestTrade", {}).get("s", 0) or 0,
        }

    def _compute_iv_from_spread(self, contracts: List[Dict], spot: float) -> List[Dict]:
        """Use greeks IV if available, else estimate from bid-ask spread as proxy."""
        for c in contracts:
            if c["iv"] is not None and c["iv"] > 0:
                continue
            # Proxy: wider spread on OTM options implies higher IV
            if c["mid"] > 0 and spot > 0:
                spread = c["ask"] - c["bid"]
                moneyness = abs(c["strike"] - spot) / spot
                c["iv"] = max(0.05, min(2.0, (spread / c["mid"]) * 2 + moneyness))
            else:
                c["iv"] = None
        return contracts

    def _compute_surface_metrics(
        self, symbol: str, spot: float, expirations: List[str]
    ) -> Dict[str, Any]:
        """Compute IV metrics across expirations."""
        surface = {}
        all_ivs = []
        otm_put_ivs = []
        otm_call_ivs = []
        avg_iv_by_dte = {}

        for exp in expirations:
            contracts = self._get_option_chain(symbol, exp)
            contracts = self._compute_iv_from_spread(contracts, spot)

            exp_ivs = []
            for c in contracts:
                if c["iv"] is None or c["iv"] <= 0:
                    continue
                exp_ivs.append(c["iv"])
                all_ivs.append(c["iv"])

                # OTM classification for skew
                if c["type"] == "put" and c["strike"] < spot * 0.97:
                    otm_put_ivs.append(c["iv"])
                elif c["type"] == "call" and c["strike"] > spot * 1.03:
                    otm_call_ivs.append(c["iv"])

            dte = max(0, (date.fromisoformat(exp) - date.today()).days)
            if exp_ivs:
                avg_iv = sum(exp_ivs) / len(exp_ivs)
                avg_iv_by_dte[dte] = avg_iv
                surface[exp] = {
                    "dte": dte,
                    "avg_iv": round(avg_iv, 4),
                    "min_iv": round(min(exp_ivs), 4),
                    "max_iv": round(max(exp_ivs), 4),
                    "n_contracts": len(exp_ivs),
                }

        current_iv = sum(all_ivs) / len(all_ivs) if all_ivs else 0

        # Put/call skew
        avg_put_iv = sum(otm_put_ivs) / len(otm_put_ivs) if otm_put_ivs else 0
        avg_call_iv = sum(otm_call_ivs) / len(otm_call_ivs) if otm_call_ivs else 0
        skew_ratio = round(avg_put_iv / avg_call_iv, 4) if avg_call_iv > 0 else 0

        # Term structure slope (short vs long DTE)
        sorted_dtes = sorted(avg_iv_by_dte.keys())
        if len(sorted_dtes) >= 2:
            short_iv = avg_iv_by_dte[sorted_dtes[0]]
            long_iv = avg_iv_by_dte[sorted_dtes[-1]]
            term_slope = round((long_iv - short_iv) / max(short_iv, 0.01), 4)
        else:
            term_slope = 0.0

        return {
            "spot": round(spot, 2),
            "current_iv": round(current_iv, 4),
            "otm_put_iv": round(avg_put_iv, 4),
            "otm_call_iv": round(avg_call_iv, 4),
            "skew_ratio": skew_ratio,
            "term_structure_slope": term_slope,
            "surface": surface,
        }

    def _load_iv_history(self) -> Dict:
        try:
            with open(self.history_path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_iv_history(self, history: Dict):
        with open(self.history_path, "w") as f:
            json.dump(history, f, indent=2)

    def _compute_iv_rank_percentile(
        self, symbol: str, current_iv: float, history: Dict
    ) -> Tuple[float, float]:
        """IV rank = (current - 52w low) / (52w high - 52w low). IV percentile = % of days below current."""
        entries = history.get(symbol, [])
        if len(entries) < 5:
            return 50.0, 50.0  # not enough data

        ivs = [e["iv"] for e in entries]
        iv_min = min(ivs)
        iv_max = max(ivs)
        iv_range = iv_max - iv_min
        iv_rank = ((current_iv - iv_min) / iv_range * 100) if iv_range > 0 else 50.0
        iv_percentile = sum(1 for iv in ivs if iv < current_iv) / len(ivs) * 100

        return round(max(0, min(100, iv_rank)), 1), round(iv_percentile, 1)

    def _update_iv_history(self, history: Dict, symbol: str, current_iv: float):
        today_str = date.today().isoformat()
        if symbol not in history:
            history[symbol] = []
        # Don't duplicate today
        if history[symbol] and history[symbol][-1].get("date") == today_str:
            history[symbol][-1]["iv"] = current_iv
        else:
            history[symbol].append({"date": today_str, "iv": current_iv})
        # Keep 252 trading days (~1 year)
        history[symbol] = history[symbol][-252:]

    def _send_telegram(self, text: str):
        if _send_topic:
            try:
                _send_topic(text[:4000], topic="research")
                return
            except Exception:
                pass
        if not self.bot_token:
            logger.warning("No TELEGRAM_BOT_TOKEN, skipping alert")
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_notification": True, "message_thread_id": 74,
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    def run(self):
        logger.info("Volatility Surface Tracker starting...")
        expirations = self._get_expirations()
        logger.info(f"Target expirations: {expirations}")
        history = self._load_iv_history()

        results = {}
        alerts = []

        for symbol in SYMBOLS:
            logger.info(f"Processing {symbol}...")
            spot = self._get_stock_price(symbol)
            if not spot:
                logger.warning(f"Could not get spot price for {symbol}, skipping")
                continue

            metrics = self._compute_surface_metrics(symbol, spot, expirations)
            current_iv = metrics["current_iv"]

            if current_iv <= 0:
                logger.warning(f"No valid IV data for {symbol}, skipping")
                continue

            self._update_iv_history(history, symbol, current_iv)
            iv_rank, iv_percentile = self._compute_iv_rank_percentile(symbol, current_iv, history)

            flags = []
            if iv_rank < 20:
                flags.append("IV_CHEAP")
            if iv_rank > 80:
                flags.append("IV_EXPENSIVE")
            if metrics["skew_ratio"] > 1.3:
                flags.append("SKEW_ALERT")

            results[symbol] = {
                "spot": metrics["spot"],
                "current_iv": current_iv,
                "iv_rank": iv_rank,
                "iv_percentile": iv_percentile,
                "otm_put_iv": metrics["otm_put_iv"],
                "otm_call_iv": metrics["otm_call_iv"],
                "skew_ratio": metrics["skew_ratio"],
                "term_structure_slope": metrics["term_structure_slope"],
                "flags": flags,
                "surface": metrics["surface"],
            }

            if flags:
                alerts.append(f"<b>{symbol}</b>: {', '.join(flags)} (IV rank {iv_rank}, skew {metrics['skew_ratio']})")

            logger.info(
                f"  {symbol}: IV={current_iv:.4f}, rank={iv_rank}, pctile={iv_percentile}, "
                f"skew={metrics['skew_ratio']}, slope={metrics['term_structure_slope']}, flags={flags}"
            )

        # Save history
        self._save_iv_history(history)

        # Write output
        output = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scanner": "volatility_surface",
            "symbols_processed": len(results),
            "expirations_scanned": expirations,
            "symbols": results,
        }
        with open(self.output_path, "w") as f:
            json.dump(output, f, indent=2)
        logger.info(f"Output written to {self.output_path}")

        # Telegram alerts
        if alerts:
            msg = "<b>Volatility Surface Alerts</b>\n" + "\n".join(alerts)
            self._send_telegram(msg)
            logger.info(f"Sent {len(alerts)} alerts via Telegram")

        logger.info("Volatility Surface Tracker complete.")


if __name__ == "__main__":
    tracker = VolatilitySurfaceTracker()
    tracker.run()
