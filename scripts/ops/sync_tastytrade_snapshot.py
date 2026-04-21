#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import socket
import time
import sys
from pathlib import Path
from typing import Any, Dict, Optional
from contextlib import contextmanager

import requests

SCRIPT_ROOT = Path(__file__).resolve().parents[2]
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

from dashboard.api import server as dashboard_server

API_URL = "https://api.tastyworks.com"
VM_HOST = os.getenv("TASTYTRADE_SYNC_VM_HOST", "openclaw@20.124.180.8")
VM_ROOT = os.getenv("TASTYTRADE_SYNC_VM_ROOT", "/opt/global-sentinel")
REMOTE_PORTFOLIO_SNAPSHOT_PATH = f"{VM_ROOT}/data/broker_snapshots/portfolio.json"
REMOTE_SNAPSHOT_PATH = f"{VM_ROOT}/data/broker_snapshots/tastytrade.json"
REMOTE_SESSION_PATH = f"{VM_ROOT}/.tastytrade_session.json"
LOCAL_CACHE_DIR = Path(os.getenv("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "global-sentinel"
LOCAL_SESSION_PATH = LOCAL_CACHE_DIR / "tastytrade_session.json"
LOCAL_SNAPSHOT_PATH = LOCAL_CACHE_DIR / "tastytrade_snapshot.json"


def _load_remote_env() -> Dict[str, str]:
    raw = subprocess.check_output(["ssh", VM_HOST, f"cat {VM_ROOT}/.env"], text=True)
    env: Dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip()
    return env


@contextmanager
def _temporary_env(values: Dict[str, str]):
    original: Dict[str, Optional[str]] = {}
    try:
        for key, value in values.items():
            original[key] = os.environ.get(key)
            os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _auth_headers(token: str, token_type: str = "session") -> Dict[str, str]:
    auth_value = f"Bearer {token}" if token_type == "oauth2" else token
    return {
        "Authorization": auth_value,
        "Content-Type": "application/json",
        "User-Agent": "global-sentinel/1.0",
    }


def _request_json(url: str, *, headers: Dict[str, str], body: Optional[Dict[str, Any]] = None, method: str = "GET") -> Any:
    response = requests.request(method, url, headers=headers, json=body, timeout=20)
    response.raise_for_status()
    if not response.text:
        return {}
    return response.json()


def _login(username: str, password: str, otp: Optional[str]) -> Dict[str, Any]:
    response = requests.post(
        f"{API_URL}/sessions",
        json={"login": username, "password": password, "remember-me": True},
        headers={"Content-Type": "application/json", "User-Agent": "global-sentinel/1.0"},
        timeout=20,
    )
    if response.status_code in (200, 201):
        payload = response.json().get("data", {})
        return {
            "session_token": payload.get("session-token") or payload.get("session_token") or "",
            "remember_token": payload.get("remember-token") or payload.get("remember_token") or "",
            "status_code": response.status_code,
            "token_type": "session",
        }

    if response.status_code != 403:
        raise RuntimeError(f"TastyTrade login failed with status {response.status_code}")

    challenge_token = response.headers.get("X-Tastyworks-Challenge-Token")
    if not challenge_token:
        raise RuntimeError("TastyTrade login requires 2FA but no challenge token was returned")

    challenge_response = requests.post(
        f"{API_URL}/device-challenge",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "global-sentinel/1.0",
            "X-Tastyworks-Challenge-Token": challenge_token,
        },
        timeout=20,
    )
    challenge_response.raise_for_status()

    if not otp:
        otp = input("TastyTrade OTP: ").strip()

    verify_response = requests.post(
        f"{API_URL}/sessions",
        json={"login": username, "password": password, "remember-me": True},
        headers={
            "Content-Type": "application/json",
            "User-Agent": "global-sentinel/1.0",
            "X-Tastyworks-Challenge-Token": challenge_token,
            "X-Tastyworks-OTP": otp,
        },
        timeout=20,
    )
    verify_response.raise_for_status()
    payload = verify_response.json().get("data", {})
    return {
        "session_token": payload.get("session-token") or payload.get("session_token") or "",
        "remember_token": payload.get("remember-token") or payload.get("remember_token") or "",
        "status_code": verify_response.status_code,
        "token_type": "session",
    }


def _try_oauth(session_token: str, env: Dict[str, str]) -> Dict[str, Any]:
    client_id = env.get("TASTYTRADE_CLIENT_ID", "")
    client_secret = env.get("TASTYTRADE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return {}

    authorize_response = requests.post(
        f"{API_URL}/oauth/authorize",
        json={
            "client_id": client_id,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
            "response_type": "code",
            "scope": "openid",
        },
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {session_token}", "User-Agent": "global-sentinel/1.0"},
        allow_redirects=False,
        timeout=20,
    )
    if authorize_response.status_code not in (200, 201, 302):
        return {}

    auth_code = None
    if authorize_response.status_code in (200, 201):
        payload = authorize_response.json()
        if isinstance(payload, dict):
            auth_code = payload.get("data", {}).get("code") or payload.get("code")
    else:
        location = authorize_response.headers.get("Location", "")
        if "code=" in location:
            auth_code = location.split("code=")[1].split("&")[0]

    if not auth_code:
        return {}

    token_response = requests.post(
        f"{API_URL}/oauth/token",
        json={
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": auth_code,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
        },
        headers={"Content-Type": "application/json", "User-Agent": "global-sentinel/1.0"},
        timeout=20,
    )
    token_response.raise_for_status()
    payload = token_response.json()
    return {
        "token_type": "oauth2",
        "access_token": payload.get("access_token") or "",
        "refresh_token": payload.get("refresh_token") or "",
    }


def _balance_value(balances: Dict[str, Any], *keys: str) -> float:
    return dashboard_server._safe_float(dashboard_server._first_present(*(balances.get(key) for key in keys)))


def _account_snapshot(account: Dict[str, Any], balances: Dict[str, Any], positions: list[Dict[str, Any]]) -> Dict[str, Any]:
    equity = _balance_value(
        balances,
        "net-liquidating-value",
        "net_liquidating_value",
        "liquidation-value",
        "liquidation_value",
        "equity",
    )
    cash = _balance_value(
        balances,
        "cash-balance",
        "cash_balance",
        "cash-available-to-withdraw",
        "cash_available_to_withdraw",
        "settled-cash",
        "settled_cash",
    )
    buying_power = _balance_value(
        balances,
        "derivative-buying-power",
        "derivative_buying_power",
        "equity-buying-power",
        "equity_buying_power",
        "buying-power",
        "buying_power",
        "day-trade-excess",
        "day_trade_excess",
    ) or equity
    payload = {
        "label": f"tastytrade_{account['account-number']}",
        "broker": "tastytrade",
        "display_label": f"TastyTrade {account['account-number']}",
        "account_number": account["account-number"],
        "equity": equity,
        "cash": cash,
        "buying_power": buying_power,
        "portfolio_value": equity or dashboard_server._safe_float(sum(position.get("market_value", 0.0) for position in positions)),
        "positions": positions,
        "position_count": len(positions),
        "status": "ok",
        "timestamp_utc": dashboard_server._utc_now_iso(),
    }
    return payload


def _fetch_accounts(token: str, token_type: str) -> list[Dict[str, Any]]:
    accounts_payload = _request_json(
        f"{API_URL}/customers/me/accounts",
        headers=_auth_headers(token, token_type),
    )
    items = accounts_payload.get("data", {}).get("items", [])
    accounts: list[Dict[str, Any]] = []
    for item in items:
        account = item.get("account", {}) if isinstance(item, dict) else {}
        if not isinstance(account, dict):
            continue
        account_number = str(account.get("account-number") or "").strip()
        if not account_number:
            continue
        balances_payload = _request_json(
            f"{API_URL}/accounts/{account_number}/balances",
            headers=_auth_headers(token, token_type),
        )
        positions_payload = _request_json(
            f"{API_URL}/accounts/{account_number}/positions",
            headers=_auth_headers(token, token_type),
        )
        balances = dashboard_server._payload_object(balances_payload)
        positions = dashboard_server._parse_tastytrade_positions(positions_payload)
        accounts.append(_account_snapshot(account, balances, positions))
    return accounts


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _push_to_vm(local_path: Path, remote_path: str) -> None:
    remote_dir = str(Path(remote_path).parent)
    content = local_path.read_bytes()
    subprocess.run(
        ["ssh", VM_HOST, f"mkdir -p {remote_dir} && cat > {remote_path}"],
        input=content,
        check=True,
    )


def _load_json_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_remote_json_file(remote_path: str) -> Dict[str, Any]:
    try:
        raw = subprocess.check_output(["ssh", VM_HOST, f"cat {remote_path}"], text=True)
    except Exception:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_cached_tastytrade_session() -> Dict[str, Any]:
    session = _load_json_file(LOCAL_SESSION_PATH)
    if session.get("session_token") or session.get("access_token"):
        return session
    return _load_remote_json_file(REMOTE_SESSION_PATH)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_ibkr_tunnel(local_port: int) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [
            "ssh",
            "-N",
            "-L",
            f"{local_port}:127.0.0.1:5000",
            VM_HOST,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )


def _fetch_alpaca_accounts(remote_env: Dict[str, str]) -> list[Dict[str, Any]]:
    overrides = {
        key: value
        for key, value in remote_env.items()
        if key.startswith("ALPACA_")
    }
    with _temporary_env(overrides):
        return [dashboard_server._fetch_alpaca_account(acct) for acct in dashboard_server._get_alpaca_accounts()]


def _fetch_ibkr_accounts(remote_env: Dict[str, str]) -> list[Dict[str, Any]]:
    local_port = _find_free_port()
    tunnel = _start_ibkr_tunnel(local_port)
    time.sleep(2.0)
    overrides = {
        key: value
        for key, value in remote_env.items()
        if key.startswith("IBKR_")
    }
    overrides["IBKR_CLIENT_PORTAL_BASE_URL"] = f"https://127.0.0.1:{local_port}/v1/api"
    try:
        with _temporary_env(overrides):
            return [dashboard_server._fetch_ibkr_account(acct) for acct in dashboard_server._get_ibkr_accounts()]
    finally:
        tunnel.terminate()
        try:
            tunnel.wait(timeout=5)
        except Exception:
            tunnel.kill()


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch broker live data locally and sync a portfolio snapshot to the Azure VM.")
    parser.add_argument("--otp", help="Current 2FA code for the TastyTrade login challenge.")
    parser.add_argument("--skip-vm-session", action="store_true", help="Do not upload the refreshed TastyTrade session cache to the VM.")
    args = parser.parse_args()

    env = _load_remote_env()
    broker_accounts: list[Dict[str, Any]] = []
    broker_errors: list[Dict[str, str]] = []

    try:
        broker_accounts.extend(_fetch_alpaca_accounts(env))
    except Exception as exc:
        broker_errors.append({"broker": "alpaca", "error": str(exc)})

    try:
        broker_accounts.extend(_fetch_ibkr_accounts(env))
    except Exception as exc:
        broker_errors.append({"broker": "ibkr", "error": str(exc)})

    username = env.get("TASTYTRADE_USERNAME", "")
    password = env.get("TASTYTRADE_PASSWORD", "")
    if not username or not password:
        raise RuntimeError("TastyTrade credentials are not configured on the VM")

    cached_session = _load_cached_tastytrade_session()
    login_data: Dict[str, Any] = {}
    session_token = ""
    token_type = "session"
    tastytrade_accounts: list[Dict[str, Any]] = []

    cached_token = str(
        dashboard_server._first_present(
            cached_session.get("session_token"),
            cached_session.get("session-token"),
            cached_session.get("access_token"),
        )
        or ""
    ).strip()
    if cached_token:
        cached_token_type = str(
            dashboard_server._first_present(
                cached_session.get("token_type"),
                cached_session.get("auth_type"),
            )
            or "session"
        ).strip().lower() or "session"
        try:
            tastytrade_accounts = _fetch_accounts(cached_token, cached_token_type)
            session_token = cached_token
            token_type = cached_token_type
            login_data = {
                "session_token": str(cached_session.get("session_token") or cached_token),
                "remember_token": str(cached_session.get("remember_token") or ""),
                "token_type": cached_token_type,
                "status_code": 200,
            }
        except Exception:
            tastytrade_accounts = []

    if not tastytrade_accounts:
        login_data = _login(username, password, args.otp)
        session_token = login_data["session_token"]
        token_type = login_data["token_type"]
        tastytrade_accounts = _fetch_accounts(session_token, token_type)

    if token_type != "oauth2":
        oauth_data = _try_oauth(session_token, env)
        if oauth_data.get("token_type") == "oauth2" and oauth_data.get("access_token"):
            token_type = "oauth2"
            session_token = oauth_data["access_token"]
        else:
            oauth_data = {}
    else:
        oauth_data = {
            "token_type": "oauth2",
            "access_token": session_token,
            "refresh_token": str(cached_session.get("refresh_token") or ""),
        }

    broker_accounts.extend(tastytrade_accounts)
    snapshot = {
        "generated_at_utc": dashboard_server._utc_now_iso(),
        "source": "local_broker_sync",
        "accounts": broker_accounts,
        "broker_errors": broker_errors,
    }
    _write_json(LOCAL_SNAPSHOT_PATH, snapshot)
    _push_to_vm(LOCAL_SNAPSHOT_PATH, REMOTE_PORTFOLIO_SNAPSHOT_PATH)
    _push_to_vm(LOCAL_SNAPSHOT_PATH, REMOTE_SNAPSHOT_PATH)

    session_cache = {
        "username": username,
        "session_token": session_token,
        "remember_token": login_data.get("remember_token", ""),
        "token_type": token_type,
        "access_token": oauth_data.get("access_token", ""),
        "refresh_token": oauth_data.get("refresh_token", ""),
        "timestamp": dashboard_server._utc_now_iso(),
    }
    LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(LOCAL_SESSION_PATH, session_cache)
    if not args.skip_vm_session:
        _push_to_vm(LOCAL_SESSION_PATH, REMOTE_SESSION_PATH)

    print(
        json.dumps(
            {
                "account_count": len(broker_accounts),
                "snapshot": str(LOCAL_SNAPSHOT_PATH),
                "remote_snapshot": REMOTE_PORTFOLIO_SNAPSHOT_PATH,
                "broker_errors": broker_errors,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
