# Candidate CI failure classification

Historical candidate: `bf587256e4999c967797b0ce5b1ba5bb6103dc02`, PR #53 (now merged).
Evidence: [Tests run 38613643520](https://github.com/josephgiardello-cloud/ngo-homesuite/actions/runs/38613643520).
All 32 failed job logs were downloaded and classified. Credentials are not
included in this report. This review is not a passing release certification.

## Full failed-job classification

| Failed jobs | Count | Observed failure | What was not tested |
|---|---:|---|---|
| Python 3.8 test jobs, Ubuntu/Windows/macOS | 3 | Pinned Flask 3.1.3 does not support Python 3.8 | Application tests |
| Python 3.9 test jobs, Ubuntu/Windows/macOS | 3 | Pinned Flask-WTF 1.3.0 requires Python >=3.10 | Application tests |
| Python 3.10 test jobs, Ubuntu/Windows/macOS | 3 | NumPy >=2.4.0 has no compatible Python 3.10 release | Application tests |
| Python 3.11, 3.12, 3.13 test jobs, Ubuntu/Windows/macOS | 9 | LangChain 0.3.27 requires core <1.0; Ollama integration 1.1.0 requires core >=1.2.21 | Application tests |
| Code Quality | 1 | Same LangChain dependency resolution failure | Ruff/type checks |
| Workflow Supply Chain Policy | 1 | Same dependency resolution failure | Workflow policy |
| Tenant Boundary Gate | 1 | Same dependency resolution failure | Tenant regressions |
| Critical Paths (donations/audit/encryption) | 1 | Same dependency resolution failure | Financial/encryption tests |
| DB Policy (unencrypted), DB Policy (encrypted-policy) | 2 | Same dependency resolution failure | DB policy tests |
| OpenAPI Contract + Client Generation | 1 | Same dependency resolution failure | OpenAPI/client compatibility |
| Performance Smoke | 1 | Same dependency resolution failure | Runtime performance |
| DAST Smoke | 1 | Same dependency resolution failure | Dynamic security probes |
| Scalability Benchmark | 1 | Same dependency resolution failure | Load thresholds |
| Container Security | 1 | Image dependency installation fails on the same conflicting LangChain requirements | Image vulnerability scan |
| Docker Matrix CI [plaintext], Docker Matrix CI [sqlcipher] | 2 | Dockerfiles copy nonexistent `requirements.lock` | Image builds and tests |
| Security Checks | 1 | Gitleaks cannot resolve the parent of the candidate in shallow history | Meaningful secret/dependency scanning |

## Secret-scan classification

The Gitleaks log shows an unknown revision for `bf58725^..bf58725`, approximately
zero bytes scanned, a partial scan, and unexpected exit code 1. Its
"no leaks found in partial scan" message is **not** evidence of a clean scan.
The hosted failure did not establish a real leaked credential or a fixture hit.

The fix fetches full Git history in secret-scan checkouts. An official
Gitleaks 8.24.3 binary, checked against its published checksum, was then run
locally with redaction against the exact candidate commit range and the full
committed PR branch range. Both completed with exit 0 and zero findings.
This applies only to those scanned committed ranges, not all historic commits,
untracked data, production secrets, or the future candidate.

A separate directory-mode local scan also included the ignored virtualenv.
It reported dependency-code patterns and four existing synthetic key values
in configuration tests, plus the old synthetic key baked into the encrypted
CI Dockerfile. The test fixtures were inspected in their masking/config-loading
contexts, not assumed to be real credentials. The image key was removed and is
now generated at runtime. No production credential was confirmed in this
exercise, so there is no identified credential to revoke. Do not suppress a
future real finding; revoke/rotate it at its provider and assess history.

### Complete fetched-history follow-up (2026-10-14)

A checksum-verified official Gitleaks 8.24.3 binary scanned every fetched Git ref
with `git --log-opts="--all"` after confirming the checkout was not shallow:
283 commits, approximately 6.56 MB. The initial complete scan exited 1 with five
findings, not zero. Every historical source location was inspected:

| Historical commit | Source / lines | Classification |
| --- | --- | --- |
| `111f3fb240022fe9cfe4db3dd581a01e1f864ec7` | `Dockerfile.ci-sqlcipher`:55 | Literal synthetic CI encryption key in a testing image; removed from the current image, never approved for real data |
| `350437d6d771b39c4224269e8bb2d54ded8d331d` | `ngo_homesuite/config/test_config_manager.py`:145,152 | Explicit test environment setup and assertion for a synthetic encryption key |
| `350437d6d771b39c4224269e8bb2d54ded8d331d` | `ngo_homesuite/config/test_config_manager.py`:199,216 | Synthetic config masking/unmasking fixtures |

Only these five exact historical finding fingerprints are recorded in
`.gitleaksignore`; no directory, file or general credential rule is excluded.
The current config fixtures now generate disposable keys at runtime, while
preserving the loaded/masked/unmasked value assertions. The current CI image also
generates its key at runtime. No matching literal fixture key remains in the
candidate's source; history exceptions do not exempt future commits or values.
The reviewed complete-history rerun exited 0 with no unclassified findings.
No live provider credential was identified. Historical deployment of the CI
fixture key is not assessed: if it protected real data, operators must rotate
that key and assess affected copies. This local scan covers fetched reachable
history, not deleted/unreachable refs, ignored environments, or future commits.
The committed candidate must still run the hosted full-history scan. The initial
`--all` command did not explicitly include merge-resolution diffs. The corrected
command is `--all --full-history --diff-merges=first-parent`: against `6ad8b24`,
it scanned 338 commits (approximately 9.82 MB), with zero unclassified findings
and no additional exceptions. This supersedes the earlier history-coverage claim.

## Corrections and local evidence

- CI matrix now matches the declared Python 3.10 minimum.
- Compatible runtime requirements: LangChain 1.0.0, Ollama 0.6.1,
  botocore matching boto3 1.42.18, and NumPy >=2.2.0.
  The complete manifest resolves on the tested Windows/Python 3.13 environment.
  Resolution alone does not certify every platform or retrieval index upgrade.
- CI images install real bundled manifests and configured pytest plugins in
  `/opt/venv`, accessible to the non-root test user. Docker coverage extraction
  now reads the container that actually executed tests, not a fresh container,
  and missing evidence is an explicit failure.
- The plaintext Python 3.13 image built successfully. All 162 focused
  financial/audit/reporting/Minion/payment/CI-policy tests passed both locally
  and in that image. Chroma retrieval initialization was explicitly asserted,
  with networking disabled; actual embedding/model inference was not tested.
- The legacy pysqlcipher3 extension was reproduced as incompatible with Python
  3.13. The user selected modernization rather than reducing the encrypted
  test interpreter. The encrypted image uses the maintained `sqlcipher3-binary`
  adapter; actual encrypted-file, wrong-key, reopen and rekey tests are required.
  Its Python 3.13 Linux image built successfully; 26 connection/adapter
  regressions passed with no skips, including the real encryption/rekey cases.
  A subsequent image rebuild encountered a shared Docker extraction-cache
  error. The latest hermetic fixture was verified with a read-only bind mount
  into the successfully built image; no global Docker cache/reset was performed.
- Focused financial/connection mypy and new regression Ruff checks pass.
  The broad pre-existing Ruff policy is not clean and is not declared passing.

## Release remains blocked

The first follow-up candidate, `d27391b`, was committed, pushed and tested
locally; fresh draft PR #54 was opened because PRs #52/#53 were already merged.
Its PR checks were blocked by dependency merge conflicts with current main.
The user approved merging main: retain its Chroma 1.5.9, requests/chardet and
Black upgrades plus its reference lock file, while keeping pytest-asyncio 1.4.0
because 0.25.2 was demonstrated incompatible with the declared pytest 9.
The post-merge SHA requires fresh dependency resolution and test evidence;
`d27391b` artifacts are not certification of a later commit.

### Post-merge candidate `6ad8b24`

The approved merge was committed and pushed as
`6ad8b24bdb6be07db005bb27bcc3123f797cecb3`. Exact committed-source local results:
1,098 passed, two PostgreSQL skips and five explicitly deselected SQLCipher cases;
the separate PostgreSQL lane passed 31 without skips, and the Linux SQLCipher
lanes passed 29 boundary cases plus 13 adapter/connection cases without skips.
The encrypted image revision label matches the candidate. Dependencies resolve,
both drift guards pass, and real Chroma 1.5.9 initialization was confirmed with
outbound networking blocked. Retrieval upgrades and model inference are not certified.

Both jobs in [hosted isolation run 38646573452](https://github.com/josephgiardello-cloud/ngo-homesuite/actions/runs/38646573452)
passed, but their artifacts identify synthetic PR merge `419177cd`, not the
branch candidate. The follow-up workflow explicitly checks out the PR head,
binds the manifest to `CANDIDATE_COMMIT_SHA`, records the triggering merge SHA
separately, and names artifacts after the tested head. Neither the old hosted
artifacts nor the local `6ad8b24` reports certify the follow-up commit.

The complete 26-job log archive for
[Tests run 38646573343](https://github.com/josephgiardello-cloud/ngo-homesuite/actions/runs/38646573343)
was downloaded and its ZIP integrity verified. All 24 failed jobs were classified:

| Failed jobs | Count | Observed failure | What was not tested |
|---|---:|---|---|
| Python 3.10, Ubuntu/Windows/macOS | 3 | Import of Python 3.11-only `enum.StrEnum` | Application suite |
| Python 3.11, Ubuntu/Windows/macOS | 3 | Minion donor CSV f-string uses backslashes unsupported before Python 3.12 | Application suite |
| Python 3.12/3.13, Ubuntu/Windows/macOS | 6 | Required OpenAPI paths absent from the runtime URL map | Application suite |
| Tenant Boundary Gate | 1 | Same Minion syntax error during inventory-test collection | Tenant gate |
| Critical Paths | 1 | Same syntax error prevents route fixtures starting | Complete critical-path lane |
| OpenAPI Contract + Client Generation | 1 | Same syntax error prevents API-docs fixtures starting | Client generation |
| Code Quality | 1 | Same syntax error plus six undefined-name errors | Clean full-project lint |
| Docker Matrix CI, both modes | 2 | Images build, but an inline comment invalidates JSON-form CMD; shell executes `[pytest,` | Default image test command and coverage |
| DB Policy (encrypted-policy) | 1 | Plain sqlite3 migration runner explicitly rejects encrypted migrations | Encrypted migration preflight |
| Performance Smoke, Scalability Benchmark, DAST Smoke | 3 | Target service is unreachable/connection refused | Performance thresholds or DAST results |
| Security Checks | 1 | Bandit reports four high-severity findings and skips Minion due to syntax error; both secret-scan steps pass | Dependency audit and later dynamic checks |
| Container Security | 1 | Trivy installer exits 1 before scanning; image build succeeds | Image vulnerability assessment |

Dependency-installation failures are resolved in this run; downstream checks are
now exposing runtime, compatibility, scanner and operational failures instead.
Passing secret scans do not certify the skipped Bandit file or dependency audit.
The malformed Docker commands are corrected without disabling their test or
coverage requirements; the remaining baseline defects require separate fixes
and fresh hosted evidence. The tenant disposition and retention gates remain
deliberately blocked, with no invented approvals or historical record changes.

Rerunning the old SHA does not validate newer changes. Before release, run the
complete hosted workflow on the exact reviewed candidate SHA, including
dependency auditing, all platform tests, migrations, OpenAPI, DAST, container
security, performance and full lint/type gates. Newly exposed failures must
be fixed without turning failed checks into optional checks.

Repository administrators must enforce required passing checks in branch/ruleset
protection. Those hosted administrative settings were not changed or verified
here. Complete tenant isolation evidence, organization-approved audit retention,
historical recurring payment reconciliation, and supported encrypted upgrades
remain additional production prerequisites.
