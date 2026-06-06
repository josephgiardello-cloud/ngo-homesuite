# Feature Maturity Status

Last updated: 2026-06-04

This document separates feature availability from production maturity. The README links here to avoid overstating readiness.

For the current full feature inventory, see `docs/full_feature_list.md`.

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
| Campaign management | Available | Operational | 86% | Batch/delivery models, unsubscribe/suppression controls, segmentation endpoints, queue visibility, due-batch processing, failed-recipient retry controls, donor preference history, and explicit lifecycle transitions (pause/resume/opt-in-all/opt-out-all) are in production. Remaining work centers on deeper composer UX and richer query-builder ergonomics. |
| Grants workflows | Available | Operational | 82% | Lifecycle/disbursement guards, budget-line accounting, reconciliation fields, saved-search alerts/workbench UX, and grant compliance package generation are in place. Remaining work focuses on deeper funder/state-specific automation. |
| Memberships | Available | Operational | Membership management includes role-gated routes plus searchable/filterable/paginated member listing (`/api/v2/membership/members`); admin UX polish remains. |
| Volunteers | Available | Operational | Role-gated volunteer accounting/workflow routes include searchable/paginated volunteer list paths with route-contract and RBAC coverage. |
| Reporting exports/charts | Available | Operational | Export endpoints and chart/report paths are role-gated; Wave 1 route-level step-up hardening now covers high-risk destructive/export paths across main, reporting, grants-admin, program, volunteer, and v2 campaign segment deletion flows with focused regression validation. |
| Unified constituent journeys + soft credits | Available | Operational | v2 donor journey snapshots (`/api/v2/donors/<id>/journey`) and relational soft-credit CRUD on donations are live with route-contract coverage. |
| Role-based dashboard intelligence | Available | Operational | `/api/v2/intelligence/dashboard` delivers admin/staff/viewer-scoped payloads with preview restrictions and date validation. |
| Donor journey automations | Available | Operational | `/api/v2/donor-journeys/automations/run` and `/api/v2/donor-journeys/automations/events` provide idempotent trigger execution with durable audit rows. |
| Smart financial guardrails intelligence | Available | Operational | `/api/v2/intelligence/financial-guardrails` exposes guardrail status, watchlists, role-aware next actions, and risk scoring. |
| Integrated form ecosystem | Available | Operational | Internal/public v2 form ingest endpoints provide dedupe/idempotency, donor upsert, donation/task linkage, and tenant-safe submission listing. |
| Collaboration channels/messages/presence | Available | Foundational | v2 collab channels/messages/presence APIs are active (`/api/v2/collab/*`) with server-sent event message stream support (`/api/v2/collab/channels/<id>/stream`); full websocket transport and richer UX remain future work. |
| OpenAPI docs | Available | Foundational | Starter spec and docs routes exist; endpoint coverage still expanding. |
| AI Minion + governance | Available | Operational | Approval-gated actions, role-aware tooling, and tenant-id enforcement checks are in place; legacy audit writes on AI routes now bridge into canonical `security_audit_events` under Flask runtime. Continue broad negative-test expansion across all AI pathways. |
| Multi-tenant hardening | In progress | Operational | API v2 cross-tenant mutation denials and route-level RBAC audit include campaign queue/retry and membership member-list surfaces, plus `/ai/chat` tenant-mismatch denial coverage. Remaining work focuses on exhaustive mutation-path verification and AI-context isolation evidence. |
| End-to-end integration journeys | Available | Foundational | Baseline donor->donation->receipt journey coverage is active; broaden scenario depth (multi-role, payment failure, and recovery paths) for release confidence. |
| Production observability | Partial | Operational | Structured request logs, Prometheus/Loki/Promtail stack artifacts, CI validation checks, and 2026-06-04 observability evidence lane artifacts are in place. |

## Notes for Maintainers

- Update this file whenever feature claims in README change.
- Prefer stating `Foundational` over implying production maturity unless runbooks and failure tests exist.
- Keep roadmap references in sync with this status file.

