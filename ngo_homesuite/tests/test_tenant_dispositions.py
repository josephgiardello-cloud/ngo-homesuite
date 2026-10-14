from pathlib import Path

import pytest

from tools.verify_tenant_dispositions import (
    inventory_digest,
    inventory_entries,
    verify_dispositions,
)


def ledger(tmp_path: Path):
    (tmp_path / "proof.py").write_text("verified proof fixture", encoding="utf-8")
    inventory = {"models": [{"model": "Donation"}], "parse_errors": []}
    review = {
        "schema_version": 1, "inventory_sha256": inventory_digest(inventory),
        "reviews": [{
            "entry_id": next(iter(inventory_entries(inventory))),
            "disposition": "tenant_scoped", "owner": "data-platform",
            "reviewer": "security-reviewer", "justification": "Scoped read/write negative tests.",
            "evidence": ["proof.py"], "engines": ["postgresql", "sqlite_sqlcipher"],
        }],
    }
    return inventory, review


def test_empty_review_ledger_is_release_blocking(tmp_path):
    inventory, _ = ledger(tmp_path)
    review = {"schema_version": 1, "reviews": []}
    assert verify_dispositions(inventory, review, root=tmp_path, strict=False) == 1
    with pytest.raises(ValueError, match="no reviewed tenant disposition"):
        verify_dispositions(inventory, review, root=tmp_path, strict=True)


def test_valid_review_and_stale_snapshot(tmp_path):
    inventory, review = ledger(tmp_path)
    assert verify_dispositions(inventory, review, root=tmp_path, strict=True) == 0
    inventory["models"].append({"model": "Indirect"})
    with pytest.raises(ValueError, match="stale"):
        verify_dispositions(inventory, review, root=tmp_path, strict=True)


@pytest.mark.parametrize("field,value", [
    ("disposition", "safe"), ("owner", ""), ("reviewer", ""),
    ("justification", ""), ("evidence", []), ("evidence", ["missing"]),
    ("evidence", ["../outside"]), ("engines", ["sqlite_sqlcipher"]),
])
def test_incomplete_dispositions_are_never_accepted(tmp_path, field, value):
    inventory, review = ledger(tmp_path)
    review["reviews"][0][field] = value
    with pytest.raises(ValueError):
        verify_dispositions(inventory, review, root=tmp_path, strict=True)
