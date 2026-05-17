from __future__ import annotations

from datetime import date
from io import BytesIO

import openpyxl
import pytest

from ngo_homesuite.grants.facade import GrantsFacade
from ngo_homesuite.models.core import Organization, db
from ngo_homesuite.services.expense_service import ExpenseService


grant_service = GrantsFacade()


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


def _mk_awarded_grant(org_id: int, *, amount_awarded: float = 1200.0):
    grant = grant_service.create_grant(
        organization_id=org_id,
        funder_name="Export Funder",
        title="Exportable Budget Grant",
        amount_requested=amount_awarded,
    )
    grant_service.advance_grant_status(grant.id, org_id, new_status="submitted")
    grant_service.advance_grant_status(
        grant.id,
        org_id,
        new_status="awarded",
        amount_awarded=amount_awarded,
    )
    grant_service.add_disbursement(
        grant_id=grant.id,
        organization_id=org_id,
        amount=amount_awarded,
        received_date=date(2026, 5, 16),
    )
    return grant


def test_budget_summary_reports_budget_vs_actuals(ctx):
    org = _mk_org("Report Org A", "report-org-a")
    grant = _mk_awarded_grant(org.id, amount_awarded=1000.0)

    grant_service.create_budget_line(
        grant.id,
        org.id,
        category="personnel",
        allocated_amount=600.0,
    )
    grant_service.create_budget_line(
        grant.id,
        org.id,
        category="travel",
        allocated_amount=400.0,
    )

    ExpenseService().create_expense(
        org.id,
        project_id=None,
        fund_id=None,
        amount=550.0,
        currency="USD",
        payee="Staffing Vendor",
        description="Personnel spending",
        grant_id=grant.id,
        expense_category="personnel",
    )

    summary = grant_service.get_grant_budget_summary(grant.id, org.id)

    assert summary["grant_id"] == grant.id
    assert summary["total_allocated"] == pytest.approx(1000.0)
    assert summary["total_spent"] == pytest.approx(550.0)
    assert summary["total_remaining"] == pytest.approx(450.0)
    assert len(summary["lines"]) == 2


def test_excel_export_contains_report_data(ctx):
    org = _mk_org("Report Org B", "report-org-b")
    grant = _mk_awarded_grant(org.id, amount_awarded=900.0)

    grant_service.create_budget_line(
        grant.id,
        org.id,
        category="equipment",
        allocated_amount=900.0,
    )

    file_bytes = grant_service.export_grant_budget_report_excel(grant.id, org.id)

    assert isinstance(file_bytes, bytes)
    assert len(file_bytes) > 200

    workbook = openpyxl.load_workbook(filename=BytesIO(file_bytes))
    sheet = workbook["Grant Budget"]

    assert sheet["A1"].value == "Grant ID"
    assert sheet["A2"].value == "Title"
    assert sheet["A10"].value == "Budget Line ID"


def test_pdf_export_returns_valid_pdf(ctx):
    org = _mk_org("Report Org C", "report-org-c")
    grant = _mk_awarded_grant(org.id, amount_awarded=750.0)

    grant_service.create_budget_line(
        grant.id,
        org.id,
        category="program_services",
        allocated_amount=750.0,
    )

    pdf_bytes = grant_service.export_grant_budget_report_pdf(grant.id, org.id)

    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 500
    assert pdf_bytes.startswith(b"%PDF")
