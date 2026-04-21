#!/usr/bin/env python3
"""
Global Sentinel — Options Unusual Activity Scanner

Scans options chains for top 20 symbols via Alpaca options API.
Flags unusual activity: volume > 2x OI, volume > 10000, or single-strike spikes.
Outputs sorted results to data/quantum_feed/options_flow.json.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone, date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("global_sentinel.options_flow_scanner")

SCAN_SYMBOLS = [
    "SPY", "QQQ", "NVDA", "TSLA", "AMD", "META", "AAPL", "AMZN",
    "GOOGL", "MSFT", "PLTR", "COIN", "XLE", "XLF", "SOXL", "TQQQ",
    "IWM", "TLT", "GLD", "UVXY",
]

TOP_N = 50


class OptionsFlowScanner:
    def __init__(self):
        self.repo_root = Path(os.environ.get("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
        self.api_key = os.environ.get("ALPACA_API_KEY", "")
        self.api_secret = os.environ.get("ALPACA_SECRET_KEY", "")
        self.broker_url = "https://paper-api.alpaca.markets"
        self.data_url = "https://data.alpaca.markets"
        self.output_path = self.repo_root / "data" / "quantum_feed" / "options_flow.json"
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def _api_get(self, url: str) -> Optional[Any]:
        """Authenticated GET request."""
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

    def _get_snapshots(self, symbol: str) -> Dict[str, Any]:
        """Get options snapshots for a symbol from data API."""
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

    def _parse_contract_id(self, contract_id: str, symbol: str) -> Dict[str, Any]:
        """Parse OCC-style contract ID: SYMBOLYYMMDDCSSSSSSSS"""
        remainder = contract_id[len(symbol):] if contract_id.startswith(symbol) else contract_id
        result: Dict[str, Any] = {"symbol": symbol, "expiry": "", "type": "", "strike": 0.0}
        if len(remainder) >= 15:
            yy = remainder[0:2]
            mm = remainder[2:4]
            dd = remainder[4:6]
            result["expiry"] = f"20{yy}-{mm}-{dd}"
            result["type"] = "call" if remainder[6].upper() == "C" else "put"
            try:
                result["strike"] = int(remainder[7:15]) / 1000.0
            except ValueError:
                pass
        return result

    def scan(self) -> Dict[str, Any]:
        """Main scan: iterate symbols, collect unusual activity."""
        if not self.api_key or not self.api_secret:
            logger.error("Missing ALPACA_API_KEY or ALPACA_SECRET_KEY")
            return {"error": "missing_api_keys"}

        unusual_contracts: List[Dict[str, Any]] = []
        symbol_stats: Dict[str, Any] = {}
        total_call_vol = 0
        total_put_vol = 0

        for symbol in SCAN_SYMBOLS:
            logger.info(f"Scanning {symbol}...")
            try:
                snapshots = self._get_snapshots(symbol)
                if not snapshots:
                    logger.info(f"  {symbol}: no snapshot data")
                    continue

                sym_call_vol = 0
                sym_put_vol = 0
                sym_call_oi = 0
                sym_put_oi = 0
                sym_contracts_scanned = 0

                volumes: List[int] = []
                contract_data: List[Dict[str, Any]] = []

                for contract_id, snap in snapshots.items():
                    if not isinstance(snap, dict):
                        continue
                    sym_contracts_scanned += 1
                    parsed = self._parse_contract_id(contract_id, symbol)

                    trade = snap.get("latestTrade", {}) or {}
                    quote = snap.get("latestQuote", {}) or {}
                    oi = snap.get("openInterest", 0) or 0
                    daily = snap.get("dailyBar", {}) or {}
                    volume = daily.get("v", 0) or 0
                    if volume == 0:
                        volume = trade.get("s", 0) or 0

                    ask = quote.get("ap", 0) or 0
                    bid = quote.get("bp", 0) or 0
                    mid = (ask + bid) / 2.0 if ask and bid else 0

                    if parsed["type"] == "call":
                        sym_call_vol += volume
                        sym_call_oi += oi
                    else:
                        sym_put_vol += volume
                        sym_put_oi += oi

                    if volume > 0:
                        volumes.append(volume)

                    contract_data.append({
                        "contract_id": contract_id,
                        "symbol": symbol,
                        "strike": parsed["strike"],
                        "expiry": parsed["expiry"],
                        "type": parsed["type"],
                        "volume": volume,
                        "open_interest": oi,
                        "vol_oi_ratio": round(volume / max(oi, 1), 2),
                        "mid_price": round(mid, 4),
                        "est_premium": round(volume * mid * 100, 2),
                    })

                avg_vol = sum(volumes) / max(len(volumes), 1) if volumes else 0

                for cd in contract_data:
                    vol = cd["volume"]
                    oi_val = cd["open_interest"]
                    reasons = []

                    if oi_val > 0 and vol > 2 * oi_val:
                        reasons.append(f"vol>2xOI (ratio={cd['vol_oi_ratio']})")
                    if vol > 10000:
                        reasons.append(f"high_vol={vol}")
                    if avg_vol > 0 and vol > 5 * avg_vol and vol > 50:
                        reasons.append(f"spike vs avg ({vol} vs {avg_vol:.0f})")

                    if reasons:
                        direction = "bullish" if cd["type"] == "call" else "bearish"
                        cd["unusual_reasons"] = reasons
                        cd["implied_direction"] = direction
                        unusual_contracts.append(cd)

                total_call_vol += sym_call_vol
                total_put_vol += sym_put_vol
                pc_ratio = round(sym_put_vol / max(sym_call_vol, 1), 3)
                symbol_stats[symbol] = {
                    "contracts_scanned": sym_contracts_scanned,
                    "call_volume": sym_call_vol,
                    "put_volume": sym_put_vol,
                    "call_oi": sym_call_oi,
                    "put_oi": sym_put_oi,
                    "put_call_ratio": pc_ratio,
                }
                logger.info(
                    f"  {symbol}: {sym_contracts_scanned} contracts, "
                    f"P/C={pc_ratio}, "
                    f"unusual={len([c for c in unusual_contracts if c['symbol'] == symbol])}"
                )
                time.sleep(0.4)

            except Exception as e:
                logger.error(f"  {symbol} error: {e}")
                continue

        # Sort by volume descending, keep top 50
        unusual_contracts.sort(key=lambda x: x["volume"], reverse=True)
        top_unusual = unusual_contracts[:TOP_N]

        # Summary
        market_pc = round(total_put_vol / max(total_call_vol, 1), 3)
        call_unusual = len([c for c in top_unusual if c["type"] == "call"])
        put_unusual = len([c for c in top_unusual if c["type"] == "put"])

        # Most active strikes
        strike_counts: Dict[str, int] = {}
        for c in top_unusual:
            key = f"{c['symbol']} {c['strike']} {c['type']}"
            strike_counts[key] = strike_counts.get(key, 0) + c["volume"]
        most_active = sorted(strike_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        if call_unusual > put_unusual * 1.5:
            smart_money = "bullish"
        elif put_unusual > call_unusual * 1.5:
            smart_money = "bearish"
        else:
            smart_money = "neutral"

        result = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scanner": "options_flow_scanner",
            "symbols_scanned": len(SCAN_SYMBOLS),
            "total_unusual_found": len(unusual_contracts),
            "top_unusual_shown": len(top_unusual),
            "summary": {
                "market_put_call_ratio": market_pc,
                "total_call_volume": total_call_vol,
                "total_put_volume": total_put_vol,
                "unusual_calls": call_unusual,
                "unusual_puts": put_unusual,
                "smart_money_direction": smart_money,
                "most_active_strikes": [{"strike": k, "volume": v} for k, v in most_active],
            },
            "symbol_stats": symbol_stats,
            "unusual_contracts": top_unusual,
        }

        self.output_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        logger.info(f"Wrote {len(top_unusual)} unusual contracts to {self.output_path}")
        return result


def main():
    scanner = OptionsFlowScanner()
    result = scanner.scan()
    print(json.dumps({
        "symbols_scanned": result.get("symbols_scanned", 0),
        "total_unusual_found": result.get("total_unusual_found", 0),
        "smart_money": result.get("summary", {}).get("smart_money_direction", "unknown"),
        "market_pc_ratio": result.get("summary", {}).get("market_put_call_ratio", 0),
    }, indent=2))


if __name__ == "__main__":
    main()
