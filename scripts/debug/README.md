# Debug Script Index

Root-level underscore scripts were moved to `scripts/debug/root_legacy_probes/` to keep the repository root focused on product code.

## Running a legacy probe

Run probes from the repository root so relative imports and local paths behave as expected.

```powershell
.venv\Scripts\python.exe scripts\debug\root_legacy_probes\_dash_check.py
```

## Notes

- Files in `root_legacy_probes` are developer diagnostics and one-off verification tools.
- These scripts are not part of production runtime paths.
- Prefer adding new diagnostics under `scripts/debug/` instead of the repository root.
