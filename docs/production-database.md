# Production Database Guide

This project now supports SQLite, PostgreSQL, and MySQL/MariaDB as first-class runtime backends.

## 1. Install DB drivers

For production relational backends:

```bash
pip install -r requirements-db.txt
```

## 2. Configure environment

Required in production:

- `FLASK_ENV=production`
- `SECRET_KEY=<strong-random-value>`
- `DATABASE_URL_FILE=<path-to-secret-file-containing-db-connection-url>`

Examples:

```bash
# PostgreSQL
DATABASE_URL_FILE=/run/secrets/database_url
DB_BACKEND=postgresql

# PostgreSQL with psycopg3 driver
DATABASE_URL_FILE=/run/secrets/database_url

# Legacy provider format is also accepted and normalized at runtime
DATABASE_URL_FILE=/run/secrets/database_url

# MySQL / MariaDB
DATABASE_URL_FILE=/run/secrets/database_url
DB_BACKEND=mysql
```

The secret file should contain the full DSN for the target backend and must not be committed.

## 3. Configure connection pooling

Optional tuning variables:

- `DB_POOL_SIZE` (default: `10`)
- `DB_MAX_OVERFLOW` (default: `20`)
- `DB_POOL_TIMEOUT_SEC` (default: `30`)
- `DB_POOL_RECYCLE_SEC` (default: `1800`)
- `DB_POOL_PRE_PING` (default: `true`)

## 4. Run schema migrations

Use the deploy migration runner for PostgreSQL and MySQL deployments:

```bash
python -m ngo_homesuite.db.deploy_migrate upgrade
```

Rollback one Alembic step:

```bash
python -m ngo_homesuite.db.deploy_migrate downgrade --revision -1
```

Note: startup auto-migration helper is SQLite-only. For non-SQLite backends, use the deploy migration runner in CI/CD before starting the web process.

For Docker Compose deployments, run the dedicated migrator service before the app:

```bash
docker compose run --rm migrator
docker compose up -d app
```

The default Compose contract enforces this ordering automatically via `depends_on: migrator: service_completed_successfully`.

For SQLite deployments, continue to use `python -m ngo_homesuite.db.migrate` and the backup/restore drill for rollback validation.

## 5. Validate connection and startup

Run smoke tests after deployment:

```bash
pytest ngo_homesuite/test_runtime_config.py -v
```

## 6. Operational recommendations

- Enable backups/snapshots at the database platform level.
- Set connection limits in the DB server to match application pool settings.
- Use read replicas for reporting-heavy workloads where available.
- Monitor pool saturation and query latency via app + DB telemetry.
