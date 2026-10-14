# Finding 5: tenant-boundary inventory and remaining release blockers

This is an evidence inventory, **not a certification of complete tenant isolation**.
The machine-readable companion, `tenant_boundary_inventory.json`, includes every
discovered entry with its source path/line, classification, and explicit unknowns.
Source-file SHA-256 values identify the snapshot; regenerate after code changes.
The inventory does not execute handlers. Separate tests use synthetic PostgreSQL
16 and encrypted Linux SQLCipher databases; no real organization data or production
instance was used. Full surface verification remains incomplete.

## Repeatable offline inventory

From the repository root:

```powershell
.\.venv\Scripts\python.exe tools\inventory_tenant_boundaries.py --output docs\tenant_boundary_inventory.json
```

The tool scans first-party Python and SQL recursively, excluding tests, dependency
trees, hidden directories, build output, and caches. It introspects all routes in
an in-memory SQLite testing application, with seeding and schedulers disabled and
outbound socket connections denied. It also imports the lazily registered
`BootstrapToken` and the separate legacy `Report` model. It never executes
migration SQL. Optional/conditional routes are retained in the static inventory
even when absent from this runtime profile.

Snapshot counts:

| Surface | Entries | Classification / verified coverage |
| --- | ---: | --- |
| First-party source files | 322 | 282 Python, 40 SQL; zero Python parse errors |
| Declared and mapped ORM models | 77 | All 77 declarations represented in the mapper inventory |
| Models with `organization_id` | 66 | Includes lazily loaded `BootstrapToken` and excluded `User` |
| Models with `org_id` | 2 | Workflow instance/event records |
| Direct tenant ORM SELECT scopes | 67 | Discovered dynamically, replacing the nine-model allowlist |
| Inherited tenant-column models | 0 | Discovery checks mapper attributes, not only local-table columns |
| Indirect FK-owned ORM scopes | 4 | Explicit parent predicates and flush checks; persisted tests on both engines |
| Runtime routes / static route declarations | 429 / 435 | Every profile route and every discovered declaration inventoried; not all route boundaries tested |
| Export candidates | 125 | Conservative keyword/call evidence; not all actual export/download behavior verified |
| Worker/job/scheduling candidates | 89 | Includes scheduler registration sites and CLI/system candidates; not 89 deployed workers |
| Registered AI tools / static tool declarations | 31 / 31 | Handler locations and approval/mutation flags recorded; end-to-end tenant checks remain unverified |
| SQL/DML/dispatch callsite candidates | 710 | 551 execution/dispatch calls, 95 SQL text constructors, 64 DML/bulk candidates; overlapping evidence, not distinct SQL statements |
| Python SQL literals | 390 | Includes SQL passed through variables/helpers; execution not inferred |
| Static migration SQL statements | 283 | Trigger bodies retained as complete statements; never executed |
| Raw table declarations / unique names | 100 / 88 | 45 direct-column declarations, 6 metadata-only direct columns, 13 FK-owned candidates, 24 global-or-unknown tables |
| Production `skip_tenant_rls=` invocation candidates | 0 | Eight literal/check evidence records, including the listener/tool itself; existing tests intentionally exercise the bypass |

Local tenant tokens are recorded for each candidate. **A token is not an
authorization check; its absence is not proof of a leak.** Transitive service
authorization, imported aliases, generated SQL, runtime-only registrations,
non-Python/SQL artifacts and deployment configuration require separate review.
Candidates may be duplicates, shared-system operations or false positives.
Unknowns are deliberately retained, never silently marked safe.

The snapshot includes the modern SQLCipher adapter, Minion's explicit reconciliation
tenant argument, strict SQL boundaries, native RLS and scoped job runner. Paths are
portable repository-relative paths and source hashes are refreshed after changes.
Inventory/disposition tests validate repeatability and stale-review rejection.
These changes do not certify every raw connection or AI tool isolated.

## ORM coverage and explicit exclusions

`tenant_scoped_models()` discovers all currently registered Flask-SQLAlchemy
models with a mapped `organization_id` or `org_id`, including late registration
and inherited columns. Active tenant queries and aliases get bound criteria;
authenticated requests without an assigned tenant get false criteria. Workflow
`org_id` is compared as a string, matching its persisted representation.
Normal flush-based cross-tenant writes also inspect both column names.

`User` is explicitly excluded from **read** criteria because login, user loading
and bootstrap identity resolution happen before tenant membership is established.
This is not a blanket user-directory authorization exemption: tenant-owned
administrative reads must use explicit organization authorization. Existing
flush-time `User` organization checks remain in place.

Explicit indirect scopes:

| Model | Ownership chain enforced by ORM reads and flushes |
| --- | --- |
| `AIMessage` | `conversation_id` → `AIConversation.organization_id` |
| `DonationReceipt` | `donation_id` → `Donation.organization_id` |
| `P2PPageDonation` | Links both tenant-owned pages and donations; shortest FK path alone does not validate both tenants agree |
| `StewardshipStep` | `journey_id` → `StewardshipJourney.organization_id` |

Unscoped models retained in the inventory:

| Model | Ownership evidence / reason coverage is not claimed |
| --- | --- |
| `EventDiscountCode` | `event_id` is not a FK; legacy `events` ownership must be established explicitly |
| `SecurityAuditEvent` | `actor_org_id` and `resource_org_id` can differ; a shared security chain is not a single-tenant model. Authorized audit reads need explicit policy |
| `Organization` | Tenant root/public/bootstrap resolution; not automatically filtered by this listener |
| `WorkflowDefinitionRecord` | Shared/global-or-unknown definition records; no tenant column |
| `Report` | Separate declarative registry, no tenant column; the repository rejects tenant-bound queries, but unrestricted system calls are not tenant-isolated |

## Verified tests versus unknown coverage

The tenant-registry regressions execute criteria for **every registered direct
model under both tenants**, inspect bound SQL and missing-membership criteria,
and use two-row aliased SQL projections to verify filtering for both tenants.
They reject cross-tenant flushes for every direct model in both directions.
These generic projections/invalid-write guards are not full persisted
domain-lifecycle or cross-FK-consistency tests.

Persisted two-tenant reads, aliases, counts and ID lookups cover previously
omitted `Beneficiary`/`Volunteer`; workflow records cover `org_id` reads/writes.
Existing donation/grant and twenty cross-tenant route regressions also pass.
Bootstrap, login, public smoke and public Stripe checkout regressions pass:
**72 tests** in the combined targeted validation command.

There is no claim that all 429 routes, 125 export candidates, 31 AI tools,
89 worker candidates or 88 raw tables have negative two-tenant end-to-end
coverage. That missing coverage is a release blocker, not a completed checklist.

Persisted boundary tests cover indirect children, aliases/counts/IDs, FK and
relationship reparenting, two-parent P2P associations, raw/Core/bulk/engine SQL,
worker contexts and tenantless self-authentication exceptions. The narrow auth
exception permits only the actor's authentication fields on auth routes, never
role/tenant changes, another user or a non-auth route.

`docs/tenant_boundary_dispositions.json` deliberately has no approved reviews.
`tools/verify_tenant_dispositions.py --strict` currently rejects all 2,950
unreviewed inventory entries (overlapping evidence, not 2,950 unique endpoints).
Every approved disposition needs owner, reviewer, justification and existing
evidence; `tenant_scoped` requires both engines. Reviews bind to the complete
inventory digest. This procedural gate does not authenticate reviewer signatures.

## Audited background tenant context

`background_tenant_context(org_id, actor=..., reason=..., audit=...)` is an
**opt-in API**, not automatically wired into existing jobs. It:

- Requires an existing positive integer organization, a non-request Flask app context,
  nonempty actor/reason, and an audit callback.
- Rejects nested tenant contexts and pending ORM flush writes.
- Emits a start event before work; a failed start audit prevents work.
- Clears the session identity map at entry/exit so cached `Session.get()` rows
  cannot cross that boundary, and restores context on success or failure.
- Emits a finish event with completed/failed outcome; does not implicitly commit.

The integrating worker must supply a **durable** audit sink, authorize the
principal for the selected tenant, and commit explicitly inside the boundary.
Session removal rolls back any existing uncommitted transaction; callers must
finish prior work before entering this boundary, including raw/Core DML that
cannot be detected through ORM dirty-state checks.
An arbitrary callback is not proof of durable auditing. A finish-audit failure
propagates after context cleanup. Child threads do not automatically inherit
tenant context. Inside this boundary raw/Core SQL, bulk DML, direct-engine calls
and scope bypass are rejected; scoped ORM reads and validated flushes are allowed.

`events/scheduler.py` and `grants/scheduler.py` use `db/tenant_jobs.py` with an
explicit `TENANT_WORKER_ORGANIZATION_IDS` allowlist. Empty/invalid/duplicate
allowlists fail; durable canonical start/finish audits surround each tenant's
callback. Start-audit failure prevents work and callback failure is re-raised.
Legacy unowned event/reminder SQL is intentionally rejected before delivery;
the new runner is not a tenant migration of those queues. The generic
`utils/email_worker.py` creates and reads/writes `email_queue` and
`email_queue_dead_letter` without a tenant column. That queue cannot be isolated
merely by setting context. These require persisted tenant/principal identity,
explicit authorized per-tenant dispatch, durable audit events and two-tenant
retry/dead-letter tests before shared-tenant production use.

## Practical database isolation: deployment blocker

The ORM listener is application defense, not native RLS. Strict SQL guarding is
always active in audited worker contexts and opt-in for requests via
`TENANT_SQL_GUARD_ENABLED=1`. Do not enable it blindly: auth/global/public/bootstrap
paths need explicit contracts before tenantless requests can use this boundary.
Outside strict contexts, raw/Core/bulk paths, maintenance connections, null tenant
writes and cached identities retain limitations. Separate legacy SQLite cursor
connections are not intercepted. An ordinary request must not switch tenants
within one identity map. Full direct-FK consistency is not yet enforced.

Legacy declarations for `donations`, `donors`, `expenses`, `funds`, `projects`
and `volunteers` lack a declared tenant column even though their ORM metadata
has one. This is historical source evidence, not a finding about a deployed
schema. Legacy accounting exports in `utils/accounting_export.py` query entire
donation/expense tables through SQLite cursors, without a tenant predicate;
raw export connections are not protected by this listener. Legacy email/event
queues, event discount codes, audit tables and other global-or-unknown tables
must receive an explicit ownership/system-access decision.

PostgreSQL migration `20261014_0002` enables and forces 71 table policies: 65
integer-tenant tables, two string-tenant tables and four indirect chains. Both
`USING` and `WITH CHECK` bind to transaction-local `ngo.organization_id`; missing
context denies protected rows. Production web startup rejects superuser/BYPASSRLS
roles. Explicit migrator processes are separate from web-service initialization.
The frozen policy set excludes auth users, tenant roots, dual-org audit records and
legacy/unowned tables; it is not a complete application isolation certification.

PostgreSQL 16 proof uses a real `NOSUPERUSER NOBYPASSRLS` role, raw cross-tenant
reads/writes/deletes, missing tenant, indirect/two-parent constraints, injected
migration rollback/retry and data-preserving downgrade. The actual deployment
runner also proves fresh install/downgrade; `migrations/alembic.ini` supplies its
required logging configuration. Compiled savepoint recovery remains usable after
a failed statement. The combined PostgreSQL/migration run passed 35 tests.

SQLite has no native row policies. The tested compensating control rejects unsafe
session/engine SQL inside strict contexts and scopes persisted ORM operations,
including actual ciphertext/ordinary-SQLite denial. Linux SQLCipher boundary and
adapter/connection checks passed 42 tests with no skips (two PostgreSQL cases
deselected). This does not certify legacy cursor connections, all production ORM
configuration, key custody, in-flight rotation or every surface. Separate tenant
files/authorized routing may be needed for remaining legacy persistence; that
deployment design is not implemented or certified here.

Do not claim Finding 5 fully remediated or enable untrusted shared-database
multi-tenancy until the chosen database boundary, indirect ownership,
raw/DML callsites, all exposed surfaces and audited worker dispatch are tested.
