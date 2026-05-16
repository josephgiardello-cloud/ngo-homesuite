from __future__ import annotations

from ngo_homesuite.rbac import Role, can_transition_workflow


def test_org_admin_can_transition_any_event() -> None:
    assert can_transition_workflow(Role.ORG_ADMIN.value, "some_future_event") is True


def test_case_worker_has_limited_transition_allowlist() -> None:
    assert can_transition_workflow(Role.CASE_WORKER.value, "intake_submit") is True
    assert can_transition_workflow(Role.CASE_WORKER.value, "workflow_audit_replay") is False


def test_unknown_role_is_rejected() -> None:
    assert can_transition_workflow("not_a_real_role", "intake_submit") is False
