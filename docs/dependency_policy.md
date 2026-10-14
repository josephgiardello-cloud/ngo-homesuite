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

CI tests Python 3.10–3.13, matching the declared Python 3.10 minimum.
The NumPy lower bound must admit a Python 3.10-compatible release; NumPy
2.4 and newer cannot be the unconditional minimum for that matrix.

Keep the LangChain family on compatible `langchain-core` ranges. LangChain
0.3.27 requires core below 1.0, whereas the pinned Ollama integration 1.1.0
requires core 1.2.21 or newer. The runtime bundle therefore uses LangChain
1.0.0. This major-version update needs full RAG integration validation before
release. Likewise, the pinned boto3 and botocore versions must resolve together.
The Ollama integration requires `ollama>=0.6.1`; Chroma 0.5.5 required NumPy
below 2.0 while the Python 3.13 retrieval stack requires NumPy 2.1 or newer.
Chroma 0.6.3 removes that contradictory upper bound. Existing Chroma indexes
must be backed up and upgrade-tested before adopting this retrieval bundle.

CI Docker images install these same runtime bundles, plus the pytest plugins
they execute, into `/opt/venv` so the non-root test user can access them.
The Linux x86_64 encrypted image additionally installs the `sqlcipher` extra's
`sqlcipher3-binary==0.6.0`, which provides a maintained Python 3.13 wheel.
The adapter prefers `sqlcipher3` and retains explicitly installed legacy
`pysqlcipher3` support; Windows/native legacy installs are not certified by the
Linux image. Encryption never falls back silently to ordinary SQLite.
`requirements.lock.txt` is a historical environment snapshot, not the CI image
installation source (it includes platform-specific and unrelated packages).

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


## Security Auditing

- CI runs pip-audit to check for known vulnerabilities in the dependency tree.
- Gitleaks checkouts fetch complete history so pull-request commit ranges can
  be scanned. A missing revision or partial scan is an operational failure,
  not evidence that the candidate is free of leaked credentials.
