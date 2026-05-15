from ngo_homesuite.db.engine import get_session
from ngo_homesuite.db.models import Report

def fetch_reports(report_type: str):
    with get_session() as session:
        return (
            session.query(Report)
            .filter(Report.type == report_type)
            .all()
        )
