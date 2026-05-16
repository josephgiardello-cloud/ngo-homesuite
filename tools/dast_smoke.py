from __future__ import annotations

import argparse
import json
import random
import string
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def _http_request(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 5.0,
) -> tuple[int, dict[str, str], str]:
    req = urllib.request.Request(url=url, data=data, method=method)
    for key, value in (headers or {}).items():
        req.add_header(key, value)

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = int(response.status)
            response_headers = {k: v for k, v in response.headers.items()}
            body = response.read().decode("utf-8", errors="ignore")
            return status, response_headers, body
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        response_headers = {k: v for k, v in exc.headers.items()}
        body = exc.read().decode("utf-8", errors="ignore")
        return status, response_headers, body


def _rand_user() -> str:
    suffix = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(8))
    return f"dast_{suffix}"


def _run(base_url: str, timeout: float) -> list[CheckResult]:
    base = base_url.rstrip("/")
    results: list[CheckResult] = []

    status, headers, _ = _http_request("GET", f"{base}/", timeout=timeout)
    results.append(
        CheckResult(
            name="security_headers_root",
            passed=(
                status == 200
                and headers.get("X-Content-Type-Options") == "nosniff"
                and headers.get("X-Frame-Options") == "SAMEORIGIN"
                and "Content-Security-Policy" in headers
            ),
            detail=f"status={status}",
        )
    )

    status, _, _ = _http_request("GET", f"{base}/auth/logout", timeout=timeout)
    results.append(
        CheckResult(
            name="logout_get_blocked",
            passed=status == 405,
            detail=f"status={status}",
        )
    )

    username = _rand_user()
    register_payload = urllib.parse.urlencode(
        {
            "username": username,
            "email": f"{username}@example.local",
            "password": "StrongPass1!",
            "password_confirm": "StrongPass1!",
        }
    ).encode("utf-8")
    status, _, _ = _http_request(
        "POST",
        f"{base}/auth/register",
        data=register_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    results.append(
        CheckResult(
            name="register_for_open_redirect_probe",
            passed=status in {200, 302},
            detail=f"status={status}",
        )
    )

    login_payload = urllib.parse.urlencode(
        {
            "username": username,
            "password": "StrongPass1!",
        }
    ).encode("utf-8")
    status, headers, _ = _http_request(
        "POST",
        f"{base}/auth/login?next=https://evil.example/phish",
        data=login_payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=timeout,
    )
    location = headers.get("Location", "")
    results.append(
        CheckResult(
            name="login_open_redirect_blocked",
            passed=(status in {200, 302} and "evil.example" not in location),
            detail=f"status={status} location={location}",
        )
    )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Run lightweight DAST smoke checks against public/auth routes")
    parser.add_argument("--base-url", required=True, help="Base URL, for example http://127.0.0.1:8765")
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--report-json", default="", help="Optional path to write JSON report")
    args = parser.parse_args()

    started = time.perf_counter()
    results = _run(args.base_url, args.timeout)
    duration_ms = (time.perf_counter() - started) * 1000.0

    failed = [r for r in results if not r.passed]
    report = {
        "base_url": args.base_url,
        "duration_ms": round(duration_ms, 1),
        "results": [r.__dict__ for r in results],
        "passed": len(failed) == 0,
    }

    if args.report_json:
        with open(args.report_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)

    for r in results:
        print(f"{r.name}: {'PASS' if r.passed else 'FAIL'} ({r.detail})")

    if failed:
        print(f"failed_checks={len(failed)}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
