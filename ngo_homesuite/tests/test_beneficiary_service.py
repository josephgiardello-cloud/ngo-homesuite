from __future__ import annotations

import pytest
from werkzeug.exceptions import NotFound

from ngo_homesuite.models.core import Organization, db
from ngo_homesuite.services.beneficiary_service import (
    beneficiary_program_summary,
    create_beneficiary,
    delete_beneficiary,
    get_beneficiary,
    list_beneficiaries,
    update_beneficiary,
)


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def ctx(app):
    with app.app_context():
        yield



def _mk_org(name: str, slug: str) -> Organization:
    org = Organization(name=name, slug=slug, is_active=True)
    db.session.add(org)
    db.session.flush()
    return org



def test_beneficiary_crud_and_program_summary(ctx):
    org = _mk_org("Beneficiary Org", "beneficiary-org")

    b1 = create_beneficiary(
        organization_id=org.id,
        first_name="Ariana",
        last_name="Lopez",
        program="Education",
        status="active",
    )
    b2 = create_beneficiary(
        organization_id=org.id,
        first_name="Miguel",
        last_name="Ramos",
        program="Health",
        status="active",
    )

    all_rows = list_beneficiaries(org.id)
    assert {b.id for b in all_rows} >= {b1.id, b2.id}

    update_beneficiary(b1.id, org.id, status="completed", notes="Graduated from program")
    updated = get_beneficiary(b1.id, org.id)
    assert updated is not None
    assert updated.status == "completed"

    summary = beneficiary_program_summary(org.id)
    assert summary["Education"] >= 1
    assert summary["Health"] >= 1

    delete_beneficiary(b2.id, org.id)
    assert get_beneficiary(b2.id, org.id) is None



def test_beneficiary_service_blocks_cross_tenant_mutation(ctx):
    org_a = _mk_org("Beneficiary Org A", "beneficiary-org-a")
    org_b = _mk_org("Beneficiary Org B", "beneficiary-org-b")

    rec = create_beneficiary(
        organization_id=org_a.id,
        first_name="Cross",
        last_name="Tenant",
        program="Livelihood",
    )

    assert get_beneficiary(rec.id, org_b.id) is None

    with pytest.raises(NotFound):
        update_beneficiary(rec.id, org_b.id, status="inactive")

    with pytest.raises(NotFound):
        delete_beneficiary(rec.id, org_b.id)
