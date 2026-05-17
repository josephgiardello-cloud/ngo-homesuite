from __future__ import annotations

from collections import defaultdict
from threading import RLock


class InMemoryMetrics:
    """Thread-safe lightweight metrics sink (Prometheus-style exposition)."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = defaultdict(float)
        self._hist_counts: dict[str, int] = defaultdict(int)
        self._hist_sums: dict[str, float] = defaultdict(float)

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None = None) -> str:
        labels = labels or {}
        if not labels:
            return name
        rendered = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{rendered}}}"

    def inc(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += float(value)

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._hist_counts[key] += 1
            self._hist_sums[key] += float(value)

    def set(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = float(value)

    def render_prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            for key, value in sorted(self._counters.items()):
                lines.append(f"{key} {value}")
            for key, value in sorted(self._gauges.items()):
                lines.append(f"{key} {value}")
            for key, count in sorted(self._hist_counts.items()):
                lines.append(f"{key}_count {count}")
                lines.append(f"{key}_sum {self._hist_sums[key]}")
        return "\n".join(lines) + "\n"
