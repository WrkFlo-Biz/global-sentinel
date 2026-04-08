#!/usr/bin/env python3
"""
Tastytrade OAuth2 Authentication Helper for Global Sentinel
Handles the full auth flow including device challenge (2FA).

Usage:
  Step 1: python3 tastytrade_auth.py challenge   # Triggers OTP email
  Step 2: python3 tastytrade_auth.py login <OTP>  # Completes login, gets refresh token
  Step 3: python3 tastytrade_auth.py test          # Test the saved session
"""
import json, os, sys, datetime
import requests
from pathlib import Path

REPO_ROOT = Path(os.getenv("GLOBAL_SENTINEL_REPO_ROOT", "/opt/global-sentinel"))
SESSION_FILE = REPO_ROOT / ".tastytrade_session.json"
ENV_FILE = REPO_ROOT / ".env"
API_URL = "https://api.tastyworks.com"

def load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.strip().split("=", 1)
                env[k] = v
    return env

def save_session(data):
    SESSION_FILE.write_text(json.dumps(data, indent=2))
    print(f"Session saved to {SESSION_FILE}")

def load_session():
    if SESSION_FILE.exists():
        return json.loads(SESSION_FILE.read_text())
    return None

def step1_challenge():
    env = load_env()
    username = env.get("TASTYTRADE_USERNAME", os.getenv("TASTYTRADE_USERNAME", ""))
    password = env.get("TASTYTRADE_PASSWORD", os.getenv("TASTYTRADE_PASSWORD", ""))
    if not username or not password:
        print("ERROR: TASTYTRADE_USERNAME and TASTYTRADE_PASSWORD not set")
        return False

    print(f"Authenticating as {username}...")
    r = requests.post(f"{API_URL}/sessions", json={"login": username, "password": password},
                      headers={"Content-Type": "application/json"})

    if r.status_code == 201:
        data = r.json()["data"]
        print("Direct login succeeded (no device challenge)!")
        session_token = data["session-token"]
        remember_token = data.get("remember-token", "")
        save_session({"session_token": session_token, "remember_token": remember_token,
                       "username": username, "timestamp": datetime.datetime.now().isoformat(),
                       "auth_type": "direct"})
        _try_oauth(session_token, env)
        return True

    if r.status_code != 403:
        print(f"Unexpected status: {r.status_code}")
        print(r.text)
        return False

    challenge_token = r.headers.get("X-Tastyworks-Challenge-Token")
    if not challenge_token:
        print("ERROR: No challenge token in response")
        return False

    r2 = requests.post(f"{API_URL}/device-challenge", headers={
        "Content-Type": "application/json",
        "X-Tastyworks-Challenge-Token": challenge_token
    })

    if r2.status_code != 200:
        print(f"Device challenge failed: {r2.status_code}")
        print(r2.text)
        return False

    data = r2.json()["data"]
    print(f"OTP sent to email for device: {data.get('device', 'Unknown')}")
    print(f"Location: {data.get('location', 'Unknown')}")
    save_session({"challenge_token": challenge_token, "username": username,
                   "timestamp": datetime.datetime.now().isoformat(), "status": "awaiting_otp"})
    print(f"\nNow run: python3 {__file__} login <OTP_CODE>")
    return True

def step2_login(otp_code):
    env = load_env()
    session_data = load_session()
    if not session_data or session_data.get("status") != "awaiting_otp":
        print("ERROR: No pending challenge. Run 'challenge' first.")
        return False

    username = env.get("TASTYTRADE_USERNAME", os.getenv("TASTYTRADE_USERNAME", ""))
    password = env.get("TASTYTRADE_PASSWORD", os.getenv("TASTYTRADE_PASSWORD", ""))
    challenge_token = session_data["challenge_token"]

    r = requests.post(f"{API_URL}/sessions", json={
        "login": username, "password": password, "remember-me": True
    }, headers={
        "Content-Type": "application/json",
        "X-Tastyworks-Challenge-Token": challenge_token,
        "X-Tastyworks-OTP": str(otp_code)
    })

    if r.status_code != 201:
        print(f"Login failed: {r.status_code}")
        print(r.text)
        return False

    data = r.json()["data"]
    session_token = data["session-token"]
    remember_token = data.get("remember-token", "")
    print(f"Login successful! Token: {session_token[:20]}...")

    save_session({"session_token": session_token, "remember_token": remember_token,
                   "username": username, "timestamp": datetime.datetime.now().isoformat(),
                   "auth_type": "otp_verified"})
    _try_oauth(session_token, env)
    return True

def _try_oauth(session_token, env):
    client_id = env.get("TASTYTRADE_CLIENT_ID", os.getenv("TASTYTRADE_CLIENT_ID", ""))
    client_secret = env.get("TASTYTRADE_CLIENT_SECRET", os.getenv("TASTYTRADE_CLIENT_SECRET", ""))
    if not client_id or not client_secret:
        print("No OAuth client credentials. Using session token directly.")
        return

    print("Attempting OAuth token exchange...")
    r = requests.post(f"{API_URL}/oauth/authorize", json={
        "client_id": client_id, "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
        "response_type": "code", "scope": "openid"
    }, headers={"Content-Type": "application/json", "Authorization": f"Bearer {session_token}"},
        allow_redirects=False)

    print(f"OAuth authorize: {r.status_code}")
    if r.status_code in (200, 201):
        resp_data = r.json()
        auth_code = None
        if isinstance(resp_data, dict):
            auth_code = resp_data.get("data", {}).get("code") or resp_data.get("code")
        if auth_code:
            r2 = requests.post(f"{API_URL}/oauth/token", json={
                "grant_type": "authorization_code", "client_id": client_id,
                "client_secret": client_secret, "code": auth_code,
                "redirect_uri": "urn:ietf:wg:oauth:2.0:oob"
            }, headers={"Content-Type": "application/json"})
            if r2.status_code == 200:
                token_data = r2.json()
                sd = load_session()
                sd["refresh_token"] = token_data.get("refresh_token")
                sd["access_token"] = token_data.get("access_token")
                sd["token_type"] = "oauth2"
                save_session(sd)
                print("OAuth refresh token obtained and saved!")
                return
            print(f"Token exchange failed: {r2.status_code} {r2.text[:300]}")
        print(f"Response: {json.dumps(resp_data, indent=2)[:500]}")
    elif r.status_code == 302:
        location = r.headers.get("Location", "")
        if "code=" in location:
            auth_code = location.split("code=")[1].split("&")[0]
            print(f"Got auth code from redirect: {auth_code[:20]}...")
    else:
        print(f"OAuth not available ({r.status_code}). Using session token.")

    sd = load_session()
    sd["token_type"] = "session"
    save_session(sd)

def test_connection():
    session_data = load_session()
    if not session_data:
        print("ERROR: No saved session. Run 'challenge' and 'login' first.")
        return False

    token = session_data.get("access_token") or session_data.get("session_token")
    token_type = session_data.get("token_type", "session")
    if not token:
        print("ERROR: No token in saved session")
        return False

    print(f"Testing with {token_type} token: {token[:20]}...")

    r = requests.get(f"{API_URL}/customers/me", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    if r.status_code == 200:
        customer = r.json().get("data", {})
        print(f"Customer: {customer.get('first-name', '?')} {customer.get('last-name', '?')}")
        print(f"Email: {customer.get('email', '?')}")
    elif session_data.get("refresh_token"):
        print(f"Token expired ({r.status_code}), refreshing...")
        env = load_env()
        r2 = requests.post(f"{API_URL}/oauth/token", json={
            "grant_type": "refresh_token",
            "client_secret": env.get("TASTYTRADE_CLIENT_SECRET", ""),
            "refresh_token": session_data["refresh_token"]
        }, headers={"Content-Type": "application/json"})
        if r2.status_code == 200:
            new_data = r2.json()
            session_data["access_token"] = new_data["access_token"]
            save_session(session_data)
            token = new_data["access_token"]
            print("Token refreshed!")
        else:
            print(f"Refresh failed: {r2.status_code}")
            return False
    else:
        print(f"Auth failed: {r.status_code} {r.text[:300]}")
        return False

    r = requests.get(f"{API_URL}/customers/me/accounts", headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    if r.status_code == 200:
        accounts = r.json().get("data", {}).get("items", [])
        for acct in accounts:
            a = acct.get("account", {})
            num = a.get("account-number", "?")
            print(f"\nAccount: {num}")
            print(f"  Type: {a.get('account-type-name', '?')}")
            print(f"  Status: {a.get('status', {})}")

            r3 = requests.get(f"{API_URL}/accounts/{num}/balances", headers={
                "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
            if r3.status_code == 200:
                bal = r3.json().get("data", {})
                print(f"  Cash Balance: {bal.get('cash-balance', '?')}")
                print(f"  Buying Power: {bal.get('derivative-buying-power', bal.get('buying-power', '?'))}")
                print(f"  Net Liq: {bal.get('net-liquidating-value', '?')}")
    return True

def renew_session():
    session_data = load_session()
    if not session_data or not session_data.get("remember_token"):
        print("No remember token. Need to re-authenticate.")
        return False

    env = load_env()
    username = env.get("TASTYTRADE_USERNAME", "")
    r = requests.post(f"{API_URL}/sessions", json={
        "login": username, "remember-token": session_data["remember_token"]
    }, headers={"Content-Type": "application/json"})

    if r.status_code == 201:
        data = r.json()["data"]
        session_data["session_token"] = data["session-token"]
        session_data["remember_token"] = data.get("remember-token", session_data["remember_token"])
        session_data["timestamp"] = datetime.datetime.now().isoformat()
        save_session(session_data)
        print("Session renewed with remember token!")
        return True
    print(f"Renewal failed: {r.status_code} {r.text[:300]}")
    return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print(f"  python3 {__file__} challenge    # Trigger OTP email")
        print(f"  python3 {__file__} login <OTP>  # Complete login")
        print(f"  python3 {__file__} test          # Test session")
        print(f"  python3 {__file__} renew         # Renew with remember token")
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "challenge":
        step1_challenge()
    elif cmd == "login":
        if len(sys.argv) < 3:
            print("Usage: login <OTP_CODE>")
            sys.exit(1)
        step2_login(sys.argv[2])
    elif cmd == "test":
        test_connection()
    elif cmd == "renew":
        renew_session()
    else:
        print(f"Unknown: {cmd}")
