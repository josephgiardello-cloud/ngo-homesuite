"""Tests for E-1: Campaign Projection Engine."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta

import pytest

from ngo_homesuite.app_factory import create_app
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import Campaign, Donation, Donor, Organization, User, db
from ngo_homesuite.services.campaign_projection_service import (
    project_campaign,
    project_with_conversion_boost,
)


@pytest.fixture(scope='module')
def app():
    return create_app(TestingConfig)


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def org(app):
    with app.app_context():
        o = Organization.query.filter_by(name='ProjTestOrg').first()
        if o is None:
            o = Organization(name='ProjTestOrg', slug='projtestorg', email='proj@test.local')
            db.session.add(o)
            db.session.commit()
        yield o


@pytest.fixture()
def campaign_with_history(app, org):
    """Campaign with 10 days of donations trending upward."""
    with app.app_context():
        o = Organization.query.filter_by(name='ProjTestOrg').first()
        c = Campaign.query.filter_by(name='ProjTestCampaign').first()
        if c is None:
            c = Campaign(
                organization_id=o.id,
                name='ProjTestCampaign',
                slug='projtest-campaign',
                goal_amount=10_000.0,
                raised_amount=0.0,
                status='active',
                start_date=date.today() - timedelta(days=10),
                end_date=date.today() + timedelta(days=20),
            )
            db.session.add(c)
            db.session.flush()

            total = 0.0
            for i in range(10):
                amt = 100.0 + i * 20.0
                total += amt
                d = Donation(
                    organization_id=o.id,
                    campaign_id=c.id,
                    donor_name='Test Donor',
                    amount=amt,
                    donation_date=datetime.now() - timedelta(days=9 - i),
                    status='received',
                )
                db.session.add(d)
            c.raised_amount = total
            db.session.commit()
        yield c


@pytest.fixture()
def campaign_no_history(app, org):
    """Campaign with no donations."""
    with app.app_context():
        o = Organization.query.filter_by(name='ProjTestOrg').first()
        c = Campaign.query.filter_by(name='ProjTestEmpty').first()
        if c is None:
            c = Campaign(
                organization_id=o.id,
                name='ProjTestEmpty',
                slug='projtest-empty',
                goal_amount=5_000.0,
                raised_amount=0.0,
                status='active',
                start_date=date.today() - timedelta(days=3),
                end_date=date.today() + timedelta(days=27),
            )
            db.session.add(c)
            db.session.commit()
        yield c


# ---------------------------------------------------------------------------
# Unit Tests
# ---------------------------------------------------------------------------

class TestProjectCampaign:
    def test_raises_for_unknown_campaign(self, app, org):
        with app.app_context():
            o = Organization.query.filter_by(name='ProjTestOrg').first()
            with pytest.raises(ValueError, match='not found'):
                project_campaign(999_999, o.id)

    def test_raises_for_wrong_org(self, app, campaign_with_history):
        with app.app_context():
            c = Campaign.query.filter_by(name='ProjTestCampaign').first()
            with pytest.raises(ValueError):
                project_campaign(c.id, 999_999)

    def test_insufficient_data_method_for_empty_campaign(self, app, campaign_no_history):
        with app.app_context():
            c = Campaign.query.filter_by(name='ProjTestEmpty').first()
            o = db.session.get(Organization, c.organization_id)
            result = project_campaign(c.id, o.id)
        assert result['method'] == 'insufficient_data'
        assert result['projected_raised'] == result['raised_to_date']
        assert result['days_to_goal'] is None

    def test_regression_method_for_rich_history(self, app, campaign_with_history):
        with app.app_context():
            c = Campaign.query.filter_by(name='ProjTestCampaign').first()
            o = db.session.get(Organization, c.organization_id)
            result = project_campaign(c.id, o.id)
        assert result['method'] == 'regression'
        assert result['projected_raised'] >= result['raised_to_date']
        assert result['confidence_low'] <= result['projected_raised']
        assert result['confidence_high'] >= result['projected_raised']
        assert result['goal_amount'] == 10_000.0

    def test_result_keys_present(self, app, campaign_with_history):
        with app.app_context():
            c = Campaign.query.filter_by(name='ProjTestCampaign').first()
            o = db.session.get(Organization, c.organization_id)
            result = project_campaign(c.id, o.id)
        required = {
            'campaign_id', 'raised_to_date', 'goal_amount', 'projected_raised',
            'confidence_low', 'confidence_high', 'days_to_goal', 'on_pace',
            'method', 'days_elapsed', 'days_remaining',
        }
        assert required <= result.keys()

    def test_on_pace_reflects_projection(self, app, campaign_with_history):
        with app.app_context():
            c = Campaign.query.filter_by(name='ProjTestCampaign').first()
            o = db.session.get(Organization, c.organization_id)
            result = project_campaign(c.id, o.id)
        assert result['on_pace'] == (result['projected_raised'] >= result['goal_amount'])


class TestProjectWithBoost:
    def test_boost_increases_projected(self, app, campaign_with_history):
        with app.app_context():
            c = Campaign.query.filter_by(name='ProjTestCampaign').first()
            o = db.session.get(Organization, c.organization_id)
            base = project_campaign(c.id, o.id)
            boosted = project_with_conversion_boost(c.id, o.id, 50.0)

        assert boosted['projected_raised'] >= base['projected_raised']
        assert boosted['method'].startswith('boosted_')
        assert boosted['boost_pct'] == 50.0

    def test_negative_boost_reduces_projected(self, app, campaign_with_history):
        with app.app_context():
            c = Campaign.query.filter_by(name='ProjTestCampaign').first()
            o = db.session.get(Organization, c.organization_id)
            base = project_campaign(c.id, o.id)
            boosted = project_with_conversion_boost(c.id, o.id, -50.0)

        assert boosted['projected_raised'] <= base['projected_raised']

    def test_invalid_boost_raises(self, app, campaign_with_history):
        with app.app_context():
            c = Campaign.query.filter_by(name='ProjTestCampaign').first()
            o = db.session.get(Organization, c.organization_id)
            with pytest.raises(ValueError):
                project_with_conversion_boost(c.id, o.id, -100.0)


class TestProjectionApiEndpoint:
    def _force_login(self, client, app, user_id):
        """Bypass login form by injecting Flask-Login session directly."""
        with client.session_transaction() as sess:
            sess['_user_id'] = str(user_id)
            sess['_fresh'] = True

    def _make_staff(self, app, username='proj_staff_user'):
        """Create a staff user (staff is in roles_required for projection endpoint)."""
        with app.app_context():
            u = User.query.filter_by(username=username).first()
            if u is None:
                u = User(username=username, email=f'{username}@test.local', role='staff', is_active=True)
                u.set_password('Staff1234!')
                db.session.add(u)
                db.session.commit()
            return u.id

    def test_projection_endpoint_requires_login(self, client, campaign_with_history):
        with client.application.app_context():
            c = Campaign.query.filter_by(name='ProjTestCampaign').first()
            cid = c.id
        resp = client.get(f'/api/v2/campaigns/{cid}/projection')
        assert resp.status_code in (302, 401)

    def test_projection_endpoint_returns_data(self, client, app, campaign_with_history):
        uid = self._make_staff(app)
        with app.app_context():
            c = Campaign.query.filter_by(name='ProjTestCampaign').first()
            cid = c.id
        self._force_login(client, app, uid)
        resp = client.get(f'/api/v2/campaigns/{cid}/projection')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data is not None
        assert 'projected_raised' in data

    def test_projection_endpoint_404_for_bad_campaign(self, client, app):
        uid = self._make_staff(app)
        self._force_login(client, app, uid)
        resp = client.get('/api/v2/campaigns/999999/projection')
        assert resp.status_code == 404
