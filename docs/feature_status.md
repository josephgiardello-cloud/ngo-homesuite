# Feature Maturity Status

Last updated: 2026-05-16

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
| Events management | Partial | Foundational | 60% | Data model and route scaffolding exist, full lifecycle UX still limited. Email reminders are now service-backed, but route/UI integration is still pending. |
| Campaign management | Partial | Foundational | 55% | Campaign entities and relationships present; full end-to-end UI and fundraising aggregation polish remain incomplete. |
| Grants workflows | Available | Foundational | 70% | Core lifecycle/disbursement guards and mutation audit events are in place. Budget-line accounting is functional in services/tests, while approval/compliance operationalization is still in progress. |
| Memberships | Partial | Operational | Role-gated membership management routes validated by route-policy and RBAC audits; admin UX polish remains. |
| Volunteers | Partial | Operational | Role-gated volunteer accounting/workflow routes with explicit route-contract and RBAC audit coverage. |
| Reporting exports/charts | Available | Operational | Export endpoints and chart/report paths are role-gated and now covered by route-policy and RBAC audit tests. |
| OpenAPI docs | Available | Foundational | Starter spec and docs routes exist; endpoint coverage still expanding. |
| AI Copilot + governance | Available | Operational | Approval-gated actions, role-aware tooling, baseline hardening in place. |
| Multi-tenant hardening | In progress | Operational | API v2 cross-tenant mutation denials plus route-level RBAC audit now enforce most high-risk mutators. |
| Production observability | Partial | Operational | Structured request logs, Prometheus/Loki/Promtail stack artifacts, and CI validation checks are in place. |

## Notes for Maintainers

- Update this file whenever feature claims in README change.
- Prefer stating `Foundational` over implying production maturity unless runbooks and failure tests exist.
- Keep roadmap references in sync with this status file.
