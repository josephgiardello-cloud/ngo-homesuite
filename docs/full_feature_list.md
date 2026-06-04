# Full Feature List

Last updated: 2026-06-03

This is the current product feature inventory for NGO HomeSuite.

## Platform, Security, and Governance

- Multi-tenant organization scoping and tenant-aware route checks
- Role-based access controls across web and API routes
- MFA (TOTP), backup codes, and role-based MFA enforcement policy
- Step-up authentication for sensitive/destructive operations
- OAuth login provider integration paths (Google and extensible providers)
- Security audit event logging and policy enforcement events
- Runtime configuration validation and production safety guards
- Distributed rate-limit policy support (`RATELIMIT_STORAGE_URI`) for production
- Release evidence bundle validation tooling (`tools/verify_release_evidence_bundle.py`)
- OpenAPI required-route drift validation tooling (`tools/check_openapi_route_drift.py`)

## Donor and Fundraising Operations

- Donor CRUD, profile management, and donor detail timelines
- Donor merge/dedupe support and import tooling
- Donation capture, status transitions, and receipt generation
- Public donation page and embedded donation widget paths
- Recurring donation plan workflows
- Donation exports and reporting integrations
- Soft-credit linkage and donor-journey snapshots

## Peer-to-Peer Fundraising (P2P)

- P2P campaign creation, publish/close lifecycle, and management flows
- Public P2P campaign pages and leaderboard endpoints
- Goal/thermometer and donor contribution tracking
- P2P route-level tenant and authorization protections

## Campaign and Engagement

- Campaign segmentation and audience previews
- Campaign send workflows and delivery status modeling
- Queue visibility for campaign batches
- Due-batch processing controls
- Failed-batch retry controls
- Suppression and unsubscribe handling
- Campaign performance/engagement metrics surfaces

## Grants and Compliance

- Grant lifecycle transitions with guardrails
- Disbursement and budget line accounting controls
- Reconciliation-related fields and workflows
- Saved search, opportunity search, and lifecycle reporting routes
- Compliance package generation endpoint and export path
- Evidence-pack and audit-oriented compliance support utilities

## Memberships, Volunteers, Programs, and Tasks

- Membership management and member-list API with filter/search/pagination
- Volunteer shift scheduling, completion, and training workflows
- Program case/intake pathways and lifecycle updates
- Task board and reminder candidate APIs
- Project board, milestones, and dependency contract surfaces

## Collaboration and Shared Workflows

- v2 collaboration channels, messages, and presence APIs
- Cross-tenant protections for collaboration message endpoints
- Opinionated workflow execution endpoints (donation follow-up, grants, program impact)
- Workflow page and API wrappers for common operational runs

## Intelligence, Insights, and AI

- Role-based dashboard intelligence endpoint (`/api/v2/intelligence/dashboard`)
- Financial guardrails intelligence endpoint (`/api/v2/intelligence/financial-guardrails`)
- Donor journey automation run and event history endpoints
- Activity and insight feeds for operational monitoring
- AI Copilot service routes with role-aware/tool-aware controls

## Forms, Integrations, and External Connectivity

- Integrated form ecosystem ingestion (internal and public token-auth paths)
- Form submission dedupe/idempotency and tenant-safe persistence
- Integration operations routes (status and sync visibility)
- Accounting/calendar integration support routes and sync logs

## API, Docs, and Observability

- Versioned API v1 and v2 route surfaces
- OpenAPI contract at `docs/openapi.yaml`
- Required v2 path manifest at `docs/openapi_required_v2_paths.txt`
- API docs endpoints (`/api/openapi.yaml`, `/api/docs`, `/api/swagger`)
- Request-ID propagation checks and observability test lanes
- Metrics endpoint validation and monitoring stack documentation

## Release and Operational Readiness Assets

- Production checklist and release process runbooks
- Backup/restore drill playbook and key-rotation drill guidance
- DAST smoke artifact support and scalability benchmark artifact support
- CI policy tests for dependency drift, release hygiene, and route protection
