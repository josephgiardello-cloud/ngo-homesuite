
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, cast

from flask import current_app, has_app_context
from sqlalchemy import desc, func, select
from werkzeug.exceptions import NotFound

from ngo_homesuite.db.repositories.reports import fetch_reports
from ngo_homesuite.models.core import (
    Beneficiary,
    Donation,
    Donor,
    Expense,
    Fund,
    ProgramCase,
    Project,
    RecurringDonationPlan,
    Task,
    db,
)
from ngo_homesuite.services.donor_signal_service import get_donor_signal


class ReportingService:
    _dashboard_cache: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}

    @staticmethod
    def _safe_amount(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def generate_report(
        self,
        report_type: str,
        params: Mapping[str, Any],
        actor: str,
        organization_id: int | None = None,
    ) -> list[int]:
        rows = fetch_reports(report_type, organization_id=organization_id)
        return [r.id for r in rows]

    def organization_dashboard_summary(
        self,
        organization_id: int,
        *,
        recent_donations_limit: int = 5,
        period: str = "30d",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        recent_donations_limit = max(1, min(50, int(recent_donations_limit)))
        cache_ttl_seconds = int(
            (current_app.config.get("REPORTING_DASHBOARD_CACHE_TTL_SECONDS", 15) if has_app_context() else 15) or 0
        )
        cache_key = (
            int(organization_id),
            int(recent_donations_limit),
            str(period or "30d"),
            start_date.isoformat() if start_date else None,
            end_date.isoformat() if end_date else None,
        )
        now_epoch = datetime.now(timezone.utc).timestamp()
        if cache_ttl_seconds > 0:
            cached = self._dashboard_cache.get(cache_key)
            if cached is not None:
                expires_at, payload = cached
                if now_epoch < float(expires_at):
                    return payload

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        start_30d = now - timedelta(days=30)
        start_prev_30d = now - timedelta(days=60)
        start_90d = now - timedelta(days=90)
        start_prev_90d = now - timedelta(days=180)
        start_ytd = datetime(year=now.year, month=1, day=1)
        ytd_days = max(1, (now.date() - start_ytd.date()).days + 1)
        start_prev_ytd = start_ytd - timedelta(days=ytd_days)

        def _sum_donations(start: datetime, end: datetime | None = None) -> float:
            query = select(func.coalesce(func.sum(Donation.amount), 0.0)).where(
                Donation.organization_id == organization_id,
                Donation.donation_date >= start,
            )
            if end is not None:
                query = query.where(Donation.donation_date < end)
            return float(db.session.scalar(query) or 0.0)

        def _sum_expenses(start: datetime, end: datetime | None = None) -> float:
            query = select(func.coalesce(func.sum(Expense.amount), 0.0)).where(
                Expense.organization_id == organization_id,
                Expense.paid_at >= start,
            )
            if end is not None:
                query = query.where(Expense.paid_at < end)
            return float(db.session.scalar(query) or 0.0)

        def _delta_pct(current: float, previous: float) -> float | None:
            if previous <= 0:
                return 0.0 if current == 0 else 100.0
            return round(((current - previous) / previous) * 100.0, 1)

        def _safe_pct(numerator: float, denominator: float) -> float:
            if denominator <= 0:
                return 0.0
            return round((numerator / denominator) * 100.0, 1)

        def _trend_window(start: datetime, prev_start: datetime, *, end: datetime | None = None) -> dict[str, float | None]:
            donations_current = _sum_donations(start, end)
            expenses_current = _sum_expenses(start, end)
            donations_previous = _sum_donations(prev_start, start)
            expenses_previous = _sum_expenses(prev_start, start)
            return {
                "donations": donations_current,
                "expenses": expenses_current,
                "net": donations_current - expenses_current,
                "previous_donations": donations_previous,
                "previous_expenses": expenses_previous,
                "previous_net": donations_previous - expenses_previous,
                "donations_delta_pct": _delta_pct(donations_current, donations_previous),
                "expenses_delta_pct": _delta_pct(expenses_current, expenses_previous),
                "net_delta_pct": _delta_pct(donations_current - expenses_current, donations_previous - expenses_previous),
            }

        def _bucketed_amounts(kind: str, start: datetime, end: datetime, *, buckets: int = 6, scale: float = 1.0) -> list[float]:
            if end <= start:
                return [0.0] * buckets
            if kind == "donations":
                rows = db.session.execute(
                    select(Donation.donation_date, Donation.amount)
                    .where(
                        Donation.organization_id == organization_id,
                        Donation.donation_date >= start,
                        Donation.donation_date < end,
                    )
                ).all()
            else:
                rows = db.session.execute(
                    select(Expense.paid_at, Expense.amount)
                    .where(
                        Expense.organization_id == organization_id,
                        Expense.paid_at >= start,
                        Expense.paid_at < end,
                    )
                ).all()
            totals = [0.0] * buckets
            span_seconds = max(1.0, (end - start).total_seconds())
            for row in rows:
                when = row[0]
                amount = float(row[1] or 0.0)
                if when is None:
                    continue
                offset = max(0.0, min(span_seconds, (when - start).total_seconds()))
                idx = min(buckets - 1, int((offset / span_seconds) * buckets))
                totals[idx] += amount
            return [round(value * scale, 2) for value in totals]

        def _monthly_overview(months: int = 6) -> dict[str, Any]:
            month_start = datetime(year=now.year, month=now.month, day=1)
            labels: list[str] = []
            month_windows: list[tuple[datetime, datetime, str]] = []
            for offset in range(months - 1, -1, -1):
                anchor_month = month_start.month - offset
                anchor_year = month_start.year
                while anchor_month <= 0:
                    anchor_month += 12
                    anchor_year -= 1
                start = datetime(year=anchor_year, month=anchor_month, day=1)
                if anchor_month == 12:
                    end = datetime(year=anchor_year + 1, month=1, day=1)
                else:
                    end = datetime(year=anchor_year, month=anchor_month + 1, day=1)
                label = start.strftime("%b")
                labels.append(label)
                month_windows.append((start, end, label))

            donations_values: list[float] = []
            expenses_values: list[float] = []
            net_values: list[float] = []
            for start, end, _label in month_windows:
                d = _sum_donations(start, end)
                e = _sum_expenses(start, end)
                donations_values.append(round(d, 2))
                expenses_values.append(round(e, 2))
                net_values.append(round(d - e, 2))

            max_value = max([1.0, *donations_values, *expenses_values])
            return {
                "labels": labels,
                "donations": donations_values,
                "expenses": expenses_values,
                "net": net_values,
                "max_value": float(max_value),
            }

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
        donation_transaction_count = int(
            db.session.scalar(select(func.count(Donation.id)).where(Donation.organization_id == organization_id)) or 0
        )
        avg_gift_amount = float(total_donations) / donation_transaction_count if donation_transaction_count > 0 else 0.0
        recurring_active_plans = int(
            db.session.scalar(
                select(func.count(RecurringDonationPlan.id)).where(
                    RecurringDonationPlan.organization_id == organization_id,
                    RecurringDonationPlan.status == "active",
                )
            )
            or 0
        )
        recurring_plan_rows = db.session.execute(
            select(RecurringDonationPlan.amount, RecurringDonationPlan.frequency).where(
                RecurringDonationPlan.organization_id == organization_id,
                RecurringDonationPlan.status == "active",
            )
        ).all()
        recurring_monthly_commitment = 0.0
        for amount_raw, frequency_raw in recurring_plan_rows:
            amount = float(amount_raw or 0.0)
            frequency = str(frequency_raw or "").strip().lower()
            if frequency == "monthly":
                recurring_monthly_commitment += amount
            elif frequency == "quarterly":
                recurring_monthly_commitment += amount / 3.0
            elif frequency == "yearly":
                recurring_monthly_commitment += amount / 12.0
        recent_donation_rows = db.session.execute(
            select(
                Donation.id,
                Donation.donor_name,
                Donation.currency,
                Donation.amount,
                Donation.donation_date,
                Donation.status,
            )
            .where(Donation.organization_id == organization_id)
            .order_by(Donation.donation_date.desc())
            .limit(recent_donations_limit)
        ).all()
        recent_donations = [
            {
                "id": int(row.id),
                "donor_name": row.donor_name,
                "currency": row.currency,
                "amount": float(row.amount or 0.0),
                "donation_date": row.donation_date,
                "status": row.status,
            }
            for row in recent_donation_rows
        ]

        trend_30d = _trend_window(start_30d, start_prev_30d)
        trend_90d = _trend_window(start_90d, start_prev_90d)
        trend_ytd = _trend_window(start_ytd, start_prev_ytd)

        normalized_period = str(period or "30d").strip().lower()
        if normalized_period not in {"30d", "90d", "ytd", "custom"}:
            normalized_period = "30d"

        custom_start = start_date
        custom_end = end_date
        if custom_start is not None:
            custom_start = custom_start.replace(hour=0, minute=0, second=0, microsecond=0)
        if custom_end is not None:
            custom_end = custom_end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        custom_valid = custom_start is not None and custom_end is not None and custom_end > custom_start
        if normalized_period == "custom" and not custom_valid:
            normalized_period = "30d"

        period_labels = {
            "30d": "Last 30 Days",
            "90d": "Last 90 Days",
            "ytd": "Year to Date",
            "custom": "Custom Range",
        }

        if normalized_period == "custom" and custom_start is not None and custom_end is not None:
            custom_days = max(1, (custom_end - custom_start).days)
            custom_prev_start = custom_start - timedelta(days=custom_days)
            period_focus = _trend_window(custom_start, custom_prev_start, end=custom_end)
            focus_start = custom_start
            focus_end = custom_end
        elif normalized_period == "90d":
            period_focus = trend_90d
            focus_start = start_90d
            focus_end = now
        elif normalized_period == "ytd":
            period_focus = trend_ytd
            focus_start = start_ytd
            focus_end = now
        else:
            period_focus = trend_30d
            focus_start = start_30d
            focus_end = now

        if normalized_period == "30d":
            comparison_label = "90-Day Run Rate (30D Equivalent)"
            comparison_donations = float(trend_90d["donations"] or 0.0) / 3.0
            comparison_expenses = float(trend_90d["expenses"] or 0.0) / 3.0
            comparison_net = comparison_donations - comparison_expenses
            comparison_donations_delta = _delta_pct(float(period_focus["donations"] or 0.0), comparison_donations)
            comparison_expenses_delta = _delta_pct(float(period_focus["expenses"] or 0.0), comparison_expenses)
            comparison_net_delta = _delta_pct(float(period_focus["net"] or 0.0), comparison_net)
            comparison_start = start_90d
            comparison_end = now
            comparison_scale = 1.0 / 3.0
        elif normalized_period == "90d":
            comparison_label = "Previous 90 Days"
            comparison_donations = float(trend_90d["previous_donations"] or 0.0)
            comparison_expenses = float(trend_90d["previous_expenses"] or 0.0)
            comparison_net = float(trend_90d["previous_net"] or 0.0)
            comparison_donations_delta = cast(float | None, trend_90d["donations_delta_pct"])
            comparison_expenses_delta = cast(float | None, trend_90d["expenses_delta_pct"])
            comparison_net_delta = cast(float | None, trend_90d["net_delta_pct"])
            comparison_start = start_prev_90d
            comparison_end = start_90d
            comparison_scale = 1.0
        elif normalized_period == "custom" and custom_start is not None and custom_end is not None:
            comparison_label = "Previous Matching Window"
            custom_days = max(1, (custom_end - custom_start).days)
            comparison_start = custom_start - timedelta(days=custom_days)
            comparison_end = custom_start
            comparison_scale = 1.0
            comparison_donations = _sum_donations(comparison_start, comparison_end)
            comparison_expenses = _sum_expenses(comparison_start, comparison_end)
            comparison_net = comparison_donations - comparison_expenses
            comparison_donations_delta = _delta_pct(float(period_focus["donations"] or 0.0), comparison_donations)
            comparison_expenses_delta = _delta_pct(float(period_focus["expenses"] or 0.0), comparison_expenses)
            comparison_net_delta = _delta_pct(float(period_focus["net"] or 0.0), comparison_net)
        else:
            comparison_label = "Prior YTD Window"
            comparison_donations = float(trend_ytd["previous_donations"] or 0.0)
            comparison_expenses = float(trend_ytd["previous_expenses"] or 0.0)
            comparison_net = float(trend_ytd["previous_net"] or 0.0)
            comparison_donations_delta = cast(float | None, trend_ytd["donations_delta_pct"])
            comparison_expenses_delta = cast(float | None, trend_ytd["expenses_delta_pct"])
            comparison_net_delta = cast(float | None, trend_ytd["net_delta_pct"])
            comparison_start = start_prev_ytd
            comparison_end = start_ytd
            comparison_scale = 1.0

        focus_series = {
            "donations": _bucketed_amounts("donations", focus_start, focus_end, buckets=6),
            "expenses": _bucketed_amounts("expenses", focus_start, focus_end, buckets=6),
        }
        comparison_series = {
            "donations": _bucketed_amounts("donations", comparison_start, comparison_end, buckets=6, scale=comparison_scale),
            "expenses": _bucketed_amounts("expenses", comparison_start, comparison_end, buckets=6, scale=comparison_scale),
        }

        latest_donation_date = db.session.scalar(
            select(func.max(Donation.donation_date)).where(Donation.organization_id == organization_id)
        )
        missing_donation_dates = int(
            db.session.scalar(
                select(func.count(Donation.id)).where(
                    Donation.organization_id == organization_id,
                    Donation.donation_date.is_(None),
                )
            )
            or 0
        )

        fundraising_goal = float(total_budget) if float(total_budget) > 0 else max(10000.0, float(total_donations) * 1.2 or 10000.0)
        expense_cap = float(total_budget) if float(total_budget) > 0 else max(float(total_expenses) * 1.25, 5000.0)
        fundraising_progress = 0.0 if fundraising_goal <= 0 else min(100.0, round((float(total_donations) / fundraising_goal) * 100.0, 1))
        expense_progress = 0.0 if expense_cap <= 0 else min(100.0, round((float(total_expenses) / expense_cap) * 100.0, 1))

        alerts: list[dict[str, str]] = []
        if float(total_donations) - float(total_expenses) < 0:
            alerts.append({
                "level": "warning",
                "title": "Expenses are above total donations",
                "detail": "Review spending cadence or run a fundraising push for cashflow stability.",
            })
        if int(project_count) == 0:
            alerts.append({
                "level": "info",
                "title": "No active projects",
                "detail": "Activate at least one project so program tracking and reporting are meaningful.",
            })
        if int(donor_count) == 0:
            alerts.append({
                "level": "info",
                "title": "No donors recorded",
                "detail": "Add your first donors to unlock engagement and retention tracking.",
            })
        if missing_donation_dates > 0:
            alerts.append({
                "level": "warning",
                "title": "Some donations are missing dates",
                "detail": f"{missing_donation_dates} donation record(s) do not have donation_date set.",
            })
        if latest_donation_date and latest_donation_date < (now - timedelta(days=60)):
            alerts.append({
                "level": "info",
                "title": "No recent donation activity",
                "detail": "The latest donation is older than 60 days.",
            })

        monthly_overview = _monthly_overview(months=6)
        expense_ratio_pct = _safe_pct(float(total_expenses), max(float(total_donations), 1.0))
        net_margin_pct = _safe_pct(float(total_donations) - float(total_expenses), max(float(total_donations), 1.0))
        recurring_penetration_pct = _safe_pct(float(recurring_active_plans), max(float(donor_count), 1.0))
        beneficiaries_per_project = round(float(beneficiary_count) / max(float(project_count), 1.0), 2)
        data_completeness_pct = max(0.0, round(100.0 - _safe_pct(float(missing_donation_dates), max(float(donation_transaction_count), 1.0)), 1))
        stale_days = 0
        if latest_donation_date:
            stale_days = max(0, int((now - latest_donation_date).days))

        category_metrics = {
            "fundraising": {
                "total_donations": float(total_donations),
                "transactions": donation_transaction_count,
                "avg_gift": round(avg_gift_amount, 2),
                "recurring_monthly_commitment": round(recurring_monthly_commitment, 2),
                "score": max(0.0, min(100.0, round((_safe_pct(float(recurring_monthly_commitment), max(float(total_donations), 1.0)) * 0.4) + (50.0 if donation_transaction_count > 0 else 0.0), 1))),
            },
            "financial_health": {
                "net_cashflow": float(total_donations) - float(total_expenses),
                "expense_ratio_pct": expense_ratio_pct,
                "net_margin_pct": net_margin_pct,
                "score": max(0.0, min(100.0, round(60.0 + (net_margin_pct * 0.6) - max(0.0, expense_ratio_pct - 80.0), 1))),
            },
            "program_delivery": {
                "active_projects": int(project_count),
                "active_beneficiaries": int(beneficiary_count),
                "beneficiaries_per_project": beneficiaries_per_project,
                "score": max(0.0, min(100.0, round(min(100.0, beneficiaries_per_project * 12.0) if project_count > 0 else 0.0, 1))),
            },
            "donor_engagement": {
                "total_donors": int(donor_count),
                "active_recurring_plans": recurring_active_plans,
                "recurring_penetration_pct": recurring_penetration_pct,
                "score": max(0.0, min(100.0, round((recurring_penetration_pct * 1.5) + (40.0 if donor_count > 0 else 0.0), 1))),
            },
            "operations": {
                "missing_donation_dates": missing_donation_dates,
                "days_since_last_donation": stale_days,
                "data_completeness_pct": data_completeness_pct,
                "score": max(0.0, min(100.0, round((data_completeness_pct * 0.8) + (20.0 if stale_days <= 30 else 5.0), 1))),
            },
        }

        lifecycle_window_days = max(1, (focus_end - focus_start).days)
        lifecycle_prev_start = focus_start - timedelta(days=lifecycle_window_days)
        lifecycle_prev_end = focus_start

        current_donor_ids = {
            int(donor_id)
            for donor_id in db.session.execute(
                select(Donation.donor_id)
                .where(
                    Donation.organization_id == organization_id,
                    Donation.donor_id.is_not(None),
                    Donation.donation_date >= focus_start,
                    Donation.donation_date < focus_end,
                )
                .distinct()
            ).scalars().all()
            if donor_id is not None
        }
        prev_donor_ids = {
            int(donor_id)
            for donor_id in db.session.execute(
                select(Donation.donor_id)
                .where(
                    Donation.organization_id == organization_id,
                    Donation.donor_id.is_not(None),
                    Donation.donation_date >= lifecycle_prev_start,
                    Donation.donation_date < lifecycle_prev_end,
                )
                .distinct()
            ).scalars().all()
            if donor_id is not None
        }
        first_gift_map: dict[int, datetime] = {}
        if current_donor_ids:
            first_gift_rows = db.session.execute(
                select(Donation.donor_id, func.min(Donation.donation_date))
                .where(
                    Donation.organization_id == organization_id,
                    Donation.donor_id.in_(list(current_donor_ids)),
                )
                .group_by(Donation.donor_id)
            ).all()
            first_gift_map = {
                int(row[0]): row[1]
                for row in first_gift_rows
                if row[0] is not None and row[1] is not None
            }

        new_donor_ids = {
            donor_id
            for donor_id in current_donor_ids
            if donor_id in first_gift_map and focus_start <= first_gift_map[donor_id] < focus_end
        }
        returning_donor_ids = current_donor_ids - new_donor_ids
        retained_donor_ids = current_donor_ids.intersection(prev_donor_ids)
        lapsed_donor_ids = prev_donor_ids - current_donor_ids
        reactivated_donor_ids = {
            donor_id
            for donor_id in current_donor_ids - prev_donor_ids
            if donor_id in first_gift_map and first_gift_map[donor_id] < lifecycle_prev_start
        }

        campaign_rows = db.session.execute(
            select(
                Donation.purpose,
                func.count(Donation.id),
                func.coalesce(func.sum(Donation.amount), 0.0),
            )
            .where(
                Donation.organization_id == organization_id,
                Donation.donation_date >= focus_start,
                Donation.donation_date < focus_end,
            )
            .group_by(Donation.purpose)
        ).all()

        campaign_breakdown: list[dict[str, Any]] = []
        unattributed_donations = 0
        unattributed_amount = 0.0
        for purpose_raw, gift_count_raw, amount_raw in campaign_rows:
            gift_count = int(gift_count_raw or 0)
            amount = float(amount_raw or 0.0)
            purpose = str(purpose_raw or "").strip()
            if not purpose:
                unattributed_donations += gift_count
                unattributed_amount += amount
                continue
            campaign_breakdown.append({
                "campaign_id": None,
                "campaign_name": purpose,
                "donations": gift_count,
                "amount": round(amount, 2),
                "share_pct": _safe_pct(amount, max(float(period_focus["donations"] or 0.0), 1.0)),
            })
        campaign_breakdown.sort(key=lambda item: float(item["amount"]), reverse=True)

        active_project_rows = db.session.execute(
            select(Project.id, Project.name, Project.budget)
            .where(
                Project.organization_id == organization_id,
                Project.status.in_(["active", "planned"]),
            )
        ).all()
        project_expense_rows = db.session.execute(
            select(Expense.project_id, func.coalesce(func.sum(Expense.amount), 0.0))
            .where(
                Expense.organization_id == organization_id,
                Expense.project_id.is_not(None),
                Expense.paid_at >= focus_start,
                Expense.paid_at < focus_end,
            )
            .group_by(Expense.project_id)
        ).all()
        project_expenses_map = {int(row[0]): float(row[1] or 0.0) for row in project_expense_rows if row[0] is not None}
        project_variance_rows: list[dict[str, Any]] = []
        for project_id_raw, project_name_raw, budget_raw in active_project_rows:
            project_id = int(project_id_raw)
            budget = float(budget_raw or 0.0)
            spent = float(project_expenses_map.get(project_id, 0.0))
            variance = budget - spent
            utilization_pct = _safe_pct(spent, max(budget, 1.0)) if budget > 0 else 0.0
            project_variance_rows.append({
                "project_id": project_id,
                "project_name": str(project_name_raw or f"Project #{project_id}"),
                "budget": round(budget, 2),
                "spent": round(spent, 2),
                "variance": round(variance, 2),
                "utilization_pct": utilization_pct,
                "is_over": spent > budget if budget > 0 else False,
            })
        project_variance_rows.sort(key=lambda item: (not bool(item["is_over"]), -float(item["utilization_pct"]), -float(item["spent"])))

        month_start = datetime(year=now.year, month=now.month, day=1)
        if now.month == 12:
            next_month_start = datetime(year=now.year + 1, month=1, day=1)
        else:
            next_month_start = datetime(year=now.year, month=now.month + 1, day=1)
        month_days = max(1, (next_month_start - month_start).days)
        month_elapsed_days = max(1, (now - month_start).days + 1)
        mtd_donations = _sum_donations(month_start, now)
        mtd_expenses = _sum_expenses(month_start, now)
        donation_daily_run_rate = mtd_donations / month_elapsed_days
        expense_daily_run_rate = mtd_expenses / month_elapsed_days
        projected_month_donations = donation_daily_run_rate * month_days
        projected_month_expenses = expense_daily_run_rate * month_days
        projected_month_net = projected_month_donations - projected_month_expenses

        cohort_month_start = datetime(year=now.year, month=now.month, day=1)
        cohort_month_windows: list[datetime] = []
        for offset in range(5, -1, -1):
            month = cohort_month_start.month - offset
            year = cohort_month_start.year
            while month <= 0:
                month += 12
                year -= 1
            cohort_month_windows.append(datetime(year=year, month=month, day=1))
        cohort_start = cohort_month_windows[0]
        cohort_month_keys = [dt.strftime("%Y-%m") for dt in cohort_month_windows]

        first_gift_all_rows = db.session.execute(
            select(Donation.donor_id, func.min(Donation.donation_date))
            .where(
                Donation.organization_id == organization_id,
                Donation.donor_id.is_not(None),
            )
            .group_by(Donation.donor_id)
        ).all()
        first_gift_month: dict[int, str] = {
            int(row[0]): row[1].strftime("%Y-%m")
            for row in first_gift_all_rows
            if row[0] is not None and row[1] is not None
        }

        donor_month_rows = db.session.execute(
            select(Donation.donor_id, func.strftime("%Y-%m", Donation.donation_date))
            .where(
                Donation.organization_id == organization_id,
                Donation.donor_id.is_not(None),
                Donation.donation_date >= cohort_start,
                Donation.donation_date < now,
            )
            .group_by(Donation.donor_id, func.strftime("%Y-%m", Donation.donation_date))
        ).all()
        donors_by_month: dict[str, set[int]] = {key: set() for key in cohort_month_keys}
        for donor_id_raw, month_key_raw in donor_month_rows:
            if donor_id_raw is None or month_key_raw is None:
                continue
            month_key = str(month_key_raw)
            if month_key in donors_by_month:
                donors_by_month[month_key].add(int(donor_id_raw))

        cohort_labels = [dt.strftime("%b") for dt in cohort_month_windows]
        cohort_new: list[int] = []
        cohort_returning: list[int] = []
        cohort_retained: list[int] = []
        prev_month_donors: set[int] = set()
        for month_key in cohort_month_keys:
            month_donors = donors_by_month.get(month_key, set())
            new_count = sum(1 for donor_id in month_donors if first_gift_month.get(donor_id) == month_key)
            returning_count = max(0, len(month_donors) - new_count)
            retained_count = len(month_donors.intersection(prev_month_donors)) if prev_month_donors else 0
            cohort_new.append(int(new_count))
            cohort_returning.append(int(returning_count))
            cohort_retained.append(int(retained_count))
            prev_month_donors = set(month_donors)

        payload = {
            "beneficiary_count": int(beneficiary_count),
            "project_count": int(project_count),
            "donor_count": int(donor_count),
            "total_donations": float(total_donations),
            "total_budget": float(total_budget),
            "total_expenses": float(total_expenses),
            "net_cashflow": float(total_donations) - float(total_expenses),
            "total_funds": int(total_funds),
            "donation_transaction_count": donation_transaction_count,
            "avg_gift_amount": round(avg_gift_amount, 2),
            "recurring_active_plans": recurring_active_plans,
            "recurring_monthly_commitment": round(recurring_monthly_commitment, 2),
            "recent_donations": recent_donations,
            "trend_30d": {
                "donations": float(trend_30d["donations"] or 0.0),
                "expenses": float(trend_30d["expenses"] or 0.0),
                "net": float(trend_30d["net"] or 0.0),
                "donations_delta_pct": cast(float | None, trend_30d["donations_delta_pct"]),
                "expenses_delta_pct": cast(float | None, trend_30d["expenses_delta_pct"]),
            },
            "trend_90d": {
                "donations": float(trend_90d["donations"] or 0.0),
                "expenses": float(trend_90d["expenses"] or 0.0),
                "net": float(trend_90d["net"] or 0.0),
                "donations_delta_pct": cast(float | None, trend_90d["donations_delta_pct"]),
                "expenses_delta_pct": cast(float | None, trend_90d["expenses_delta_pct"]),
            },
            "trend_ytd": {
                "donations": float(trend_ytd["donations"] or 0.0),
                "expenses": float(trend_ytd["expenses"] or 0.0),
                "net": float(trend_ytd["net"] or 0.0),
                "donations_delta_pct": cast(float | None, trend_ytd["donations_delta_pct"]),
                "expenses_delta_pct": cast(float | None, trend_ytd["expenses_delta_pct"]),
            },
            "selected_period": normalized_period,
            "selected_period_label": period_labels[normalized_period],
            "period_focus": {
                "donations": float(period_focus["donations"] or 0.0),
                "expenses": float(period_focus["expenses"] or 0.0),
                "net": float(period_focus["net"] or 0.0),
                "donations_delta_pct": cast(float | None, period_focus["donations_delta_pct"]),
                "expenses_delta_pct": cast(float | None, period_focus["expenses_delta_pct"]),
                "net_delta_pct": cast(float | None, period_focus["net_delta_pct"]),
            },
            "period_focus_series": focus_series,
            "period_comparison": {
                "label": comparison_label,
                "donations": comparison_donations,
                "expenses": comparison_expenses,
                "net": comparison_net,
                "donations_delta_pct": comparison_donations_delta,
                "expenses_delta_pct": comparison_expenses_delta,
                "net_delta_pct": comparison_net_delta,
            },
            "period_comparison_series": comparison_series,
            "custom_range": {
                "start_date": custom_start.date().isoformat() if custom_valid and custom_start else None,
                "end_date": (custom_end - timedelta(days=1)).date().isoformat() if custom_valid and custom_end else None,
            },
            "monthly_overview": monthly_overview,
            "category_metrics": category_metrics,
            "goal_progress": {
                "fundraising_goal": fundraising_goal,
                "fundraising_progress": fundraising_progress,
                "expense_cap": expense_cap,
                "expense_progress": expense_progress,
            },
            "donor_lifecycle": {
                "window_days": lifecycle_window_days,
                "current_active_donors": len(current_donor_ids),
                "new_donors": len(new_donor_ids),
                "returning_donors": len(returning_donor_ids),
                "retained_donors": len(retained_donor_ids),
                "lapsed_donors": len(lapsed_donor_ids),
                "reactivated_donors": len(reactivated_donor_ids),
                "retention_pct": _safe_pct(float(len(retained_donor_ids)), max(float(len(prev_donor_ids)), 1.0)),
                "lapse_pct": _safe_pct(float(len(lapsed_donor_ids)), max(float(len(prev_donor_ids)), 1.0)),
            },
            "campaign_attribution": {
                "top_campaigns": campaign_breakdown[:5],
                "attributed_amount": round(sum(float(item["amount"]) for item in campaign_breakdown), 2),
                "unattributed_amount": round(unattributed_amount, 2),
                "unattributed_donations": int(unattributed_donations),
                "coverage_pct": _safe_pct(
                    sum(float(item["amount"]) for item in campaign_breakdown),
                    max(float(period_focus["donations"] or 0.0), 1.0),
                ),
            },
            "budget_variance": {
                "window_label": period_labels[normalized_period],
                "project_budget_total": round(sum(float(row["budget"]) for row in project_variance_rows), 2),
                "project_spent_total": round(sum(float(row["spent"]) for row in project_variance_rows), 2),
                "project_variance_total": round(sum(float(row["variance"]) for row in project_variance_rows), 2),
                "over_budget_projects": int(sum(1 for row in project_variance_rows if row["is_over"])),
                "top_projects": project_variance_rows[:5],
            },
            "forecast": {
                "month_label": now.strftime("%B %Y"),
                "days_elapsed": month_elapsed_days,
                "days_in_month": month_days,
                "mtd_donations": round(mtd_donations, 2),
                "mtd_expenses": round(mtd_expenses, 2),
                "donation_daily_run_rate": round(donation_daily_run_rate, 2),
                "expense_daily_run_rate": round(expense_daily_run_rate, 2),
                "projected_month_donations": round(projected_month_donations, 2),
                "projected_month_expenses": round(projected_month_expenses, 2),
                "projected_month_net": round(projected_month_net, 2),
            },
            "donor_cohorts": {
                "labels": cohort_labels,
                "new": cohort_new,
                "returning": cohort_returning,
                "retained": cohort_retained,
            },
            "alerts": alerts,
            "data_freshness": {
                "generated_at": now.isoformat(timespec="seconds"),
                "latest_donation_date": latest_donation_date.isoformat() if latest_donation_date else None,
            },
            "data_quality": {
                "missing_donation_dates": missing_donation_dates,
            },
        }
        if cache_ttl_seconds > 0:
            self._dashboard_cache[cache_key] = (now_epoch + cache_ttl_seconds, payload)
        return payload

    @staticmethod
    def _normalize_intelligence_role(role: str | None) -> str:
        normalized = str(role or "viewer").strip().lower()
        role_map = {
            "fundraiser": "staff",
            "volunteer_manager": "volunteer",
            "org_admin": "admin",
            "executive": "admin",
        }
        return role_map.get(normalized, normalized)

    @staticmethod
    def _role_profile(role: str) -> str:
        if role == "admin":
            return "executive_director"
        if role == "staff":
            return "major_gifts_officer"
        if role == "volunteer":
            return "field_operations"
        return "operations_viewer"

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _safe_float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _period_window(
        *,
        period: str,
        now: datetime,
        start_date: datetime | None,
        end_date: datetime | None,
    ) -> tuple[datetime, datetime]:
        normalized = str(period or "30d").strip().lower()
        if normalized == "custom" and start_date is not None and end_date is not None:
            start = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
            end = end_date.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            if end > start:
                return start, end
        if normalized == "90d":
            return now - timedelta(days=90), now
        if normalized == "ytd":
            return datetime(year=now.year, month=1, day=1), now
        return now - timedelta(days=30), now

    def role_based_intelligence(
        self,
        organization_id: int,
        *,
        role: str,
        actor_user_id: int | None = None,
        period: str = "30d",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        recent_donations_limit: int = 10,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        normalized_role = self._normalize_intelligence_role(role)
        summary = self.organization_dashboard_summary(
            organization_id,
            recent_donations_limit=max(5, min(25, int(recent_donations_limit))),
            period=period,
            start_date=start_date,
            end_date=end_date,
        )
        window_start, window_end = self._period_window(
            period=str(summary.get("selected_period") or period),
            now=now,
            start_date=start_date,
            end_date=end_date,
        )

        top_donor_rows = db.session.execute(
            select(
                Donor.id,
                Donor.name,
                Donor.email,
                func.coalesce(func.sum(Donation.amount), 0.0).label("raised_amount"),
                func.count(Donation.id).label("gift_count"),
                func.max(Donation.donation_date).label("last_gift_date"),
            )
            .join(Donation, Donation.donor_id == Donor.id)
            .where(
                Donor.organization_id == organization_id,
                Donation.organization_id == organization_id,
                Donation.donation_date >= window_start,
                Donation.donation_date < window_end,
            )
            .group_by(Donor.id, Donor.name, Donor.email)
            .order_by(desc("raised_amount"))
            .limit(8)
        ).all()
        top_donors = [
            {
                "donor_id": self._safe_int(row.id),
                "name": str(row.name or "Unnamed donor"),
                "email": str(row.email or "") or None,
                "raised_amount": round(self._safe_float(row.raised_amount), 2),
                "gift_count": self._safe_int(row.gift_count),
                "last_gift_date": row.last_gift_date.isoformat() if row.last_gift_date else None,
            }
            for row in top_donor_rows
        ]

        open_tasks_count = self._safe_int(
            db.session.scalar(
                select(func.count(Task.id)).where(
                    Task.organization_id == organization_id,
                    Task.status.in_(["open", "in_progress"]),
                )
            )
        )
        overdue_tasks_count = self._safe_int(
            db.session.scalar(
                select(func.count(Task.id)).where(
                    Task.organization_id == organization_id,
                    Task.status.in_(["open", "in_progress"]),
                    Task.due_date.is_not(None),
                    Task.due_date < now,
                )
            )
        )
        high_priority_tasks_count = self._safe_int(
            db.session.scalar(
                select(func.count(Task.id)).where(
                    Task.organization_id == organization_id,
                    Task.status.in_(["open", "in_progress"]),
                    Task.priority.in_(["high", "urgent"]),
                )
            )
        )

        high_risk_case_count = self._safe_int(
            db.session.scalar(
                select(func.count(ProgramCase.id)).where(
                    ProgramCase.organization_id == organization_id,
                    ProgramCase.status.in_(["open", "in_progress", "on_hold"]),
                    ProgramCase.risk_level.in_(["high", "critical"]),
                )
            )
        )

        assigned_task_rows: list[dict[str, Any]] = []
        assigned_open_count = 0
        assigned_overdue_count = 0
        if actor_user_id is not None:
            assigned_open_count = self._safe_int(
                db.session.scalar(
                    select(func.count(Task.id)).where(
                        Task.organization_id == organization_id,
                        Task.assigned_to_id == int(actor_user_id),
                        Task.status.in_(["open", "in_progress"]),
                    )
                )
            )
            assigned_overdue_count = self._safe_int(
                db.session.scalar(
                    select(func.count(Task.id)).where(
                        Task.organization_id == organization_id,
                        Task.assigned_to_id == int(actor_user_id),
                        Task.status.in_(["open", "in_progress"]),
                        Task.due_date.is_not(None),
                        Task.due_date < now,
                    )
                )
            )
            task_rows = db.session.execute(
                select(
                    Task.id,
                    Task.title,
                    Task.priority,
                    Task.status,
                    Task.due_date,
                    Donor.id.label("donor_id"),
                    Donor.name.label("donor_name"),
                )
                .outerjoin(Donor, Donor.id == Task.donor_id)
                .where(
                    Task.organization_id == organization_id,
                    Task.assigned_to_id == int(actor_user_id),
                    Task.status.in_(["open", "in_progress"]),
                )
                .order_by(
                    Task.due_date.asc().nulls_last(),
                    Task.priority.desc(),
                )
                .limit(12)
            ).all()
            assigned_task_rows = [
                {
                    "task_id": self._safe_int(row.id),
                    "title": str(row.title or ""),
                    "priority": str(row.priority or "medium"),
                    "status": str(row.status or "open"),
                    "due_date": row.due_date.isoformat() if row.due_date else None,
                    "donor_id": self._safe_int(row.donor_id) if row.donor_id is not None else None,
                    "donor_name": str(row.donor_name or "") or None,
                }
                for row in task_rows
            ]

        donor_lifecycle = cast(dict[str, Any], summary.get("donor_lifecycle") or {})
        forecast = cast(dict[str, Any], summary.get("forecast") or {})
        budget_variance = cast(dict[str, Any], summary.get("budget_variance") or {})
        period_focus = cast(dict[str, Any], summary.get("period_focus") or {})
        campaign_attribution = cast(dict[str, Any], summary.get("campaign_attribution") or {})
        alerts = cast(list[dict[str, Any]], summary.get("alerts") or [])

        next_best_actions: list[dict[str, str]] = []
        if self._safe_float(period_focus.get("net")) < 0:
            next_best_actions.append({
                "priority": "high",
                "action": "Stabilize cashflow this week",
                "rationale": "Current period net is negative; pause discretionary spend and accelerate donor asks.",
            })
        if self._safe_int(donor_lifecycle.get("lapsed_donors")) > 0:
            next_best_actions.append({
                "priority": "high",
                "action": "Run a lapsed donor reactivation sprint",
                "rationale": f"{self._safe_int(donor_lifecycle.get('lapsed_donors'))} donors lapsed in the current comparison window.",
            })
        if high_priority_tasks_count > 0:
            next_best_actions.append({
                "priority": "medium",
                "action": "Clear high-priority follow-ups",
                "rationale": f"{high_priority_tasks_count} high/urgent open tasks are pending.",
            })
        if not next_best_actions:
            next_best_actions.append({
                "priority": "medium",
                "action": "Maintain current operating cadence",
                "rationale": "No immediate risk trigger detected in finance, delivery, or donor engagement.",
            })

        common_payload = {
            "role": normalized_role,
            "profile": self._role_profile(normalized_role),
            "selected_period": str(summary.get("selected_period") or "30d"),
            "selected_period_label": str(summary.get("selected_period_label") or "Last 30 Days"),
            "generated_at": now.isoformat(timespec="seconds"),
            "risk_alerts": alerts,
            "operational_queues": {
                "organization_open_tasks": open_tasks_count,
                "organization_overdue_tasks": overdue_tasks_count,
                "high_priority_tasks": high_priority_tasks_count,
                "high_risk_cases": high_risk_case_count,
            },
            "portfolio_highlights": {
                "top_donors": top_donors,
                "top_campaigns": cast(list[dict[str, Any]], campaign_attribution.get("top_campaigns") or []),
                "over_budget_projects": self._safe_int(budget_variance.get("over_budget_projects")),
            },
            "next_best_actions": next_best_actions,
        }

        if normalized_role == "admin":
            scorecard = [
                {
                    "id": "period_revenue",
                    "label": "Period Revenue",
                    "value": round(self._safe_float(period_focus.get("donations")), 2),
                    "unit": "currency",
                    "trend_pct": period_focus.get("donations_delta_pct"),
                },
                {
                    "id": "period_net",
                    "label": "Period Net",
                    "value": round(self._safe_float(period_focus.get("net")), 2),
                    "unit": "currency",
                    "trend_pct": period_focus.get("net_delta_pct"),
                },
                {
                    "id": "retention_pct",
                    "label": "Donor Retention",
                    "value": round(self._safe_float(donor_lifecycle.get("retention_pct")), 1),
                    "unit": "percent",
                },
                {
                    "id": "projected_month_net",
                    "label": "Projected Month-End Net",
                    "value": round(self._safe_float(forecast.get("projected_month_net")), 2),
                    "unit": "currency",
                },
            ]
            return {
                **common_payload,
                "scorecard": scorecard,
                "focus_areas": [
                    {
                        "area": "Financial trajectory",
                        "detail": "Projection and period-net deltas determine runway confidence.",
                    },
                    {
                        "area": "Portfolio risk",
                        "detail": "Over-budget projects and high-risk cases require leadership escalation.",
                    },
                ],
                "drilldowns": {
                    "forecast": forecast,
                    "budget_variance": budget_variance,
                    "donor_lifecycle": donor_lifecycle,
                },
            }

        if normalized_role == "staff":
            period_revenue = self._safe_float(period_focus.get("donations"))
            top_donor_revenue = round(sum(self._safe_float(item.get("raised_amount")) for item in top_donors[:5]), 2)
            scorecard = [
                {
                    "id": "portfolio_revenue",
                    "label": "Portfolio Revenue",
                    "value": top_donor_revenue,
                    "unit": "currency",
                },
                {
                    "id": "period_revenue",
                    "label": "Total Period Revenue",
                    "value": round(period_revenue, 2),
                    "unit": "currency",
                    "trend_pct": period_focus.get("donations_delta_pct"),
                },
                {
                    "id": "assigned_open_tasks",
                    "label": "My Open Moves",
                    "value": assigned_open_count,
                    "unit": "count",
                },
                {
                    "id": "assigned_overdue_tasks",
                    "label": "My Overdue Moves",
                    "value": assigned_overdue_count,
                    "unit": "count",
                },
            ]
            return {
                **common_payload,
                "scorecard": scorecard,
                "focus_areas": [
                    {
                        "area": "Top donor cultivation",
                        "detail": "Prioritize donors with highest period contribution and stale follow-up windows.",
                    },
                    {
                        "area": "Task execution",
                        "detail": "Close overdue and high-priority stewardship actions first.",
                    },
                ],
                "operational_queues": {
                    **cast(dict[str, Any], common_payload["operational_queues"]),
                    "my_open_tasks": assigned_open_count,
                    "my_overdue_tasks": assigned_overdue_count,
                    "my_task_queue": assigned_task_rows,
                },
                "drilldowns": {
                    "top_donors": top_donors,
                    "my_task_queue": assigned_task_rows,
                },
            }

        scorecard = [
            {
                "id": "period_revenue",
                "label": "Period Revenue",
                "value": round(self._safe_float(period_focus.get("donations")), 2),
                "unit": "currency",
                "trend_pct": period_focus.get("donations_delta_pct"),
            },
            {
                "id": "open_tasks",
                "label": "Open Tasks",
                "value": open_tasks_count,
                "unit": "count",
            },
            {
                "id": "high_risk_cases",
                "label": "High Risk Cases",
                "value": high_risk_case_count,
                "unit": "count",
            },
            {
                "id": "data_quality_missing_dates",
                "label": "Missing Donation Dates",
                "value": self._safe_int(cast(dict[str, Any], summary.get("data_quality") or {}).get("missing_donation_dates")),
                "unit": "count",
            },
        ]
        return {
            **common_payload,
            "scorecard": scorecard,
            "focus_areas": [
                {
                    "area": "Operational health",
                    "detail": "Track open workload, overdue tasks, and case risk trends.",
                },
                {
                    "area": "Fundraising pulse",
                    "detail": "Monitor campaign contribution share and donor activity freshness.",
                },
            ],
            "drilldowns": {
                "campaign_attribution": campaign_attribution,
                "top_donors": top_donors,
            },
        }

    def financial_guardrails_intelligence(
        self,
        organization_id: int,
        *,
        role: str,
        actor_user_id: int | None = None,
        period: str = "30d",
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        normalized_role = self._normalize_intelligence_role(role)
        summary = self.organization_dashboard_summary(
            organization_id,
            recent_donations_limit=15,
            period=period,
            start_date=start_date,
            end_date=end_date,
        )

        period_focus = cast(dict[str, Any], summary.get("period_focus") or {})
        forecast = cast(dict[str, Any], summary.get("forecast") or {})
        budget_variance = cast(dict[str, Any], summary.get("budget_variance") or {})
        donor_lifecycle = cast(dict[str, Any], summary.get("donor_lifecycle") or {})

        period_net = self._safe_float(period_focus.get("net"))
        projected_month_net = self._safe_float(forecast.get("projected_month_net"))
        over_budget_projects = self._safe_int(budget_variance.get("over_budget_projects"))
        lapsed_donors = self._safe_int(donor_lifecycle.get("lapsed_donors"))

        grant_watchlist: list[dict[str, Any]] = []
        unreconciled_transactions_over_30d = 0
        try:
            from ngo_homesuite.grants.models import Grant, GrantBudgetLine, GrantBudgetTransaction

            budget_line_rows = db.session.execute(
                select(
                    GrantBudgetLine.id,
                    GrantBudgetLine.grant_id,
                    Grant.title,
                    GrantBudgetLine.category,
                    GrantBudgetLine.allocated_amount,
                    GrantBudgetLine.committed_amount,
                    GrantBudgetLine.reconciled_amount,
                    GrantBudgetLine.status,
                )
                .join(Grant, Grant.id == GrantBudgetLine.grant_id)
                .where(
                    GrantBudgetLine.organization_id == organization_id,
                    Grant.organization_id == organization_id,
                )
            ).all()

            for row in budget_line_rows:
                allocated = self._safe_float(row.allocated_amount)
                committed = self._safe_float(row.committed_amount)
                reconciled = self._safe_float(row.reconciled_amount)
                consumed = max(committed, reconciled)
                utilization_pct = round((consumed / allocated) * 100.0, 1) if allocated > 0 else 0.0
                status = "ok"
                if allocated > 0 and consumed > allocated:
                    status = "critical"
                elif utilization_pct >= 90.0:
                    status = "warning"

                if status != "ok":
                    grant_watchlist.append(
                        {
                            "budget_line_id": self._safe_int(row.id),
                            "grant_id": self._safe_int(row.grant_id),
                            "grant_title": str(row.title or f"Grant #{self._safe_int(row.grant_id)}"),
                            "category": str(row.category or "uncategorized"),
                            "allocated_amount": round(allocated, 2),
                            "consumed_amount": round(consumed, 2),
                            "remaining_amount": round(allocated - consumed, 2),
                            "utilization_pct": utilization_pct,
                            "status": status,
                            "line_status": str(row.status or "pending"),
                        }
                    )

            cutoff = now - timedelta(days=30)
            unreconciled_transactions_over_30d = self._safe_int(
                db.session.scalar(
                    select(func.count(GrantBudgetTransaction.id)).where(
                        GrantBudgetTransaction.organization_id == organization_id,
                        GrantBudgetTransaction.reconciled_at.is_(None),
                        GrantBudgetTransaction.created_at < cutoff,
                    )
                )
            )
        except Exception:
            grant_watchlist = []
            unreconciled_transactions_over_30d = 0

        grant_watchlist.sort(
            key=lambda item: (
                item.get("status") != "critical",
                item.get("status") != "warning",
                -self._safe_float(item.get("utilization_pct")),
            )
        )

        guardrails: list[dict[str, Any]] = []

        cash_status = "ok"
        if period_net < 0:
            cash_status = "critical"
        elif period_net < (0.15 * max(self._safe_float(period_focus.get("donations")), 1.0)):
            cash_status = "warning"
        guardrails.append(
            {
                "id": "period_net_cashflow",
                "label": "Period Net Cashflow",
                "status": cash_status,
                "observed": round(period_net, 2),
                "threshold": "must stay >= 0",
                "detail": "Current period inflow minus outflow should remain non-negative for operating stability.",
            }
        )

        forecast_status = "ok"
        if projected_month_net < 0:
            forecast_status = "critical"
        elif projected_month_net < (0.10 * max(self._safe_float(forecast.get("projected_month_donations")), 1.0)):
            forecast_status = "warning"
        guardrails.append(
            {
                "id": "projected_month_net",
                "label": "Projected Month-End Net",
                "status": forecast_status,
                "observed": round(projected_month_net, 2),
                "threshold": "projected net should stay >= 0",
                "detail": "Run-rate projection highlights end-of-month runway pressure before it materializes.",
            }
        )

        budget_status = "ok"
        if over_budget_projects > 0:
            budget_status = "critical" if over_budget_projects >= 3 else "warning"
        guardrails.append(
            {
                "id": "project_budget_control",
                "label": "Project Budget Control",
                "status": budget_status,
                "observed": over_budget_projects,
                "threshold": "0 over-budget projects",
                "detail": "Projects exceeding allocated budget indicate immediate spend-control intervention needs.",
            }
        )

        grant_status = "ok"
        if unreconciled_transactions_over_30d > 0:
            grant_status = "critical" if unreconciled_transactions_over_30d >= 10 else "warning"
        guardrails.append(
            {
                "id": "grant_reconciliation_hygiene",
                "label": "Grant Reconciliation Hygiene",
                "status": grant_status,
                "observed": unreconciled_transactions_over_30d,
                "threshold": "no unreconciled grant transactions older than 30 days",
                "detail": "Aging unreconciled grant transactions increase audit and restricted-fund compliance risk.",
            }
        )

        donor_risk_status = "ok"
        if lapsed_donors > 0:
            donor_risk_status = "warning"
        guardrails.append(
            {
                "id": "revenue_retention_risk",
                "label": "Revenue Retention Risk",
                "status": donor_risk_status,
                "observed": lapsed_donors,
                "threshold": "minimize lapsed donors in comparison window",
                "detail": "Lapsed donors are a leading indicator of near-term revenue compression.",
            }
        )

        risk_score = 0
        for item in guardrails:
            status = str(item.get("status") or "ok")
            if status == "critical":
                risk_score += 25
            elif status == "warning":
                risk_score += 12
        risk_score = min(100, risk_score)
        overall_status = "ok"
        if risk_score >= 60:
            overall_status = "critical"
        elif risk_score >= 25:
            overall_status = "warning"

        next_best_actions: list[dict[str, str]] = []
        if period_net < 0 or projected_month_net < 0:
            next_best_actions.append(
                {
                    "priority": "high",
                    "action": "Execute 14-day cash stabilization plan",
                    "rationale": "Negative current or projected net requires immediate spend triage and accelerated fundraising conversion.",
                }
            )
        if over_budget_projects > 0:
            next_best_actions.append(
                {
                    "priority": "high",
                    "action": "Freeze discretionary spend on over-budget projects",
                    "rationale": f"{over_budget_projects} active projects are over budget and need manager-approved recovery plans.",
                }
            )
        if unreconciled_transactions_over_30d > 0:
            next_best_actions.append(
                {
                    "priority": "medium",
                    "action": "Clear aged grant reconciliation backlog",
                    "rationale": f"{unreconciled_transactions_over_30d} grant transactions are unreconciled beyond 30 days.",
                }
            )
        if lapsed_donors > 0:
            next_best_actions.append(
                {
                    "priority": "medium",
                    "action": "Launch targeted reactivation asks",
                    "rationale": f"{lapsed_donors} donors lapsed in the selected comparison window.",
                }
            )
        if not next_best_actions:
            next_best_actions.append(
                {
                    "priority": "medium",
                    "action": "Maintain guardrail monitoring cadence",
                    "rationale": "All monitored financial guardrails are currently inside expected thresholds.",
                }
            )

        return {
            "role": normalized_role,
            "profile": self._role_profile(normalized_role),
            "selected_period": str(summary.get("selected_period") or period),
            "selected_period_label": str(summary.get("selected_period_label") or "Last 30 Days"),
            "generated_at": now.isoformat(timespec="seconds"),
            "overall_status": overall_status,
            "risk_score": risk_score,
            "financial_posture": {
                "period_donations": round(self._safe_float(period_focus.get("donations")), 2),
                "period_expenses": round(self._safe_float(period_focus.get("expenses")), 2),
                "period_net": round(period_net, 2),
                "projected_month_net": round(projected_month_net, 2),
                "over_budget_projects": over_budget_projects,
                "unreconciled_grant_transactions_over_30d": unreconciled_transactions_over_30d,
            },
            "guardrails": guardrails,
            "watchlists": {
                "project_budget_top": cast(list[dict[str, Any]], budget_variance.get("top_projects") or []),
                "grant_budget_lines": grant_watchlist[:10],
            },
            "next_best_actions": next_best_actions,
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
        donor_signal = get_donor_signal(int(organization_id), donor=donor)
        return {
            "donor": donor,
            "donation_count": donation_count,
            "donation_total": float(total_amount),
            "recent_donations": recent_donations,
            "recurring_plans": recurring_plans,
            "first_gift_date": min(dates).date().isoformat() if dates else None,
            "last_gift_date": max(dates).date().isoformat() if dates else None,
            "active_recurring_plans": sum(1 for plan in recurring_plans if plan.status == "active"),
            "donor_signal": donor_signal,
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
                monthly_donations[key] += self._safe_amount(donation.amount)
        for expense in expenses:
            if expense.paid_at:
                key = expense.paid_at.strftime("%Y-%m")
                monthly_expenses[key] += self._safe_amount(expense.amount)

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
                totals[str(donation.purpose)] += self._safe_amount(donation.amount)
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
            totals[donor_name] += self._safe_amount(donation.amount)
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
