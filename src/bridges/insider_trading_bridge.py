#!/usr/bin/env python3
"""
Global Sentinel — SEC EDGAR Form 4 Insider Trading Bridge

Scans top tradeable symbols for recent Form 4 insider trades using edgartools.
Returns insider buy/sell signals with dollar amounts.
FREE data source — SEC EDGAR (10 req/sec rate limit).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("global_sentinel.insider_trading_bridge")

try:
    from edgar import Company, set_identity
    EDGAR_AVAILABLE = True
except ImportError:
    EDGAR_AVAILABLE = False
    logger.warning("[InsiderTradingBridge] edgartools not installed")

try:
    import yaml
except ImportError:
    yaml = None


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None or not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


class InsiderTradingBridge:
    """Fetch SEC EDGAR Form 4 insider trading data for watchlist symbols."""

    DISPLAY_NAME = "insider_trading"
    CATEGORY = "fundamental_intelligence"

    # Top tradeable symbols for insider scanning
    DEFAULT_SYMBOLS = [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "JPM",
        "V", "JNJ", "XOM", "CVX", "LMT", "RTX", "BA", "CAT",
        "GS", "MS", "UNH", "PFE",
    ]

    def __init__(self, repo_root: str = "/opt/global-sentinel"):
        self.repo_root = Path(repo_root)
        if EDGAR_AVAILABLE:
            set_identity("Global Sentinel globalsentinel@wrkflo.biz")
        # Load symbols from watchlist or use defaults
        self.symbols = self._load_symbols()
        self._last_poll: Optional[datetime] = None

    def _load_symbols(self) -> List[str]:
        """Load equity symbols from watchlist, filtering to tradeable stocks."""
        wl = _load_yaml(self.repo_root / "config" / "assets_watchlist.yaml")
        symbols = []
        skip_patterns = ["USD", "XAU", "UST", "VIX", "BRN", "BZ=F", "^", "GC=F",
                         "USD/", "SPY", "QQQ", "IWM", "DIA", "XL", "EEM", "EFA",
                         "FXI", "GLD", "TLT", "VTI", "VOO", "MDY", "VTWO"]
        for section in ["equity_indices", "aviation_travel", "travel_hospitality",
                        "supply_chain", "insurance_risk"]:
            for item in wl.get(section, []):
                if not isinstance(item, dict):
                    continue
                sym = str(item.get("symbol", "")).strip()
                if not sym or any(p in sym for p in skip_patterns):
                    continue
                symbols.append(sym)
        # Deduplicate and limit
        seen = set()
        out = []
        for s in (symbols if symbols else self.DEFAULT_SYMBOLS):
            if s not in seen:
                seen.add(s)
                out.append(s)
        return out[:20]

    def poll(self) -> Dict[str, Any]:
        """Poll SEC EDGAR for Form 4 insider trades."""
        if not EDGAR_AVAILABLE:
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "bridge": "insider_trading",
                "error": "edgartools_not_installed",
                "signals": [],
            }

        signals = []
        errors = []
        buy_total = 0.0
        sell_total = 0.0

        for symbol in self.symbols:
            try:
                result = self._scan_insider_trades(symbol)
                if result:
                    signals.extend(result["trades"])
                    buy_total += result["buy_total"]
                    sell_total += result["sell_total"]
                # SEC EDGAR rate limit: 10 req/sec
                time.sleep(0.12)
            except Exception as e:
                errors.append({"symbol": symbol, "error": str(e)})
                logger.warning(f"[InsiderTradingBridge] {symbol} error: {e}")

        # Calculate aggregate insider sentiment
        net_insider = buy_total - sell_total
        sentiment = "bullish" if net_insider > 0 else "bearish" if net_insider < 0 else "neutral"

        self._last_poll = datetime.now(timezone.utc)
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bridge": "insider_trading",
            "source": "sec_edgar_form4",
            "symbols_scanned": len(self.symbols),
            "total_signals": len(signals),
            "aggregate_buy_value": round(buy_total, 2),
            "aggregate_sell_value": round(sell_total, 2),
            "net_insider_value": round(net_insider, 2),
            "insider_sentiment": sentiment,
            "signals": signals[:50],  # Cap output
            "errors": errors,
        }

    def _scan_insider_trades(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Scan a single symbol for recent Form 4 filings using edgartools Form4 object."""
        try:
            company = Company(symbol)
            filings = company.get_filings(form="4")
            if filings is None or len(filings) == 0:
                return None

            trades = []
            buy_total = 0.0
            sell_total = 0.0
            cutoff = datetime.now(timezone.utc) - timedelta(days=14)

            # Process recent filings (up to 10)
            for i, filing in enumerate(filings[:10]):
                try:
                    filed_date = getattr(filing, "filing_date", None)
                    if filed_date and hasattr(filed_date, "year"):
                        filed_dt = datetime(filed_date.year, filed_date.month, filed_date.day, tzinfo=timezone.utc)
                        if filed_dt < cutoff:
                            continue

                    accession = str(getattr(filing, "accession_no", getattr(filing, "accession_number", "")))

                    # Parse the Form 4 data object for real insider info
                    filer = "Unknown"
                    position = ""
                    try:
                        form4 = filing.obj()
                        filer = getattr(form4, "insider_name", "Unknown") or "Unknown"
                        position = getattr(form4, "position", "") or ""

                        # Extract transaction activities
                        activities = form4.get_transaction_activities()
                        for act in activities:
                            tx_type = getattr(act, "transaction_type", "")
                            shares = getattr(act, "shares", 0) or 0
                            value = getattr(act, "value", 0) or 0
                            price = getattr(act, "price_per_share", None)
                            code = getattr(act, "code", "")

                            # Classify: P=Purchase, S=Sale, M=Exercise, F=Tax
                            if code == "P":
                                tx_label = "purchase"
                                buy_total += float(value) if value else 0
                            elif code == "S":
                                tx_label = "sale"
                                sell_total += float(value) if value else 0
                            elif code == "M":
                                tx_label = "exercise"
                            elif code == "F":
                                tx_label = "tax_withholding"
                                sell_total += float(value) if value else 0
                            else:
                                tx_label = tx_type or "other"

                            trade_info = {
                                "symbol": symbol,
                                "filer": str(filer),
                                "position": str(position),
                                "filed_date": str(filed_date) if filed_date else None,
                                "accession": accession,
                                "form": "4",
                                "transaction_type": tx_label,
                                "transaction_code": code,
                                "shares": int(shares) if shares else 0,
                                "value": round(float(value), 2) if value else 0,
                                "price_per_share": float(price) if price and str(price).replace(".", "").isdigit() else None,
                                "url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={symbol}&type=4",
                            }
                            trades.append(trade_info)
                    except Exception as parse_err:
                        # Fallback: at least record the filing with basic info
                        logger.debug(f"[InsiderTradingBridge] {symbol} Form4 parse: {parse_err}")
                        trades.append({
                            "symbol": symbol,
                            "filer": filer,
                            "filed_date": str(filed_date) if filed_date else None,
                            "accession": accession,
                            "form": "4",
                            "transaction_type": "parse_error",
                            "shares": 0,
                            "value": 0,
                        })

                    time.sleep(0.12)  # SEC EDGAR rate limit
                except Exception:
                    continue

            return {
                "trades": trades,
                "buy_total": buy_total,
                "sell_total": sell_total,
            }
        except Exception as e:
            logger.debug(f"[InsiderTradingBridge] {symbol} scan error: {e}")
            return None
