from __future__ import annotations

from typing import Any

try:
    from prometheus_client import Counter, Histogram, generate_latest, REGISTRY
except Exception:  # pragma: no cover - optional dependency fallback
    Counter = None  # type: ignore[assignment]
    Histogram = None  # type: ignore[assignment]
    generate_latest = None  # type: ignore[assignment]
    REGISTRY = None  # type: ignore[assignment]


donation_counter: Any
request_latency: Any
error_counter: Any

if Counter is not None and Histogram is not None:
    donation_counter = Counter("donations_total", "Total donations")
    request_latency = Histogram("http_request_duration_seconds", "HTTP request latency in seconds")
    error_counter = Counter("errors_total", "Total application errors", ["error_type"])
else:  # pragma: no cover - optional dependency fallback
    donation_counter = None
    request_latency = None
    error_counter = None


def inc_donations(value: float = 1.0) -> None:
    if donation_counter is None:
        return
    donation_counter.inc(float(value))


def observe_request_latency(seconds: float) -> None:
    if request_latency is None:
        return
    request_latency.observe(float(seconds))


def inc_error(error_type: str) -> None:
    if error_counter is None:
        return
    error_counter.labels(error_type=str(error_type)).inc(1.0)


def render_latest_metrics() -> bytes | None:
    if generate_latest is None or REGISTRY is None:
        return None
    return generate_latest(REGISTRY)
