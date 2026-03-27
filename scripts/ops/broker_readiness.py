#!/usr/bin/env python3
"""
Global Sentinel — Multi-Broker Weekly Readiness Check

Runs weekly Sunday 6 PM ET. Checks API connectivity, auth, equity,
buying power, and day trades remaining for all configured brokers.
Sends a Telegram summary.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

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
logger = logging.getLogger("global_sentinel.broker_readiness")

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


class BrokerReadinessChecker:
    def __init__(self):
        self.repo_root = Path(os.environ.get("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
        self.output_path = self.repo_root / "data" / "quantum_feed" / "broker_readiness.json"
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "7091381625")

    def _send_telegram(self, text: str):
        if _send_topic:
            try:
                _send_topic(text[:4000], topic="system")
                return
            except Exception:
                pass
        if not self.bot_token:
            logger.warning("No TELEGRAM_BOT_TOKEN")
            return
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML", "message_thread_id": 74,
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
        except Exception as e:
            logger.warning(f"Telegram failed: {e}")

    def _check_alpaca(self, name: str, key_env: str, secret_env: str, base_url: str) -> Dict:
        """Check an Alpaca account endpoint."""
        api_key = os.environ.get(key_env, "")
        api_secret = os.environ.get(secret_env, "")

        if not api_key or not api_secret:
            return {
                "name": name,
                "status": "disconnected",
                "error": f"Missing env: {key_env}/{secret_env}",
                "equity": 0,
                "buying_power": 0,
                "day_trades_remaining": "N/A",
            }

        req = urllib.request.Request(f"{base_url}/v2/account")
        req.add_header("APCA-API-KEY-ID", api_key)
        req.add_header("APCA-API-SECRET-KEY", api_secret)

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            equity = round(float(data.get("equity", 0)), 2)
            buying_power = round(float(data.get("buying_power", 0)), 2)
            daytrade_count = int(data.get("daytrade_count", 0))
            pdt_check = data.get("pattern_day_trader", False)
            # 3 day trades allowed if not PDT, unlimited if equity > 25k
            if equity >= 25000 or pdt_check:
                dt_remaining = "unlimited"
            else:
                dt_remaining = max(0, 3 - daytrade_count)

            issues = []
            if equity < 100:
                issues.append("LOW_BALANCE")
            if not pdt_check and equity < 25000 and daytrade_count >= 3:
                issues.append("NO_DAY_TRADES")

            return {
                "name": name,
                "status": "connected",
                "equity": equity,
                "buying_power": buying_power,
                "day_trades_remaining": dt_remaining,
                "daytrade_count": daytrade_count,
                "issues": issues,
            }
        except Exception as e:
            return {
                "name": name,
                "status": "disconnected",
                "error": str(e),
                "equity": 0,
                "buying_power": 0,
                "day_trades_remaining": "N/A",
            }

    def _check_tastytrade(self) -> Dict:
        """Check Tastytrade API auth."""
        username = os.environ.get("TASTYTRADE_USERNAME", "")
        password = os.environ.get("TASTYTRADE_PASSWORD", "")
        remember_token = os.environ.get("TASTYTRADE_REMEMBER_TOKEN", "")

        if not username:
            return {
                "name": "Tastytrade",
                "status": "disconnected",
                "error": "No TASTYTRADE_USERNAME configured",
                "equity": 0,
                "buying_power": 0,
            }

        # Try session token first, then password auth
        auth_url = "https://api.tastyworks.com/sessions"

        # Try remember token
        if remember_token:
            try:
                payload = json.dumps({
                    "login": username,
                    "remember-token": remember_token,
                }).encode()
                req = urllib.request.Request(
                    auth_url, data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                if data.get("data", {}).get("session-token"):
                    session_token = data["data"]["session-token"]
                    return self._tastytrade_account_check(session_token)
            except Exception as e:
                logger.info(f"Tastytrade remember-token auth failed: {e}")

        # Try password
        if password:
            try:
                payload = json.dumps({
                    "login": username,
                    "password": password,
                }).encode()
                req = urllib.request.Request(
                    auth_url, data=payload,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read())
                if data.get("data", {}).get("session-token"):
                    session_token = data["data"]["session-token"]
                    return self._tastytrade_account_check(session_token)
            except Exception as e:
                return {
                    "name": "Tastytrade",
                    "status": "disconnected",
                    "error": f"Auth failed: {e}",
                    "equity": 0,
                    "buying_power": 0,
                    "issues": ["AUTH_EXPIRED"],
                }

        return {
            "name": "Tastytrade",
            "status": "disconnected",
            "error": "No password or remember-token",
            "equity": 0,
            "buying_power": 0,
            "issues": ["AUTH_EXPIRED"],
        }

    def _tastytrade_account_check(self, session_token: str) -> Dict:
        """Fetch Tastytrade account details with session token."""
        try:
            req = urllib.request.Request("https://api.tastyworks.com/customers/me/accounts")
            req.add_header("Authorization", session_token)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())

            accounts = data.get("data", {}).get("items", [])
            if not accounts:
                return {
                    "name": "Tastytrade",
                    "status": "connected",
                    "equity": 0,
                    "buying_power": 0,
                    "note": "No accounts found",
                }

            # Get first account balance
            acct_num = accounts[0].get("account", {}).get("account-number", "")
            req2 = urllib.request.Request(
                f"https://api.tastyworks.com/accounts/{acct_num}/balances"
            )
            req2.add_header("Authorization", session_token)
            with urllib.request.urlopen(req2, timeout=15) as resp2:
                bal = json.loads(resp2.read())

            bal_data = bal.get("data", {})
            equity = round(float(bal_data.get("net-liquidating-value", 0)), 2)
            bp = round(float(bal_data.get("derivative-buying-power", 0)), 2)

            issues = []
            if equity < 100:
                issues.append("LOW_BALANCE")

            return {
                "name": "Tastytrade",
                "status": "connected",
                "account": acct_num,
                "equity": equity,
                "buying_power": bp,
                "issues": issues,
            }
        except Exception as e:
            return {
                "name": "Tastytrade",
                "status": "disconnected",
                "error": f"Account fetch failed: {e}",
                "equity": 0,
                "buying_power": 0,
            }

    def _check_ibkr(self) -> Dict:
        """Check if IBKR client portal gateway is running."""
        # Check systemd service
        try:
            result = subprocess.run(
                ["systemctl", "is-active", "gs-ibkr-cpgw"],
                capture_output=True, text=True, timeout=5,
            )
            service_active = result.stdout.strip() == "active"
        except Exception:
            service_active = False

        # Check port 5000
        port_ok = False
        try:
            req = urllib.request.Request("https://localhost:5000/v1/api/iserver/auth/status")
            import ssl
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                data = json.loads(resp.read())
                port_ok = True
                authenticated = data.get("authenticated", False)
        except Exception:
            authenticated = False

        status = "connected" if (service_active and port_ok and authenticated) else "disconnected"
        issues = []
        if not service_active:
            issues.append("SERVICE_DOWN")
        if not port_ok:
            issues.append("PORT_5000_UNREACHABLE")
        if port_ok and not authenticated:
            issues.append("NOT_AUTHENTICATED")

        return {
            "name": "IBKR Gateway",
            "status": status,
            "service_active": service_active,
            "port_5000_ok": port_ok,
            "authenticated": authenticated,
            "equity": 0,
            "buying_power": 0,
            "issues": issues,
        }

    def run(self):
        logger.info("Broker Readiness Check starting...")
        now = datetime.now(timezone.utc)

        brokers = []

        # 1. Alpaca Live
        logger.info("Checking Alpaca Live...")
        brokers.append(self._check_alpaca(
            "Alpaca Live",
            "ALPACA_API_KEY_LIVE", "ALPACA_SECRET_KEY_LIVE",
            "https://api.alpaca.markets",
        ))

        # 2. Alpaca Paper Day Trade
        logger.info("Checking Alpaca Paper (Day Trade)...")
        brokers.append(self._check_alpaca(
            "Alpaca Paper DayTrade",
            "ALPACA_API_KEY", "ALPACA_SECRET_KEY",
            "https://paper-api.alpaca.markets",
        ))

        # 3. Alpaca Paper MedLong
        logger.info("Checking Alpaca Paper (MedLong)...")
        brokers.append(self._check_alpaca(
            "Alpaca Paper MedLong",
            "ALPACA_API_KEY_MEDLONG", "ALPACA_SECRET_KEY_MEDLONG",
            "https://paper-api.alpaca.markets",
        ))

        # 4. Tastytrade
        logger.info("Checking Tastytrade...")
        brokers.append(self._check_tastytrade())

        # 5. IBKR
        logger.info("Checking IBKR Gateway...")
        brokers.append(self._check_ibkr())

        # Build output
        output = {
            "timestamp": now.isoformat(),
            "checker": "broker_readiness",
            "brokers": brokers,
            "all_ok": all(b["status"] == "connected" for b in brokers),
        }

        with open(self.output_path, "w") as f:
            json.dump(output, f, indent=2)
        logger.info(f"Results written to {self.output_path}")

        # Telegram summary
        lines = ["<b>Weekly Broker Readiness Check</b>"]
        for b in brokers:
            icon = "\u2705" if b["status"] == "connected" else "\u274c"
            eq_str = f"${b['equity']:,.0f}" if b.get("equity") else "N/A"
            issues_str = ""
            if b.get("issues"):
                issues_str = f" ({', '.join(b['issues'])})"
            elif b.get("error"):
                issues_str = f" ({b['error'][:50]})"
            lines.append(f"{icon} <b>{b['name']}</b>: {b['status']} | {eq_str}{issues_str}")

        self._send_telegram("\n".join(lines))
        logger.info("Telegram summary sent.")
        logger.info("Broker Readiness Check complete.")


if __name__ == "__main__":
    checker = BrokerReadinessChecker()
    checker.run()
