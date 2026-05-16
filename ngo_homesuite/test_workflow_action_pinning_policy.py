from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".github" / "workflows"
SHA1_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def _iter_uses_values(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "uses" and isinstance(value, str):
                yield value
            else:
                yield from _iter_uses_values(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_uses_values(item)


def _is_third_party_uses(uses_value: str) -> bool:
    if uses_value.startswith("./") or uses_value.startswith("docker://"):
        return False
    if "@" not in uses_value or "/" not in uses_value:
        return False
    owner = uses_value.split("/", 1)[0].strip().lower()
    return owner != "actions"


def _is_sha_pinned(uses_value: str) -> bool:
    if "@" not in uses_value:
        return False
    ref = uses_value.rsplit("@", 1)[1].strip()
    return bool(SHA1_RE.fullmatch(ref))


def test_third_party_actions_are_sha_pinned() -> None:
    workflow_files = sorted(WORKFLOWS_DIR.glob("*.yml"))
    assert workflow_files, "No workflow files found under .github/workflows"

    violations: list[str] = []

    for workflow_file in workflow_files:
        data = yaml.safe_load(workflow_file.read_text(encoding="utf-8")) or {}
        for uses_value in _iter_uses_values(data):
            if _is_third_party_uses(uses_value) and not _is_sha_pinned(uses_value):
                violations.append(f"{workflow_file.name}: uses: {uses_value}")

    assert not violations, (
        "Third-party GitHub Actions must be pinned to full commit SHA refs. Violations:\n"
        + "\n".join(violations)
    )
