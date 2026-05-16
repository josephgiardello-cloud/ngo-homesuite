# Grant Management Standards Alignment

This document maps NGO HomeSuite grant features to a widely used grant lifecycle model and highlights compliance-oriented implementation targets.

## Lifecycle Model

The product aligns grant execution to three phases:

1. Pre-Award
- Opportunity research and qualification
- Proposal development
- Submission and deadline tracking

2. Award
- Negotiation and acceptance
- Budget and restricted-fund setup
- Ownership, approvals, and operating cadence

3. Post-Award
- Program implementation
- Financial monitoring and disbursement tracking
- Reporting, compliance, and closeout

Recommended status path in the application:
- prospect -> in_progress -> submitted -> awarded -> reporting -> closed

## Current Feature Mapping

### Pre-Award
- Pipeline status tracking via grants APIs and web routes
- Deadline management with grant calendar milestone endpoint
- Proposal metadata captured in grant detail fields

Current APIs:
- GET /api/v2/grants
- POST /api/v2/grants
- POST /api/v2/grants/{id}/advance
- GET /api/v2/grants/calendar

### Award
- Award transition handling with validation in service layer
- Award amount capture and lifecycle date support
- Restricted funding summary for awarded/reporting/closed grants

Current APIs:
- GET /api/v2/grants/restricted-funds

### Post-Award
- Disbursement recording and tracking
- Upcoming reporting and lifecycle milestones exposed in calendar events
- Operational use in task board and activity feed UI surfaces

Current APIs:
- POST /api/v2/grants/{id}/disbursements
- GET /api/v2/grants/calendar
- GET /api/v2/grants/restricted-funds

## Compliance and Controls Notes

- Tenant isolation is enforced at query and route layers.
- SQL guardrails are validated by static tests to block dynamic execution patterns.
- Auditability is supported through append-only and integrity mechanisms already present in platform controls.

## Near-Term Enhancements

1. Proposal Tracker Depth
- Submission history timeline entries
- Funder portal links
- Proposal document version metadata

2. Restricted Fund Accounting Depth
- Expense allocation and draw-rule policies
- Variance reporting (budget vs actual by grant)
- Explicit approval checkpoints before status transitions

3. Outcome and Impact Integration
- Link grant-funded activities to beneficiary/program outcome metrics
- Pre-built impact report payloads for funder-specific templates

4. Calendar and Reminder Maturity
- Expand milestone reminders by lifecycle phase
- Surface role-based calendar views for grant owners and finance staff

## Operational Review Cadence

Recommended recurring review:
- Monthly: lifecycle status hygiene and stale pipeline analysis
- Quarterly: restricted-fund variance review and reporting timeliness
- Semiannual: standards gap analysis against leading nonprofit grant workflows and compliance requirements
