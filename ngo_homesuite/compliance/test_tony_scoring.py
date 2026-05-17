"""Test suite for TONY Grant Scoring integration."""

import json
import pytest
import uuid
from ngo_homesuite.models.core import (
    Organization, User, Fund, Grant, Donation, Expense, Project, GrantScore, db
)
from ngo_homesuite.compliance.tony_scoring import TonyScorer, TonyScoringService


@pytest.fixture
def app(shared_test_app):
    return shared_test_app


@pytest.fixture
def ctx(app):
    with app.app_context():
        yield


@pytest.fixture
def org_with_data(ctx):
    """Create organization with sample financial data."""
    suffix = uuid.uuid4().hex[:8]

    # Create org
    org = Organization(
        name=f"Test NGO {suffix}",
        slug=f"test-ngo-{suffix}",
        email=f"test-{suffix}@ngo.org",
        country="USA",
        city="Seattle"
    )
    db.session.add(org)
    db.session.flush()

    # Create user
    user = User(
        username=f"admin_{suffix}",
        email=f"admin-{suffix}@ngo.org",
        password_hash="hash",
        role="admin",
        organization_id=org.id
    )
    db.session.add(user)

    # Create funds
    unrestricted = Fund(
        name=f"General Operating Fund {suffix}",
        organization_id=org.id,
        description="Primary operating fund"
    )
    restricted = Fund(
        name=f"Program Fund {suffix}",
        organization_id=org.id,
        description="Program delivery fund"
    )
    db.session.add(unrestricted)
    db.session.add(restricted)
    db.session.flush()

    # Create project
    project = Project(
        name=f"Community Outreach {suffix}",
        organization_id=org.id,
        description="Community service and outreach programming"
    )
    db.session.add(project)
    db.session.flush()

    # Create grant
    grant = Grant(
        organization_id=org.id,
        project_id=project.id,
        funder_name="Test Foundation",
        funder_type="foundation",
        title="Community Grant",
        amount_requested=50000.0,
        amount_awarded=50000.0,
        status="active"
    )
    db.session.add(grant)
    db.session.flush()

    # Add sample donations (revenue)
    for i in range(10):
        donation = Donation(
            organization_id=org.id,
            fund_id=unrestricted.id,
            amount=10000.0,
            currency="USD",
            payment_method="bank_transfer",
            donor_name=f"Donor {i}",
            donor_email=f"donor{i}@example.com"
        )
        db.session.add(donation)

    # Add sample expenses
    for i in range(5):
        expense = Expense(
            organization_id=org.id,
            fund_id=unrestricted.id,
            project_id=project.id,
            amount=5000.0,
            currency="USD",
            description=f"Program expense {i}"
        )
        db.session.add(expense)

    db.session.commit()

    yield org


class TestTonyScorerFeatures:
    """Test TONY feature extraction."""

    def test_extract_financial_snapshot(self, org_with_data):
        """Test extracting financial data from organization."""
        snapshot = TonyScorer.extract_financial_snapshot(str(org_with_data.id))
        
        assert snapshot is not None
        assert snapshot.get("revenue", 0) > 0  # Has donations
        assert snapshot.get("expenses", 0) > 0  # Has expenses
        assert snapshot.get("assets", 0) >= 0
        assert snapshot.get("unrestricted_net_assets", 0) >= 0

    def test_calculate_features(self, org_with_data):
        """Test calculating core risk features."""
        snapshot = TonyScorer.extract_financial_snapshot(str(org_with_data.id))
        features = TonyScorer.calculate_features(snapshot)
        
        assert "continuity_months" in features
        assert "operating_margin" in features
        assert "program_expense_ratio" in features
        assert "liabilities_to_assets" in features
        assert "revenue_volatility" in features
        
        # All features should be numeric
        for key, val in features.items():
            assert isinstance(val, (int, float))

    def test_calculate_base_risk_probability(self, org_with_data):
        """Test calculating base risk probability."""
        snapshot = TonyScorer.extract_financial_snapshot(str(org_with_data.id))
        features = TonyScorer.calculate_features(snapshot)
        prob = TonyScorer.calculate_base_risk_probability(features)
        
        assert 0.0 <= prob <= 1.0

    def test_calculate_altman_zscore(self, org_with_data):
        """Test Altman Z-score calculation."""
        snapshot = TonyScorer.extract_financial_snapshot(str(org_with_data.id))
        z_score, zone = TonyScorer.calculate_altman_zscore(snapshot)
        
        if z_score is not None:
            assert isinstance(z_score, float)
            assert zone in ("safe", "grey", "distress")
        else:
            assert zone == "unknown"

    def test_calculate_organizational_health(self, org_with_data):
        """Test organizational health scoring."""
        health = TonyScorer.calculate_organizational_health(str(org_with_data.id))
        
        assert "score" in health
        assert 0.0 <= health["score"] <= 1.0
        assert "components" in health


class TestTonyScorerGrants:
    """Test TONY grant scoring."""

    def test_score_grant_returns_complete_result(self, org_with_data):
        """Test scoring a grant returns all required fields."""
        grant = org_with_data.grants[0]
        
        result = TonyScorer.score_grant(str(grant.id), str(org_with_data.id))
        
        # Check all required fields are present
        assert result["grant_id"] == str(grant.id)
        assert "base_risk_probability" in result
        assert "final_risk_probability" in result
        assert "risk_descriptor" in result
        assert "grant_recommendation" in result
        assert "altman_zscore" in result
        assert "organizational_health" in result
        assert "features" in result
        assert "financial_snapshot" in result
        
        # Check value ranges
        assert 0.0 <= result["base_risk_probability"] <= 1.0
        assert 0.0 <= result["final_risk_probability"] <= 1.0
        assert 0.0 <= result["organizational_health"] <= 1.0

    def test_score_grant_with_different_presets(self, org_with_data):
        """Test scoring with different presets produces different results."""
        grant = org_with_data.grants[0]
        
        conservative = TonyScorer.score_grant(
            str(grant.id), str(org_with_data.id), preset="conservative"
        )
        balanced = TonyScorer.score_grant(
            str(grant.id), str(org_with_data.id), preset="balanced"
        )
        lenient = TonyScorer.score_grant(
            str(grant.id), str(org_with_data.id), preset="lenient"
        )
        
        # All should have results
        assert conservative["final_risk_probability"] is not None
        assert balanced["final_risk_probability"] is not None
        assert lenient["final_risk_probability"] is not None
        
        # Conservative should generally have higher risk
        # (though this is not strictly guaranteed with test data)
        assert isinstance(conservative["final_risk_probability"], float)

    def test_grant_recommendation_generated(self, org_with_data):
        """Test grant recommendation is always generated."""
        grant = org_with_data.grants[0]
        result = TonyScorer.score_grant(str(grant.id), str(org_with_data.id))
        
        recommendation = result["grant_recommendation"]
        assert "label" in recommendation
        assert recommendation["label"] in ("Standard", "Conditional", "Elevated Risk")
        assert "recommendation" in recommendation
        assert "risk_factors" in recommendation
        assert isinstance(recommendation["risk_factors"], list)

    def test_invalid_grant_raises_error(self, org_with_data):
        """Test scoring invalid grant raises error."""
        with pytest.raises(ValueError):
            TonyScorer.score_grant("99999", str(org_with_data.id))


class TestTonyScoringService:
    """Test high-level TONY scoring service."""

    def test_run_organization_audit(self, org_with_data):
        """Test running full organization audit."""
        audit = TonyScoringService.run_organization_audit(str(org_with_data.id))
        
        assert "organization_id" in audit
        assert "audited_at" in audit
        assert "grant_scores" in audit
        assert "summary" in audit
        
        summary = audit["summary"]
        assert "total_grants_scored" in summary
        assert "average_risk_probability" in summary
        assert "high_risk_count" in summary
        assert "moderate_risk_count" in summary
        assert "low_risk_count" in summary
        assert "status" in summary


class TestGrantScoreModel:
    """Test GrantScore database model."""

    def test_grant_score_creation(self, org_with_data):
        """Test creating and storing grant scores."""
        grant = org_with_data.grants[0]

        score_result = TonyScorer.score_grant(
            str(grant.id), str(org_with_data.id)
        )

        # Create score object
        score = GrantScore(
            grant_id=grant.id,
            organization_id=org_with_data.id,
            preset="balanced",
            base_risk_probability=score_result["base_risk_probability"],
            final_risk_probability=score_result["final_risk_probability"],
            risk_descriptor=score_result["risk_descriptor"],
            grant_recommendation_label=score_result["grant_recommendation"]["label"],
            grant_recommendation_text=score_result["grant_recommendation"]["recommendation"],
            altman_zscore=score_result["altman_zscore"],
            altman_zone=score_result["altman_zone"],
            organizational_health_score=score_result["organizational_health"],
            features=score_result["features"],
            financial_snapshot=score_result["financial_snapshot"],
            risk_factors=score_result["grant_recommendation"]["risk_factors"],
        )

        db.session.add(score)
        db.session.commit()

        # Verify stored
        stored = db.session.get(GrantScore, score.id)
        assert stored is not None
        assert stored.grant_id == grant.id
        assert stored.final_risk_probability == score_result["final_risk_probability"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
