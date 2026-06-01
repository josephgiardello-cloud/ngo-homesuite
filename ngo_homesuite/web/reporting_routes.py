"""Reporting routes: funder reports, scheduled reports."""
from __future__ import annotations

from datetime import date

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ngo_homesuite.web.auth_routes import require_step_up_auth
from ngo_homesuite.web.rbac import roles_required


reporting_bp = Blueprint("reporting", __name__, url_prefix="/reports")


def _org_id() -> int:
    return int(current_user.organization_id)


# ---------------------------------------------------------------------------
# Funder Report
# ---------------------------------------------------------------------------

@reporting_bp.get("/funder")
@login_required
def funder_report_route():
    """Generate a funder-specific report.

    Query params:
      funder_name (required)
      start       YYYY-MM-DD (optional, defaults to Jan 1 current year)
      end         YYYY-MM-DD (optional, defaults to today)
    """
    from ngo_homesuite.services.advanced_reporting_service import funder_report

    funder_name = (request.args.get("funder_name") or "").strip()
    if not funder_name:
        return jsonify({"error": "funder_name is required"}), 400

    start_str = request.args.get("start")
    end_str = request.args.get("end")

    start = date.fromisoformat(start_str) if start_str else None
    end = date.fromisoformat(end_str) if end_str else None

    report = funder_report(_org_id(), funder_name, start_date=start, end_date=end)
    return jsonify(report)


# ---------------------------------------------------------------------------
# Scheduled Reports
# ---------------------------------------------------------------------------

@reporting_bp.get("/scheduled")
@login_required
def list_scheduled_reports_route():
    from ngo_homesuite.services.advanced_reporting_service import list_scheduled_reports

    items = list_scheduled_reports(_org_id())
    return jsonify([
        {
            "id": r.id,
            "name": r.name,
            "report_type": r.report_type,
            "frequency": r.frequency,
            "delivery_email": r.delivery_email,
            "is_active": r.is_active,
            "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
            "next_run_at": r.next_run_at.isoformat() if r.next_run_at else None,
        }
        for r in items
    ])


@reporting_bp.post("/scheduled")
@login_required
@roles_required("admin", "staff")
def create_scheduled_report_route():
    from ngo_homesuite.services.advanced_reporting_service import create_scheduled_report

    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"error": "name is required"}), 400
    if not data.get("report_type"):
        return jsonify({"error": "report_type is required"}), 400
    if not data.get("frequency"):
        return jsonify({"error": "frequency is required"}), 400

    report = create_scheduled_report(
        _org_id(),
        data["name"],
        data["report_type"],
        data["frequency"],
        delivery_email=data.get("delivery_email"),
        parameters=data.get("parameters"),
        created_by_id=current_user.id,
    )
    return jsonify({
        "id": report.id,
        "name": report.name,
        "report_type": report.report_type,
        "frequency": report.frequency,
        "next_run_at": report.next_run_at.isoformat() if report.next_run_at else None,
    }), 201


@reporting_bp.patch("/scheduled/<int:report_id>")
@login_required
@roles_required("admin", "staff")
def update_scheduled_report_route(report_id: int):
    from ngo_homesuite.services.advanced_reporting_service import update_scheduled_report

    data = request.get_json(silent=True) or {}
    report = update_scheduled_report(report_id, _org_id(), **data)
    return jsonify({"id": report.id, "name": report.name, "is_active": report.is_active})


@reporting_bp.delete("/scheduled/<int:report_id>")
@login_required
@roles_required("admin")
@require_step_up_auth
def delete_scheduled_report_route(report_id: int):
    from ngo_homesuite.services.advanced_reporting_service import delete_scheduled_report

    delete_scheduled_report(report_id, _org_id())
    return ("", 204)


# ---------------------------------------------------------------------------
# Longitudinal / Impact Trends
# ---------------------------------------------------------------------------

@reporting_bp.get("/trends/giving")
@login_required
def giving_trends_route():
    """Year-by-year donation totals and donor counts."""
    from ngo_homesuite.services.advanced_reporting_service import giving_summary_by_year

    return jsonify(giving_summary_by_year(_org_id()))


@reporting_bp.get("/trends/retention")
@login_required
def retention_trends_route():
    """Donor retention rate for a given year."""
    from ngo_homesuite.services.advanced_reporting_service import donor_retention_rate

    year = request.args.get("year", type=int)
    return jsonify(donor_retention_rate(_org_id(), year=year))
