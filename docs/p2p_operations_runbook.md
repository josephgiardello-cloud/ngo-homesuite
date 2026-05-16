# P2P Operations Runbook

Last updated: 2026-05-16

This runbook covers day-to-day operation and guardrails for staff-managed P2P pages.

## Scope

- Staff dashboard workflow: create -> publish -> close
- Public page behavior and embed script usage
- Incident handling for malformed page data and broken public slugs

## Staff Workflow

1. Create page from `/p2p/manage` with donor owner and title.
2. Verify draft page metadata in dashboard list.
3. Publish only after content review and goal verification.
4. Close page when campaign ends or abuse/spam is detected.

## Validation Guardrails

Service-level validation now enforces:

- title must be non-empty and <= 180 chars
- goal must be non-negative
- story must be <= 5000 chars
- slug fallback to `fundraiser` when source text cannot slugify

## Public Surface Checks

- Public page returns JSON and HTML depending on `Accept`.
- Embed script uses origin-relative iframe source.
- Host header values must not appear in generated embed output.

## Release Validation

Run these before release:

```bash
pytest ngo_homesuite/web/test_sprint1_features.py ngo_homesuite/tests/test_gap_analysis_services.py::TestP2PFundraising -v --maxfail=10
```

## Incident Response

- If malformed content causes rendering errors, close the page from dashboard.
- If a slug collision or invalid slug behavior is observed, recreate page and capture logs.
- If cross-tenant linkage is suspected, run tenant-boundary gate and review audit events.
