"""Check declared runtime dependencies against the complete requirements bundle."""

from __future__ import annotations

import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


def normalize_dep_name(name: str) -> str:
    return canonicalize_name(name)


def _add_dependency(deps: dict[str, str], value: str) -> None:
    requirement = Requirement(value)
    name = normalize_dep_name(requirement.name)
    normalized = name + str(requirement)[len(requirement.name):]
    if name in deps and deps[name] != normalized:
        raise ValueError(f"Conflicting requirements for {name}: {deps[name]} and {normalized}")
    deps[name] = normalized


def extract_dependencies(file_path: Path) -> dict[str, str]:
    deps: dict[str, str] = {}
    active: set[Path] = set()

    def read(path: Path) -> None:
        path = path.resolve()
        if path in active:
            raise ValueError(f"Cyclic requirements include: {path}")
        active.add(path)
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split(" #", 1)[0].strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(("-r ", "--requirement ")):
                read(path.parent / line.split(maxsplit=1)[1])
            elif line.startswith("--requirement="):
                read(path.parent / line.split("=", 1)[1])
            else:
                _add_dependency(deps, line)
        active.remove(path)

    read(file_path)
    return deps


def extract_pyproject_deps(file_path: Path) -> dict[str, str]:
    project = tomllib.loads(file_path.read_text(encoding="utf-8"))["project"]
    deps: dict[str, str] = {}
    for value in project.get("dependencies", []):
        _add_dependency(deps, value)
    return deps


def check_drift(pyproject_path: Path, requirements_path: Path) -> bool:
    declared = extract_pyproject_deps(pyproject_path)
    bundled = extract_dependencies(requirements_path)
    missing = sorted(declared.keys() - bundled.keys())
    extra = sorted(bundled.keys() - declared.keys())
    mismatched = sorted(name for name in declared.keys() & bundled.keys() if declared[name] != bundled[name])
    for name in missing:
        print(f"Missing from requirements bundle: {declared[name]}")
    for name in extra:
        print(f"Missing from pyproject dependencies: {bundled[name]}")
    for name in mismatched:
        print(f"Version/specifier drift: pyproject={declared[name]} requirements={bundled[name]}")
    drift = bool(missing or extra or mismatched)
    if drift:
        print("Update pyproject.toml and the included requirements profiles together.")
    else:
        print("No drift detected between pyproject.toml and the requirements bundle")
    return drift


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    return int(check_drift(root / "pyproject.toml", root / "requirements.txt"))


if __name__ == "__main__":
    sys.exit(main())
