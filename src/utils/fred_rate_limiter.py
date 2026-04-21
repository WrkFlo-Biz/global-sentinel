#!/usr/bin/env python3
"""
Global Sentinel — FRED API Rate Limiter

FRED API limit: 120 requests/minute.
This limiter uses a token bucket at 100 tokens/minute (20 headroom).
Thread-safe. Logs rate limit hits. Exposes bucket state to quantum_feed.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger("global_sentinel.fred_rate_limiter")

REPO = Path("/opt/global-sentinel")
RATE_LIMIT_LOG = REPO / "logs" / "rate_limits.jsonl"
RATE_LIMIT_STATE = REPO / "data" / "quantum_feed" / "rate_limit_state.json"


class FREDRateLimiter:
    """Token-bucket rate limiter for FRED API.

    - Bucket capacity: 100 tokens
    - Refill rate: 100 tokens per 60 seconds
    - Wait up to `wait_timeout` seconds for a token, then skip
    """

    def __init__(
        self,
        max_tokens: int = 100,
        refill_period: float = 60.0,
        wait_timeout: float = 5.0,
    ):
        self.max_tokens = max_tokens
        self.refill_period = refill_period
        self.wait_timeout = wait_timeout
        self.tokens = float(max_tokens)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()
        self._total_acquired = 0
        self._total_waited = 0
        self._total_rejected = 0

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        new_tokens = elapsed * (self.max_tokens / self.refill_period)
        self.tokens = min(self.max_tokens, self.tokens + new_tokens)
        self.last_refill = now

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """Acquire a token. Blocks up to `timeout` seconds.

        Returns True if token acquired, False if timed out.
        """
        if timeout is None:
            timeout = self.wait_timeout

        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    self._total_acquired += 1
                    return True

            if time.monotonic() >= deadline:
                with self._lock:
                    self._total_rejected += 1
                self._log_rate_limit_hit()
                return False

            # Wait briefly before retry
            with self._lock:
                self._total_waited += 1
            time.sleep(0.1)

    def get_state(self) -> dict:
        """Return current bucket state."""
        with self._lock:
            self._refill()
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "limiter": "fred_api",
                "max_tokens": self.max_tokens,
                "current_tokens": round(self.tokens, 2),
                "refill_period_seconds": self.refill_period,
                "tokens_per_second": round(self.max_tokens / self.refill_period, 3),
                "total_acquired": self._total_acquired,
                "total_waited": self._total_waited,
                "total_rejected": self._total_rejected,
                "utilization_pct": round(
                    (1 - self.tokens / self.max_tokens) * 100, 1
                ) if self.max_tokens > 0 else 0,
            }

    def save_state(self):
        """Write current state to quantum_feed for monitoring."""
        try:
            state = self.get_state()
            RATE_LIMIT_STATE.parent.mkdir(parents=True, exist_ok=True)
            RATE_LIMIT_STATE.write_text(json.dumps(state, indent=2))
        except Exception as e:
            logger.warning("Failed to save rate limit state: %s", e)

    def _log_rate_limit_hit(self):
        """Log a rate limit rejection."""
        try:
            RATE_LIMIT_LOG.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "limiter": "fred_api",
                "event": "rate_limit_hit",
                "tokens_remaining": round(self.tokens, 2),
                "total_rejected": self._total_rejected,
            }
            with open(RATE_LIMIT_LOG, "a") as f:
                f.write(json.dumps(entry) + "\n")
            logger.warning(
                "FRED rate limit hit: %d rejected total, %.1f tokens remaining",
                self._total_rejected, self.tokens,
            )
        except Exception as e:
            logger.warning("Failed to log rate limit hit: %s", e)


# Global singleton instance
_instance: Optional[FREDRateLimiter] = None
_instance_lock = threading.Lock()


def get_fred_limiter() -> FREDRateLimiter:
    """Get or create the global FRED rate limiter singleton."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = FREDRateLimiter()
    return _instance


def acquire_fred_token(timeout: float = 5.0) -> bool:
    """Convenience: acquire a FRED API token. Returns False if rate limited."""
    return get_fred_limiter().acquire(timeout=timeout)


def save_fred_state():
    """Convenience: save current FRED limiter state to disk."""
    get_fred_limiter().save_state()
