from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


REQ_FILES = [
    "requirements-core.txt",
    "requirements-db.txt",
    "requirements-ai.txt",
    "requirements-cloud.txt",
]


def _normalize_name(spec: str) -> str:
    m = re.match(r"^\s*([A-Za-z0-9_.-]+)", spec)
    if not m:
        return ""
    return m.group(1).lower().replace("_", "-")


def _normalize_spec(spec: str) -> str:
    return re.sub(r"\s+", "", spec.strip())


def _iter_requirements(path: Path) -> list[str]:
    items: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-r"):
            continue
        items.append(line)
    return items


def main() -> int:
    root = Path(__file__).resolve().parents[1]

    pyproject_path = root / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    deps = pyproject.get("project", {}).get("dependencies", [])

    pyproject_specs = {_normalize_name(dep): _normalize_spec(dep) for dep in deps}

    required_specs: dict[str, str] = {}
    for rel in REQ_FILES:
        file_path = root / rel
        for spec in _iter_requirements(file_path):
            name = _normalize_name(spec)
            if not name:
                continue
            required_specs[name] = _normalize_spec(spec)

    missing: list[str] = []
    mismatched: list[str] = []

    for name, req_spec in sorted(required_specs.items()):
        pyproject_spec = pyproject_specs.get(name)
        if pyproject_spec is None:
            missing.append(req_spec)
            continue
        if pyproject_spec != req_spec:
            mismatched.append(f"{name}: pyproject={pyproject_spec} requirements={req_spec}")

    if missing or mismatched:
        if missing:
            print("Missing from pyproject dependencies:")
            for item in missing:
                print(f"  - {item}")
        if mismatched:
            print("Version/specifier drift:")
            for item in mismatched:
                print(f"  - {item}")
        return 1

    print("Dependency drift check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
