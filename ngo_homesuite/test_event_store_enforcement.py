from __future__ import annotations

from pathlib import Path

import pytest

from ngo_homesuite.audit import AuditEvent, DbEventStore
from ngo_homesuite.models.core import db
from ngo_homesuite.persistence import WriteGateViolation
from ngo_homesuite.persistence.repositories import WorkflowRepository
from ngo_homesuite.workflow_engine import WorkflowInstance


ROOT = Path(__file__).resolve().parent.parent


def test_workflow_write_boundary_is_orchestrated_through_write_gate() -> None:
    container_text = (ROOT / "ngo_homesuite/app/container.py").read_text(encoding="utf-8")
    gate_text = (ROOT / "ngo_homesuite/app/write_gate.py").read_text(encoding="utf-8")
    repo_text = (ROOT / "ngo_homesuite/persistence/repositories/workflow_repository.py").read_text(encoding="utf-8")
    store_text = (ROOT / "ngo_homesuite/audit/event_store.py").read_text(encoding="utf-8")
    state_machine_text = (ROOT / "ngo_homesuite/workflow_engine/state_machine.py").read_text(encoding="utf-8")
    base_repo_text = (ROOT / "ngo_homesuite/persistence/base_repository.py").read_text(encoding="utf-8")

    # Core WriteGate orchestration
    assert "write_gate.execute(" in container_text
    assert "WorkflowWriteHandler" in gate_text
    assert "append_batch(result.events" in gate_text
    assert "version=1" in state_machine_text
    assert "version=int(saved.version or 1)" in gate_text
    assert "Version sequence violation" in store_text
    assert "Duplicate idempotency key" in store_text
    assert "event_emitter=collector" in gate_text
    
    # New BaseRepository enforcement
    assert "BaseRepository" in repo_text
    assert "from ngo_homesuite.persistence.base_repository import enforce_write_gate" in store_text
    assert "class BaseRepository" in base_repo_text
    assert "assert_in_write_gate" in base_repo_text
    assert "@enforce_write_gate" in store_text


def test_repository_save_rejects_direct_writes() -> None:
    repository = WorkflowRepository()
    instance = WorkflowInstance(
        instance_id="wfi_test_1",
        org_id="org_test_1",
        workflow_type="case_intake",
        current_step="intake",
    )

    with pytest.raises(WriteGateViolation, match="All writes must go through WriteGate"):
        repository.save(instance)


def test_db_event_store_rejects_direct_appends() -> None:
    store = DbEventStore()
    event = AuditEvent(
        event_id="evt_test_1",
        org_id="org_test_1",
        event_type="donation_created",
        aggregate_type="donation",
        aggregate_id="don_1",
        actor_id="actor_1",
        payload={"amount": 50},
        version=1,
    )

    with pytest.raises(WriteGateViolation, match="All writes must go through WriteGate"):
        store.append(event)
