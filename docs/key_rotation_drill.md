# Key Rotation Drill (Non-Prod)

Run this drill in staging or another non-production environment before any production release that touches database encryption, secret handling, or recovery procedures.

## Preconditions

- A non-production database copy exists.
- A fresh backup has been taken and validated.
- Old and new SQLCipher-compatible keys are stored in your secret manager.
- Rotation operator identity is recorded for the audit trail.

## Commands

Linux/macOS:

```bash
export NGO_HOMESUITE_OLD_KEY='hex:<old-key>'
export NGO_HOMESUITE_NEW_KEY='hex:<new-key>'
python -m ngo_homesuite.db.cron_safe_rotate_db_key
```

Windows PowerShell:

```powershell
$env:NGO_HOMESUITE_OLD_KEY = 'hex:<old-key>'
$env:NGO_HOMESUITE_NEW_KEY = 'hex:<new-key>'
python -m ngo_homesuite.db.connection
```

## Validate After Rotation

1. Application can reconnect with the new key.
2. Application cannot reconnect with the old key.
3. Backup/restore drill still passes against the rotated database.
4. Provenance/audit metadata records the key rotation event.
5. Monitoring captures the rotation window and any failures.

## Rollback Drill

If rotation validation fails:

1. Stop application writers.
2. Restore from the verified pre-rotation backup.
3. Confirm database integrity.
4. Re-run application startup checks with the old key.
5. Record incident details and do not proceed to production until root cause is resolved.

## Release Policy

Treat a failed key-rotation drill as release-blocking.