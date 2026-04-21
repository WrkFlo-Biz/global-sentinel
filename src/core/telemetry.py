#!/usr/bin/env python3
"""OpenTelemetry-aware telemetry helpers with JSON fallbacks.

The module intentionally degrades gracefully when OpenTelemetry is not
installed. In that case, spans and metrics are written to local JSONL sinks
under ``telemetry/`` so the rest of the system can still emit observability
data without additional dependencies.
"""
from __future__ import annotations

import contextlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

from src.core.structured_logger import TRACE_ID_CTX, SPAN_ID_CTX, _generate_id


TELEMETRY_DIR = Path("telemetry")
TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
TRACE_SINK = TELEMETRY_DIR / "traces.jsonl"
METRIC_SINK = TELEMETRY_DIR / "metrics.jsonl"


def _write_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, default=str) + "\n")


try:
    from opentelemetry import metrics as otel_metrics
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider

    _otel_available = True
    if otel_trace.get_tracer_provider().__class__.__name__ == "ProxyTracerProvider":
        otel_trace.set_tracer_provider(TracerProvider())
    if otel_metrics.get_meter_provider().__class__.__name__ == "NoOpMeterProvider":
        otel_metrics.set_meter_provider(MeterProvider())
    tracer = otel_trace.get_tracer("global-sentinel")
    meter = otel_metrics.get_meter("global-sentinel")
except Exception:  # pragma: no cover - fallback path is the default in local tests
    _otel_available = False
    tracer = None
    meter = None


def record_metric(name: str, value: float, **attributes: Any) -> None:
    """Record a metric sample to the local JSON sink."""
    _write_jsonl(
        METRIC_SINK,
        {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "name": name,
            "value": value,
            "attributes": attributes,
            "trace_id": TRACE_ID_CTX.get(),
            "span_id": SPAN_ID_CTX.get(),
        },
    )


@contextlib.contextmanager
def start_span(name: str, **attributes: Any) -> Iterator[Dict[str, Any]]:
    """Start a span-like context with OpenTelemetry or a JSON fallback."""
    trace_id = TRACE_ID_CTX.get() or _generate_id()
    span_id = _generate_id()
    TRACE_ID_CTX.set(trace_id)
    SPAN_ID_CTX.set(span_id)
    start = time.perf_counter()

    if _otel_available and tracer is not None:
        with tracer.start_as_current_span(name) as span:  # pragma: no cover - depends on optional dep
            for key, value in attributes.items():
                span.set_attribute(key, value)
            yield {"trace_id": trace_id, "span_id": span_id, "otel_span": span}
            return

    try:
        yield {"trace_id": trace_id, "span_id": span_id}
    finally:
        duration_ms = round((time.perf_counter() - start) * 1000.0, 3)
        _write_jsonl(
            TRACE_SINK,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "name": name,
                "trace_id": trace_id,
                "span_id": span_id,
                "duration_ms": duration_ms,
                "attributes": attributes,
            },
        )
