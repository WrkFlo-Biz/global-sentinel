#!/usr/bin/env python3
"""Cross-process file-based rate limiter using fcntl.flock()."""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger("global_sentinel.file_rate_limiter")
DATA_DIR = Path("/opt/global-sentinel/data")


class FileBasedRateLimiter:
    """Cross-process token bucket using a shared JSON file with file locking."""

    def __init__(self, api_key: str, max_rpm: int = 150, refill_period: float = 60.0):
        key_hash = hashlib.sha256(api_key.encode()).hexdigest()[:12]
        self._path = DATA_DIR / f".alpaca_rate_limit_{key_hash}.json"
        self.max_tokens = max_rpm
        self.refill_period = refill_period
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    def acquire(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with open(self._path, "a+") as f:
                    fcntl.flock(f, fcntl.LOCK_EX)
                    try:
                        f.seek(0)
                        raw = f.read().strip()
                        state = json.loads(raw) if raw else {}
                    except (json.JSONDecodeError, ValueError):
                        state = {}
                    now = time.time()
                    last_refill = state.get("last_refill", now)
                    tokens = state.get("tokens", float(self.max_tokens))
                    elapsed = now - last_refill
                    new_tokens = elapsed * (self.max_tokens / self.refill_period)
                    tokens = min(self.max_tokens, tokens + new_tokens)
                    if tokens >= 1.0:
                        tokens -= 1.0
                        state = {"last_refill": now, "tokens": tokens}
                        f.seek(0); f.truncate()
                        f.write(json.dumps(state)); f.flush()
                        fcntl.flock(f, fcntl.LOCK_UN)
                        return True
                    state = {"last_refill": now, "tokens": tokens}
                    f.seek(0); f.truncate()
                    f.write(json.dumps(state)); f.flush()
                    fcntl.flock(f, fcntl.LOCK_UN)
            except OSError:
                return True  # Fail open
            time.sleep(0.5)
        return False
