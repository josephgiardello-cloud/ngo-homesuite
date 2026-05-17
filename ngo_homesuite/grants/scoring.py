"""Grant opportunity scoring utilities.

Scoring pattern ported and adapted from the TONY diagnostic tool
(josephgiardello-cloud/Tony, tony-core/tony/scoring.py and
tony/tony/tony/tony/src/tony/scoring.py).

Provides a weighted pipeline-risk score for a grant opportunity
based on observable fields — no external data required.

Score components
----------------
* probability_score  — reward high probability (0–1)
* deadline_urgency   — penalise opportunities whose deadline is very near
                       or already past (proxy for delayed-reporting penalty)
* amount_uncertainty — penalise a wide min/max spread (volatility overlay)
* stage_score        — reward advanced pipeline stages

Each component is normalised to [0, 1]. The final ``priority_score``
is the weighted sum, also clamped to [0, 1].  Higher = higher priority.
"""

from __future__ import annotations

from datetime import date
from typing import Optional


# Default component weights (tunable per organisation if needed)
_DEFAULT_WEIGHTS: dict[str, float] = {
    "probability_weight": 0.40,
    "deadline_weight": 0.25,
    "amount_certainty_weight": 0.20,
    "stage_weight": 0.15,
}

_STAGE_SCORES: dict[str, float] = {
    "identified": 0.10,
    "qualified": 0.30,
    "in_progress": 0.55,
    "submitted": 0.80,
    "awarded": 1.00,
    "declined": 0.00,
    "archived": 0.00,
}

# Deadline urgency: days-to-deadline → urgency penalty scalar
_DEADLINE_WARN_DAYS = 14   # very urgent
_DEADLINE_OK_DAYS   = 90   # comfortable


def _deadline_score(deadline: Optional[date], today: Optional[date] = None) -> float:
    """Return a 0–1 score: 1.0 = comfortable, 0.0 = overdue/missing."""
    if deadline is None:
        return 0.50  # unknown deadline — neutral
    ref = today or date.today()
    days_left = (deadline - ref).days
    if days_left < 0:
        return 0.0  # past due
    if days_left <= _DEADLINE_WARN_DAYS:
        return 0.10
    if days_left >= _DEADLINE_OK_DAYS:
        return 1.0
    # Linear interpolation between warn and ok
    return (days_left - _DEADLINE_WARN_DAYS) / (_DEADLINE_OK_DAYS - _DEADLINE_WARN_DAYS)


def _amount_certainty_score(amount_min: Optional[float], amount_max: Optional[float]) -> float:
    """Return 0–1 certainty score: 1.0 = exact amount known, 0.0 = very wide range."""
    if amount_min is None and amount_max is None:
        return 0.0  # no amount info
    if amount_min is None or amount_max is None:
        return 0.50  # partial info
    spread = abs(float(amount_max) - float(amount_min))
    base = max(float(amount_min), float(amount_max))
    if base == 0:
        return 1.0
    relative_spread = spread / base
    # Score decreases as relative spread grows; cap at 0.0 for >100 % spread
    return max(0.0, 1.0 - relative_spread)


def score_grant_opportunity(
    *,
    probability: float = 0.0,
    deadline: Optional[date] = None,
    amount_min: Optional[float] = None,
    amount_max: Optional[float] = None,
    status: str = "identified",
    weights: Optional[dict[str, float]] = None,
    today: Optional[date] = None,
) -> dict[str, float]:
    """Compute a weighted priority/risk score for a single grant opportunity.

    Returns a dict with individual component scores and the aggregated
    ``priority_score`` (0–1, higher = higher priority).

    Parameters
    ----------
    probability:
        Award probability [0, 1].
    deadline:
        Application deadline date.
    amount_min / amount_max:
        Estimated grant range.
    status:
        Current pipeline stage.
    weights:
        Optional override for ``_DEFAULT_WEIGHTS``.
    today:
        Reference date for deadline calculation (default: today).
    """
    w = {**_DEFAULT_WEIGHTS, **(weights or {})}

    p_score = float(probability)
    d_score = _deadline_score(deadline, today)
    a_score = _amount_certainty_score(amount_min, amount_max)
    s_score = _STAGE_SCORES.get(status, 0.0)

    priority = (
        p_score * w["probability_weight"]
        + d_score * w["deadline_weight"]
        + a_score * w["amount_certainty_weight"]
        + s_score * w["stage_weight"]
    )

    return {
        "probability_score": round(p_score, 4),
        "deadline_score": round(d_score, 4),
        "amount_certainty_score": round(a_score, 4),
        "stage_score": round(s_score, 4),
        "priority_score": round(min(max(priority, 0.0), 1.0), 4),
    }
