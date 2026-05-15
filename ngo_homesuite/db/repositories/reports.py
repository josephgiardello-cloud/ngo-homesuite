from ngo_homesuite.db.engine import get_session
from ngo_homesuite.db.models import Report

def fetch_reports(report_type: str, organization_id: int | None = None):
    with get_session() as session:
        query = session.query(Report).filter(Report.type == report_type)
        if organization_id is not None:
            if not hasattr(Report, "organization_id"):
                raise PermissionError("Report backend is not organization-scoped; refusing cross-tenant query.")
            query = query.filter(Report.organization_id == organization_id)
        return query.all()
