
from ngo_homesuite.db.repositories.reports import fetch_reports

class ReportingService:
    def generate_report(self, report_type, params, actor):
        rows = fetch_reports(report_type)
        return [r.id for r in rows]
