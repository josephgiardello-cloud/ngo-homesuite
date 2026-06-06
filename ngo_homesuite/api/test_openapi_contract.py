from __future__ import annotations

from pathlib import Path

import yaml


def test_openapi_spec_has_unique_operation_ids_and_responses() -> None:
    spec_path = Path("docs/openapi.yaml")
    assert spec_path.exists(), "OpenAPI spec file is missing: docs/openapi.yaml"

    spec = yaml.safe_load(spec_path.read_text(encoding="utf-8"))
    assert isinstance(spec, dict)
    assert spec.get("openapi") == "3.0.3"

    paths = spec.get("paths")
    assert isinstance(paths, dict) and paths, "OpenAPI paths must be present"

    operation_ids: list[str] = []

    for path, methods in paths.items():
        assert isinstance(path, str) and path.startswith("/")
        assert isinstance(methods, dict) and methods

        for method, operation in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "options", "head"}:
                continue
            assert isinstance(operation, dict)

            operation_id = operation.get("operationId")
            assert operation_id, f"Missing operationId for {method.upper()} {path}"
            operation_ids.append(str(operation_id))

            responses = operation.get("responses")
            assert isinstance(responses, dict) and responses, f"Missing responses for {method.upper()} {path}"
            assert any(str(code).startswith(("2", "4", "5")) for code in responses.keys()), (
                f"Expected at least one 2xx/4xx/5xx response for {method.upper()} {path}"
            )

    assert len(operation_ids) == len(set(operation_ids)), "operationId values must be unique"


def test_openapi_required_v2_paths_are_declared() -> None:
    spec = yaml.safe_load(Path("docs/openapi.yaml").read_text(encoding="utf-8"))
    declared_paths = spec.get("paths") or {}

    required_file = Path("docs/openapi_required_v2_paths.txt")
    assert required_file.exists(), "Missing required v2 paths file: docs/openapi_required_v2_paths.txt"
    required_entries = [
        line.strip()
        for line in required_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    assert required_entries, "docs/openapi_required_v2_paths.txt must contain at least one required path"

    valid_methods = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
    missing_paths: list[str] = []
    missing_methods: list[str] = []

    for entry in required_entries:
        method = None
        path = entry

        parts = entry.split(maxsplit=1)
        if len(parts) == 2 and parts[0].upper() in valid_methods:
            method = parts[0].upper()
            path = parts[1].strip()

        assert path.startswith("/"), f"Invalid required path entry: {entry}"

        operations = declared_paths.get(path)
        if operations is None:
            missing_paths.append(path)
            continue

        if method is not None:
            if not isinstance(operations, dict) or method.lower() not in {
                str(op).lower() for op in operations.keys()
            }:
                missing_methods.append(f"{method} {path}")

    assert not missing_paths, (
        f"Required OpenAPI paths missing from docs/openapi.yaml: {missing_paths}"
    )
    assert not missing_methods, (
        "Required OpenAPI methods missing from docs/openapi.yaml: "
        f"{missing_methods}"
    )


def test_openapi_route_drift_checker_script_exists() -> None:
    assert Path("tools/check_openapi_route_drift.py").exists(), (
        "Missing OpenAPI drift checker: tools/check_openapi_route_drift.py"
    )
