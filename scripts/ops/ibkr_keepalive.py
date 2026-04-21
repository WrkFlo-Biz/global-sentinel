#!/usr/bin/env python3
"""Keep IBKR Client Portal session alive by tickling auth endpoint every 5 min."""
import urllib.request, json, time, ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

while True:
    try:
        # Tickle to keep session alive
        req = urllib.request.Request("https://localhost:5000/v1/api/tickle", method="POST")
        resp = json.loads(urllib.request.urlopen(req, timeout=5, context=ctx).read())
        
        # Check auth status
        req2 = urllib.request.Request("https://localhost:5000/v1/api/iserver/auth/status")
        status = json.loads(urllib.request.urlopen(req2, timeout=5, context=ctx).read())
        authenticated = status.get("authenticated", False)
        
        ts = time.strftime("%H:%M:%S")
        if authenticated:
            print(f"[{ts}] IBKR session alive — authenticated")
        else:
            print(f"[{ts}] IBKR session NOT authenticated — login required")
    except Exception as e:
        print(f"[{time.strftime('%H:%M:%S')}] Keepalive error: {e}")
    
    time.sleep(300)  # 5 min
