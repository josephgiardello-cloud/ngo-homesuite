# NGO HomeSuite

Python 3.13 staff CLI plus a versioned FastAPI API and same-origin browser workspace.
The initial web release covers donors, donations, allocations, projects, grants, users,
and audit history. The original SQLite file is **not automatically migrated by the API**.

## Install and validate

From PowerShell, using the project's selected Python environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe tools\release_gate.py
```

The VS Code **Release candidate gates** task runs the same checks as CI.
Tests use temporary databases, never the organization's real database.

## Provision organizations

Back up existing data first. Take a consistent SQLite backup rather than copying an
open database without its WAL. Stop writers before migrating. Migration failures abort
the whole upgrade and must be resolved before retrying; duplicate donor emails are one
example. Supported inputs are the unversioned legacy CLI schema (version 0) and version 1.
Version 2 is the current schema; newer unknown versions are rejected.

```powershell
# Fresh organization; prompts securely for the first admin password.
.\.venv\Scripts\python.exe -m backend.app.provision .\alpha.db --tenant alpha --username admin
# Create the second admin, authenticated by the first admin.
.\.venv\Scripts\python.exe -m backend.app.provision .\alpha.db --tenant alpha --username payer --role admin --admin-username admin
# Explicitly migrate an existing database without creating a user:
.\.venv\Scripts\python.exe -m backend.app.provision .\ngo_database.db --tenant default
```

Create a private `tenants.json` mapping trusted organization aliases to **distinct**
database files, relative to the manifest directory:

```json
{
  "alpha": "alpha.db",
  "beta": "beta.db"
}
```

Provision each listed database separately. Each organization has its own users.
Do not map the same file, symlink, or hard link to multiple organizations. Keep aliases
stable: they are part of external event IDs. The manifest is server configuration;
there is no HTTP endpoint that registers databases or accepts database paths.

```powershell
$env:NGOHS_TENANTS_FILE = "$PWD\tenants.json"
# Only for loopback development without HTTPS:
$env:NGOHS_INSECURE_LOCAL = "1"
.\.venv\Scripts\python.exe -m uvicorn backend.app.server:app_factory --factory --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. Default cookies require HTTPS. In production, unset
`NGOHS_INSECURE_LOCAL`, terminate TLS through a trusted proxy, restrict database/manifest
filesystem permissions, and set `NGOHS_ALLOWED_HOSTS` to the comma-separated public
hostnames. Do not expose the development listener directly to the Internet.
The existing CLI SQLCipher connection option uses `NGOHS_DB_KEY` and a separately
installed driver. The new service/API suite is currently validated with standard
SQLite only; encrypted deployments require driver-specific validation before release.

## CLI and domain contracts

```powershell
$env:NGOHS_DB_PATH = "$PWD\alpha.db"
$env:NGOHS_TENANT = "alpha"
.\.venv\Scripts\python.exe -m ngo_homesuite.main
```

The CLI defaults to `ngo_database.db` and organization alias `default`. Use the same alias
in the server manifest. The CLI migrates its selected database on startup, as before.
Donation receipt/idempotency keys are shown before saving; reuse a key only for the same
request. A generated key must be saved if the operation needs to be retried.
Amounts must be finite, positive, and have at most two decimals. Allocations can leave
part of a donation unassigned, but cannot exceed its total.

Grant drafts are created by admins or fundraisers. Admin approval reserves project
budget. Only a **different admin** can mark an approved grant paid. Payment is an
accounting transition, not an integration with a bank. Cancelling an unpaid grant
releases its reservation; paid grants are immutable. Project spent updates must cover
paid grants and cannot consume reserved funds. Grant CLI menu entries are 40–42.

## API v1

Open `/docs` for request contracts. Login takes `tenant`, `username`, and `password`.
Authenticated operations use an opaque HttpOnly cookie; mutation requests also need
the `X-CSRF-Token` returned by login or `/api/v1/session`. Roles are re-read at the service
boundary; clients cannot supply an organization or role on mutation bodies.

Money inputs are **decimal strings**. Donation creation requires `idempotency_key`.
Repeated identical requests return the same `{ "id": ... }` with HTTP 201 and create
no extra financial record or audit event. Changed payloads with the same key return 409.
Other successful mutations return `{ "ok": true }`. Domain failures use
`{ "detail": "..." }`; malformed requests use FastAPI's standard 422 validation response.
Collections currently return the most recent 500 records; this initial staff workspace
does not implement pagination or bulk export.

## Durable events

Every business mutation in the shared domain commits its business state, audit row, and outbox
event together. The worker drains the outbox to a separate durable SQLite integration
inbox:

```powershell
.\.venv\Scripts\python.exe -m backend.app.worker .\tenants.json alpha .\integration-inbox.db
```

Schedule this command under an operator-managed job runner. Delivery is **at least
once**, not exactly once: a consumer must deduplicate `organization:event_id`.
The included inbox does so and rejects conflicting payloads for an existing ID.
Failure is logged, persisted in `outbox.last_error`, and returned as a failed process.
Pending events survive retries, worker downtime, and crashes between delivery and
acknowledgment. No external delivery destination is silently assumed.

## Architecture ownership and evidence

| Component | Accountable owner role | Contract | Executable evidence |
|---|---|---|---|
| `domain/services.py` | Domain maintainer | Authorization, money limits, donation idempotency, grant transitions; no transport I/O | Invariant/property/state-machine tests; CLI and API shared-path tests |
| `domain/store.py` | Persistence maintainer | Trusted database-per-organization registry; scoped transactions and current schema only | Tenant, concurrency, migration-version tests |
| `db/schema.py` | Persistence maintainer | Atomic, ordered migrations; no swallowed schema errors | Fresh install, v0/v1 upgrade, failure/recovery and future-version tests |
| `domain/identity.py` | Authentication maintainer | Tenant-bound opaque sessions, login backoff, persisted roles, first-admin bootstrap | API cookie, CSRF, revocation, login and CLI user tests |
| `domain/outbox.py`, `backend/app/worker.py` | Integration maintainer | Durable producer, stable delivery IDs, visible failures, idempotent receiver | Actual process-crash tests and configured worker tests |
| `backend/app/api.py` | API maintainer | Thin v1 adapters, validation, same-origin boundary, explicit response shapes | OpenAPI and API contract tests |
| `backend/app/static/` | Staff experience maintainer | Role-aware staff journeys; text-only rendering of user data | Chromium donation/allocation/audit and two-admin grant journeys |
| `tools/release_gate.py` | Release maintainer | Fail-closed lint, type, dependencies, offline secrets, all test layers | Shared local/CI release task |

These are accountable **roles**, not invented staff assignments. The team must assign
named people before operational handover. No AI adapter exists in this repository;
future AI/job adapters must call these services with an authenticated actor instead
of writing financial tables directly.

### Explicit remaining boundaries

This release does not claim that every historical CLI feature has been hardened.
Campaign/pledge administration, donor tags/interactions, volunteers, payroll, exports,
and backups remain CLI-only legacy paths. Their existing best-effort audit helper is
not part of the transactional service guarantee. No web/API adapter exposes those
write paths. They require a subsequent service extraction before offering equivalent
API/AI access or claiming application-wide audit completeness.

SQLite has no row-level security. Isolation here is physical database selection behind
authenticated sessions, not tenant-ID filters. This does not defend against an operator
with filesystem access or a compromised application process; stronger infrastructure
separation is needed for that threat model.

Type checks cover the new domain, API, and migrations; legacy code retains its existing
typing debt. Lint scans all source, with four explicit F401 compatibility/baseline
exceptions listed in `pyproject.toml`. Offline secret scanning never verifies candidates
against third-party services. Dependency scanning sends public package names/versions
only. Release gates must pass before tagging a release candidate; CI requires a GitHub
repository, which this workspace is not currently connected to.
