from __future__ import annotations

import pytest

from ngo_homesuite.services import donor_service as donor_service_module
from ngo_homesuite.services.donor_service import DonorService


def test_create_donor_rolls_back_on_commit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    service = DonorService()
    events: list[str] = []

    class _FakeSession:
        def add(self, _obj) -> None:
            events.append("add")

        def commit(self) -> None:
            events.append("commit")
            raise RuntimeError("commit failed")

        def rollback(self) -> None:
            events.append("rollback")

    monkeypatch.setattr(donor_service_module.db, "session", _FakeSession())

    with pytest.raises(RuntimeError, match="commit failed"):
        service.create_donor(
            1,
            "Test Donor",
            email="donor@example.org",
            donor_type="individual",
            status="active",
            preferred_contact_method="email",
        )

    assert events == ["add", "commit", "rollback"]
