# NGO HomeSuite Implementation Backlog: Competitive Parity (2026 Q2)

**Objective:** Close remaining product gaps vs. CiviCRM across security, grants operations, and donor engagement.

**Timeline:** 5 sprints (May–June 2026)  
**Baseline:** 668 passing tests, 12 skipped (main @ 9011c9c)

## Reality Check Snapshot (Verified 2026-05-18)

This snapshot reconciles backlog planning assumptions with current code state.

- **Milestone A (Security + Compliance):** Partially delivered.
	- Implemented: TOTP enrollment/setup APIs, backup-code generation/consumption, role-based 2FA enforcement hook, step-up OTP endpoint.
	- Remaining gaps: no clear encryption-at-rest path for TOTP secret field, limited endpoint-level adoption of `@require_step_up_auth`, and no in-repo evidence of an external formal security review.
- **Milestone B (Grants Budget Accounting):** Mostly delivered at data-model and service layers.
	- Implemented: budget lines, budget transactions, reconciliation fields, variance calculations/alerts in services.
	- Remaining gaps: operational workflows/report UX still uneven; state/funder-specific operational automation remains partial.
- **Milestone C (Bulk Donor Email Engine):** Partially delivered.
	- Implemented: campaign batch/delivery models, unsubscribe flow, suppression handling, open/click tracking counters.
	- Remaining gaps: advanced segment query builder and explicit `EmailCampaignQueue`-style architecture from this backlog are still not complete as originally scoped.
- **Milestone D/E (Reporting + Competitive Polish):** Largely planned/in progress; many items remain backlog candidates.

Documentation drift note: some items listed as unchecked below are partially or fully implemented under evolved file names and structures. Keep this backlog as planning intent, not source-of-truth implementation evidence.

## Progress Addendum (2026-06-02)

The following strategic parity items were implemented end-to-end in the current codebase and validated with targeted route-contract tests:

- Unified constituent journeys with donor 360 payloads and timeline aggregation.
- Relational donor soft-credit attribution on donation records.
- Role-based intelligence dashboard for admin/staff/viewer personas.
- Donor journey automations with durable idempotency and cooldown auditing.
- Smart financial guardrails intelligence with risk scoring and watchlists.
- Integrated form ecosystem ingestion (internal + public token-auth) with dedupe/idempotency and CRM linkage.

Primary implementation touchpoints:

- `ngo_homesuite/web/v2_routes.py`
- `ngo_homesuite/services/activity_timeline_service.py`
- `ngo_homesuite/services/stewardship_service.py`
- `ngo_homesuite/services/reporting_service.py`
- `ngo_homesuite/services/form_ecosystem_service.py`
- `ngo_homesuite/models/core.py`
- `ngo_homesuite/migrations/0035_add_donor_soft_credits.sql`
- `ngo_homesuite/migrations/0036_add_donor_journey_automation_events.sql`
- `ngo_homesuite/migrations/0037_add_form_submission_events.sql`

---

## Milestone A: Security + Compliance (Sprint 1 – 2 weeks)

### 🔴 TICKET A-1: TOTP 2FA Enrollment & Verification  
**Priority:** P0 (blocker for admin/staff trust)  
**Estimate:** 8 points  
**Acceptance Criteria:**
- [ ] Admin/Staff can enroll TOTP via authenticator app (QR code + manual entry).
- [ ] Backup codes generated & displayed once on enrollment.
- [ ] Backup codes stored encrypted in database with hash index.
- [ ] TOTP verification on login (6-digit input, 30-second window, drift tolerance ±1).
- [ ] Audit log: "2FA_ENROLLED", "2FA_VERIFIED", "2FA_BACKUP_CODE_USED".

**Technical Approach:**
- Add `pyotp` dependency.
- Model: `User.totp_secret` (encrypted), `User.totp_backup_codes` (hashed list).
- Route: `POST /auth/totp/enroll` → generate secret → return QR + manual entry.
- Route: `POST /auth/totp/verify-setup` → validate TOTP token → store secret.
- Middleware: check `user.totp_enabled` on login, prompt for OTP if enabled.

**Files to Create/Modify:**
- `ngo_homesuite/auth/models.py` (add TOTP fields)
- `ngo_homesuite/auth/totp_service.py` (new: generate, verify, backup code logic)
- `ngo_homesuite/web/auth_routes.py` (add enrollment/verification routes)
- `ngo_homesuite/web/templates/auth/totp_enroll.html` (new: QR display)
- Tests: `ngo_homesuite/web/test_auth_totp.py`

---

### 🟡 TICKET A-2: 2FA Enforcement Policy by Role  
**Priority:** P0  
**Estimate:** 5 points  
**Acceptance Criteria:**
- [ ] Admin role requires 2FA enrollment (enforced on first login post-feature).
- [ ] Staff role optional but recommended (warning banner).
- [ ] Viewer role: no 2FA required.
- [ ] Policy bypass only via superuser override (with audit trail).
- [ ] Migration: set `totp_required_flag` on all admin accounts.

**Technical Approach:**
- Config: `ROLES_REQUIRING_2FA = ["admin"]`.
- Pre-request hook: if user.role in ROLES_REQUIRING_2FA and not user.totp_enabled, redirect to enrollment.
- Audit event: "POLICY_2FA_ENFORCEMENT_TRIGGERED" with context.

**Files to Modify:**
- `ngo_homesuite/flask_config.py` (add config)
- `ngo_homesuite/web/session_hardening.py` (add policy check)
- Migrations: `ngo_homesuite/migrations/add_totp_fields.py`

---

### 🟢 TICKET A-3: 2FA Recovery & "Step-Up" Auth for Sensitive Actions  
**Priority:** P1  
**Estimate:** 5 points  
**Acceptance Criteria:**
- [ ] Admin can recover lost TOTP via backup codes (one-time use, audit logged).
- [ ] "Sensitive actions" (delete user, change admin role, export donor list) require step-up OTP re-entry.
- [ ] Step-up session expires after 15 minutes or on logout.
- [ ] Audit log: "SENSITIVE_ACTION_ATTEMPTED", "STEP_UP_OTP_VERIFIED", "STEP_UP_OTP_FAILED".

**Technical Approach:**
- Add `g.step_up_verified_at` timestamp (short-lived).
- Decorator: `@require_step_up_auth` on sensitive routes.
- Route: `POST /auth/step-up-otp` → validate + set timestamp.

**Files to Create/Modify:**
- `ngo_homesuite/auth/decorators.py` (add @require_step_up_auth)
- `ngo_homesuite/web/auth_routes.py` (add recovery route + step-up route)
- `ngo_homesuite/web/admin_routes.py` (apply @require_step_up_auth to sensitive endpoints)
- Tests: `ngo_homesuite/web/test_auth_step_up.py`

---

## Milestone B: Grants Budget Accounting (Sprint 2–3 – 3 weeks)

### 🔴 TICKET B-1: Grant Budget Line Item Model & Schema  
**Priority:** P0  
**Estimate:** 8 points  
**Acceptance Criteria:**
- [ ] Model: `GrantBudgetLine(grant_id, category, description, awarded_amount, budget_status)`.
- [ ] Budget status: PLANNED, APPROVED, LOCKED.
- [ ] Categories: Personnel, Travel, Equipment, Indirect, Other.
- [ ] Soft-delete support (archive old budgets).
- [ ] Schema migration with backfill (all existing grants → empty budget).
- [ ] Indexes: (grant_id, budget_status), (grant_id, category).

**Technical Approach:**
- Add SQLAlchemy model in `ngo_homesuite/models/core.py`.
- Migration file: `ngo_homesuite/migrations/add_grant_budget_lines.py`.
- Relationship: `Grant.budget_lines` one-to-many.

**Files to Create/Modify:**
- `ngo_homesuite/models/core.py` (add GrantBudgetLine)
- `ngo_homesuite/migrations/add_grant_budget_lines.py` (new)
- `ngo_homesuite/db/schema.py` (update schema docs)

---

### 🟡 TICKET B-2: Budget Commitment & Expense Tracking  
**Priority:** P0  
**Estimate:** 13 points  
**Acceptance Criteria:**
- [ ] New model: `BudgetTransaction(budget_line_id, type, amount, date, description, receipt_id, status)`.
- [ ] Transaction types: COMMITMENT, EXPENSE, ACCRUAL, REVERSAL.
- [ ] Transaction status: PENDING, APPROVED, REJECTED, RECONCILED.
- [ ] Automatic validation: sum of transactions ≤ awarded_amount (warning on exceed).
- [ ] Soft-delete support.
- [ ] Indexes: (budget_line_id, type), (budget_line_id, status), (date).

**Technical Approach:**
- SQLAlchemy model + relationship chain: Grant → BudgetLine → BudgetTransaction.
- Trigger/validator on save: compute remaining balance.

**Files to Create/Modify:**
- `ngo_homesuite/models/core.py` (add BudgetTransaction)
- `ngo_homesuite/migrations/add_budget_transactions.py` (new)

---

### 🟡 TICKET B-3: Budget Variance Report & Dashboard  
**Priority:** P1  
**Estimate:** 13 points  
**Acceptance Criteria:**
- [ ] Report: grant name, budget line, awarded, committed, expensed, remaining, variance %.
- [ ] Variance alerts: > 90% spent (yellow), > 100% spent (red).
- [ ] Filters: grant, budget category, date range, variance status.
- [ ] Export: CSV with variance trend (month-over-month).
- [ ] API endpoint: `GET /api/v2/reports/grant-budget-variance?grant_id=X&from_date=X&to_date=X`.
- [ ] Dashboard widget: top 5 grants by variance risk.

**Technical Approach:**
- Query builder in `ngo_homesuite/services/reporting_service.py`.
- Aggregation: SUM(BudgetTransaction.amount) grouped by budget_line.
- Cache result (expire 1 hour) if >50 lines per grant.

**Files to Create/Modify:**
- `ngo_homesuite/services/reporting_service.py` (add grant_budget_variance_report method)
- `ngo_homesuite/web/reports_routes.py` (add endpoint + template)
- `ngo_homesuite/web/templates/reports/grant_budget_variance.html` (new)
- Tests: `ngo_homesuite/services/test_grant_budget_reporting.py`

---

### 🟢 TICKET B-4: Reconciliation & Audit Trail for Budget Transactions  
**Priority:** P2  
**Estimate:** 8 points  
**Acceptance Criteria:**
- [ ] Reconciliation workflow: mark transaction as RECONCILED (locks from edit).
- [ ] Bulk reconciliation: select date range → mark all as reconciled.
- [ ] Audit log: "BUDGET_TRANSACTION_CREATED", "BUDGET_TRANSACTION_RECONCILED", "BUDGET_TRANSACTION_REJECTED".
- [ ] Cannot delete reconciled transactions (soft-delete only).
- [ ] Report: reconciliation status by date.

**Technical Approach:**
- Add `reconciled_at`, `reconciled_by_user_id` fields to BudgetTransaction.
- Pre-update hook: check RECONCILED status → reject non-superuser edits.

**Files to Modify:**
- `ngo_homesuite/models/core.py` (add reconciliation fields)
- `ngo_homesuite/migrations/add_budget_reconciliation_fields.py` (new)

---

## Milestone C: Bulk Donor Email Engine (Sprint 3–4 – 3 weeks)

### 🔴 TICKET C-1: Donor Segmentation & Query Builder  
**Priority:** P0  
**Estimate:** 13 points  
**Acceptance Criteria:**
- [ ] Segment types: by tag, by recency (last donation < 30/90/180 days), by amount (min/max range), by campaign.
- [ ] Composite filters: (tag='major_donor' AND recency < 90 days) OR amount > $5000.
- [ ] Save segment with name + description.
- [ ] Preview: show count + sample emails before send.
- [ ] Export segment to CSV.

**Technical Approach:**
- Query builder pattern in `ngo_homesuite/services/donor_segment_service.py`.
- Dynamic SQL construction with ORM or raw SQL builder (safety: parameterized queries only).
- Cache segment count (expire 1 hour).

**Files to Create/Modify:**
- `ngo_homesuite/models/core.py` (add DonorSegment model)
- `ngo_homesuite/services/donor_segment_service.py` (new: build_segment_query, preview_segment)
- `ngo_homesuite/web/donor_segment_routes.py` (new: create, edit, preview endpoints)
- `ngo_homesuite/web/templates/segments/` (new folder: create.html, preview.html)
- Tests: `ngo_homesuite/services/test_donor_segment_service.py`

---

### 🟡 TICKET C-2: Email Campaign Composer & Queue  
**Priority:** P0  
**Estimate:** 13 points  
**Acceptance Criteria:**
- [ ] Campaign model: name, subject, body (HTML + plain text), segment_id, scheduled_at, status (DRAFT, QUEUED, SENDING, SENT, FAILED).
- [ ] Template support: basic Jinja2 ({{ donor.first_name }}, {{ donor.total_donated }}).
- [ ] WYSIWYG editor (or markdown).
- [ ] Preview: render with sample donor data.
- [ ] Schedule for future send or immediate.
- [ ] Compose route: `POST /campaigns/email/create`.

**Technical Approach:**
- Model: `EmailCampaign(segment_id, subject, body, scheduled_at, status)`.
- Template rendering: Jinja2 environment with restricted context.
- Queue table: `EmailCampaignQueue(campaign_id, donor_id, sent_at, open_count, click_count, bounce)`.

**Files to Create/Modify:**
- `ngo_homesuite/models/core.py` (add EmailCampaign, EmailCampaignQueue)
- `ngo_homesuite/services/email_campaign_service.py` (new: render, queue, send logic)
- `ngo_homesuite/web/email_campaign_routes.py` (new: create, edit, preview, schedule endpoints)
- `ngo_homesuite/web/templates/campaigns/email/` (new folder: compose.html, preview.html)
- Tests: `ngo_homesuite/web/test_email_campaign_routes.py`

---

### 🟡 TICKET C-3: Campaign Send, Retry & Failure Handling  
**Priority:** P0  
**Estimate:** 13 points  
**Acceptance Criteria:**
- [ ] Background job: process EmailCampaignQueue (batched by 50/hour to avoid rate limits).
- [ ] Retry logic: failed sends retry up to 3 times (exponential backoff: 5m, 15m, 1h).
- [ ] Bounce handling: hard bounce → add to suppression list, soft bounce → retry.
- [ ] Status tracking: QUEUED → SENDING → DELIVERED / BOUNCED / FAILED.
- [ ] Audit log: "EMAIL_CAMPAIGN_SENT", "EMAIL_CAMPAIGN_BOUNCE", "EMAIL_CAMPAIGN_FAILED".
- [ ] Summary: sent count, bounce count, failure count, retry count.

**Technical Approach:**
- Scheduled task (celery or APScheduler): process_email_campaign_queue every 5 minutes.
- Rate limit: ~10 emails/second (adjust per your email provider).
- Bounce detection: parse SMTP response codes + provider webhooks (if applicable).

**Files to Create/Modify:**
- `ngo_homesuite/services/email_campaign_service.py` (add send_campaign, handle_bounce methods)
- `ngo_homesuite/events/scheduler.py` (add process_email_campaign_queue task)
- `ngo_homesuite/migrations/add_email_campaign_queue.py` (new)

---

### 🟢 TICKET C-4: Unsubscribe, Opt-Out & Suppression List  
**Priority:** P0  
**Estimate:** 8 points  
**Acceptance Criteria:**
- [ ] Unsubscribe link in email footer (one-click, no login required).
- [ ] Unsubscribe endpoint: `GET /email/unsubscribe/<token>` → marks donor as opt-out.
- [ ] Suppression list: query before campaign send, skip opted-out donors.
- [ ] Audit log: "DONOR_UNSUBSCRIBED_EMAIL", "DONOR_OPT_OUT_ADDED".
- [ ] Admin can manually add/remove from suppression list.
- [ ] Compliance: CAN-SPAM / GDPR unsubscribe requirement.

**Technical Approach:**
- Secure token: HMAC(donor_id, secret) to avoid enumeration.
- Donor model: add `email_opt_out_at` timestamp.
- Pre-send validation: skip donors with recent opt_out_at.

**Files to Modify:**
- `ngo_homesuite/models/core.py` (add email_opt_out_at field)
- `ngo_homesuite/web/email_routes.py` (add unsubscribe endpoint)
- `ngo_homesuite/services/email_campaign_service.py` (filter opt-out donors before queue)

---

### 🟢 TICKET C-5: Email Delivery & Engagement Metrics  
**Priority:** P1  
**Estimate:** 8 points  
**Acceptance Criteria:**
- [ ] Tracking: open count (via pixel), click count (via link rewrite).
- [ ] Summary dashboard: campaign name, sent date, open rate %, click rate %, bounce rate %.
- [ ] Per-donor view: did they open? did they click? which links?
- [ ] Trend: compare campaign performance over time.
- [ ] Export: engagement report to CSV.

**Technical Approach:**
- Pixel tracking: 1x1 transparent GIF at `/email/track/open/<token>`.
- Link rewrite: proxy links through `/email/track/click/<token>/<link_id>` (redirect to real URL).
- Store: EmailCampaignQueue.open_count, click_count, clicked_links JSON.

**Files to Create/Modify:**
- `ngo_homesuite/web/email_tracking_routes.py` (new: open + click handlers)
- `ngo_homesuite/web/email_campaign_routes.py` (add metrics endpoint)
- `ngo_homesuite/web/templates/campaigns/email/metrics.html` (new)

---

## Milestone D: Reporting Expansion (Sprint 4 – 2 weeks)

### 🟡 TICKET D-1: Advanced Donor Insights Dashboard  
**Priority:** P1  
**Estimate:** 13 points  
**Acceptance Criteria:**
- [ ] Widgets: total donors, total raised YTD, avg donation, donor retention %, churn rate.
- [ ] Cohort analysis: donors by acquisition cohort (year), retention rate by cohort.
- [ ] Lifetime value (LTV) distribution chart.
- [ ] Giving trends: monthly/quarterly, year-over-year comparison.
- [ ] Segment performance: top 5 segments by engagement, by giving.

**Technical Approach:**
- Query aggregations in reporting service.
- Chart library: Chart.js or D3.js.
- Cache results (expire 4 hours).

**Files to Create/Modify:**
- `ngo_homesuite/services/donor_analytics_service.py` (new)
- `ngo_homesuite/web/dashboard_routes.py` (add analytics endpoint)
- `ngo_homesuite/web/templates/dashboards/donor_insights.html` (new)

---

### 🟡 TICKET D-2: Grant Pipeline & Forecast Report  
**Priority:** P1  
**Estimate:** 10 points  
**Acceptance Criteria:**
- [ ] Pipeline: grants by stage (application, pending review, awarded, closed).
- [ ] Total by stage, likelihood of award (if applicable).
- [ ] Forecast: projected revenue if all pending → awarded.
- [ ] Timeline: grant maturity (days since application, days to decision).
- [ ] Export to CSV.

**Technical Approach:**
- Aggregate Grant.status + Grant.created_at + Grant.decision_date.

**Files to Create/Modify:**
- `ngo_homesuite/services/grant_analytics_service.py` (new)
- `ngo_homesuite/web/reports_routes.py` (add grant pipeline endpoint)

---

## Milestone E: Competitive Polish (Sprint 5 – 2 weeks)

### 🟡 TICKET E-1: Campaign Projection Engine  
**Priority:** P2  
**Estimate:** 10 points  
**Acceptance Criteria:**
- [ ] Input: goal ($ or # donors), historical avg donation, assumed conversion rate.
- [ ] Output: projected raised, confidence interval (±10%), days to goal.
- [ ] Chart: cumulative raised over time (if on pace).
- [ ] Scenario: "what if conversion increases 20%?" → new projection.

**Technical Approach:**
- Regression model: fit historical donation trend → extrapolate.
- Fallback: use simple linear projection if insufficient history.

**Files to Create/Modify:**
- `ngo_homesuite/services/campaign_projection_service.py` (new)
- `ngo_homesuite/web/campaign_routes.py` (add projection endpoint)

---

### 🟢 TICKET E-2: Event Discount Code Management  
**Priority:** P2  
**Estimate:** 8 points  
**Acceptance Criteria:**
- [ ] Model: code, discount (% or $), usage limit, expiry date, active flag.
- [ ] Code generation: random or custom.
- [ ] Validation on event registration: apply discount if code valid + not expired + under limit.
- [ ] Tracking: which code was used for which registration.
- [ ] Report: discount code usage by code (count, revenue impact).

**Technical Approach:**
- Model: `EventDiscountCode(event_id, code, discount_type, discount_value, usage_limit, expires_at)`.
- Pre-registration validation: check code in middleware or service.

**Files to Create/Modify:**
- `ngo_homesuite/models/core.py` (add EventDiscountCode)
- `ngo_homesuite/services/event_registration_service.py` (add discount validation)
- `ngo_homesuite/migrations/add_event_discount_codes.py` (new)

---

## Testing & QA Strategy

Each ticket includes:
- Unit tests (model, service logic)
- Integration tests (API endpoints)
- E2E tests (happy path + error cases)
- **Test coverage target:** ≥85% for new code

Regression suite:
- Run full suite after each milestone.
- Tag new tests with `@pytest.mark.milestone_X`.
- Monitor baseline: all 668 tests must remain passing.

---

## Risk & Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Email deliverability issues (bounces, spam) | High | High | Early integration with mailgun/sendgrid sandbox; audit bounce rates weekly. |
| Budget transaction volume (1000s per grant) | Medium | Medium | Index optimization; query caching; batch operations. |
| 2FA usability (user lockout) | Medium | Medium | Backup codes documented; admin recovery flow; dry-run before enforcement. |
| Scope creep on reporting | High | Low | Fix ticket scope per milestone; defer "nice-to-have" to Q3. |

---

## Success Metrics

- **Security:** 100% of admins enrolled in 2FA within 2 weeks of release.
- **Operations:** 50%+ of grants with budget lines created within 4 weeks.
- **Engagement:** 1st email campaign sent within 3 weeks; 20%+ open rate on pilot.
- **Test health:** maintain ≥85% coverage; zero regression failures.

---

## Rollback Plan

Each milestone has a rollback branch (e.g., `rollback/milestone-A`) tagged at the point of release. If critical issue occurs:
1. Identify failed feature (2FA, budgets, email).
2. Checkout rollback branch → hotfix → retest.
3. Merge rollback to main if regression too severe.
4. Schedule postmortem + root cause analysis.

---

## Next Steps

### Priority Queue (Actionable)

1. **P0: Security assurance and closure evidence**
	- Produce a release-candidate security evidence bundle: DAST artifact, security lane output, manual pentest notes, and risk sign-off.
	- Complete external review path (or explicit risk acceptance) and link evidence in `docs/production_checklist.md`.
2. **P0: MFA hardening follow-through**
	- Enforce encryption-at-rest for TOTP secret storage and document key management/rotation.
	- Expand endpoint-level adoption of step-up authentication for sensitive actions.
3. **P0: Campaign maturity gaps**
	- Implement advanced donor segmentation/query-builder scope from Milestone C-1.
	- Close queue orchestration/retry model deltas that remain from C-2/C-3 planning assumptions.
4. **P1: Grants operationalization**
	- Add complete operational UX/reporting around budget variance/reconciliation.
	- Prioritize state/funder-specific automation pathways and closeout/compliance packaging.
5. **P1: Test and release confidence hardening**
	- Replace skipped legacy integration-journey coverage with active end-to-end paths.
	- Eliminate or explicitly gate remaining RBAC/test skips before release decisions.
6. **P2: Repository hygiene and fallback reduction**
	- Relocate root-level debug/probe scripts to a tooling sandbox and keep out release workflows.
	- Continue reducing legacy fallback dependency surfaces, keeping emergency toggles audited and off by default.

### Immediate Evidence Baseline

- Targeted release-gate lane (security/tenant/campaign/grants contracts) executed on 2026-05-18: **112 passed, 2 skipped, 0 failed**.
- Skips were both in `ngo_homesuite/web/test_rbac_audit_matrix.py` and tied to runtime/feature availability, not assertion failures.

---

**Owner:** [Your team]  
**Last Updated:** 2026-06-04  
**Status:** Active; release documentation synchronized and evidence refreshed, with external pentest sign-off still pending for strict production gate
