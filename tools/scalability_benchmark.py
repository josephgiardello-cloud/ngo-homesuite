from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def _request_once(url: str, timeout: float) -> tuple[int, float, str | None]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return int(response.status), elapsed_ms, None
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return int(exc.code), elapsed_ms, str(exc)
    except Exception as exc:  # pragma: no cover
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return 0, elapsed_ms, str(exc)


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run endpoint benchmark and emit JSON report")
    parser.add_argument("--base-url", required=True, help="Base URL, for example http://127.0.0.1:8765")
    parser.add_argument("--endpoint", action="append", dest="endpoints", default=[])
    parser.add_argument("--total-requests", type=int, default=300)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-overall-p95-ms", type=float, default=2200.0)
    parser.add_argument("--report-json", required=True)
    args = parser.parse_args()

    endpoints = args.endpoints or ["/health", "/", "/give", "/p2p/leaderboard"]
    base = args.base_url.rstrip("/")
    urls = [f"{base}{endpoints[i % len(endpoints)]}" for i in range(args.total_requests)]

    by_endpoint: dict[str, list[float]] = {ep: [] for ep in endpoints}
    statuses: list[int] = []
    failures: list[str] = []

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {
            pool.submit(_request_once, urls[i], args.timeout): endpoints[i % len(endpoints)]
            for i in range(len(urls))
        }
        for future in as_completed(futures):
            endpoint = futures[future]
            status, elapsed_ms, error = future.result()
            statuses.append(status)
            by_endpoint.setdefault(endpoint, []).append(elapsed_ms)
            if error is not None or status >= 500 or status == 0:
                failures.append(f"endpoint={endpoint} status={status} error={error}")
    duration_ms = (time.perf_counter() - started) * 1000.0

    overall_latencies = [item for values in by_endpoint.values() for item in values]
    overall = {
        "avg_ms": round(statistics.fmean(overall_latencies), 1) if overall_latencies else 0.0,
        "p95_ms": round(_percentile(overall_latencies, 95.0), 1),
        "max_ms": round(max(overall_latencies), 1) if overall_latencies else 0.0,
        "requests": len(statuses),
        "failures": len(failures),
        "duration_ms": round(duration_ms, 1),
    }

    endpoint_stats: dict[str, dict[str, float | int]] = {}
    for endpoint, latencies in by_endpoint.items():
        endpoint_stats[endpoint] = {
            "requests": len(latencies),
            "avg_ms": round(statistics.fmean(latencies), 1) if latencies else 0.0,
            "p95_ms": round(_percentile(latencies, 95.0), 1),
            "max_ms": round(max(latencies), 1) if latencies else 0.0,
        }

    report = {
        "base_url": args.base_url,
        "overall": overall,
        "endpoints": endpoint_stats,
        "sample_failure": failures[0] if failures else "",
    }

    with open(args.report_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(json.dumps(report, indent=2))

    if failures:
        return 2
    if overall["p95_ms"] > args.max_overall_p95_ms:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
