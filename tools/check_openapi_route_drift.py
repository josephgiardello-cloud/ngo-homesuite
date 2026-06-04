from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig


def _load_spec_paths(spec_path: Path) -> set[str]:
    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
    paths = spec.get("paths") or {}
    if not isinstance(paths, dict):
        raise RuntimeError("OpenAPI paths section must be a mapping")
    return {str(path).strip() for path in paths.keys() if str(path).strip()}


def _load_required_paths(path_file: Path) -> list[str]:
    lines = [line.strip() for line in path_file.read_text(encoding="utf-8").splitlines()]
    required = [line for line in lines if line and not line.startswith("#")]
    if not required:
        raise RuntimeError(f"No required paths found in {path_file}")
    return required


def _runtime_paths() -> set[str]:
    app = create_app(TestingConfig)
    collected: set[str] = set()
    with app.app_context():
        for rule in app.url_map.iter_rules():
            path = str(rule.rule or "").strip()
            if not path.startswith("/api/v2/"):
                continue
            normalized = path.replace("<int:", "{").replace("<string:", "{").replace("<", "{").replace(">", "}")
            collected.add(normalized)
    return collected


def main() -> None:
    spec_paths = _load_spec_paths(Path("docs/openapi.yaml"))
    required_paths = _load_required_paths(Path("docs/openapi_required_v2_paths.txt"))
    runtime_paths = _runtime_paths()

    missing_in_runtime = sorted(path for path in required_paths if path not in runtime_paths)
    if missing_in_runtime:
        raise RuntimeError(f"Required OpenAPI paths missing from runtime URL map: {missing_in_runtime}")

    missing_in_spec = sorted(path for path in required_paths if path not in spec_paths)
    if missing_in_spec:
        raise RuntimeError(f"Required OpenAPI paths missing from docs/openapi.yaml: {missing_in_spec}")

    print("OpenAPI route drift check passed for required v2 paths")


if __name__ == "__main__":
    main()
