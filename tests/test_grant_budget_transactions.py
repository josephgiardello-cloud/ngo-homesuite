"""Tests for B-2: Budget Transactions & Commitment Tracking."""
import pytest
import uuid
from ngo_homesuite.models.core import db, User, Organization
from ngo_homesuite.grants.models import Grant, GrantBudgetLine, GrantBudgetTransaction
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.app_factory import create_app


def _unique_token() -> str:
    return uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def app():
    """Create Flask app for testing."""
    _app = create_app(TestingConfig)
    return _app


@pytest.fixture
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture
def org(app):
    """Create test organization."""
    with app.app_context():
        token = _unique_token()
        o = Organization(
            name=f"Test Org {token}",
            slug=f"test-org-{token}",
            email=f"org-{token}@test.org",
            country="US",
        )
        db.session.add(o)
        db.session.commit()
        org_id = o.id
        yield o
        try:
            db.session.query(Organization).filter_by(id=org_id).delete()
            db.session.commit()
        except Exception:
            pass


@pytest.fixture
def admin_user(app, org):
    """Create admin user."""
    with app.app_context():
        token = _unique_token()
        u = User(
            username=f"testadmin_{token}",
            email=f"admin_{token}@test.org",
            organization_id=org.id,
            role="admin",
            is_active=True,
        )
        u.set_password("test_password")
        db.session.add(u)
        db.session.commit()
        user_id = u.id
        yield u
        try:
            db.session.query(User).filter_by(id=user_id).delete()
            db.session.commit()
        except Exception:
            pass


@pytest.fixture
def grant(app, org):
    """Create test grant."""
    with app.app_context():
        token = _unique_token()
        g = Grant(
            organization_id=org.id,
            funder_name="Test Foundation",
            title=f"Test Grant {token}",
            amount_awarded=100000.0,
            status="active",
        )
        db.session.add(g)
        db.session.commit()
        grant_id = g.id
        yield g
        try:
            db.session.query(Grant).filter_by(id=grant_id).delete()
            db.session.commit()
        except Exception:
            pass


@pytest.fixture
def budget_line(app, grant, org):
    """Create test budget line."""
    with app.app_context():
        token = _unique_token()
        line = GrantBudgetLine(
            grant_id=grant.id,
            organization_id=org.id,
            category=f"Personnel-{token}",
            line_name=f"Staff Salaries {token}",
            allocated_amount=50000.0,
            status="pending",
        )
        db.session.add(line)
        db.session.commit()
        line_id = line.id
        yield line
        try:
            db.session.query(GrantBudgetLine).filter_by(id=line_id).delete()
            db.session.commit()
        except Exception:
            pass


# ============================================================================
# Model Tests
# ============================================================================


class TestGrantBudgetTransactionModel:
    """Test GrantBudgetTransaction model."""

    def test_create_commit_transaction(self, app, budget_line, grant, org, admin_user):
        """Test creating a commitment transaction."""
        with app.app_context():
            txn = GrantBudgetTransaction(
                budget_line_id=budget_line.id,
                grant_id=grant.id,
                organization_id=org.id,
                transaction_type="commit",
                amount=10000.0,
                description="Initial commitment",
                created_by_user_id=admin_user.id,
            )
            db.session.add(txn)
            db.session.commit()

            assert txn.id is not None
            assert txn.transaction_type == "commit"
            assert txn.amount == 10000.0
            assert txn.created_at is not None

    def test_transaction_types(self, app, budget_line, grant, org):
        """Test different transaction types."""
        with app.app_context():
            types = ["commit", "reconcile", "reverse", "adjust"]
            for tx_type in types:
                txn = GrantBudgetTransaction(
                    budget_line_id=budget_line.id,
                    grant_id=grant.id,
                    organization_id=org.id,
                    transaction_type=tx_type,
                    amount=1000.0,
                )
                db.session.add(txn)
            
            db.session.commit()
            txns = db.session.query(GrantBudgetTransaction).filter_by(budget_line_id=budget_line.id).all()
            assert {txn.transaction_type for txn in txns} == set(types)

    def test_budget_line_has_committed_and_reconciled_amounts(self, app, budget_line):
        """Test that budget line tracks committed and reconciled amounts."""
        with app.app_context():
            line = db.session.query(GrantBudgetLine).filter_by(id=budget_line.id).first()
            assert line.committed_amount == 0.0
            assert line.reconciled_amount == 0.0
            assert line.status == "pending"

    def test_budget_line_status_field(self, app, budget_line):
        """Test budget line status field."""
        with app.app_context():
            line = db.session.query(GrantBudgetLine).filter_by(id=budget_line.id).first()
            assert line.status in ["pending", "active", "closed"]
            line.status = "active"
            db.session.commit()
            
            refreshed = db.session.query(GrantBudgetLine).filter_by(id=budget_line.id).first()
            assert refreshed.status == "active"


# ============================================================================
# Route Tests
# ============================================================================


class TestBudgetTransactionRoutes:
    """Test budget transaction routes."""

    def test_unauthorized_access_commit_route(self, client):
        """Test that unauthenticated requests are redirected."""
        response = client.post("/admin/grants/1/budget/lines/1/commit")
        assert response.status_code == 302  # Redirect to login

    def test_commit_budget_line_success(self, client, admin_user, grant, budget_line):
        """Test successful budget line commitment."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Commit budget
        response = client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/commit",
            json={"amount": 10000.0, "description": "Q1 commitment"},
        )
        assert response.status_code == 201
        data = response.get_json()
        assert data["success"] is True
        assert data["committed_total"] == 10000.0
        assert data["remaining"] == 40000.0

    def test_commit_budget_line_exceeds_allocated(self, client, admin_user, grant, budget_line):
        """Test that commit cannot exceed allocated amount."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Try to commit more than allocated
        response = client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/commit",
            json={"amount": 60000.0},  # Exceeds 50000.0 allocated
        )
        assert response.status_code == 400
        data = response.get_json()
        assert "exceeds allocated" in data["error"].lower()

    def test_commit_budget_line_negative_amount(self, client, admin_user, grant, budget_line):
        """Test that negative amounts are rejected."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Try to commit negative amount
        response = client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/commit",
            json={"amount": -1000.0},
        )
        assert response.status_code == 400
        assert "positive" in response.get_json()["error"].lower()

    def test_reconcile_budget_line_success(self, client, admin_user, grant, budget_line):
        """Test successful budget line reconciliation."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Reconcile expense
        response = client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/reconcile",
            json={"amount": 5000.0, "expense_id": 1, "description": "Invoice INV-001"},
        )
        assert response.status_code == 201
        data = response.get_json()
        assert data["success"] is True
        assert data["reconciled_total"] == 5000.0

    def test_reconcile_budget_line_exceeds_allocated(self, client, admin_user, grant, budget_line):
        """Test that reconciliation cannot exceed allocated amount."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Try to reconcile more than allocated
        response = client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/reconcile",
            json={"amount": 60000.0},
        )
        assert response.status_code == 400
        assert "exceeds allocated" in response.get_json()["error"].lower()

    def test_get_line_status(self, client, admin_user, grant, budget_line):
        """Test getting budget line status with variance metrics."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # First commit some funds
        client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/commit",
            json={"amount": 15000.0},
        )

        # Get status
        response = client.get(f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/status")
        assert response.status_code == 200
        data = response.get_json()
        assert data["allocated"] == 50000.0
        assert data["committed"] == 15000.0
        assert data["variance_pct"] == 30.0  # 15000/50000 = 30%

    def test_get_variance_alerts(self, client, admin_user, grant, budget_line):
        """Test getting variance alerts for grant."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Commit above threshold (>10%)
        client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/commit",
            json={"amount": 6000.0},  # 12% variance
        )

        # Get alerts
        response = client.get(f"/admin/grants/{grant.id}/budget/variance-alerts")
        assert response.status_code == 200
        data = response.get_json()
        assert data["alert_count"] > 0
        assert any(a["type"] == "over-committed" for a in data["alerts"])

    def test_list_line_transactions(self, client, admin_user, grant, budget_line):
        """Test listing transaction history for budget line."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Create multiple transactions
        client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/commit",
            json={"amount": 10000.0},
        )
        client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/reconcile",
            json={"amount": 5000.0},
        )

        # List transactions
        response = client.get(f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/transactions")
        assert response.status_code == 200
        data = response.get_json()
        assert data["transaction_count"] >= 2
        assert any(t["type"] == "commit" for t in data["transactions"])
        assert any(t["type"] == "reconcile" for t in data["transactions"])

    def test_commit_with_missing_amount(self, client, admin_user, grant, budget_line):
        """Test that missing amount is rejected."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Try to commit without amount
        response = client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/commit",
            json={"description": "No amount provided"},
        )
        assert response.status_code == 400
        assert "amount required" in response.get_json()["error"].lower()

    def test_commit_with_invalid_grant(self, client, admin_user):
        """Test that invalid grant returns 404."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Try to commit to non-existent grant
        response = client.post(
            "/admin/grants/9999/budget/lines/1/commit",
            json={"amount": 1000.0},
        )
        assert response.status_code == 404

    def test_reconcile_with_invalid_line(self, client, admin_user, grant):
        """Test that invalid budget line returns 404."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Try to reconcile to non-existent line
        response = client.post(
            f"/admin/grants/{grant.id}/budget/lines/9999/reconcile",
            json={"amount": 1000.0},
        )
        assert response.status_code == 404


# ============================================================================
# Variance Calculation Tests
# ============================================================================


class TestVarianceCalculation:
    """Test variance calculation logic."""

    def test_variance_calculation_over_committed(self, client, admin_user, grant, budget_line):
        """Test variance calculation for over-committed scenario."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Commit 20000, reconcile 5000 -> variance = 15000 (30%)
        client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/commit",
            json={"amount": 20000.0},
        )
        client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/reconcile",
            json={"amount": 5000.0},
        )

        # Get status
        response = client.get(f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/status")
        data = response.get_json()
        assert data["variance"] == 15000.0
        assert data["variance_pct"] == 30.0

    def test_variance_status_transitions(self, client, admin_user, grant, budget_line):
        """Test variance status field transitions."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Reconcile everything -> should transition to closed
        client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/reconcile",
            json={"amount": 50000.0},  # Full amount
        )

        response = client.get(f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/status")
        data = response.get_json()
        assert data["variance_status"] == "closed"

    def test_utilization_alert_approaching_limit(self, client, admin_user, grant, budget_line):
        """Test alert for approaching budget limit (>90%)."""
        # Login
        rv = client.post(
            "/auth/login",
            data={"username": admin_user.username, "password": "test_password"},
            follow_redirects=True,
        )
        assert rv.status_code == 200

        # Commit + reconcile to 91% utilization
        client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/commit",
            json={"amount": 45000.0},
        )
        client.post(
            f"/admin/grants/{grant.id}/budget/lines/{budget_line.id}/reconcile",
            json={"amount": 1000.0},
        )

        # Get alerts
        response = client.get(f"/admin/grants/{grant.id}/budget/variance-alerts")
        data = response.get_json()
        assert any(a["type"] == "approaching-limit" for a in data["alerts"])
