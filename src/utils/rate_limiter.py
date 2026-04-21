#!/usr/bin/env python3
"""
Global Sentinel — Alpaca API Rate Limiter & Retry Utility

Alpaca enforces 200 requests/minute per account. This module provides:
1. Token-bucket rate limiter (per API key) to stay under limits
2. Exponential backoff retry wrapper for transient errors (429, 5xx)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("global_sentinel.rate_limiter")

# ---------------------------------------------------------------------------
# Token-bucket rate limiter (thread-safe, per-key)
# ---------------------------------------------------------------------------

class TokenBucketRateLimiter:
    """Token-bucket rate limiter. Default: 180 req/min (conservative vs 200 limit)."""

    def __init__(self, max_tokens: int = 180, refill_period: float = 60.0):
        self.max_tokens = max_tokens
        self.refill_period = refill_period
        self.tokens = float(max_tokens)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float = 30.0) -> bool:
        """Block until a token is available or timeout expires. Returns True if acquired."""
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True
            # Wait a bit before retrying
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self.last_refill
        new_tokens = elapsed * (self.max_tokens / self.refill_period)
        self.tokens = min(self.max_tokens, self.tokens + new_tokens)
        self.last_refill = now


# Global registry of rate limiters keyed by API key
_limiters: Dict[str, TokenBucketRateLimiter] = {}
_registry_lock = threading.Lock()


def get_limiter(api_key: str, max_rpm: int = 180) -> TokenBucketRateLimiter:
    """Get or create a rate limiter for the given API key."""
    with _registry_lock:
        if api_key not in _limiters:
            _limiters[api_key] = TokenBucketRateLimiter(max_tokens=max_rpm, refill_period=60.0)
        return _limiters[api_key]


# ---------------------------------------------------------------------------
# Exponential backoff retry
# ---------------------------------------------------------------------------

def retry_with_backoff(
    fn: Callable[..., Any],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retryable_status_codes: tuple = (429, 500, 502, 503, 504),
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
) -> Any:
    """
    Execute fn() with exponential backoff on retryable errors.

    fn should raise an exception with an `http_status` or `payload` attribute
    (like BrokerAdapterError) for status-code-based retry decisions.
    For urllib errors, checks the `code` attribute.
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except Exception as exc:
            last_exc = exc
            # Determine if retryable
            status = _extract_status(exc)
            is_retryable = (
                status in retryable_status_codes
                or getattr(exc, "retryable", False)
                or (hasattr(exc, "payload") and isinstance(exc.payload, dict) and exc.payload.get("retryable"))
            )

            if not is_retryable or attempt >= max_retries:
                raise

            delay = min(base_delay * (2 ** attempt), max_delay)
            # Check for Retry-After header hint
            retry_after = _extract_retry_after(exc)
            if retry_after and retry_after > 0:
                delay = min(retry_after, max_delay)

            if on_retry:
                on_retry(attempt + 1, exc, delay)
            else:
                logger.warning(
                    "Alpaca API retry %d/%d after %s (status=%s, delay=%.1fs)",
                    attempt + 1, max_retries, type(exc).__name__, status, delay,
                )

            time.sleep(delay)

    raise last_exc  # Should never reach here


def _extract_status(exc: Exception) -> Optional[int]:
    """Extract HTTP status code from various exception types."""
    # BrokerAdapterError
    if hasattr(exc, "payload") and isinstance(exc.payload, dict):
        return exc.payload.get("http_status")
    # urllib.error.HTTPError
    if hasattr(exc, "code"):
        return exc.code
    # requests.exceptions.HTTPError
    if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        return exc.response.status_code
    return None


def _extract_retry_after(exc: Exception) -> Optional[float]:
    """Try to extract Retry-After header value from exception."""
    try:
        # urllib.error.HTTPError
        if hasattr(exc, "headers"):
            val = exc.headers.get("Retry-After")
            if val:
                return float(val)
        # requests response
        if hasattr(exc, "response") and hasattr(exc.response, "headers"):
            val = exc.response.headers.get("Retry-After")
            if val:
                return float(val)
    except (ValueError, TypeError, AttributeError):
        pass
    return None
