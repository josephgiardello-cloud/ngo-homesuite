
from __future__ import annotations

from typing import Any, Mapping

from ngo_homesuite.db.repositories.reports import fetch_reports

class ReportingService:
    def generate_report(
        self,
        report_type: str,
        params: Mapping[str, Any],
        actor: str,
        organization_id: int | None = None,
    ) -> list[int]:
        rows = fetch_reports(report_type, organization_id=organization_id)
        return [r.id for r in rows]
