# Feature Maturity Status

Last updated: 2026-06-02

This document separates feature availability from production maturity. The README links here to avoid overstating readiness.

Maturity scale:
- `Not started`: Planned only, no meaningful implementation yet.
- `Foundational`: Working baseline, partial workflows, limited edge-case coverage.
- `Operational`: Used in primary workflows, decent test coverage, still needs polish.
- `Production-ready`: Strong coverage, operational hardening, and documented runbooks.

| Area | Status | Maturity | Progress | Notes |
|---|---|---|---|---|
| Donors CRUD + profiles | Available | Operational | Web list/detail/create/edit/delete, dedupe/merge, donor insights in UI. |
| Donations + receipts + recurring | Available | Operational | Public give flow, recurring plans, receipt PDFs, export paths. |
| Peer Fundraising (P2P) API | Available | Operational | Tenant checks, audit events, aggregated leaderboard, pagination. |
| Peer Fundraising (P2P) Web (public) | Available | Operational | Public page, thermometer, leaderboard, embeddable widget script, and DAST smoke probes in CI. |
| P2P Staff workflow | Available | Operational | Dashboard create/publish/close flow plus service-level validation for title/goal/story edge cases; UX iteration still pending. |
| Events management | Available | Operational | 85% | Event board routes/UI and reminder dispatch flow are active with route-level regression coverage; further UX polish and deeper lifecycle automation remain. |
| Campaign management | Partial | Foundational | 65% | Batch/delivery models, unsubscribe/suppression controls, and engagement counters are implemented. Advanced segmentation/query builder and full composer/queue orchestration are still incomplete. |
| Grants workflows | Available | Foundational | 75% | Lifecycle/disbursement guards, budget-line accounting, reconciliation fields, and saved-search alerts/workbench UX are in place. Funder/state-specific operational automation and complete compliance packaging remain in progress. |
| Memberships | Partial | Operational | Role-gated membership management routes validated by route-policy and RBAC audits; admin UX polish remains. |
| Volunteers | Partial | Operational | Role-gated volunteer accounting/workflow routes with explicit route-contract and RBAC audit coverage. |
| Reporting exports/charts | Available | Operational | Export endpoints and chart/report paths are role-gated; Wave 1 route-level step-up hardening now covers high-risk destructive/export paths across main, reporting, grants-admin, program, volunteer, and v2 campaign segment deletion flows with focused regression validation. |
| Unified constituent journeys + soft credits | Available | Operational | v2 donor journey snapshots (`/api/v2/donors/<id>/journey`) and relational soft-credit CRUD on donations are live with route-contract coverage. |
| Role-based dashboard intelligence | Available | Operational | `/api/v2/intelligence/dashboard` delivers admin/staff/viewer-scoped payloads with preview restrictions and date validation. |
| Donor journey automations | Available | Operational | `/api/v2/donor-journeys/automations/run` and `/api/v2/donor-journeys/automations/events` provide idempotent trigger execution with durable audit rows. |
| Smart financial guardrails intelligence | Available | Operational | `/api/v2/intelligence/financial-guardrails` exposes guardrail status, watchlists, role-aware next actions, and risk scoring. |
| Integrated form ecosystem | Available | Operational | Internal/public v2 form ingest endpoints provide dedupe/idempotency, donor upsert, donation/task linkage, and tenant-safe submission listing. |
| OpenAPI docs | Available | Foundational | Starter spec and docs routes exist; endpoint coverage still expanding. |
| AI Copilot + governance | Available | Operational | Approval-gated actions, role-aware tooling, and tenant-id enforcement checks are in place; broader explicit negative-test depth for all AI pathways should continue to expand. |
| Multi-tenant hardening | In progress | Operational | API v2 cross-tenant mutation denials and route-level RBAC audit cover most high-risk mutators; remaining work focuses on complete mutation-path verification and AI-context isolation evidence. |
| End-to-end integration journeys | Available | Foundational | Baseline donor->donation->receipt journey coverage is active; broaden scenario depth (multi-role, payment failure, and recovery paths) for release confidence. |
| Production observability | Partial | Operational | Structured request logs, Prometheus/Loki/Promtail stack artifacts, and CI validation checks are in place. |

## Notes for Maintainers

- Update this file whenever feature claims in README change.
- Prefer stating `Foundational` over implying production maturity unless runbooks and failure tests exist.
- Keep roadmap references in sync with this status file.
