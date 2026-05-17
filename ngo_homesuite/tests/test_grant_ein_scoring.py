"""Tests for TONY-ported improvements: EIN validation and grant opportunity scoring.

Covers:
- validate_ein() — valid and invalid EIN formats
- create_opportunity() with funder_ein — stored and validated
- update_opportunity() with funder_ein — updated and validated
- score_grant_opportunity() — weighted scoring logic
"""

from __future__ import annotations

from datetime import date

import pytest

from ngo_homesuite.grants.facade import GrantsFacade
from ngo_homesuite.models.core import Organization, db
from ngo_homesuite.shared_kernel.validators import ValidationError, validate_ein
from ngo_homesuite.grants.scoring import (
    score_grant_opportunity,
    _deadline_score,
    _amount_certainty_score,
)


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


# ---------------------------------------------------------------------------
# EIN validation — pure unit tests (no DB)
# ---------------------------------------------------------------------------

class TestValidateEin:
    def test_valid_ein_passes(self):
        validate_ein("12-3456789")  # should not raise

    def test_valid_ein_with_leading_zero(self):
        validate_ein("04-1234567")

    def test_invalid_ein_missing_dash(self):
        with pytest.raises(ValidationError, match="Invalid EIN"):
            validate_ein("123456789")

    def test_invalid_ein_wrong_segment_length(self):
        with pytest.raises(ValidationError, match="Invalid EIN"):
            validate_ein("1-3456789")

    def test_invalid_ein_letters(self):
        with pytest.raises(ValidationError, match="Invalid EIN"):
            validate_ein("AB-1234567")

    def test_invalid_ein_empty(self):
        with pytest.raises(ValidationError, match="Invalid EIN"):
            validate_ein("")

    def test_invalid_ein_extra_digits(self):
        with pytest.raises(ValidationError, match="Invalid EIN"):
            validate_ein("12-34567890")


# ---------------------------------------------------------------------------
# create_opportunity — funder_ein persistence and validation
# ---------------------------------------------------------------------------

class TestCreateOpportunityWithEin:
    def test_create_opportunity_stores_valid_funder_ein(self, ctx):
        org = _mk_org("EIN Org A", "ein-org-a")
        opp = grant_service.create_opportunity(
            organization_id=org.id,
            funder_name="Maine Community Foundation",
            program_name="Housing",
            title="EIN Storage Test",
            funder_ein="04-1234567",
        )
        assert opp.funder_ein == "04-1234567"

    def test_create_opportunity_without_ein_is_allowed(self, ctx):
        org = _mk_org("EIN Org B", "ein-org-b")
        opp = grant_service.create_opportunity(
            organization_id=org.id,
            funder_name="Funder B",
            program_name="Health",
            title="No EIN Test",
        )
        assert opp.funder_ein is None

    def test_create_opportunity_rejects_invalid_ein(self, ctx):
        org = _mk_org("EIN Org C", "ein-org-c")
        with pytest.raises(ValueError, match="Invalid EIN"):
            grant_service.create_opportunity(
                organization_id=org.id,
                funder_name="Funder C",
                program_name="Education",
                title="Bad EIN Test",
                funder_ein="not-an-ein",
            )

    def test_create_opportunity_strips_whitespace_from_ein(self, ctx):
        org = _mk_org("EIN Org D", "ein-org-d")
        opp = grant_service.create_opportunity(
            organization_id=org.id,
            funder_name="Funder D",
            program_name="Youth",
            title="Whitespace EIN",
            funder_ein="  22-7654321  ",
        )
        assert opp.funder_ein == "22-7654321"

    def test_create_opportunity_empty_string_ein_stored_as_none(self, ctx):
        org = _mk_org("EIN Org E", "ein-org-e")
        opp = grant_service.create_opportunity(
            organization_id=org.id,
            funder_name="Funder E",
            program_name="Food",
            title="Empty EIN",
            funder_ein="   ",
        )
        assert opp.funder_ein is None


# ---------------------------------------------------------------------------
# update_opportunity — funder_ein update and validation
# ---------------------------------------------------------------------------

class TestUpdateOpportunityEin:
    def test_update_opportunity_sets_funder_ein(self, ctx):
        org = _mk_org("EIN Update Org A", "ein-upd-org-a")
        opp = grant_service.create_opportunity(
            organization_id=org.id,
            funder_name="Update Funder",
            program_name="Arts",
            title="EIN Update Test",
        )
        updated = grant_service.update_opportunity(
            opp.id, org.id, funder_ein="55-1234567"
        )
        assert updated.funder_ein == "55-1234567"

    def test_update_opportunity_rejects_invalid_ein(self, ctx):
        org = _mk_org("EIN Update Org B", "ein-upd-org-b")
        opp = grant_service.create_opportunity(
            organization_id=org.id,
            funder_name="Update Funder B",
            program_name="Science",
            title="EIN Update Invalid",
        )
        with pytest.raises(ValueError, match="Invalid EIN"):
            grant_service.update_opportunity(opp.id, org.id, funder_ein="bad")

    def test_update_opportunity_clears_funder_ein(self, ctx):
        org = _mk_org("EIN Update Org C", "ein-upd-org-c")
        opp = grant_service.create_opportunity(
            organization_id=org.id,
            funder_name="Update Funder C",
            program_name="Literacy",
            title="EIN Clear Test",
            funder_ein="33-9876543",
        )
        updated = grant_service.update_opportunity(
            opp.id, org.id, funder_ein=""
        )
        assert updated.funder_ein is None


# ---------------------------------------------------------------------------
# score_grant_opportunity — pure unit tests
# ---------------------------------------------------------------------------

class TestScoreGrantOpportunity:
    def test_score_has_required_keys(self):
        result = score_grant_opportunity(probability=0.5)
        assert set(result) == {
            "probability_score",
            "deadline_score",
            "amount_certainty_score",
            "stage_score",
            "priority_score",
        }

    def test_priority_score_clamped_to_unit_interval(self):
        result = score_grant_opportunity(
            probability=1.0,
            deadline=date(2099, 1, 1),
            amount_min=10000,
            amount_max=10000,
            status="awarded",
        )
        assert 0.0 <= result["priority_score"] <= 1.0

    def test_high_probability_raises_score(self):
        low = score_grant_opportunity(probability=0.1)
        high = score_grant_opportunity(probability=0.9)
        assert high["priority_score"] > low["priority_score"]

    def test_awarded_stage_raises_score_vs_identified(self):
        identified = score_grant_opportunity(status="identified")
        awarded = score_grant_opportunity(status="awarded")
        assert awarded["stage_score"] > identified["stage_score"]

    def test_far_deadline_higher_score_than_near_deadline(self):
        today = date(2026, 6, 1)
        near = score_grant_opportunity(
            probability=0.5, deadline=date(2026, 6, 5), today=today
        )
        far = score_grant_opportunity(
            probability=0.5, deadline=date(2026, 9, 1), today=today
        )
        assert far["deadline_score"] > near["deadline_score"]

    def test_past_due_deadline_is_zero(self):
        today = date(2026, 6, 1)
        result = score_grant_opportunity(
            probability=0.5, deadline=date(2026, 5, 1), today=today
        )
        assert result["deadline_score"] == 0.0

    def test_exact_amount_known_maximises_certainty(self):
        result = score_grant_opportunity(amount_min=50000, amount_max=50000)
        assert result["amount_certainty_score"] == 1.0

    def test_wide_spread_lowers_certainty(self):
        narrow = score_grant_opportunity(amount_min=40000, amount_max=50000)
        wide = score_grant_opportunity(amount_min=10000, amount_max=100000)
        assert narrow["amount_certainty_score"] > wide["amount_certainty_score"]

    def test_no_amount_info_gives_zero_certainty(self):
        result = score_grant_opportunity()
        assert result["amount_certainty_score"] == 0.0

    def test_custom_weights_applied(self):
        default = score_grant_opportunity(probability=0.8)
        custom = score_grant_opportunity(
            probability=0.8,
            weights={"probability_weight": 0.0, "deadline_weight": 0.25,
                     "amount_certainty_weight": 0.50, "stage_weight": 0.25},
        )
        # With probability_weight=0 the contribution of probability drops
        assert custom["priority_score"] != default["priority_score"]

    def test_declined_stage_scores_zero(self):
        result = score_grant_opportunity(status="declined")
        assert result["stage_score"] == 0.0
