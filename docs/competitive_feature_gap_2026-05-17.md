# NGO HomeSuite Competitive Feature Gap (May 17, 2026)

## Summary
This matrix validates which features are already present, partially present, or still missing versus the requested comparison baseline.

## Feature Status Matrix
| Feature | Status | Evidence | Notes |
|---|---|---|---|
| Real-time chat | Partial | `ngo_homesuite/web/ai_routes.py` | AI chat endpoints exist, but no user-to-user realtime messaging system (channels/presence/message bus). |
| Email campaign sending | Partial | `ngo_homesuite/services/reminder_service.py`, `ngo_homesuite/services/stewardship_service.py` | Transactional/reminder email exists; no audience-based bulk campaign composer, send, and analytics flow. |
| Public registration pages | Exists | `ngo_homesuite/web/auth_routes.py`, `ngo_homesuite/web/main_routes.py`, `ngo_homesuite/web/p2p_routes.py` | Public account registration, public donation page, and public P2P pages are already implemented. |
| Project management | Partial | `ngo_homesuite/models/core.py`, `ngo_homesuite/web/tasks_routes.py` | Project entity and tasks exist; not yet a full PM suite (milestones/board views/dependencies). |
| Group management | Exists | `ngo_homesuite/services/smart_groups_service.py`, `ngo_homesuite/web/smart_groups_routes.py` | Smart Groups / dynamic audience CRUD + evaluation already present. |
| Dashboard charts | Exists | `ngo_homesuite/web/main_routes.py`, `ngo_homesuite/web/templates/reports.html` | Report charts are present via Chart.js (plus TONY dashboard charts). |
| Duplicate detection | Partial | `ngo_homesuite/services/donor_service.py`, `ngo_homesuite/compliance/p2p_fraud_detector.py` | Donor merge and duplicate donation detection exist, but no universal dedupe pipeline/workbench. |
| Volunteer shift scheduling | Exists | `ngo_homesuite/services/volunteer_service.py`, `ngo_homesuite/web/volunteer_routes.py` | Shift CRUD + completion flows are implemented. |
| 2FA | Exists | `ngo_homesuite/models/core.py`, `ngo_homesuite/web/auth_routes.py` | TOTP enrollment/confirm/login paths, backup-code rotation/consumption, role-based enrollment policy, and step-up OTP are implemented. |
| WebAuthn/passkeys | Exists | `ngo_homesuite/web/auth_routes.py` | Passkey registration and authentication begin/complete flows are implemented. |
| OAuth login (user auth) | Exists | `ngo_homesuite/web/auth_routes.py` | OAuth provider status, authorize redirects, callbacks, identity normalization, account linking, and auto-provisioning are implemented for end-user sign-in. |
| CardDAV/CalDAV sync | Missing | `ngo_homesuite/web/integrations_routes.py` | Calendar sync endpoint exists, but not CardDAV/CalDAV protocol support. |
| Contact status workflows | Partial | `ngo_homesuite/models/core.py`, `ngo_homesuite/services/engagement_scoring_service.py` | Segments/scores exist, but no explicit contact lifecycle workflow engine with transitions and SLA policies. |
| Newsletter subscription management | Partial | `ngo_homesuite/utils/mailchimp_service.py` | External unsubscribe helper exists; no first-class internal subscription center/preferences model. |
| Photo uploads | Missing | `ngo_homesuite/web/` | Import uploads exist, but no donor/volunteer/campaign photo upload + storage + serving flow. |
| Custom fields | Partial | `ngo_homesuite/models/core.py` | Metadata JSON support exists, but no typed custom-field schema/admin UI/validation layer. |

## Delivery Order (Recommended)
1. Engagement parity: newsletter subscriptions, contact status workflows, campaign sending.
2. Data quality + UX parity: duplicate workbench, custom fields, photo uploads.
3. Protocol integrations: CardDAV/CalDAV sync.
4. PM polish: project management milestones/boards if still needed after above.

## Definition of Done (applies to each feature)
- Domain model + migrations
- Service layer with tenant isolation
- API and/or web routes
- UI screens
- Audit logging and observability metrics
- Tests: unit + route + regression
