# Feature Maturity Status

Last updated: 2026-05-16

This document separates feature availability from production maturity. The README links here to avoid overstating readiness.

Maturity scale:
- `Not started`: Planned only, no meaningful implementation yet.
- `Foundational`: Working baseline, partial workflows, limited edge-case coverage.
- `Operational`: Used in primary workflows, decent test coverage, still needs polish.
- `Production-ready`: Strong coverage, operational hardening, and documented runbooks.

| Area | Status | Maturity | Notes |
|---|---|---|---|
| Donors CRUD + profiles | Available | Operational | Web list/detail/create/edit/delete, dedupe/merge, donor insights in UI. |
| Donations + receipts + recurring | Available | Operational | Public give flow, recurring plans, receipt PDFs, export paths. |
| Peer Fundraising (P2P) API | Available | Operational | Tenant checks, audit events, aggregated leaderboard, pagination. |
| Peer Fundraising (P2P) Web (public) | Available | Operational | Public page, thermometer, leaderboard, embeddable widget script. |
| P2P Staff workflow | Available | Foundational | New dashboard flow for create/publish/close; needs broader field validation and UX iteration. |
| Events management | Partial | Foundational | Data model and route scaffolding exist, full lifecycle UX still limited. |
| Campaign management | Partial | Foundational | Campaign entities and relationships present; full end-to-end UI not complete. |
| Grants workflows | Available | Foundational | API and workflow paths exist; real-world process depth still expanding. |
| Memberships | Partial | Foundational | Core entities and routes exist, broader admin UX pending. |
| Volunteers | Partial | Foundational | Basic model and references; richer workflow/testing needed. |
| Reporting exports/charts | Available | Foundational | Useful baseline; advanced analytics and scheduled reports still roadmap items. |
| OpenAPI docs | Available | Foundational | Starter spec and docs routes exist; endpoint coverage still expanding. |
| AI Copilot + governance | Available | Operational | Approval-gated actions, role-aware tooling, baseline hardening in place. |
| Multi-tenant hardening | In progress | Foundational | Key areas covered (including P2P), full query-audit pass still in progress. |
| Production observability | Partial | Foundational | Health and metrics endpoints exist; centralized logging/monitoring stack is not fully integrated. |

## Notes for Maintainers

- Update this file whenever feature claims in README change.
- Prefer stating `Foundational` over implying production maturity unless runbooks and failure tests exist.
- Keep roadmap references in sync with this status file.
