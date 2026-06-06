"""Campaign Projection Engine (Ticket E-1).

Projects fundraising trajectory for a campaign based on historical donation data.
Uses linear regression when enough data is available; falls back to a simple
running-average rate with wider confidence intervals for sparse history.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from sqlalchemy import func, select

from ngo_homesuite.models.core import Campaign, Donation, db

# Minimum number of distinct donation days required to fit a regression line.
_MIN_DAYS_FOR_REGRESSION = 7


def _daily_totals(campaign_id: int, org_id: int) -> list[tuple[date, float]]:
    """Return a sorted list of (day, total_amount) tuples for a campaign."""
    rows = db.session.execute(
        select(
            func.date(Donation.donation_date).label('day'),
            func.sum(Donation.amount).label('total'),
        )
        .where(
            Donation.campaign_id == campaign_id,
            Donation.organization_id == org_id,
            Donation.status.notin_(['refunded', 'failed']),
        )
        .group_by(func.date(Donation.donation_date))
        .order_by(func.date(Donation.donation_date))
    ).all()
    result = []
    for row in rows:
        try:
            day = date.fromisoformat(str(row.day))
            result.append((day, float(row.total or 0.0)))
        except (TypeError, ValueError):
            pass
    return result


def _linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Return (slope, intercept) for a simple OLS regression."""
    n = len(xs)
    if n < 2:
        return 0.0, (ys[0] if ys else 0.0)
    sum_x = sum(xs)
    sum_y = sum(ys)
    sum_xx = sum(x * x for x in xs)
    sum_xy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sum_xx - sum_x ** 2
    if abs(denom) < 1e-12:
        return 0.0, sum_y / n
    slope = (n * sum_xy - sum_x * sum_y) / denom
    intercept = (sum_y - slope * sum_x) / n
    return slope, intercept


def _residual_std(xs: list[float], ys: list[float], slope: float, intercept: float) -> float:
    """Return the standard deviation of regression residuals."""
    n = len(xs)
    if n < 2:
        return 0.0
    ss = sum((y - (slope * x + intercept)) ** 2 for x, y in zip(xs, ys))
    return math.sqrt(ss / (n - 1))


def project_campaign(campaign_id: int, org_id: int) -> dict:
    """Project a campaign's fundraising trajectory.

    Returns a dict with keys:
    - ``campaign_id`` (int)
    - ``raised_to_date`` (float)   â€“ total raised so far (from model cache)
    - ``goal_amount`` (float)
    - ``projected_raised`` (float) â€“ estimated total by end_date
    - ``confidence_low`` (float)   â€“ lower 90% confidence bound
    - ``confidence_high`` (float)  â€“ upper 90% confidence bound
    - ``days_to_goal`` (int | None) â€“ calendar days until goal is hit at current rate
    - ``on_pace`` (bool)           â€“ True if projected_raised >= goal_amount
    - ``method`` (str)             â€“ "regression" | "average" | "insufficient_data"
    - ``days_elapsed`` (int)
    - ``days_remaining`` (int | None)
    """
    campaign = db.session.get(Campaign, campaign_id)
    if campaign is None or int(campaign.organization_id) != int(org_id):
        raise ValueError(f'Campaign {campaign_id} not found for org {org_id}')

    today = date.today()
    goal = float(campaign.goal_amount or 0.0)
    raised = float(campaign.raised_amount or 0.0)

    start = campaign.start_date or today
    end = campaign.end_date

    days_elapsed = max((today - start).days, 0)
    days_remaining: int | None = None
    if end is not None:
        days_remaining = max((end - today).days, 0)

    daily = _daily_totals(campaign_id, org_id)
    n = len(daily)

    # --- Method selection ---------------------------------------------------

    if n == 0:
        # No data at all: project current raised_amount with no velocity.
        return {
            'campaign_id': campaign_id,
            'raised_to_date': raised,
            'goal_amount': goal,
            'projected_raised': raised,
            'confidence_low': raised,
            'confidence_high': raised,
            'days_to_goal': None,
            'on_pace': False,
            'method': 'insufficient_data',
            'days_elapsed': days_elapsed,
            'days_remaining': days_remaining,
        }

    if n >= _MIN_DAYS_FOR_REGRESSION:
        # Fit regression on cumulative daily totals.
        first_day = daily[0][0]
        xs = [(d - first_day).days for d, _ in daily]
        # Build cumulative running totals.
        ys: list[float] = []
        running = 0.0
        for _, amt in daily:
            running += amt
            ys.append(running)

        slope, intercept = _linear_regression(xs, ys)
        residual = _residual_std(xs, ys, slope, intercept)
        # 90% confidence â‰ˆ Â±1.645 std
        z = 1.645

        if days_remaining is not None:
            future_x = float(days_elapsed + days_remaining)
        else:
            future_x = float(days_elapsed + 30)

        projected = max(slope * future_x + intercept, raised)
        low = max(projected - z * residual * math.sqrt(1 + 1 / n), raised)
        high = projected + z * residual * math.sqrt(1 + 1 / n)
        method = 'regression'
    else:
        # Sparse history: use average daily rate.
        span = max((daily[-1][0] - daily[0][0]).days, 1)
        avg_daily = raised / max(span, 1)

        if days_remaining is not None:
            projected = raised + avg_daily * days_remaining
        else:
            projected = raised + avg_daily * 30

        # Wider confidence for sparse data: Â±30 % of projected.
        confidence_band = projected * 0.30
        low = max(projected - confidence_band, raised)
        high = projected + confidence_band
        residual = avg_daily  # not used directly
        method = 'average'

    # --- Days-to-goal estimate ----------------------------------------------
    days_to_goal: int | None = None
    if projected > 0.0 and goal > raised:
        if method == 'regression' and slope > 0:
            # Using regression: estimate days until cumulative reaches goal.
            # cumulative at day X = slope * X + intercept
            # We want slope * X + intercept = goal => X = (goal - intercept) / slope
            x_goal = (goal - intercept) / slope
            days_from_start = max(int(math.ceil(x_goal)) - days_elapsed, 0)
            days_to_goal = days_from_start if days_from_start >= 0 else 0
        elif method == 'average':
            avg_daily = (raised / max(days_elapsed, 1)) if days_elapsed > 0 else 0.0
            if avg_daily > 0:
                days_to_goal = int(math.ceil((goal - raised) / avg_daily))

    on_pace = projected >= goal

    return {
        'campaign_id': campaign_id,
        'raised_to_date': round(raised, 2),
        'goal_amount': round(goal, 2),
        'projected_raised': round(projected, 2),
        'confidence_low': round(low, 2),
        'confidence_high': round(high, 2),
        'days_to_goal': days_to_goal,
        'on_pace': on_pace,
        'method': method,
        'days_elapsed': days_elapsed,
        'days_remaining': days_remaining,
    }


def project_with_conversion_boost(campaign_id: int, org_id: int, boost_pct: float) -> dict:
    """Return a projection with a simulated percentage boost to daily donation velocity.

    ``boost_pct`` is a float (e.g. 10.0 = +10%).  The projection is purely
    hypothetical and is clearly marked with ``method`` prefixed with
    ``"boosted_"``.

    Useful for "what-if" scenario modeling in dashboards.
    """
    if boost_pct < -100.0:
        raise ValueError('boost_pct cannot be less than -100')

    base = project_campaign(campaign_id, org_id)
    multiplier = 1.0 + float(boost_pct) / 100.0
    raised = float(base['raised_to_date'])

    projected = raised + (float(base['projected_raised']) - raised) * multiplier
    low = raised + (float(base['confidence_low']) - raised) * multiplier
    high = raised + (float(base['confidence_high']) - raised) * multiplier

    goal = float(base['goal_amount'])
    days_remaining = base.get('days_remaining')

    # Recompute days_to_goal under the boost.
    days_to_goal: int | None = None
    days_elapsed = int(base.get('days_elapsed', 0))
    if goal > raised and projected > raised:
        daily_orig = (float(base['projected_raised']) - raised) / max(
            (days_remaining or 30), 1
        )
        daily_boosted = daily_orig * multiplier
        if daily_boosted > 0:
            days_to_goal = int(math.ceil((goal - raised) / daily_boosted))

    return {
        **base,
        'projected_raised': round(projected, 2),
        'confidence_low': round(max(low, raised), 2),
        'confidence_high': round(high, 2),
        'days_to_goal': days_to_goal,
        'on_pace': projected >= goal,
        'method': f"boosted_{base['method']}",
        'boost_pct': boost_pct,
    }
