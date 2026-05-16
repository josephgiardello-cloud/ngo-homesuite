from __future__ import annotations

import argparse
import statistics
import sys
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
    parser = argparse.ArgumentParser(description="Run a lightweight HTTP load smoke against key endpoints")
    parser.add_argument("--base-url", required=True, help="Base URL, for example http://127.0.0.1:8765")
    parser.add_argument(
        "--endpoint",
        action="append",
        dest="endpoints",
        default=[],
        help="Endpoint path to exercise; may be passed multiple times",
    )
    parser.add_argument("--total-requests", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--max-p95-ms", type=float, default=1500.0)
    args = parser.parse_args()

    if not args.endpoints:
        args.endpoints = ["/health", "/", "/give"]
    if args.total_requests <= 0 or args.concurrency <= 0:
        raise SystemExit("--total-requests and --concurrency must be > 0")

    urls = []
    base = args.base_url.rstrip("/")
    for index in range(args.total_requests):
        endpoint = args.endpoints[index % len(args.endpoints)]
        urls.append(f"{base}{endpoint}")

    statuses: list[int] = []
    latencies: list[float] = []
    failures: list[str] = []

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(_request_once, url, args.timeout) for url in urls]
        for future in as_completed(futures):
            status, elapsed_ms, error = future.result()
            statuses.append(status)
            latencies.append(elapsed_ms)
            if error is not None or status >= 500 or status == 0:
                failures.append(f"status={status} error={error}")

    p95 = _percentile(latencies, 95.0)
    avg = statistics.fmean(latencies) if latencies else 0.0
    print(f"requests={len(statuses)}")
    print(f"avg_ms={avg:.1f}")
    print(f"p95_ms={p95:.1f}")
    print(f"failures={len(failures)}")

    if failures:
        print("Sample failure:", failures[0], file=sys.stderr)
        return 2
    if p95 > args.max_p95_ms:
        print(
            f"Latency budget exceeded: p95 {p95:.1f}ms > {args.max_p95_ms:.1f}ms",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())