# Backup/Restore Drill (Non-Prod)

Run this drill regularly in non-production to validate your restore path.

## Command

```bash
python -m ngo_homesuite.db.backup_restore_drill --db data/homesuite.db --output-dir backups/drills
```

## What It Validates

- backup copy can be created from source SQLite DB
- restored copy can be materialized from backup
- `PRAGMA integrity_check` returns `ok` on restored DB
- table count matches between source and restored DB

## Operational Guidance

- Run weekly in staging/non-prod.
- Keep the latest drill output files for incident practice.
- Escalate immediately if integrity or table-count checks fail.
- Treat a failed drill as release-blocking until resolved.

## Release Gate Pairing

Backup/restore drill validation is not sufficient on its own. Pair it with the key-rotation drill in [docs/key_rotation_drill.md](C:/Users/josep/OneDrive/Desktop/Codes/ngo-homesuite/docs/key_rotation_drill.md) before production release sign-off.
