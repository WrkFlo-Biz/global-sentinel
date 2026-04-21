#!/usr/bin/env python3
"""Circuit breaker for broker and external API calls."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from src.core.structured_logger import get_logger


class CircuitOpenError(Exception):
    """Raised when a call is blocked because the circuit is open."""


class CircuitBreaker:
    """Stateful circuit breaker with CLOSED, OPEN, and HALF_OPEN states."""

    def __init__(self, name: str = "default", failure_threshold: int = 3, recovery_timeout_seconds: float = 60.0, half_open_max_calls: int = 1):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds
        self.half_open_max_calls = half_open_max_calls
        self._state = "CLOSED"
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._half_open_calls = 0
        self._lock = threading.Lock()
        self._logger = get_logger("circuit_breaker")

    @property
    def state(self) -> str:
        with self._lock:
            if self._state == "OPEN" and self._last_failure_time is not None:
                if (time.monotonic() - self._last_failure_time) >= self.recovery_timeout_seconds:
                    self._state = "HALF_OPEN"
                    self._half_open_calls = 0
                    self._logger.info("circuit_half_open", circuit_name=self.name)
            return self._state

    def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        current_state = self.state
        if current_state == "OPEN":
            raise CircuitOpenError(f"Circuit {self.name} is OPEN. Recovery in {self._time_until_recovery():.0f}s")
        if current_state == "HALF_OPEN":
            with self._lock:
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitOpenError(f"Circuit {self.name} is HALF_OPEN. Max probe calls reached.")
                self._half_open_calls += 1
        try:
            result = func(*args, **kwargs)
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result

    def record_success(self) -> None:
        with self._lock:
            if self._state == "HALF_OPEN":
                self._logger.info("circuit_reset_after_probe", circuit_name=self.name)
            self._state = "CLOSED"
            self._failure_count = 0
            self._half_open_calls = 0
            self._last_failure_time = None

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == "HALF_OPEN" or self._failure_count >= self.failure_threshold:
                self._state = "OPEN"
                self._half_open_calls = 0
                self._logger.warning("circuit_opened", circuit_name=self.name, failure_count=self._failure_count)

    def trip(self) -> None:
        with self._lock:
            self._state = "OPEN"
            self._last_failure_time = time.monotonic()
            self._half_open_calls = 0

    def reset(self) -> None:
        with self._lock:
            self._state = "CLOSED"
            self._failure_count = 0
            self._half_open_calls = 0
            self._last_failure_time = None

    def _time_until_recovery(self) -> float:
        if self._last_failure_time is None:
            return 0.0
        return max(0.0, self.recovery_timeout_seconds - (time.monotonic() - self._last_failure_time))

    @property
    def stats(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout_seconds": self.recovery_timeout_seconds,
            "time_until_recovery_seconds": round(self._time_until_recovery(), 3),
        }
