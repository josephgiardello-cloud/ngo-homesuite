"""Tests for grant budget model and admin routes (Milestone B-1).

Acceptance criteria:
1. GrantBudgetLine model with: grant_id, category, allocated_amount, notes
2. Migration adds table without breaking existing grant rows
3. Admin routes to create/edit/delete budget lines per grant
4. Validation: total budget lines ≤ grant award; prevent negative amounts
"""
import pytest
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from flask import json
from flask_login import current_user
from sqlalchemy import select

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import db, User, Organization
from ngo_homesuite.grants.models import Grant, GrantBudgetLine, GrantExpenseAllocation


@pytest.fixture(scope="module")
def app():
    return create_app(TestingConfig)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def org(app):
    """Create a test organization."""
    with app.app_context():
        org = Organization(
            name=f"Test Org {uuid.uuid4()}",
            email="test@org.local"
        )
        db.session.add(org)
        db.session.commit()
        org_id = org.id
    
    yield org
    
    # Cleanup (best-effort)
    with app.app_context():
        try:
            stmt = select(Organization).where(Organization.id == org_id)
            org = db.session.scalar(stmt)
            if org:
                db.session.delete(org)
                db.session.commit()
        except Exception:
            db.session.rollback()  # Ignore foreign key constraints


@pytest.fixture()
def admin_user(app, org):
    """Create a test admin user."""
    with app.app_context():
        user = User(
            username=f"admin_{uuid.uuid4()}",
            email=f"admin_{uuid.uuid4()}@test.local",
            organization_id=org.id,
            role="admin",
            is_active=True
        )
        user.set_password("test_password")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
    
    yield user
    
    # Cleanup (best-effort)
    with app.app_context():
        try:
            stmt = select(User).where(User.id == user_id)
            user = db.session.scalar(stmt)
            if user:
                db.session.delete(user)
                db.session.commit()
        except Exception:
            db.session.rollback()  # Ignore errors


class TestGrantBudgetLineModel:
    """Test GrantBudgetLine SQLAlchemy model."""

    def test_create_budget_line(self, app, org):
        """Create a budget line with required fields."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.flush()

            line = GrantBudgetLine(
                grant_id=grant.id,
                organization_id=org.id,
                category="Personnel",
                line_name="Staff Salaries",
                allocated_amount=5000.0
            )
            db.session.add(line)
            db.session.commit()

            # Verify
            stmt = select(GrantBudgetLine).where(GrantBudgetLine.id == line.id)
            result = db.session.scalar(stmt)
            assert result.category == "Personnel"
            assert result.allocated_amount == 5000.0
            assert result.line_name == "Staff Salaries"

    def test_budget_line_with_notes(self, app, org):
        """Create a budget line with optional notes."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.flush()

            line = GrantBudgetLine(
                grant_id=grant.id,
                organization_id=org.id,
                category="Equipment",
                line_name="Computers",
                allocated_amount=3000.0,
                notes="Budget for 5 laptops"
            )
            db.session.add(line)
            db.session.commit()

            stmt = select(GrantBudgetLine).where(GrantBudgetLine.id == line.id)
            result = db.session.scalar(stmt)
            assert result.notes == "Budget for 5 laptops"

    def test_unique_category_per_grant(self, app, org):
        """Enforce unique (grant_id, category) constraint."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.flush()

            line1 = GrantBudgetLine(
                grant_id=grant.id,
                organization_id=org.id,
                category="Personnel",
                line_name="Salaries",
                allocated_amount=5000.0
            )
            db.session.add(line1)
            db.session.commit()

            # Try to add duplicate category
            line2 = GrantBudgetLine(
                grant_id=grant.id,
                organization_id=org.id,
                category="Personnel",
                line_name="Wages",
                allocated_amount=2000.0
            )
            db.session.add(line2)
            
            with pytest.raises(Exception):  # IntegrityError
                db.session.commit()

    def test_grant_budget_line_relationship(self, app, org):
        """Test relationship from Grant to GrantBudgetLine."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.flush()

            line1 = GrantBudgetLine(
                grant_id=grant.id,
                organization_id=org.id,
                category="Personnel",
                line_name="Staff",
                allocated_amount=5000.0
            )
            line2 = GrantBudgetLine(
                grant_id=grant.id,
                organization_id=org.id,
                category="Equipment",
                line_name="Computers",
                allocated_amount=3000.0
            )
            db.session.add_all([line1, line2])
            db.session.commit()

            # Verify relationship
            stmt = select(Grant).where(Grant.id == grant.id)
            result = db.session.scalar(stmt)
            assert len(result.budget_lines) == 2
            assert result.budget_lines[0].category in ["Personnel", "Equipment"]


class TestGrantBudgetAdminRoutes:
    """Test admin routes for grant budget management."""

    def test_get_grant_budget_unauthorized(self, client):
        """GET /admin/grants/{id}/budget requires authentication."""
        response = client.get("/admin/grants/1/budget")
        assert response.status_code == 302  # Redirect to login

    def test_get_grant_budget_not_found(self, client, admin_user):
        """GET /admin/grants/{id}/budget returns 404 for non-existent grant."""
        with client:
            # Login
            rv = client.post(
                "/auth/login",
                data={"username": admin_user.username, "password": "test_password"},
                follow_redirects=True
            )
            assert rv.status_code == 200
            
            # Try to access non-existent grant
            response = client.get("/admin/grants/99999/budget")
            assert response.status_code == 404

    def test_get_grant_budget_success(self, client, admin_user, org, app):
        """GET /admin/grants/{id}/budget returns budget overview."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.flush()

            line = GrantBudgetLine(
                grant_id=grant.id,
                organization_id=org.id,
                category="Personnel",
                line_name="Staff",
                allocated_amount=5000.0
            )
            db.session.add(line)
            db.session.commit()
            grant_id = grant.id

        with client:
            # Login
            rv = client.post(
                "/auth/login",
                data={"username": admin_user.username, "password": "test_password"},
                follow_redirects=True
            )
            assert rv.status_code == 200

            response = client.get(f"/admin/grants/{grant_id}/budget")
            assert response.status_code == 200
            data = response.get_json()
            assert data["grant_id"] == grant_id
            assert data["total_awarded"] == 10000.0
            assert len(data["budget_lines"]) == 1

    def test_create_budget_line_success(self, client, admin_user, org, app):
        """POST /admin/grants/{id}/budget/lines creates a new budget line."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.commit()
            grant_id = grant.id

        with client:
            # Login
            rv = client.post(
                "/auth/login",
                data={"username": admin_user.username, "password": "test_password"},
                follow_redirects=True
            )
            assert rv.status_code == 200

            response = client.post(
                f"/admin/grants/{grant_id}/budget/lines",
                json={
                    "category": "Personnel",
                    "line_name": "Staff Salaries",
                    "allocated_amount": 5000.0,
                    "notes": "Annual salary budget"
                }
            )
            assert response.status_code == 201
            data = response.get_json()
            assert data["category"] == "Personnel"
            assert data["allocated_amount"] == 5000.0

    def test_create_budget_line_no_body(self, client, admin_user, org, app):
        """POST with no body returns 400 or 415 (media type)."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.commit()
            grant_id = grant.id

        with client:
            # Login
            rv = client.post(
                "/auth/login",
                data={"username": admin_user.username, "password": "test_password"},
                follow_redirects=True
            )
            assert rv.status_code == 200

            response = client.post(f"/admin/grants/{grant_id}/budget/lines")
            # Accept either 400 (bad request) or 415 (unsupported media type)
            assert response.status_code in [400, 415]

    def test_create_budget_line_missing_fields(self, client, admin_user, org, app):
        """POST with missing required fields returns 400."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.commit()
            grant_id = grant.id

        with client:
            # Login
            rv = client.post(
                "/auth/login",
                data={"username": admin_user.username, "password": "test_password"},
                follow_redirects=True
            )
            assert rv.status_code == 200

            # Missing allocated_amount
            response = client.post(
                f"/admin/grants/{grant_id}/budget/lines",
                json={
                    "category": "Personnel",
                    "line_name": "Staff"
                }
            )
            assert response.status_code == 400

    def test_create_budget_line_negative_amount(self, client, admin_user, org, app):
        """POST with negative amount returns 400."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.commit()
            grant_id = grant.id

        with client:
            # Login
            rv = client.post(
                "/auth/login",
                data={"username": admin_user.username, "password": "test_password"},
                follow_redirects=True
            )
            assert rv.status_code == 200

            response = client.post(
                f"/admin/grants/{grant_id}/budget/lines",
                json={
                    "category": "Personnel",
                    "line_name": "Staff",
                    "allocated_amount": -1000.0
                }
            )
            assert response.status_code == 400

    def test_create_budget_line_exceeds_award(self, client, admin_user, org, app):
        """POST with total exceeding award returns 400."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=5000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.commit()
            grant_id = grant.id

        with client:
            # Login
            rv = client.post(
                "/auth/login",
                data={"username": admin_user.username, "password": "test_password"},
                follow_redirects=True
            )
            assert rv.status_code == 200

            response = client.post(
                f"/admin/grants/{grant_id}/budget/lines",
                json={
                    "category": "Personnel",
                    "line_name": "Staff",
                    "allocated_amount": 6000.0
                }
            )
            assert response.status_code == 400

    def test_create_budget_line_duplicate_category(self, client, admin_user, org, app):
        """POST with duplicate category returns 409."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.flush()

            line = GrantBudgetLine(
                grant_id=grant.id,
                organization_id=org.id,
                category="Personnel",
                line_name="Existing",
                allocated_amount=2000.0
            )
            db.session.add(line)
            db.session.commit()
            grant_id = grant.id

        with client:
            # Login
            rv = client.post(
                "/auth/login",
                data={"username": admin_user.username, "password": "test_password"},
                follow_redirects=True
            )
            assert rv.status_code == 200

            response = client.post(
                f"/admin/grants/{grant_id}/budget/lines",
                json={
                    "category": "Personnel",
                    "line_name": "New Line",
                    "allocated_amount": 3000.0
                }
            )
            assert response.status_code == 409

    def test_update_budget_line_success(self, client, admin_user, org, app):
        """PUT /admin/grants/{id}/budget/lines/{line_id} updates a line."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.flush()

            line = GrantBudgetLine(
                grant_id=grant.id,
                organization_id=org.id,
                category="Personnel",
                line_name="Old Name",
                allocated_amount=5000.0
            )
            db.session.add(line)
            db.session.commit()
            grant_id = grant.id
            line_id = line.id

        with client:
            # Login
            rv = client.post(
                "/auth/login",
                data={"username": admin_user.username, "password": "test_password"},
                follow_redirects=True
            )
            assert rv.status_code == 200

            response = client.put(
                f"/admin/grants/{grant_id}/budget/lines/{line_id}",
                json={
                    "line_name": "New Name",
                    "allocated_amount": 6000.0
                }
            )
            assert response.status_code == 200

    def test_delete_budget_line_no_allocations(self, client, admin_user, org, app):
        """DELETE /admin/grants/{id}/budget/lines/{line_id} deletes if no allocations."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.flush()

            line = GrantBudgetLine(
                grant_id=grant.id,
                organization_id=org.id,
                category="Personnel",
                line_name="Staff",
                allocated_amount=5000.0
            )
            db.session.add(line)
            db.session.commit()
            grant_id = grant.id
            line_id = line.id

        with client:
            # Login
            rv = client.post(
                "/auth/login",
                data={"username": admin_user.username, "password": "test_password"},
                follow_redirects=True
            )
            assert rv.status_code == 200

            response = client.delete(f"/admin/grants/{grant_id}/budget/lines/{line_id}")
            assert response.status_code == 204

    def test_get_budget_variance_report(self, client, admin_user, org, app):
        """GET /admin/grants/{id}/budget/variance-report returns variance data."""
        with app.app_context():
            grant = Grant(
                organization_id=org.id,
                funder_name="Test Funder",
                title="Test Grant",
                amount_awarded=10000.0,
                status="awarded"
            )
            db.session.add(grant)
            db.session.flush()

            line = GrantBudgetLine(
                grant_id=grant.id,
                organization_id=org.id,
                category="Personnel",
                line_name="Staff",
                allocated_amount=5000.0
            )
            db.session.add(line)
            db.session.commit()
            grant_id = grant.id

        with client:
            # Login
            rv = client.post(
                "/auth/login",
                data={"username": admin_user.username, "password": "test_password"},
                follow_redirects=True
            )
            assert rv.status_code == 200

            response = client.get(f"/admin/grants/{grant_id}/budget/variance-report")
            assert response.status_code == 200
            data = response.get_json()
            assert data["grant_id"] == grant_id
            assert data["summary"]["total_allocated"] == 5000.0
