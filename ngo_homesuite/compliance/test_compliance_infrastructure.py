"""Test suite for compliance infrastructure.

Validates GAAP, risk scoring, grant validation, P2P fraud detection,
and continuous monitoring.
"""

import pytest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

from ngo_homesuite.models.core import (
    Organization, User, Donation, Donor, Fund, Grant, Expense, Project, P2PPage, db
)
from ngo_homesuite.compliance.gaap_validator import GaapComplianceValidator
from ngo_homesuite.compliance.risk_scoring import RiskScoringEngine
from ngo_homesuite.compliance.grant_validator import GrantPreSubmissionValidator
from ngo_homesuite.compliance.p2p_fraud_detector import P2PFraudDetector
from ngo_homesuite.compliance.monitoring import ComplianceMonitoringService


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def app(shared_test_app):
    return shared_test_app


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


@pytest.fixture
def org(ctx):
    """Create test organization."""
    suffix = uuid.uuid4().hex[:8]
    org = Organization(
        name=f"Test NGO {suffix}",
        slug=f"test-ngo-{suffix}",
        email="test@ngo.org",
        is_active=True
    )
    db.session.add(org)
    db.session.flush()
    return org


@pytest.fixture
def unrestricted_fund(org):
    """Create unrestricted fund."""
    fund = Fund(
        name="General Operations",
        organization_id=org.id,
        description="General unrestricted operations fund",
    )
    db.session.add(fund)
    db.session.flush()
    return fund


@pytest.fixture
def restricted_fund(org):
    """Create restricted fund."""
    fund = Fund(
        name="Education Program",
        organization_id=org.id,
        description="Restricted-style education program fund",
    )
    db.session.add(fund)
    db.session.flush()
    return fund


class TestGaapValidator:
    """Test GAAP compliance validator."""
    
    def test_restricted_donation_requires_fund(self, org, unrestricted_fund):
        """Restricted donations must be allocated to a fund."""
        donation = Donation(
            organization_id=org.id,
            donor_id=None,
            donor_name="Anonymous Restricted Donor",
            amount=Decimal("500.00"),
            donation_date=_now(),
        )
        donation.is_restricted = True
        db.session.add(donation)
        db.session.flush()
        
        violations = GaapComplianceValidator.validate_donation(donation)
        assert any(v.code == "GAAP_RESTRICTED_FUND_REQUIRED" for v in violations)
    
    def test_donation_amount_must_be_positive(self, org):
        """Donation amount validation."""
        donation = Donation(
            organization_id=org.id,
            donor_name="Negative Amount Donor",
            amount=Decimal("-100.00"),
            donation_date=_now(),
        )
        db.session.add(donation)
        db.session.flush()
        
        violations = GaapComplianceValidator.validate_donation(donation)
        assert any(v.code == "GAAP_INVALID_AMOUNT" for v in violations)
    
    def test_expense_requires_allocation(self, org):
        """Expense must be allocated to fund or project."""
        expense = Expense(
            organization_id=org.id,
            amount=Decimal("100.00"),
            paid_at=_now(),
        )
        db.session.add(expense)
        db.session.flush()
        
        violations = GaapComplianceValidator.validate_expense(expense)
        assert any(v.code == "GAAP_EXPENSE_ALLOCATION_REQUIRED" for v in violations)
    
    def test_fund_balance_integrity(self, org, unrestricted_fund):
        """Fund balance should match inflow - outflow."""
        donation = Donation(
            organization_id=org.id,
            fund_id=unrestricted_fund.id,
            donor_name="Funded Donor",
            amount=Decimal("500.00"),
            donation_date=_now(),
        )
        db.session.add(donation)
        db.session.flush()
        
        # Don't add expense; just validate
        violations = GaapComplianceValidator.validate_fund(unrestricted_fund)
        # This schema revision has no persisted fund balance field; mismatch is expected.
        assert any(v.code == "GAAP_FUND_BALANCE_MISMATCH" for v in violations)


class TestRiskScoring:
    """Test unified risk scoring engine."""
    
    def test_donation_risk_unusual_amount(self, org, unrestricted_fund):
        """Large donations should be flagged as higher risk."""
        # Create baseline donations
        for i in range(5):
            d = Donation(
                organization_id=org.id,
                fund_id=unrestricted_fund.id,
                donor_name=f"Baseline Donor {i}",
                amount=Decimal("100.00"),
                donation_date=_now(),
            )
            db.session.add(d)
        db.session.flush()
        
        # Create large donation
        large_donation = Donation(
            organization_id=org.id,
            fund_id=unrestricted_fund.id,
            donor_name="Large Donor",
            amount=Decimal("10000.00"),
            donation_date=_now(),
        )
        db.session.add(large_donation)
        db.session.flush()
        
        assessment = RiskScoringEngine.assess_donation(large_donation)
        assert assessment.overall_score > 0
        assert len(assessment.factors) > 0
    
    def test_grant_risk_overdue(self, org):
        """Overdue grant should be flagged."""
        grant = Grant(
            organization_id=org.id,
            title="Test Grant",
            funder_name="Test Foundation",
            application_deadline=(_now() - timedelta(days=10)).date(),
            status="in_progress",
            amount_requested=Decimal("50000.00"),
        )
        db.session.add(grant)
        db.session.flush()
        
        assessment = RiskScoringEngine.assess_grant(grant)
        assert assessment.overall_score > 30
        assert assessment.severity in ["high", "critical"]


class TestGrantValidator:
    """Test grant pre-submission validator."""
    
    def test_incomplete_grant_fails_validation(self, org):
        """Grant missing required fields fails validation."""
        grant = Grant(
            organization_id=org.id,
            title="",  # Empty title
            funder_name="",
            amount_requested=None,
            application_deadline=None,
            status="draft",
        )
        db.session.add(grant)
        db.session.flush()
        
        readiness = GrantPreSubmissionValidator.get_readiness_score(grant)
        assert readiness["status"] == "NOT_READY"
        assert readiness["blocking_issues"] > 0
    
    def test_complete_grant_passes_validation(self, org, unrestricted_fund):
        """Grant with all required fields passes validation."""
        project = Project(
            organization_id=org.id,
            name="Education Initiative",
            description=(
                "A comprehensive educational program spanning multiple regions with clear staffing, "
                "curriculum, monitoring, and long-term sustainability commitments for underserved communities."
            ),
            status="planning",
        )
        db.session.add(project)
        db.session.flush()
        
        grant = Grant(
            organization_id=org.id,
            title="Education Funding Grant",
            funder_name="Global Foundation",
            description=(
                "Request for funding a comprehensive education initiative across five regions with measurable "
                "outcomes, implementation milestones, governance controls, and a detailed delivery plan."
            ),
            amount_requested=Decimal("100000.00"),
            application_deadline=(_now() + timedelta(days=30)).date(),
            start_date=(_now() + timedelta(days=60)).date(),
            end_date=(_now() + timedelta(days=420)).date(),
            project_id=project.id,
            status="draft",
            requirements="Quarterly reports with student outcome metrics",
        )
        db.session.add(grant)
        db.session.flush()
        
        readiness = GrantPreSubmissionValidator.get_readiness_score(grant)
        assert readiness["status"] == "READY"
        assert readiness["blocking_issues"] == 0


class TestP2PFraudDetector:
    """Test P2P fraud detection."""
    
    def test_rapid_donations_flagged(self, org):
        """Multiple donations in same minute flagged."""
        donor = Donor(organization_id=org.id, name="Test Donor")
        db.session.add(donor)
        db.session.flush()

        page = P2PPage(
            organization_id=org.id,
            donor_id=donor.id,
            title="Education Campaign",
            status="active",
            public_slug=f"education-campaign-{uuid.uuid4().hex[:8]}",
            created_at=_now(),
        )
        db.session.add(page)
        db.session.flush()

        now = _now()
        
        # Create 3 donations in same minute
        for i in range(3):
            donation = Donation(
                organization_id=org.id,
                donor_id=donor.id,
                donor_name=donor.name,
                amount=Decimal("100.00"),
                donation_date=now,
            )
            db.session.add(donation)
        db.session.flush()
        
        alerts = P2PFraudDetector.analyze_p2p_page(page)
        assert any(a.alert_type == "suspicious_timing" for a in alerts)
    
    def test_duplicate_donations_flagged(self, org):
        """Duplicate donations within short window flagged."""
        donor = Donor(organization_id=org.id, name="Duplicate Donor")
        db.session.add(donor)
        db.session.flush()

        page = P2PPage(
            organization_id=org.id,
            donor_id=donor.id,
            title="Campaign",
            status="active",
            public_slug=f"campaign-{uuid.uuid4().hex[:8]}",
            created_at=_now(),
        )
        db.session.add(page)
        db.session.flush()

        now = _now()
        
        # Create duplicate donations
        for i in range(2):
            donation = Donation(
                organization_id=org.id,
                donor_id=donor.id,
                donor_name=donor.name,
                amount=Decimal("500.00"),
                donation_date=now + timedelta(minutes=i),
            )
            db.session.add(donation)
        db.session.flush()
        
        alerts = P2PFraudDetector.analyze_p2p_page(page)
        assert any(a.alert_type == "duplicate_donation" for a in alerts)
    
    def test_fraud_score_calculation(self, org):
        """Fraud score properly calculated."""
        donor = Donor(organization_id=org.id, name="Score Donor")
        db.session.add(donor)
        db.session.flush()

        page = P2PPage(
            organization_id=org.id,
            donor_id=donor.id,
            title="Campaign",
            status="active",
            public_slug=f"score-campaign-{uuid.uuid4().hex[:8]}",
            created_at=_now(),
        )
        db.session.add(page)
        db.session.flush()
        
        fraud_score = P2PFraudDetector.get_fraud_score(page)
        assert 0 <= fraud_score["fraud_score"] <= 100
        assert fraud_score["risk_level"] in ["low", "medium", "high", "critical"]


class TestComplianceMonitoring:
    """Test continuous compliance monitoring."""
    
    def test_full_audit_completes(self, org):
        """Full compliance audit runs without error."""
        findings = ComplianceMonitoringService.run_full_compliance_audit(org.id)
        
        assert "summary" in findings["audit_items"]
        assert "overall_compliance_status" in findings["audit_items"]["summary"]
        assert findings["audit_items"]["summary"]["overall_compliance_status"] in [
            "compliant", "minor_issues", "moderate_issues", "critical_issues"
        ]
    
    def test_grant_deadline_detection(self, org):
        """Grant deadline alerts properly detected."""
        # Create overdue grant
        overdue_grant = Grant(
            organization_id=org.id,
            title="Overdue Grant",
            funder_name="Foundation",
            application_deadline=(_now() - timedelta(days=5)).date(),
            status="in_progress",
            amount_requested=Decimal("10000.00"),
        )
        db.session.add(overdue_grant)
        
        # Create upcoming grant
        upcoming_grant = Grant(
            organization_id=org.id,
            title="Upcoming Grant",
            funder_name="Foundation",
            application_deadline=(_now() + timedelta(days=3)).date(),
            status="draft",
            amount_requested=Decimal("10000.00"),
        )
        db.session.add(upcoming_grant)
        db.session.flush()
        
        alerts = ComplianceMonitoringService.check_grant_deadlines(org.id)
        assert len(alerts["critical"]) > 0
        assert len(alerts["urgent"]) > 0
    
    def test_compliance_drift_detection(self, org, unrestricted_fund):
        """Compliance drift indicators properly detected."""
        # Create many unallocated donations
        for i in range(10):
            donation = Donation(
                organization_id=org.id,
                donor_name=f"Unallocated Donor {i}",
                amount=Decimal("1000.00"),
                donation_date=_now() - timedelta(days=i),
            )
            db.session.add(donation)
        db.session.flush()
        
        drift = ComplianceMonitoringService.detect_compliance_drift(org.id)
        assert "indicators" in drift
        # Should detect low allocation rate
        assert any(ind["type"] == "low_fund_allocation" for ind in drift["indicators"])
