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
