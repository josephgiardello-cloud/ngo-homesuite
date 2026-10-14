import json
from pathlib import Path

import pytest

from tools import write_candidate_evidence
from tools.verify_audit_retention_policy import verify_policy
from tools.write_candidate_evidence import test_report_summary as summarize_report

ROOT = Path(__file__).resolve().parents[2]


def test_actual_pending_policy_blocks_production_without_authorizing_a_purge():
    policy = json.loads((ROOT / "docs" / "audit_retention_policy.json").read_text())
    verify_policy(policy, strict=False)
    with pytest.raises(ValueError, match="approval"):
        verify_policy(policy, strict=True)
    policy["status"] = "approved"
    with pytest.raises(ValueError, match="security_owner"):
        verify_policy(policy, strict=True)


@pytest.mark.parametrize("tag", ["failure", "error", "skipped"])
def test_candidate_manifest_rejects_unsuccessful_or_skipped_test_evidence(tmp_path, tag):
    report = tmp_path / "results.xml"
    report.write_text(f"<testsuites><testsuite><testcase><{tag}/></testcase></testsuite></testsuites>")
    with pytest.raises(ValueError, match="without failures"):
        summarize_report(report)


def test_candidate_manifest_requires_executed_tests_and_hashes_reports(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text("<testsuites><testsuite><testcase name='real'/></testsuite></testsuites>")
    result = summarize_report(report)
    assert result["passed"] == 1
    assert len(result["sha256"]) == 64
    report.write_text("<testsuites/>")
    with pytest.raises(ValueError, match="executed tests"):
        summarize_report(report)


@pytest.mark.parametrize("candidate,trigger,accepted", [
    ("a" * 40, "b" * 40, True),
    (None, "a" * 40, True),
    ("b" * 40, "a" * 40, False),
    (None, "b" * 40, False),
    ("invalid", "a" * 40, False),
])
def test_candidate_manifest_binds_the_tested_checkout_not_the_pr_merge(
    tmp_path, monkeypatch, candidate, trigger, accepted,
):
    head = "a" * 40
    for name in ("tenant.xml", "encryption.xml"):
        (tmp_path / name).write_text("<testsuites><testsuite><testcase name='proof'/></testsuite></testsuites>")
    monkeypatch.setattr("sys.argv", [
        "write_candidate_evidence.py", "--directory", str(tmp_path), "--engine", "postgresql",
    ])
    outputs = {
        ("git", "rev-parse", "HEAD"): head,
        ("git", "status", "--porcelain", "--untracked-files=no"): "",
    }
    monkeypatch.setattr(
        write_candidate_evidence.subprocess, "check_output",
        lambda command, **kwargs: outputs[tuple(command)],
    )
    monkeypatch.setenv("GITHUB_SHA", trigger)
    if candidate is None:
        monkeypatch.delenv("CANDIDATE_COMMIT_SHA", raising=False)
    else:
        monkeypatch.setenv("CANDIDATE_COMMIT_SHA", candidate)
    if not accepted:
        with pytest.raises(ValueError, match="differs from checked-out commit"):
            write_candidate_evidence.main()
        assert not (tmp_path / "manifest.json").exists()
        return
    assert write_candidate_evidence.main() == 0
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["commit_sha"] == head
    assert manifest["trigger_sha"] == trigger


def test_candidate_manifest_rejects_modified_tracked_sources(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", [
        "write_candidate_evidence.py", "--directory", str(tmp_path), "--engine", "postgresql",
    ])
    outputs = {
        ("git", "rev-parse", "HEAD"): "a" * 40,
        ("git", "status", "--porcelain", "--untracked-files=no"): " M source.py",
    }
    monkeypatch.setattr(
        write_candidate_evidence.subprocess, "check_output",
        lambda command, **kwargs: outputs[tuple(command)],
    )
    with pytest.raises(ValueError, match="modified tracked worktree"):
        write_candidate_evidence.main()


@pytest.mark.parametrize("name", ["Dockerfile.ci", "Dockerfile.ci-sqlcipher"])
def test_ci_images_use_valid_exec_form_pytest_commands(name):
    command = (ROOT / name).read_text().rsplit("\nCMD ", 1)[1].replace("\\\n", "")
    arguments = json.loads(command)
    assert arguments[0] == "pytest"
    assert "ngo_homesuite/" in arguments
    assert "tests/" in arguments
