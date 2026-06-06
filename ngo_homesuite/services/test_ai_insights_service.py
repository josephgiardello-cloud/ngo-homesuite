from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ngo_homesuite.services.ai_insights_service import AIInsightsService


@dataclass
class _Row:
    id: int
    amount: float


def test_outlier_scores_records_prioritizes_extreme_values():
    rows = [
        _Row(id=1, amount=10.0),
        _Row(id=2, amount=11.0),
        _Row(id=3, amount=9.5),
        _Row(id=4, amount=200.0),
    ]

    scored = AIInsightsService._outlier_scores_records(rows, value_getter=lambda row: row.amount)

    assert scored
    top_record, top_score = scored[0]
    assert top_record.id == 4
    assert 0.0 <= top_score <= 1.0


def test_predict_release_readiness_reads_artifacts(tmp_path: Path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "release-evidence-bundle.json").write_text("{}", encoding="utf-8")
    (artifacts / "current_state.json").write_text("{}", encoding="utf-8")
    (tmp_path / "coverage.xml").write_text(
        '<coverage line-rate="0.85" branch-rate="0.70"></coverage>',
        encoding="utf-8",
    )

    section = AIInsightsService.predict_release_readiness(project_root=str(tmp_path))

    assert section.title == "Proactive release-readiness intelligence"
    assert section.items
    first = section.items[0]
    assert "release_risk" in first
    assert first["state"] in {"ready", "watch", "at_risk"}
