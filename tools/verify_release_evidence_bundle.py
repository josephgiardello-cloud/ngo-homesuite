from __future__ import annotations

import argparse
import json
from pathlib import Path


_ALLOWED_STATUS = {"complete", "pending", "waived"}


def _load_bundle(path: Path) -> dict:
    if not path.exists():
        raise RuntimeError(f"Missing release evidence bundle: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("Release evidence bundle must be a JSON object")
    return payload


def _validate_bundle(payload: dict, *, strict: bool) -> None:
    required_top_level = {"generated_at_utc", "release_version", "evidence"}
    missing = sorted(required_top_level - set(payload.keys()))
    if missing:
        raise RuntimeError(f"Release evidence bundle missing key(s): {missing}")

    evidence = payload.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise RuntimeError("Release evidence bundle 'evidence' must be a non-empty array")

    seen_ids: set[str] = set()
    for item in evidence:
        if not isinstance(item, dict):
            raise RuntimeError("Each evidence entry must be an object")

        for key in ("id", "path", "status", "required"):
            if key not in item:
                raise RuntimeError(f"Evidence entry missing required key: {key}")

        item_id = str(item["id"]).strip()
        if not item_id:
            raise RuntimeError("Evidence entry id must be non-empty")
        if item_id in seen_ids:
            raise RuntimeError(f"Duplicate evidence entry id: {item_id}")
        seen_ids.add(item_id)

        status = str(item["status"]).strip().lower()
        if status not in _ALLOWED_STATUS:
            raise RuntimeError(
                f"Evidence entry '{item_id}' has invalid status '{status}'. Allowed: {sorted(_ALLOWED_STATUS)}"
            )

        required = bool(item["required"])
        path = Path(str(item["path"]).strip())
        exists = path.exists()

        if status == "complete" and not exists:
            raise RuntimeError(f"Evidence entry '{item_id}' marked complete but path does not exist: {path}")

        if strict and required and status != "complete":
            raise RuntimeError(
                f"Required evidence entry '{item_id}' must be complete for strict validation (current: {status})"
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate release evidence bundle")
    parser.add_argument(
        "--path",
        default="artifacts/release-evidence-bundle.json",
        help="Path to the release evidence bundle JSON file",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Require all required entries to be complete",
    )
    args = parser.parse_args()

    bundle_path = Path(args.path)
    payload = _load_bundle(bundle_path)
    _validate_bundle(payload, strict=bool(args.strict))
    print(f"Release evidence bundle validated: {bundle_path}")


if __name__ == "__main__":
    main()
