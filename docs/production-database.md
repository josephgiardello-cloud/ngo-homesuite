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
- `DATABASE_URL=<db-connection-url>`

Examples:

```bash
# PostgreSQL
DATABASE_URL=postgresql://ngo_user:secret@db-host:5432/ngo_homesuite
DB_BACKEND=postgresql

# PostgreSQL with psycopg3 driver
DATABASE_URL=postgresql+psycopg://ngo_user:secret@db-host:5432/ngo_homesuite

# Legacy provider format is also accepted and normalized at runtime
DATABASE_URL=postgres://ngo_user:secret@db-host:5432/ngo_homesuite

# MySQL / MariaDB
DATABASE_URL=mysql+pymysql://ngo_user:secret@db-host:3306/ngo_homesuite
DB_BACKEND=mysql
```

## 3. Configure connection pooling

Optional tuning variables:

- `DB_POOL_SIZE` (default: `10`)
- `DB_MAX_OVERFLOW` (default: `20`)
- `DB_POOL_TIMEOUT_SEC` (default: `30`)
- `DB_POOL_RECYCLE_SEC` (default: `1800`)
- `DB_POOL_PRE_PING` (default: `true`)

## 4. Run schema migrations

Use Flask-Migrate/Alembic for PostgreSQL and MySQL deployments:

```bash
flask db upgrade
```

Note: startup auto-migration helper is SQLite-only. For non-SQLite backends, use migration commands in CI/CD before starting the web process.

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
