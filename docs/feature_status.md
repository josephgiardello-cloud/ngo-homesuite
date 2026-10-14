# Feature Maturity Status

Last updated: 2026-10-14

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
| Donations + receipts + manual recurring collections | Available | Regression tested | Recurring plans prepare pending collections only, not provider charges. Tenant staff must attest external collection evidence, exact amount and currency before income/schedule changes commit with the audit. No automatic recurring receipt. |
| Original-currency fund balances | Available | Regression tested | `FundService.get_fund_balance` returns `balances_by_currency`; mixed-currency legacy scalar totals are `None`, never a USD+EUR total. Original donation/expense records remain unchanged. No FX conversion is provided. |
| Calendar recurring schedules | Available | Regression tested | Validated monthly/quarterly/yearly UTC date recurrence retains its original day through month ends and leap years. Missed runs prepare one unconfirmed occurrence; retries never skip or advance it. |
| Financial audit privacy/types | Available | Regression tested | New request snapshots use salted canonical digests and minimum financial fields; updates record field names only. Collection/refund/status/receipt/delete event types are distinct. Reconciliation logs operation/tenant IDs, counts and input digests. Existing audit retention requires organization approval before production. |
| Python 3.13 SQLCipher adapter | Available | Linux adapter tested | Maintained `sqlcipher3-binary` adapter, real encrypted hex/passphrase files, plaintext/wrong-key denial, reopen and rekey tests in an encrypted Linux x86_64 image. Not certification of every ORM path, Windows, legacy upgrades or in-flight rotation. |
| Tenant boundaries and review inventory | Partial | Production blocker | 67 direct ORM scopes, four indirect ownership chains, strict worker SQL boundaries and 71 forced PostgreSQL RLS policies have synthetic persisted tests on PostgreSQL 16 and Linux SQLCipher. Request SQL guard remains opt-in; legacy connections, unreviewed surfaces and complete two-tenant production-path evidence remain blockers. See `docs/tenant_boundary_inventory.md`. |
| Candidate evidence and audit governance | Partial | Production blocker | PR/reusable dual-engine proof with SHA-bound artifacts, full-history secret scanning, reviewed-disposition validation and retention approval gates are wired. Hosted passing evidence and required branch protections are not yet certified. Retention is pending organization/legal approval; no historical purge is authorized. |
| Financial failure/concurrency proof | Available | SQLite paths verified | Keyed donation and Stripe retries, atomic donation/audit mutations, recurring-job deduplication, process-death recovery, and identity-aware reconciliation have persisted-state regression coverage. See `docs/release_process.md` for the exact contract and limitations (unkeyed public submissions, other database backends, FX aggregation, and email delivery are not certified). |
| Unassigned-user tenant boundaries | Available | Regression tested | Public registration grants no tenant membership. Staff pages and API v1 deny missing assignments; scoped ORM reads return no rows and tenant writes are rejected. Donor-profile writes require staff/admin, while assigned viewers retain read access. CSRF-enabled two-tenant tests run in CI/release tenant gates. |
| Peer Fundraising (P2P) API | Available | Operational | Tenant checks, audit events, aggregated leaderboard, pagination. |
| Peer Fundraising (P2P) Web (public) | Available | Operational | Public page, thermometer, leaderboard, embeddable widget script, and DAST smoke probes in CI. |
| P2P Staff workflow | Available | Operational | Dashboard create/publish/close flow plus service-level validation for title/goal/story edge cases; UX iteration still pending. |
| Events management | Available | Operational | 92% | Event board routes/UI, attendee registration visibility, and reminder dispatch flow are active with route-level regression coverage; remaining work is primarily UI polish rather than missing operational coverage. |
| Campaign management | Available | Operational | 92% | Batch/delivery models, unsubscribe/suppression controls, saved segment/query-builder CRUD with member preview, queue visibility, due-batch processing, failed-recipient retry controls, donor preference history, and explicit lifecycle transitions (pause/resume/opt-in-all/opt-out-all) are in production. Remaining work is primarily visual composer polish rather than missing capability. |
| Grants workflows | Available | Operational | 90% | Lifecycle/disbursement guards, budget-line accounting, reconciliation fields, grant readiness scoring, deadline alerting, saved-search alerts/workbench UX, and grant compliance package generation are in place. Remaining work focuses on specialized funder/state automation rather than core workflow coverage. |
| Memberships | Available | Operational | Membership management includes role-gated routes plus searchable/filterable/paginated member listing (`/api/v2/membership/members`); admin UX polish remains. |
| Volunteers | Available | Operational | Role-gated volunteer accounting/workflow routes include searchable/paginated volunteer list paths with route-contract and RBAC coverage. |
| Reporting exports/charts | Available | Operational | Export endpoints and chart/report paths are role-gated; Wave 1 route-level step-up hardening now covers high-risk destructive/export paths across main, reporting, grants-admin, program, volunteer, and v2 campaign segment deletion flows with focused regression validation. |
| Unified constituent journeys + soft credits | Available | Operational | v2 donor journey snapshots (`/api/v2/donors/<id>/journey`) and relational soft-credit CRUD on donations are live with route-contract coverage. |
| Role-based dashboard intelligence | Available | Operational | `/api/v2/intelligence/dashboard` delivers admin/staff/viewer-scoped payloads with preview restrictions and date validation. |
| Donor journey automations | Available | Operational | `/api/v2/donor-journeys/automations/run` and `/api/v2/donor-journeys/automations/events` provide idempotent trigger execution with durable audit rows, while journey graph and execution endpoints expose workflow structure and enrollment progress for operator review. |
| Smart financial guardrails intelligence | Available | Operational | `/api/v2/intelligence/financial-guardrails` exposes guardrail status, watchlists, role-aware next actions, and risk scoring. |
| Integrated form ecosystem | Available | Operational | Internal/public v2 form ingest endpoints provide dedupe/idempotency, donor upsert, donation/task linkage, and tenant-safe submission listing. |
| Integration protocols (CardDAV/CalDAV) | Available | Operational | `/integrations/calendar/caldav/sync`, `/integrations/contacts/carddav/sync`, and `/integrations/dav/capabilities` provide protocol sync workflows with dry-run support, provider-state introspection, and integration-event telemetry. |
| Project management board + milestones + dependencies | Available | Operational | `/api/v2/tasks/board`, `/api/v2/projects/<id>/board`, milestone CRUD paths, dependency create/delete, dependency-conflict detection, and portfolio overview are active with route-contract coverage. |
| Collaboration channels/messages/presence | Available | Operational | v2 collab channels/messages/presence APIs are active (`/api/v2/collab/*`) with SSE message streaming, typing indicators, moderation controls, inbox summaries, and presence heartbeats; route-contract coverage validates the primary collaboration workflows. |
| OpenAPI docs | Available | Foundational | Starter spec and docs routes exist; endpoint coverage still expanding. |
| AI Minion + governance | Available | Operational | Approval-gated actions, role-aware tooling, and tenant-id enforcement checks are in place; legacy audit writes on AI routes now bridge into canonical `security_audit_events` under Flask runtime. Continue broad negative-test expansion across all AI pathways. |
| Multi-tenant hardening | Available | Operational | API v2 cross-tenant mutation denials now include project milestone create and task dependency create/delete paths in addition to campaign queue/retry and membership surfaces; `/ai/chat` tenant-mismatch denial coverage remains in place with expanding audit evidence. |
| End-to-end integration journeys | Available | Operational | Integration coverage now includes donor->donation->receipt, validation-failure recovery, recurring-plan creation, and grant create/advance/disburse lifecycle journeys with automated regression tests. |
| Production observability | Partial | Operational | Structured request logs, Prometheus/Loki/Promtail stack artifacts, CI validation checks, and 2026-06-04 observability evidence lane artifacts are in place. |

## Notes for Maintainers

- Update this file whenever feature claims in README change.
- Prefer stating `Foundational` over implying production maturity unless runbooks and failure tests exist.
- Keep roadmap references in sync with this status file.
