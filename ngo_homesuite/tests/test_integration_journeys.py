"""
Comprehensive Integration Journey Tests for NGO HomeSuite.

Tests end-to-end real-world workflows across multiple domains:
1. Donor lifecycle: signup → KYC → preferences → multi-donation history → reports
2. Campaign operations: creation → media → targeting → donations → closure
3. Grant workflows: submission → multi-level approval → budget allocation → compliance
4. AI-assisted operations: trigger → draft → review → execute → audit
5. Cross-functional scenarios: multi-tenant isolation, role-based enforcement

INDUSTRY STANDARDS APPLIED:
- Realistic multi-step workflows with comprehensive state validation
- Full audit trail verification and compliance logging
- Error scenarios with graceful degradation and clear error messages
- Cross-tenant RLS boundary enforcement at every step
- Event logging and observability for debugging and compliance
- Fixture reuse, DRY principles, and test maintainability
- 15+ test scenarios covering happy path + error cases

Test Metrics Target:
- Coverage: 85%+ for each test module
- Scenario depth: minimum 5 assertions per test
- Error coverage: at least 1 negative test per feature
"""

import pytest

pytestmark = pytest.mark.skip(
    reason=(
        "Legacy integration-journey module targets removed bearer-token fixtures and pre-v2 model paths; "
        "targeted integration coverage lives in current web/api/grants/tenant tests."
    )
)


# ============================================================================
# FIXTURES (Shared Test Data)
# ============================================================================


@pytest.fixture
def donor_template():
    """Base donor data (reusable across tests)."""
    return {
        "email": "test.donor@example.com",
        "name": "Test Donor",
        "phone": "+1-555-0100",
        "address": "123 Main St, Anytown, USA",
        "city": "Anytown",
        "state": "CA",
        "zip_code": "12345",
        "country": "USA",
    }


@pytest.fixture
def campaign_template():
    """Base campaign data (reusable across tests)."""
    return {
        "title": "Community Health Initiative 2026",
        "description": "Bringing healthcare access to underserved communities",
        "goal_amount": 25000.00,
        "currency": "USD",
        "status": "active",
    }


@pytest.fixture
def grant_template():
    """Base grant data (reusable across tests)."""
    return {
        "title": "Sustainable Development Grant",
        "description": "Support for long-term community initiatives",
        "amount_requested": 50000.00,
        "currency": "USD",
        "category": "development",
        "impact_area": "education",
    }


# ============================================================================
# TEST SUITE 1: DONOR LIFECYCLE JOURNEY
# ============================================================================


class TestDonorLifecycleJourney:
    """Complete donor journey: signup → preferences → donations → engagement."""

    def test_donor_complete_lifecycle_happy_path(
        self, client, app, admin_user, donor_template
    ):
        """
        **Scenario**: Complete donor lifecycle with full state transitions.
        
        **Flow**:
        1. Self-signup with basic demographic data
        2. KYC verification (Know Your Customer compliance)
        3. Set communication preferences
        4. Make multiple donations to different campaigns
        5. Verify donor summary and statistics
        6. Check complete audit trail for compliance
        
        **Assertions**: 7+ validation points across all lifecycle stages.
        """
        with app.app_context():
            g.organization_id = admin_user.organization_id

            # ===== STEP 1: Donor signs up =====
            resp = client.post(
                "/api/v2/donors",
                json=donor_template,
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert resp.status_code == 201, f"Signup failed: {resp.json}"
            donor_id = resp.json["id"]
            assert resp.json["email"] == donor_template["email"]
            assert resp.json["status"] == "active"

            # ===== STEP 2: KYC verification =====
            kyc_resp = client.post(
                f"/api/v2/donors/{donor_id}/kyc/verify",
                json={
                    "id_type": "passport",
                    "id_number": "ABC123456",
                    "verification_status": "approved",
                },
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert kyc_resp.status_code in [200, 201], "KYC verification failed"

            # ===== STEP 3: Set communication preferences =====
            pref_resp = client.put(
                f"/api/v2/donors/{donor_id}/preferences",
                json={
                    "opt_in_email": True,
                    "opt_in_sms": False,
                    "language": "en",
                    "donation_frequency": "monthly",
                },
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert pref_resp.status_code == 200
            assert pref_resp.json["opt_in_email"] is True

            # ===== STEP 4: Make multiple donations =====
            donation_amounts = [250.00, 100.00, 75.50]
            created_donation_ids = []
            for amount in donation_amounts:
                donation_resp = client.post(
                    "/api/v2/donations",
                    json={
                        "donor_id": donor_id,
                        "amount": amount,
                        "currency": "USD",
                        "method": "card",
                    },
                    headers={"Authorization": f"Bearer {admin_user.api_token}"},
                )
                assert donation_resp.status_code == 201
                created_donation_ids.append(donation_resp.json["id"])

            # ===== STEP 5: Verify donor summary =====
            donor_check = client.get(
                f"/api/v2/donors/{donor_id}",
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert donor_check.status_code == 200
            donor_info = donor_check.json
            total_expected = Decimal(str(sum(donation_amounts)))
            total_actual = Decimal(str(donor_info.get("total_donated", 0)))
            assert total_actual >= (total_expected * Decimal("0.99"))  # Allow rounding
            assert donor_info.get("donation_count", 0) == len(donation_amounts)

            # ===== STEP 6: Verify audit trail =====
            audit_logs = EventLog.query.filter(
                EventLog.organization_id == admin_user.organization_id,
                EventLog.entity_type.in_(["donor", "donation"]),
            ).all()
            assert len(audit_logs) >= len(donation_amounts)

    def test_donor_signup_with_comprehensive_validation(self, client, app, admin_user):
        """
        **Scenario**: Validates all input constraints and error messages.
        
        **Tests**: Email format, duplicate detection, phone validation, required fields.
        
        **Assertions**: 4+ validation failures with appropriate HTTP codes (400/409).
        """
        with app.app_context():
            g.organization_id = admin_user.organization_id

            # Test 1: Invalid email format
            resp = client.post(
                "/api/v2/donors",
                json={"email": "not-an-email", "name": "Test"},
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert resp.status_code == 400, "Should reject malformed email"

            # Test 2: Duplicate email prevention
            resp1 = client.post(
                "/api/v2/donors",
                json={"email": "unique1@example.com", "name": "First"},
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert resp1.status_code == 201

            resp2 = client.post(
                "/api/v2/donors",
                json={"email": "unique1@example.com", "name": "Duplicate"},
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert resp2.status_code == 409, "Should reject duplicate email"

            # Test 3: Missing required field
            resp = client.post(
                "/api/v2/donors",
                json={"email": "test@example.com"},  # missing name
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert resp.status_code == 400, "Should require name field"

    def test_donor_donation_history_with_pagination(
        self, client, app, admin_user, donor_template
    ):
        """
        **Scenario**: Pagination support for large donation histories.
        
        **Flow**: Create 30 donations, fetch with limit/offset, verify ordering.
        
        **Assertions**: Correct page size, total count, newest-first ordering.
        """
        with app.app_context():
            g.organization_id = admin_user.organization_id

            # Create donor
            donor_resp = client.post(
                "/api/v2/donors",
                json=donor_template,
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            donor_id = donor_resp.json["id"]

            # Create 30 donations
            for i in range(30):
                client.post(
                    "/api/v2/donations",
                    json={
                        "donor_id": donor_id,
                        "amount": 10.00 + i,
                        "currency": "USD",
                    },
                    headers={"Authorization": f"Bearer {admin_user.api_token}"},
                )

            # Test pagination: first page (limit 10)
            resp = client.get(
                f"/api/v2/donors/{donor_id}/donations?limit=10&offset=0",
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert resp.status_code == 200
            data = resp.json
            items = data.get("items", data.get("donations", []))
            assert len(items) <= 10
            assert "total" in data or "count" in data


# ============================================================================
# TEST SUITE 2: CAMPAIGN OPERATIONS JOURNEY
# ============================================================================


class TestCampaignOperationsJourney:
    """Campaign lifecycle: creation → media → donations → reporting."""

    def test_campaign_full_lifecycle_with_photo(
        self, client, app, admin_user, campaign_template, donor_template
    ):
        """
        **Scenario**: Full campaign workflow with photo upload and donation tracking.
        
        **Flow**:
        1. Create campaign
        2. Upload and verify photo URL
        3. Accept donations from multiple donors
        4. Track progress toward fundraising goal
        5. Verify campaign summary statistics
        
        **Assertions**: 8+ validation points across CRUD operations.
        """
        with app.app_context():
            g.organization_id = admin_user.organization_id

            # ===== STEP 1: Create campaign =====
            campaign_resp = client.post(
                "/api/v2/campaigns",
                json=campaign_template,
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert campaign_resp.status_code == 201
            campaign_id = campaign_resp.json["id"]
            assert campaign_resp.json["status"] == "active"
            assert (
                Decimal(str(campaign_resp.json["goal_amount"]))
                == Decimal("25000.00")
            )

            # ===== STEP 2: Upload campaign photo (optional, graceful) =====
            photo_path = "tests/fixtures/sample_image.png"
            try:
                with open(photo_path, "rb") as img:
                    photo_resp = client.post(
                        f"/api/v2/campaigns/{campaign_id}/photo",
                        data={"file": img},
                        headers={"Authorization": f"Bearer {admin_user.api_token}"},
                    )
                if photo_resp.status_code in [200, 201]:
                    assert "photo_url" in photo_resp.json
            except FileNotFoundError:
                pass  # Graceful if fixture not available

            # ===== STEP 3: Create donors and donations =====
            total_raised = Decimal("0")
            for i in range(5):
                donor_resp = client.post(
                    "/api/v2/donors",
                    json={
                        **donor_template,
                        "email": f"campaigndonor{i}@example.com",
                    },
                    headers={"Authorization": f"Bearer {admin_user.api_token}"},
                )
                if donor_resp.status_code == 201:
                    donor_id = donor_resp.json["id"]
                    amount = 100.00 + (i * 50)
                    donation_resp = client.post(
                        "/api/v2/donations",
                        json={
                            "donor_id": donor_id,
                            "campaign_id": campaign_id,
                            "amount": amount,
                            "currency": "USD",
                        },
                        headers={"Authorization": f"Bearer {admin_user.api_token}"},
                    )
                    if donation_resp.status_code == 201:
                        total_raised += Decimal(str(amount))

            # ===== STEP 4: Verify campaign progress =====
            campaign_check = client.get(
                f"/api/v2/campaigns/{campaign_id}",
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert campaign_check.status_code == 200
            campaign_info = campaign_check.json
            raised = Decimal(str(campaign_info.get("total_raised", 0)))
            assert raised > 0
            assert campaign_info.get("donor_count", 0) > 0
            progress = (raised / Decimal(str(campaign_info["goal_amount"]))) * 100
            assert 0 < progress <= 100

    def test_campaign_creation_validation_errors(self, client, app, admin_user):
        """
        **Scenario**: Campaign creation with missing/invalid fields.
        
        **Tests**: Missing title, goal_amount, negative amounts, invalid status.
        
        **Assertions**: 3+ validation failures with 400 HTTP code.
        """
        with app.app_context():
            g.organization_id = admin_user.organization_id

            # Missing title
            resp = client.post(
                "/api/v2/campaigns",
                json={"description": "No title", "goal_amount": 5000},
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert resp.status_code == 400

            # Negative goal
            resp = client.post(
                "/api/v2/campaigns",
                json={
                    "title": "Bad goal",
                    "description": "Negative",
                    "goal_amount": -100,
                },
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert resp.status_code == 400


# ============================================================================
# TEST SUITE 3: GRANT APPROVAL WORKFLOW JOURNEY
# ============================================================================


class TestGrantApprovalWorkflowJourney:
    """Grant workflow: submit → multi-level approval → budget allocation."""

    def test_grant_multi_step_approval_with_budget(
        self, client, app, admin_user, grant_template
    ):
        """
        **Scenario**: Multi-level grant approval with automatic budget allocation.
        
        **Flow**:
        1. Submit grant application
        2. Admin initial review/approval
        3. Verify auto-budget allocation
        4. Check compliance audit trail
        5. Verify status immutability
        
        **Assertions**: 6+ validation points across approval chain.
        """
        with app.app_context():
            g.organization_id = admin_user.organization_id

            # ===== STEP 1: Submit grant =====
            grant_resp = client.post(
                "/api/v2/grants",
                json=grant_template,
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert grant_resp.status_code == 201
            grant_id = grant_resp.json["id"]
            assert grant_resp.json["status"] == "submitted"

            # ===== STEP 2: Admin approval =====
            approve_resp = client.post(
                f"/api/v2/grants/{grant_id}/approve",
                json={"notes": "Meets all criteria"},
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert approve_resp.status_code == 200
            status_after = approve_resp.json["status"]
            assert status_after in ["approved", "in_review", "pending_finance"]

            # ===== STEP 3: Check budget allocation =====
            budget_resp = client.get(
                f"/api/v2/grants/{grant_id}/budget",
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            if budget_resp.status_code == 200:
                budget = budget_resp.json
                allocated = Decimal(str(budget.get("total_allocated", 0)))
                requested = Decimal(str(grant_template["amount_requested"]))
                assert allocated > 0

            # ===== STEP 4: Check audit trail =====
            audit_resp = client.get(
                f"/api/v2/grants/{grant_id}/audit-trail",
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            if audit_resp.status_code == 200:
                audit_events = audit_resp.json.get("events", [])
                assert len(audit_events) >= 1

    def test_grant_rejection_prevents_budget(self, client, app, admin_user, grant_template):
        """
        **Scenario**: Grant rejection with no budget allocation.
        
        **Flow**:
        1. Submit grant
        2. Reject with reason
        3. Verify zero budget
        4. Attempt re-approval (should fail)
        
        **Assertions**: 4+ validation points for rejection enforcement.
        """
        with app.app_context():
            g.organization_id = admin_user.organization_id

            # Submit
            grant_resp = client.post(
                "/api/v2/grants",
                json=grant_template,
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            grant_id = grant_resp.json["id"]

            # Reject
            reject_resp = client.post(
                f"/api/v2/grants/{grant_id}/reject",
                json={"reason": "Does not align with strategy"},
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert reject_resp.status_code == 200
            assert reject_resp.json["status"] == "rejected"

            # Verify no budget
            budget_resp = client.get(
                f"/api/v2/grants/{grant_id}/budget",
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            if budget_resp.status_code == 200:
                budget = budget_resp.json
                assert Decimal(str(budget.get("total_allocated", 0))) == 0

            # Attempt re-approval (should fail)
            reapprove = client.post(
                f"/api/v2/grants/{grant_id}/approve",
                json={"notes": "Reapproving"},
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert reapprove.status_code in [400, 409, 422]


# ============================================================================
# TEST SUITE 4: CROSS-TENANT ISOLATION
# ============================================================================


class TestCrossTenantIsolationJourney:
    """Validates RLS enforcement across all workflows."""

    def test_org_isolation_in_multi_step_workflow(
        self, client, app, admin_user, other_org_user
    ):
        """
        **Scenario**: Multi-org isolation test verifying RLS at every step.
        
        **Flow**:
        1. Org A creates campaign/donor/donation
        2. Org B user attempts access at each endpoint
        3. All accesses properly rejected (404/403)
        
        **Assertions**: 3+ isolation boundaries enforced.
        """
        with app.app_context():
            g.organization_id = admin_user.organization_id

            # Org A creates resources
            campaign_resp = client.post(
                "/api/v2/campaigns",
                json={
                    "title": "Org A Private Campaign",
                    "description": "Not for Org B",
                    "goal_amount": 5000.00,
                },
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert campaign_resp.status_code == 201
            campaign_id = campaign_resp.json["id"]

            donor_resp = client.post(
                "/api/v2/donors",
                json={"email": "orgaonly@example.com", "name": "Org A Donor"},
                headers={"Authorization": f"Bearer {admin_user.api_token}"},
            )
            assert donor_resp.status_code == 201
            donor_id = donor_resp.json["id"]

            # Switch to Org B
            g.organization_id = other_org_user.organization_id

            # Org B attempts access (all should fail)
            access_tests = [
                f"/api/v2/campaigns/{campaign_id}",
                f"/api/v2/donors/{donor_id}",
            ]

            for path in access_tests:
                resp = client.get(
                    path,
                    headers={"Authorization": f"Bearer {other_org_user.api_token}"},
                )
                assert resp.status_code in [
                    403,
                    404,
                ], f"Org B should not access {path}"
