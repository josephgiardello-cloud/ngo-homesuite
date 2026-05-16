from __future__ import annotations

import pytest

from ngo_homesuite.models.core import GrantOpportunity, db


@pytest.fixture(scope="module")
def app(shared_test_app):
    return shared_test_app


@pytest.fixture()
def client(app):
    return app.test_client()


def _login_admin(client):
    rv = client.post(
        "/auth/login",
        data={"username": "admin", "password": "admin123!"},
        follow_redirects=False,
    )
    assert rv.status_code in (302, 303)


def test_calibrate_grants_gov_payload(client):
    _login_admin(client)
    rv = client.post(
        "/api/v2/grants/opportunities/calibrate",
        json={
            "source": "grants_gov",
            "payload": {
                "opportunityId": "GG-1001",
                "opportunityNumber": "HHS-2026-123",
                "opportunityTitle": "Community Health Expansion",
                "agencyName": "Department of Health and Human Services",
                "closeDate": "2026-12-15",
                "awardFloor": 25000,
                "awardCeiling": 100000,
                "opportunityUrl": "https://www.grants.gov/example-opportunity",
            },
        },
    )
    assert rv.status_code == 200
    payload = rv.get_json()
    assert payload["is_ready"] is True
    assert payload["score"] == 100.0
    assert payload["normalized_preview"]["external_id"] == "GG-1001"


def test_import_grants_gov_records_mode_creates_or_updates_opportunities(client, app):
    _login_admin(client)
    source_record = {
        "opportunityId": "GG-2002",
        "opportunityNumber": "HUD-2026-777",
        "opportunityTitle": "Affordable Housing Support",
        "agencyName": "Department of Housing and Urban Development",
        "closeDate": "2026-11-30",
        "awardFloor": 10000,
        "awardCeiling": 50000,
        "opportunityUrl": "https://www.grants.gov/hud-2026-777",
    }

    first = client.post(
        "/api/v2/grants/opportunities/import/grants-gov",
        json={"records": [source_record], "probability": 0.55},
    )
    assert first.status_code == 200
    first_payload = first.get_json()
    assert first_payload["imported"] == 1
    assert len(first_payload["opportunity_ids"]) == 1

    second = client.post(
        "/api/v2/grants/opportunities/import/grants-gov",
        json={"records": [source_record], "probability": 0.75},
    )
    assert second.status_code == 200
    second_payload = second.get_json()
    assert second_payload["imported"] == 1
    assert second_payload["opportunity_ids"][0] == first_payload["opportunity_ids"][0]

    with app.app_context():
        opp = db.session.get(GrantOpportunity, first_payload["opportunity_ids"][0])
        assert opp is not None
        assert opp.notes is not None
        assert "source=grants_gov" in opp.notes
        assert "external_id=GG-2002" in opp.notes
        assert float(opp.probability) == pytest.approx(0.75)


def test_import_grants_gov_records_mode_reports_calibration_failures(client):
    _login_admin(client)
    bad_record = {
        "opportunityId": "GG-MISSING",
        "opportunityTitle": "Incomplete Opportunity",
        "agencyName": "Example Agency",
    }

    rv = client.post(
        "/api/v2/grants/opportunities/import/grants-gov",
        json={"records": [bad_record]},
    )
    assert rv.status_code == 200
    payload = rv.get_json()
    assert payload["imported"] == 0
    assert payload["fetched"] == 1
    assert len(payload["calibration_failures"]) == 1
    assert "deadline" in payload["calibration_failures"][0]["missing_fields"]
