"""Reject missing, stale or unsupported tenant-boundary dispositions."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def inventory_digest(inventory: dict) -> str:
    canonical = json.dumps(inventory, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def inventory_entries(inventory: dict) -> dict[str, dict]:
    entries = {}
    for section, records in inventory.items():
        if section in {"source_files", "parse_errors"} or not isinstance(records, list):
            continue
        for index, record in enumerate(records):
            encoded = json.dumps([section, index, record], sort_keys=True, separators=(",", ":"))
            entry_id = hashlib.sha256(encoded.encode()).hexdigest()
            entries[entry_id] = {"section": section, **record}
    return entries


def verify_dispositions(inventory: dict, review: dict, *, root: Path, strict: bool) -> int:
    if inventory.get("parse_errors"):
        raise ValueError("Tenant inventory has parse errors")
    if review.get("schema_version") != 1 or not isinstance(review.get("reviews"), list):
        raise ValueError("Invalid tenant disposition ledger")
    entries = inventory_entries(inventory)
    if review["reviews"] and review.get("inventory_sha256") != inventory_digest(inventory):
        raise ValueError("Tenant dispositions refer to a stale inventory snapshot")
    seen = set()
    for item in review["reviews"]:
        entry_id = item.get("entry_id")
        if entry_id not in entries or entry_id in seen:
            raise ValueError("Unknown or duplicate tenant disposition entry")
        seen.add(entry_id)
        if item.get("disposition") not in {"tenant_scoped", "explicitly_global", "not_applicable"}:
            raise ValueError("Tenant disposition must be tenant_scoped, explicitly_global or not_applicable")
        for key in ("owner", "reviewer", "justification"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ValueError(f"Tenant disposition requires {key}")
        proofs = item.get("evidence")
        if not isinstance(proofs, list) or not proofs:
            raise ValueError("Tenant disposition requires evidence")
        for proof in proofs:
            if not isinstance(proof, str) or not proof.strip():
                raise ValueError("Invalid tenant evidence path")
            path = (root / proof).resolve()
            if not path.is_relative_to(root.resolve()) or not path.is_file():
                raise ValueError("Tenant evidence must identify an existing repository file")
        if item["disposition"] == "tenant_scoped" and set(item.get("engines", [])) != {"postgresql", "sqlite_sqlcipher"}:
            raise ValueError("Tenant-scoped dispositions require proof for both production engines")
    missing = len(entries.keys() - seen)
    if strict and missing:
        raise ValueError(f"{missing} inventory entries have no reviewed tenant disposition")
    return missing


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=Path("docs/tenant_boundary_inventory.json"))
    parser.add_argument("--review", type=Path, default=Path("docs/tenant_boundary_dispositions.json"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    review = json.loads(args.review.read_text(encoding="utf-8"))
    try:
        missing = verify_dispositions(inventory, review, root=Path.cwd(), strict=args.strict)
    except ValueError as exc:
        parser.exit(1, f"Tenant disposition gate failed: {exc}\n")
    print(f"Tenant disposition ledger validated; {missing} entries remain unreviewed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
