import json
from pathlib import Path

import pytest

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
