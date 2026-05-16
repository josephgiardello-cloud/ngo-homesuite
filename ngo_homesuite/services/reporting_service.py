
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Mapping, cast

from sqlalchemy import func, select
from werkzeug.exceptions import NotFound

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
        beneficiary_count = db.session.scalar(
            select(func.count(Beneficiary.id)).where(
                Beneficiary.organization_id == organization_id,
                Beneficiary.status == "active",
            )
        ) or 0
        project_count = db.session.scalar(
            select(func.count(Project.id)).where(
                Project.organization_id == organization_id,
                Project.status == "active",
            )
        ) or 0
        donor_count = db.session.scalar(
            select(func.count(Donor.id)).where(Donor.organization_id == organization_id)
        ) or 0
        total_donations = db.session.scalar(
            select(func.coalesce(func.sum(Donation.amount), 0.0)).where(Donation.organization_id == organization_id)
        ) or 0.0
        total_budget = db.session.scalar(
            select(func.coalesce(func.sum(Project.budget), 0.0)).where(Project.organization_id == organization_id)
        ) or 0.0
        total_expenses = db.session.scalar(
            select(func.coalesce(func.sum(Expense.amount), 0.0)).where(Expense.organization_id == organization_id)
        ) or 0.0
        total_funds = db.session.scalar(
            select(func.count(Fund.id)).where(Fund.organization_id == organization_id, Fund.is_active.is_(True))
        ) or 0
        recent_donations = cast(
            list[Donation],
            db.session.scalars(
                select(Donation)
                .where(Donation.organization_id == organization_id)
                .order_by(Donation.donation_date.desc())
                .limit(recent_donations_limit)
            ).all(),
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
        donor = db.session.scalars(
            select(Donor).where(Donor.id == donor_id, Donor.organization_id == organization_id).limit(1)
        ).first()
        if donor is None:
            raise NotFound()
        donation_count = db.session.scalar(
            select(func.count(Donation.id)).where(
                Donation.organization_id == organization_id,
                Donation.donor_id == donor.id,
            )
        ) or 0
        total_amount = db.session.scalar(
            select(func.coalesce(func.sum(Donation.amount), 0.0)).where(
                Donation.organization_id == organization_id,
                Donation.donor_id == donor.id,
            )
        ) or 0.0
        recent_donations = cast(
            list[Donation],
            db.session.scalars(
                select(Donation)
                .where(Donation.organization_id == organization_id, Donation.donor_id == donor.id)
                .order_by(Donation.donation_date.desc())
                .limit(recent_limit)
            ).all(),
        )
        recurring_plans = cast(
            list[RecurringDonationPlan],
            db.session.scalars(
                select(RecurringDonationPlan)
                .where(
                    RecurringDonationPlan.organization_id == organization_id,
                    RecurringDonationPlan.donor_id == donor.id,
                )
                .order_by(RecurringDonationPlan.created_at.desc())
            ).all(),
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

    def financial_overview(self, organization_id: int) -> dict[str, Any]:
        total_donations = db.session.scalar(
            select(func.coalesce(func.sum(Donation.amount), 0.0)).where(Donation.organization_id == organization_id)
        ) or 0.0
        total_expenses = db.session.scalar(
            select(func.coalesce(func.sum(Expense.amount), 0.0)).where(Expense.organization_id == organization_id)
        ) or 0.0
        net_total = float(total_donations) - float(total_expenses)

        monthly_donations: dict[str, float] = defaultdict(float)
        monthly_expenses: dict[str, float] = defaultdict(float)

        donations = cast(
            list[Donation],
            db.session.scalars(select(Donation).where(Donation.organization_id == organization_id)).all(),
        )
        expenses = cast(
            list[Expense],
            db.session.scalars(select(Expense).where(Expense.organization_id == organization_id)).all(),
        )

        for donation in donations:
            if donation.donation_date:
                key = donation.donation_date.strftime("%Y-%m")
                monthly_donations[key] += float(donation.amount or 0)
        for expense in expenses:
            if expense.paid_at:
                key = expense.paid_at.strftime("%Y-%m")
                monthly_expenses[key] += float(expense.amount or 0)

        labels = sorted(set(list(monthly_donations.keys()) + list(monthly_expenses.keys())))
        chart_data: dict[str, Any] = {
            "labels": labels,
            "donations": [round(monthly_donations[label], 2) for label in labels],
            "expenses": [round(monthly_expenses[label], 2) for label in labels],
            "net": [round(monthly_donations[label] - monthly_expenses[label], 2) for label in labels],
            "totals": {
                "donations": round(float(total_donations), 2),
                "expenses": round(float(total_expenses), 2),
                "net": round(net_total, 2),
            },
        }
        return {
            "total_donations": float(total_donations),
            "total_expenses": float(total_expenses),
            "net_total": net_total,
            "chart_data": chart_data,
        }

    def donation_purpose_totals(self, organization_id: int) -> list[tuple[str, float]]:
        donations = cast(
            list[Donation],
            db.session.scalars(select(Donation).where(Donation.organization_id == organization_id)).all(),
        )
        totals: dict[str, float] = defaultdict(float)
        for donation in donations:
            if donation.purpose:
                totals[str(donation.purpose)] += float(donation.amount or 0.0)
        return sorted([(purpose, round(total, 2)) for purpose, total in totals.items()], key=lambda x: x[0])

    def foundation_donor_totals(self, organization_id: int) -> list[tuple[str, float]]:
        donors = cast(
            list[Donor],
            db.session.scalars(
                select(Donor).where(
                    Donor.organization_id == organization_id,
                    Donor.donor_type == "foundation",
                )
            ).all(),
        )
        donor_names = {int(d.id): str(d.name or "") for d in donors if d.id is not None}
        if not donor_names:
            return []
        donations = cast(
            list[Donation],
            db.session.scalars(select(Donation).where(Donation.organization_id == organization_id)).all(),
        )
        totals: dict[str, float] = defaultdict(float)
        for donation in donations:
            donor_id = int(donation.donor_id) if donation.donor_id is not None else None
            if donor_id is None:
                continue
            donor_name = donor_names.get(donor_id)
            if donor_name is None:
                continue
            totals[donor_name] += float(donation.amount or 0.0)
        return sorted([(name, round(total, 2)) for name, total in totals.items()], key=lambda x: x[0])

    def project_donation_counts(self, organization_id: int) -> dict[int, int]:
        donations = cast(
            list[Donation],
            db.session.scalars(select(Donation).where(Donation.organization_id == organization_id)).all(),
        )
        counts: dict[int, int] = defaultdict(int)
        for donation in donations:
            if donation.project_id is not None:
                counts[int(donation.project_id)] += 1
        return dict(counts)
