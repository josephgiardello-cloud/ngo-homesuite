# NGO HomeSuite Competitive Feature Gap (Updated June 3, 2026)

## Summary
This matrix validates which features are already present, partially present, or still missing versus the requested comparison baseline.

Status legend:
- Exists: feature is available in production code paths.
- Partial: feature exists but lacks depth/polish expected for full parity.
- Missing: no meaningful implementation yet.

## Feature Status Matrix
| Feature | Status | Evidence | Notes |
|---|---|---|---|
| Real-time chat | Partial | `ngo_homesuite/web/v2_collab_handlers.py`, `ngo_homesuite/web/v2_routes.py` | Collaboration channels/messages/presence APIs exist under `/api/v2/collab/*`; full realtime websocket transport and richer operator UX are still pending. |
| Email campaign sending | Exists | `ngo_homesuite/services/campaign_email_service.py`, `ngo_homesuite/web/v2_routes.py`, `ngo_homesuite/web/integrations_routes.py` | Audience preview/send, suppression controls, tracking metrics, and operational smoke checks are implemented; advanced orchestration depth remains iterative. |
| Public registration pages | Exists | `ngo_homesuite/web/auth_routes.py`, `ngo_homesuite/web/main_routes.py`, `ngo_homesuite/web/p2p_routes.py` | Public account registration, public donation page, and public P2P pages are already implemented. |
| Project management | Partial | `ngo_homesuite/models/core.py`, `ngo_homesuite/web/tasks_routes.py` | Project entity and tasks exist; not yet a full PM suite (milestones/board views/dependencies). |
| Group management | Exists | `ngo_homesuite/services/smart_groups_service.py`, `ngo_homesuite/web/smart_groups_routes.py` | Smart Groups / dynamic audience CRUD + evaluation already present. |
| Dashboard charts | Exists | `ngo_homesuite/web/main_routes.py`, `ngo_homesuite/web/templates/reports.html` | Report charts are present via Chart.js (plus TONY dashboard charts). |
| Duplicate detection | Partial | `ngo_homesuite/services/donor_service.py`, `ngo_homesuite/services/form_ecosystem_service.py` | Donor merge and form-intake idempotency/dedupe are implemented; a universal operator dedupe workbench is still pending. |
| Volunteer shift scheduling | Exists | `ngo_homesuite/services/volunteer_service.py`, `ngo_homesuite/web/volunteer_routes.py` | Shift CRUD + completion flows are implemented. |
| 2FA | Exists | `ngo_homesuite/models/core.py`, `ngo_homesuite/web/auth_routes.py` | TOTP enrollment/confirm/login paths, backup-code rotation/consumption, role-based enrollment policy, and step-up OTP are implemented. |
| WebAuthn/passkeys | Exists | `ngo_homesuite/web/auth_routes.py` | Passkey registration and authentication begin/complete flows are implemented. |
| OAuth login (user auth) | Exists | `ngo_homesuite/web/auth_routes.py` | OAuth provider status, authorize redirects, callbacks, identity normalization, account linking, and auto-provisioning are implemented for end-user sign-in. |
| CardDAV/CalDAV sync | Missing | `ngo_homesuite/web/integrations_routes.py` | Calendar sync endpoint exists, but not CardDAV/CalDAV protocol support. |
| Contact status workflows | Exists | `ngo_homesuite/services/stewardship_service.py`, `ngo_homesuite/models/core.py`, `ngo_homesuite/web/v2_routes.py` | Donor-journey triggers, cooldown/idempotency controls, and automation audit events are live; richer visual workflow tooling is still a polish opportunity. |
| Newsletter subscription management | Partial | `ngo_homesuite/utils/mailchimp_service.py` | External unsubscribe helper exists; no first-class internal subscription center/preferences model. |
| Photo uploads | Exists | `ngo_homesuite/web/v2_routes.py`, `ngo_homesuite/web/main_routes.py` | Donor/campaign photo upload and media serving endpoints are implemented. |
| Custom fields | Exists | `ngo_homesuite/web/admin_routes.py`, `ngo_homesuite/web/templates/settings.html` | Admin custom-field schema management (donor/campaign) and UI editing flow are implemented. |

## Newly Landed Strategic Parity (June 2026)

| Capability | Status | Evidence | Notes |
|---|---|---|---|
| Unified constituent journeys | Exists | `ngo_homesuite/services/activity_timeline_service.py`, `ngo_homesuite/web/v2_routes.py` | Donor journey endpoint with timeline and summary payloads is active. |
| Role-based intelligence dashboards | Exists | `ngo_homesuite/services/reporting_service.py`, `ngo_homesuite/web/v2_routes.py` | Role-specific intelligence payloads for admin/staff/viewer are implemented. |
| Smart financial guardrails | Exists | `ngo_homesuite/services/reporting_service.py`, `ngo_homesuite/web/v2_routes.py` | Guardrail risk/status/watchlist intelligence endpoint is active. |
| Integrated form ecosystem | Exists | `ngo_homesuite/services/form_ecosystem_service.py`, `ngo_homesuite/web/v2_routes.py` | Internal + public token-auth ingestion with idempotency and tenant-safe persistence is implemented. |

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
