"""Create commit-bound manifests only from successful, non-skipped test reports."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path


def test_report_summary(path: Path) -> dict:
    root = ET.parse(path).getroot()
    cases = root.findall(".//testcase")
    if not cases or any(case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")):
        raise ValueError(f"Candidate proof requires executed tests without failures/errors/skips: {path.name}")
    return {"path": path.name, "passed": len(cases), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--engine", choices=["postgresql", "sqlite_sqlcipher"], required=True)
    args = parser.parse_args()
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], text=True).strip():
        raise ValueError("Candidate evidence cannot certify a modified tracked worktree")
    candidate = os.environ.get("GITHUB_SHA", sha)
    if not re.fullmatch(r"[0-9a-f]{40}", candidate) or candidate != sha:
        raise ValueError("Evidence candidate SHA differs from checked-out commit")
    reports = [test_report_summary(args.directory / name) for name in ("tenant.xml", "encryption.xml")]
    payload = {
        "schema_version": 1, "commit_sha": sha, "engine": args.engine,
        "run_url": (
            f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
            if os.environ.get("GITHUB_REPOSITORY") and os.environ.get("GITHUB_RUN_ID") else None
        ),
        "reports": reports,
        "scope": "Named engine isolation/encryption lanes only; not full release certification",
    }
    (args.directory / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Candidate engine evidence bound to {sha}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
