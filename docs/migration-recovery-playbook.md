# Migration Recovery Playbook

This playbook documents operational recovery for migration failures.

## Preconditions

- Always run preflight before applying migrations:
  - `python -m ngo_homesuite.db.migrate --dry-run --verify-backup --db-path <path>`
- Ensure backup policy is enabled in production:
  - `NGO_HOMESUITE_BACKUP_BEFORE_MIGRATE=1`
  - `NGO_HOMESUITE_REQUIRE_BACKUP_BEFORE_MIGRATE=1`
  - `NGO_HOMESUITE_RESTORE_BACKUP_ON_MIGRATION_FAIL=1`

## Normal Migration Flow

1. Run preflight and verify pending migration list.
2. Confirm backup file exists: `<db_path>.bak`.
3. Apply migrations:
   - `python -m ngo_homesuite.db.migrate --db-path <path>`
4. Verify app health and schema version table.

## Failure Scenarios

### 1) Migration SQL Failure

Symptoms:
- `MigrationApplyError` raised
- migration event `migration.apply.error`

Actions:
1. Stop application writes.
2. Confirm auto-restore happened (`migration.rollback.ok` event).
3. Validate DB opens and critical tables exist.
4. Fix migration SQL and rerun preflight.

### 2) Database Lock / Concurrent Access

Symptoms:
- `MigrationPlanError` with lock/access error
- sqlite `database is locked` style errors

Actions:
1. Stop competing writers/background jobs.
2. Retry with no concurrent DB workload.
3. If needed, temporarily increase migration timeout:
   - `NGO_HOMESUITE_MIGRATION_TIMEOUT_SEC=60`

### 3) Encrypted Database Key Present

Symptoms:
- `MigrationPlanError` indicating encrypted DB is unsupported by sqlite3 runner

Actions:
1. Do not run plaintext migration runner directly against SQLCipher DB.
2. Use SQLCipher-aware migration workflow.
3. If DB is plaintext, unset key env and rerun preflight:
   - unset `NGO_HOMESUITE_DB_KEY`

### 4) Backup Creation Failure

Symptoms:
- `MigrationBackupError`

Actions:
1. Verify DB file path exists and is writable.
2. Verify backup destination filesystem has space and permissions.
3. Keep `REQUIRE_BACKUP` enabled; do not bypass in production.

## Post-Recovery Verification

- Run smoke checks and key endpoints.
- Confirm expected `schema_version` rows and hashes.
- Confirm no unexpected drift in migration plan (`pending_count == 0`).

## Incident Record Template

- Timestamp (UTC):
- DB path:
- Migration version attempted:
- Error class/message:
- Backup path used:
- Restore performed (yes/no):
- Root cause:
- Corrective action:
- Preventive action:
