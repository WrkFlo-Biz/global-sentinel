#!/usr/bin/env python3
"""Structured JSON logging for Global Sentinel.

Every log entry is emitted as a JSON object and includes trace metadata so
execution, research, and bridge pipelines can be correlated without relying on
plain-text log parsing.
"""
from __future__ import annotations

import contextvars
import json
import logging
import uuid
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional


TRACE_ID_CTX: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("gs_trace_id", default=None)
SPAN_ID_CTX: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("gs_span_id", default=None)


def _generate_id() -> str:
    return uuid.uuid4().hex[:16]


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


class StructuredFormatter(logging.Formatter):
    """Format log records as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        context = dict(getattr(record, "structured_context", {}) or {})
        entry = {
            "timestamp": context.pop("timestamp", datetime.now(timezone.utc).isoformat()),
            "trace_id": context.pop("trace_id", TRACE_ID_CTX.get() or "no-trace"),
            "span_id": context.pop("span_id", SPAN_ID_CTX.get()),
            "packet_id": context.pop("packet_id", None),
            "module": context.pop("module", getattr(record, "module", record.name)),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        entry.update(_json_safe(context))
        return json.dumps(entry, default=str)


class StructuredLogger:
    """JSON structured logger with trace/span propagation."""

    def __init__(
        self,
        module_name: str,
        log_dir: Optional[Path] = None,
        *,
        level: int = logging.DEBUG,
        max_bytes: int = 5_000_000,
        backup_count: int = 5,
    ):
        self.module_name = module_name
        self._trace_id: Optional[str] = TRACE_ID_CTX.get()
        self._logger = logging.getLogger(f"gs.{module_name}")
        self._logger.setLevel(level)
        self._logger.propagate = False

        if not self._logger.handlers:
            stream = logging.StreamHandler()
            stream.setFormatter(StructuredFormatter())
            self._logger.addHandler(stream)

        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / f"{module_name}.jsonl"
            if not any(
                isinstance(handler, RotatingFileHandler) and Path(handler.baseFilename) == log_path
                for handler in self._logger.handlers
            ):
                file_handler = RotatingFileHandler(
                    log_path,
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
                file_handler.setFormatter(StructuredFormatter())
                self._logger.addHandler(file_handler)

    @property
    def trace_id(self) -> Optional[str]:
        return self._trace_id or TRACE_ID_CTX.get()

    def set_trace_id(self, trace_id: str) -> str:
        self._trace_id = trace_id
        TRACE_ID_CTX.set(trace_id)
        return trace_id

    def set_span_id(self, span_id: Optional[str]) -> Optional[str]:
        SPAN_ID_CTX.set(span_id)
        return span_id

    def new_trace(self) -> str:
        return self.set_trace_id(_generate_id())

    def new_span(self) -> str:
        span_id = _generate_id()
        SPAN_ID_CTX.set(span_id)
        return span_id

    def _emit(self, level: int, msg: str, **kwargs: Any) -> None:
        context = {
            "module": self.module_name,
            "trace_id": kwargs.pop("trace_id", self.trace_id or "no-trace"),
            "span_id": kwargs.pop("span_id", SPAN_ID_CTX.get()),
            "packet_id": kwargs.get("packet_id"),
        }
        context.update(kwargs)
        record = self._logger.makeRecord(
            name=self._logger.name,
            level=level,
            fn="",
            lno=0,
            msg=msg,
            args=(),
            exc_info=None,
        )
        record.structured_context = context
        self._logger.handle(record)

    def debug(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs: Any) -> None:
        self._emit(logging.CRITICAL, msg, **kwargs)


def get_logger(module_name: str, log_dir: Optional[Path] = None, **kwargs: Any) -> StructuredLogger:
    """Return a structured logger for ``module_name``."""
    return StructuredLogger(module_name, log_dir=log_dir, **kwargs)
