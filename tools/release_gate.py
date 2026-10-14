from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent


def run(*args: str) -> None:
    print(f"\nGATE: {' '.join(args)}", flush=True)
    subprocess.run([sys.executable, "-m", *args], cwd=ROOT, check=True)


def main() -> None:
    run("ruff", "check", "ngo_homesuite", "backend", "tests", "tools")
    run("mypy")
    run("pip", "check")
    print("\nGATE: installed versions match the dependency lock", flush=True)
    for line in (ROOT / "requirements-dev.lock").read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        package, pinned = line.split("==")
        installed = version(package)
        if installed != pinned:
            raise SystemExit(f"Dependency lock drift: {package} installed={installed} expected={pinned}")
    run("pip_audit", "-r", "requirements-dev.lock", "--no-deps", "--disable-pip",
        "--progress-spinner", "off")
    print("\nGATE: offline secret scan", flush=True)
    scan = subprocess.run(
        [sys.executable, "-m", "detect_secrets", "scan", "--all-files", "--no-verify",
         "--exclude-files",
         r"(^|[\\/])(\.venv|__pycache__|\.pytest_cache|\.hypothesis|\.mypy_cache|\.ruff_cache)"
         r"([\\/]|$)|\.egg-info|\.db($|-)|\.pyc$|(^|[\\/])tenants\.json$"],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    findings = json.loads(scan.stdout)["results"]
    if findings:
        for filename, entries in findings.items():
            print(f"{filename}: {len(entries)} potential secrets", file=sys.stderr)
        raise SystemExit("Secret scan failed; inspect locally before proceeding")
    run("pytest", "-q")
    run("pytest", "-q", "-m", "browser", str(Path("tests") / "test_browser.py"))
    print("\nAll release gates passed.")


if __name__ == "__main__":
    main()
