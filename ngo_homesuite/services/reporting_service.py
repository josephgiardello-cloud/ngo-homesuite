
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, cast

from sqlalchemy import func

from ngo_homesuite.db.repositories.reports import fetch_reports
from ngo_homesuite.models.core import Beneficiary, Donation, Donor, Expense, Fund, Project, RecurringDonationPlan, db


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

    def organization_dashboard_summary(self, organization_id: int, *, recent_donations_limit: int = 5) -> dict[str, Any]:
        beneficiary_count = (
            db.session.query(func.count(Beneficiary.id))
            .filter(Beneficiary.organization_id == organization_id, Beneficiary.status == "active")
            .scalar()
            or 0
        )
        project_count = (
            db.session.query(func.count(Project.id))
            .filter(Project.organization_id == organization_id, Project.status == "active")
            .scalar()
            or 0
        )
        donor_count = (
            db.session.query(func.count(Donor.id))
            .filter(Donor.organization_id == organization_id)
            .scalar()
            or 0
        )
        total_donations = (
            db.session.query(func.coalesce(func.sum(Donation.amount), 0.0))
            .filter(Donation.organization_id == organization_id)
            .scalar()
            or 0.0
        )
        total_budget = (
            db.session.query(func.coalesce(func.sum(Project.budget), 0.0))
            .filter(Project.organization_id == organization_id)
            .scalar()
            or 0.0
        )
        total_expenses = (
            db.session.query(func.coalesce(func.sum(Expense.amount), 0.0))
            .filter(Expense.organization_id == organization_id)
            .scalar()
            or 0.0
        )
        total_funds = (
            db.session.query(func.count(Fund.id))
            .filter(Fund.organization_id == organization_id, Fund.is_active.is_(True))
            .scalar()
            or 0
        )
        recent_donations = cast(
            list[Donation],
            Donation.query.filter_by(organization_id=organization_id)
            .order_by(Donation.donation_date.desc())
            .limit(recent_donations_limit)
            .all(),
        )
        return {
            "beneficiary_count": int(beneficiary_count),
            "project_count": int(project_count),
            "donor_count": int(donor_count),
            "total_donations": float(total_donations),
            "total_budget": float(total_budget),
            "total_expenses": float(total_expenses),
            "net_cashflow": float(total_donations) - float(total_expenses),
            "total_funds": int(total_funds),
            "recent_donations": recent_donations,
        }

    def donor_profile_summary(self, organization_id: int, donor_id: int, *, recent_limit: int = 10) -> dict[str, Any]:
        donor = Donor.query.filter_by(id=donor_id, organization_id=organization_id).first_or_404()
        aggregate_row = (
            db.session.query(func.count(Donation.id), func.coalesce(func.sum(Donation.amount), 0.0))
            .filter_by(organization_id=organization_id, donor_id=donor.id)
            .first()
            or (0, 0.0)
        )
        donation_count, total_amount = cast(tuple[int, float], aggregate_row)
        recent_donations = cast(
            list[Donation],
            Donation.query.filter_by(organization_id=organization_id, donor_id=donor.id)
            .order_by(Donation.donation_date.desc())
            .limit(recent_limit)
            .all(),
        )
        recurring_plans = cast(
            list[RecurringDonationPlan],
            RecurringDonationPlan.query.filter_by(organization_id=organization_id, donor_id=donor.id)
            .order_by(RecurringDonationPlan.created_at.desc())
            .all(),
        )
        dates: list[datetime] = [d.donation_date for d in recent_donations if d.donation_date is not None]
        return {
            "donor": donor,
            "donation_count": donation_count,
            "donation_total": float(total_amount),
            "recent_donations": recent_donations,
            "recurring_plans": recurring_plans,
            "first_gift_date": min(dates).date().isoformat() if dates else None,
            "last_gift_date": max(dates).date().isoformat() if dates else None,
            "active_recurring_plans": sum(1 for plan in recurring_plans if plan.status == "active"),
        }
