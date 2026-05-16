# Dependency Update Policy

## Goals

- keep runtime dependencies pinned and auditable
- prevent drift between `pyproject.toml` and requirements bundles
- ship updates in small, reviewable batches

## Source of Truth

- Runtime dependency declarations live in both:
  - `pyproject.toml` under `[project].dependencies`
  - requirements bundles (`requirements-core.txt`, `requirements-db.txt`, `requirements-ai.txt`, `requirements-cloud.txt`)

These must stay aligned.

## CI Guard

- CI executes `python tools/check_dependency_drift.py`.
- Build fails if any dependency is missing in `pyproject.toml` or has a mismatched specifier.

## Update Cadence

- Dependabot opens weekly pip dependency PRs.
- Security updates should be prioritized and merged quickly.
- Non-security updates should be grouped by subsystem where practical.

## Review Requirements

- Every dependency bump must pass full test suite.
- For major version bumps, include a short risk note in PR description.
- Update changelog/release notes when dependency changes are operationally significant.
