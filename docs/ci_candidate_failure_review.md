# Candidate CI failure classification

Candidate: `bf587256e4999c967797b0ce5b1ba5bb6103dc02`, draft PR #52.
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
The committed candidate must still run the hosted full-history scan.

## Corrections and local evidence

- CI matrix now matches the declared Python 3.10 minimum.
- Compatible runtime requirements: LangChain 1.0.0, Ollama 0.6.1,
  Chroma 0.6.3, botocore matching boto3 1.42.18, and NumPy >=2.2.0.
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

These changes are not yet a new pushed candidate. Rerunning the old SHA does
not validate the working-tree fixes. Before release, commit/push the reviewed
candidate and rerun the complete hosted workflow on its exact SHA, including
dependency auditing, all platform tests, migrations, OpenAPI, DAST, container
security, performance and full lint/type gates. Newly exposed failures must
be fixed without turning failed checks into optional checks.

Repository administrators must enforce required passing checks in branch/ruleset
protection. Those hosted administrative settings were not changed or verified
here. Complete tenant isolation evidence, organization-approved audit retention,
historical recurring payment reconciliation, and supported encrypted upgrades
remain additional production prerequisites.
