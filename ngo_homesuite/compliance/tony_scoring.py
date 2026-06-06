"""TONY Grant Scoring Integration.

Advanced 4-layer grant risk scoring algorithm adapted from TONY project.
Scores grants based on financial health, continuity, and organizational metrics.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import func, select

from ngo_homesuite.models.core import (
    Organization, Grant, GrantScore, GrantOutcomeRecord, GrantOutcomeTemplate, Project, Fund, Donation, Expense, User, db
)

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fund_balance(fund: Fund) -> float:
    value = getattr(fund, "balance", None)
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _fund_is_restricted(fund: Fund) -> bool:
    return bool(getattr(fund, "is_restricted", False))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_ratio(numerator: float | None, denominator: float | None, *, default: float = 0.0) -> float:
    numerator_value = _safe_float(numerator, default)
    denominator_value = _safe_float(denominator, 0.0)
    if abs(denominator_value) < 1e-9:
        return float(default)
    return numerator_value / denominator_value


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return float(max(lower, min(upper, value)))


def _piecewise_risk(value: float, bands: list[tuple[float, float]]) -> float:
    for threshold, risk in bands:
        if value >= threshold:
            return risk
    return bands[-1][1] if bands else 0.0

# Core features for risk model
FEATURE_COLUMNS = [
    "continuity_months",
    "operating_margin",
    "program_expense_ratio",
    "liabilities_to_assets",
    "revenue_volatility",
]

# Default configuration
DEFAULT_CONFIG = {
    "thresholds": {
        "continuity_low": 180.0,
        "continuity_moderate": 90.0,
        "risk_probability_moderate": 0.35,
        "risk_probability_high": 0.6,
    },
    "weights": {
        "base_score": 0.50,
        "financial_ratios": 0.30,
        "organizational_health": 0.20,
    },
    "scoring_presets": {
        "conservative": {
            "base_score": 0.55,
            "financial_ratios": 0.30,
            "organizational_health": 0.15,
        },
        "balanced": {
            "base_score": 0.50,
            "financial_ratios": 0.30,
            "organizational_health": 0.20,
        },
        "lenient": {
            "base_score": 0.45,
            "financial_ratios": 0.30,
            "organizational_health": 0.25,
        },
    },
    "calibration": {
        "enabled": True,
        "min_samples": 12,
        "lookback_days": 1825,
    },
}


class TonyScorer:
    """TONY scoring engine for grants."""

    @staticmethod
    def extract_financial_snapshot(org_id: str) -> dict[str, Any]:
        """Extract latest financial data for organization.
        
        Returns dict with:
        - revenue, expenses, program_expenses
        - assets, liabilities, unrestricted_net_assets
        - history: list of past year records
        """
        org = db.session.get(Organization, org_id)
        if not org:
            return {}

        # Get latest year's revenue and expense activity.
        now = _utcnow_naive()
        year_ago = now - timedelta(days=365)

        donations = db.session.execute(
            select(func.sum(Donation.amount)).where(
                (Donation.organization_id == org_id)
                & (Donation.created_at >= year_ago)
            )
        ).scalar() or 0.0

        expenses = db.session.execute(
            select(func.sum(Expense.amount)).where(
                (Expense.organization_id == org_id)
                & (Expense.created_at >= year_ago)
            )
        ).scalar() or 0.0

        # Program expenses (expenses tagged to projects); the remainder is treated as administrative overhead.
        program_expenses = db.session.execute(
            select(func.sum(Expense.amount)).where(
                (Expense.organization_id == org_id)
                & (Expense.project_id.isnot(None))
                & (Expense.created_at >= year_ago)
            )
        ).scalar() or 0.0
        admin_expenses = max(float(expenses) - float(program_expenses), 0.0)

        # Fund balances are the best available balance-sheet proxy in this app.
        funds = db.session.execute(
            select(Fund).where(Fund.organization_id == org_id)
        ).scalars().all()
        unrestricted_funds = sum(_fund_balance(f) for f in funds if not _fund_is_restricted(f))
        restricted_funds = sum(_fund_balance(f) for f in funds if _fund_is_restricted(f))

        total_assets = max(unrestricted_funds + restricted_funds, 0.0)
        # Estimate liabilities from annual operating expense pressure.
        estimated_liabilities = expenses * 0.15 if expenses > 0 else 0.0
        current_assets = total_assets
        current_liabilities = estimated_liabilities
        cash_on_hand_days = (unrestricted_funds / (expenses / 365.0)) if expenses > 0 else None

        return {
            "revenue": donations,
            "expenses": expenses,
            "program_expenses": program_expenses,
            "admin_expenses": admin_expenses,
            "admin_expense_ratio": _safe_ratio(admin_expenses, expenses),
            "assets": total_assets,
            "liabilities": estimated_liabilities,
            "debt_to_assets": _safe_ratio(estimated_liabilities, total_assets, default=1.0),
            "current_assets": current_assets,
            "current_liabilities": current_liabilities,
            "current_ratio": _safe_ratio(current_assets, current_liabilities, default=0.0),
            "unrestricted_net_assets": unrestricted_funds,
            "restricted_net_assets": restricted_funds,
            "cash_on_hand_days": cash_on_hand_days,
        }

    @staticmethod
    def calculate_features(financial_data: dict[str, float]) -> dict[str, float]:
        """Calculate core risk features from financial snapshot."""
        revenue = financial_data.get("revenue", 0.0)
        expenses = financial_data.get("expenses", 0.0)
        program_expenses = financial_data.get("program_expenses", 0.0)
        admin_expenses = financial_data.get("admin_expenses", max(expenses - program_expenses, 0.0))
        assets = financial_data.get("assets", 0.0)
        liabilities = financial_data.get("liabilities", 0.0)
        unrestricted_net = financial_data.get("unrestricted_net_assets", 0.0)
        current_assets = financial_data.get("current_assets", assets)
        current_liabilities = financial_data.get("current_liabilities", liabilities)
        cash_on_hand_days = financial_data.get("cash_on_hand_days")

        # Continuity months: runway = unrestricted net assets / monthly burn.
        monthly_burn = expenses / 12.0 if expenses > 0 else 1.0
        continuity_months = unrestricted_net / monthly_burn if monthly_burn > 0 else 0.0
        continuity_months = max(-24, min(120, continuity_months))

        # Operating margin: (revenue - expenses) / revenue.
        operating_margin = (revenue - expenses) / revenue if revenue > 0 else -1.0

        # Program expense ratio and administrative cost ratio.
        program_expense_ratio = program_expenses / expenses if expenses > 0 else 0.0
        admin_expense_ratio = admin_expenses / expenses if expenses > 0 else 1.0

        # Balance-sheet proxies.
        liabilities_to_assets = liabilities / assets if assets > 0 else 1.0
        current_ratio = _safe_ratio(current_assets, current_liabilities, default=0.0)
        operating_reserves_days = float(cash_on_hand_days) if cash_on_hand_days is not None else continuity_months * 30.4167
        debt_to_assets = liabilities_to_assets

        # Revenue volatility (proxy: assume stable if no history).
        revenue_volatility = 0.1  # Conservative default

        return {
            "continuity_months": continuity_months,
            "operating_margin": operating_margin,
            "program_expense_ratio": program_expense_ratio,
            "admin_expense_ratio": admin_expense_ratio,
            "liabilities_to_assets": liabilities_to_assets,
            "current_ratio": current_ratio,
            "operating_reserves_days": operating_reserves_days,
            "debt_to_assets": debt_to_assets,
            "equity_ratio": _safe_ratio(unrestricted_net, revenue if revenue > 0 else expenses, default=0.0),
            "revenue_volatility": revenue_volatility,
        }

    @staticmethod
    def calculate_base_risk_probability(features: dict[str, float]) -> float:
        """Compatibility wrapper for the legacy base-risk API.

        The new scoring path uses Tuckman & Chang directly, but callers that still
        rely on this helper get a stable, bounded approximation.
        """
        base = TonyScorer.tuckman_chang_vulnerability(
            equity=features.get("equity_ratio", 0.0) * max(features.get("operating_margin", 0.0) + 1.0, 1.0),
            revenue=max(features.get("continuity_months", 0.0), 1.0),
            administrative_cost_ratio=features.get("admin_expense_ratio", 0.25),
        )
        ratio_risk = np.mean([
            TonyScorer.program_expense_efficiency_score(features.get("program_expense_ratio", 0.0)),
            TonyScorer.current_ratio_score(features.get("current_ratio", 0.0), 1.0),
            TonyScorer.operating_reserves_score(features.get("operating_reserves_days", 0.0)),
            TonyScorer.debt_to_assets_score(features.get("debt_to_assets", features.get("liabilities_to_assets", 0.0))),
        ])
        return round(float(_clamp(float(0.7 * base + 0.3 * ratio_risk))), 4)

    @staticmethod
    def tuckman_chang_vulnerability(
        equity: float,
        revenue: float,
        administrative_cost_ratio: float | None = None,
    ) -> float:
        """Simplified Tuckman & Chang vulnerability score.

        Higher values mean greater financial vulnerability.
        """
        revenue_value = _safe_float(revenue)
        if revenue_value <= 0:
            return 1.0

        equity_value = _safe_float(equity)
        equity_to_revenue = equity_value / revenue_value
        equity_risk = _piecewise_risk(
            equity_to_revenue,
            [
                (0.50, 0.0),
                (0.25, 0.35),
                (0.10, 0.70),
            ],
        )
        if equity_value <= 0:
            equity_risk = 1.0

        admin_ratio = 0.25 if administrative_cost_ratio is None else _clamp(_safe_float(administrative_cost_ratio))
        admin_risk = _piecewise_risk(
            admin_ratio,
            [
                (0.15, 0.0),
                (0.25, 0.25),
                (0.35, 0.60),
            ],
        )

        return round(float(_clamp((0.55 * admin_risk) + (0.45 * equity_risk))), 4)

    @staticmethod
    def trussel_greenlee_vulnerability(
        current_ratio: float | None,
        operating_reserves_days: float | None,
        debt_to_assets: float | None,
    ) -> float:
        """Secondary nonprofit health check based on liquidity, reserves, and leverage."""
        liquidity_ratio = _safe_float(current_ratio, 0.0)
        reserve_days = _safe_float(operating_reserves_days, 0.0)
        leverage = _safe_float(debt_to_assets, 1.0)

        liquidity_risk = _piecewise_risk(
            liquidity_ratio,
            [
                (1.50, 0.0),
                (1.20, 0.20),
                (1.00, 0.50),
                (0.75, 0.80),
            ],
        )
        reserve_risk = _piecewise_risk(
            reserve_days,
            [
                (180.0, 0.0),
                (90.0, 0.20),
                (45.0, 0.50),
                (30.0, 0.75),
            ],
        )
        leverage_risk = _piecewise_risk(
            leverage,
            [
                (0.35, 0.0),
                (0.50, 0.35),
                (0.65, 0.70),
            ],
        )
        return round(float(_clamp(float(np.mean([liquidity_risk, reserve_risk, leverage_risk])))), 4)

    @staticmethod
    def program_expense_efficiency_score(program_expense_ratio: float) -> float:
        ratio = _clamp(_safe_float(program_expense_ratio))
        if ratio >= 0.85:
            return 0.0
        if ratio >= 0.75:
            return 0.20
        if ratio >= 0.65:
            return 0.45
        if ratio >= 0.50:
            return 0.75
        return 1.0

    @staticmethod
    def current_ratio_score(current_assets: float, current_liabilities: float) -> float:
        """Score based on liquidity.

        Benchmark: >1.0 baseline, 1.2-1.5 strong.
        """
        ratio = _safe_ratio(current_assets, current_liabilities, default=0.0)
        if ratio >= 1.50:
            return 0.0
        if ratio >= 1.20:
            return 0.20
        if ratio >= 1.00:
            return 0.50
        if ratio >= 0.75:
            return 0.80
        return 1.0

    @staticmethod
    def operating_reserves_score(days_cash_on_hand: float) -> float:
        """Score based on reserves.

        Benchmark: 90-180 days is the healthy operating cushion.
        """
        days = _safe_float(days_cash_on_hand, 0.0)
        if days >= 180.0:
            return 0.0
        if days >= 90.0:
            return 0.20
        if days >= 45.0:
            return 0.50
        if days >= 30.0:
            return 0.75
        return 1.0

    @staticmethod
    def debt_to_assets_score(debt_to_assets: float) -> float:
        """Score based on debt load relative to assets."""
        ratio = _clamp(_safe_float(debt_to_assets, 0.0), 0.0, 5.0)
        if ratio <= 0.35:
            return 0.0
        if ratio <= 0.50:
            return 0.35
        if ratio <= 0.65:
            return 0.70
        return 1.0

    @staticmethod
    def calculate_altman_zscore(financial_data: dict[str, float]) -> tuple[float | None, str]:
        """Calculate Altman Z-score for nonprofit.
        
        Z'' = 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4
        Returns (z_score, zone)
        """
        assets = financial_data.get("assets", 0.0)
        liabilities = financial_data.get("liabilities", 0.0)
        revenue = financial_data.get("revenue", 0.0)
        expenses = financial_data.get("expenses", 0.0)
        unrestricted = financial_data.get("unrestricted_net_assets", 0.0)

        if assets == 0:
            return None, "unknown"

        # X1: Working Capital / Assets (proxy: unrestricted / assets)
        x1 = unrestricted / assets if assets > 0 else 0.0

        # X2: Retained Earnings / Assets (proxy: book equity / assets)
        book_equity = assets - liabilities
        x2 = book_equity / assets if assets > 0 else 0.0

        # X3: EBIT / Assets (proxy: (revenue - expenses) / assets)
        ebit = revenue - expenses
        x3 = ebit / assets if assets > 0 else 0.0

        # X4: Book Equity / Liabilities
        x4 = book_equity / liabilities if liabilities > 0 else 1.0

        # Calculate Z-score
        z_score = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4

        # Determine zone
        if z_score > 2.6:
            zone = "safe"
        elif z_score < 1.1:
            zone = "distress"
        else:
            zone = "grey"

        return round(float(z_score), 4), zone

    @staticmethod
    def calculate_organizational_health(org_id: str) -> dict[str, Any]:
        """Calculate organizational capacity health score (0-1).

        The score is a proxy for an organization's ability to manage a grant,
        based on staffing, grant history, outcome tracking, and compliance load.
        Higher values mean stronger capacity.
        """
        org = db.session.get(Organization, org_id)
        if not org:
            return {"score": 0.5, "components": {}}

        grant_count = db.session.execute(
            select(func.count(Grant.id)).where(Grant.organization_id == org_id)
        ).scalar() or 0
        project_count = db.session.execute(
            select(func.count(Project.id)).where(Project.organization_id == org_id)
        ).scalar() or 0
        fund_count = db.session.execute(
            select(func.count(Fund.id)).where(Fund.organization_id == org_id)
        ).scalar() or 0
        user_count = db.session.execute(
            select(func.count(User.id)).where(
                (User.organization_id == org_id)
                & (User.role.in_(["admin", "staff", "fundraiser"]))
            )
        ).scalar() or 0
        outcome_template_count = db.session.execute(
            select(func.count(GrantOutcomeTemplate.id)).where(GrantOutcomeTemplate.organization_id == org_id)
        ).scalar() or 0
        outcome_record_count = db.session.execute(
            select(func.count(GrantOutcomeRecord.id)).where(GrantOutcomeRecord.organization_id == org_id)
        ).scalar() or 0
        overdue_grants = db.session.execute(
            select(func.count(Grant.id)).where(
                (Grant.organization_id == org_id)
                & (Grant.report_due_date.isnot(None))
                & (Grant.report_due_date < _utcnow_naive().date())
                & (Grant.status.notin_(["declined", "closed"]))
            )
        ).scalar() or 0

        compliance_risk = _clamp(_safe_ratio(overdue_grants, max(grant_count, 1), default=0.0))
        outcome_risk = 1.0 - _clamp(_safe_ratio(outcome_record_count, max(outcome_template_count, grant_count, 1), default=0.0))
        staffing_risk = 1.0 - _clamp(_safe_ratio(user_count, 4.0, default=0.0))
        delivery_risk = 1.0 - _clamp(_safe_ratio(project_count + fund_count, 12.0, default=0.0))

        capacity_risk = np.mean([compliance_risk, outcome_risk, staffing_risk, delivery_risk])
        overall = 1.0 - capacity_risk

        return {
            "score": round(float(_clamp(float(overall))), 4),
            "components": {
                "compliance_readiness": round(float(1.0 - compliance_risk), 4),
                "outcome_maturity": round(float(1.0 - outcome_risk), 4),
                "staffing_depth": round(float(1.0 - staffing_risk), 4),
                "delivery_capacity": round(float(1.0 - delivery_risk), 4),
            },
        }

    @staticmethod
    def calibrate_model_from_history(
        org_id: str | None = None,
        config: dict[str, Any] | None = None,
        feature_vector: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """Fit a lightweight calibration model from stored grant-score history.

        This is an optional empirical calibration layer. If there is not enough
        historical data, the method falls back to a heuristic-only response.
        """
        config = config or DEFAULT_CONFIG
        calibration_cfg = config.get("calibration", {}) if isinstance(config.get("calibration"), dict) else {}
        min_samples = int(calibration_cfg.get("min_samples", 12))

        query = db.session.query(GrantScore)
        if org_id is not None:
            query = query.filter(GrantScore.organization_id == org_id)
        history = query.order_by(GrantScore.scored_at.desc()).limit(250).all()

        rows: list[list[float]] = []
        labels: list[int] = []
        for item in history:
            features = item.features if isinstance(item.features, dict) else {}
            if not features:
                continue
            row = [
                _safe_float(features.get(column, 0.0))
                for column in FEATURE_COLUMNS
            ]
            rows.append(row)
            labels.append(1 if (item.grant_recommendation_label == "Elevated Risk" or _safe_float(item.final_risk_probability, 0.0) >= 0.65) else 0)

        if len(rows) < min_samples or len(set(labels)) < 2:
            return {
                "model_available": False,
                "sample_size": len(rows),
                "calibrated_probability": None,
                "feature_weights": {},
                "intercept": None,
            }

        model = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median")),
                ("scaler", StandardScaler()),
                (
                    "classifier",
                    LogisticRegression(max_iter=1000, class_weight="balanced", random_state=42),
                ),
            ]
        )
        matrix = np.asarray(rows, dtype=float)
        target = np.asarray(labels, dtype=int)
        model.fit(matrix, target)

        coefficients = model.named_steps["classifier"].coef_[0]
        intercept = float(model.named_steps["classifier"].intercept_[0])
        weights = {column: round(float(weight), 6) for column, weight in zip(FEATURE_COLUMNS, coefficients)}
        calibrated_probability = None
        if feature_vector is not None:
            vector = np.asarray([
                _safe_float(feature_vector.get(column, 0.0))
                for column in FEATURE_COLUMNS
            ], dtype=float).reshape(1, -1)
            calibrated_probability = float(model.predict_proba(vector)[0][1])

        return {
            "model_available": True,
            "sample_size": len(rows),
            "calibrated_probability": round(_clamp(calibrated_probability), 4) if calibrated_probability is not None else None,
            "feature_weights": weights,
            "intercept": round(intercept, 6),
        }

    @staticmethod
    def score_grant(
        grant_id: str,
        org_id: str,
        preset: str = "balanced",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Score a single grant using TONY algorithm.
        
        Args:
            grant_id: Grant ID to score
            org_id: Organization ID
            preset: 'conservative', 'balanced', or 'lenient'
            config: Custom configuration dict (uses DEFAULT_CONFIG if None)
            
        Returns:
            Dict with:
            - base_risk_probability
            - final_risk_probability
            - risk_descriptor
            - grant_recommendation
            - features
            - altman_zscore
            - organizational_health
            - components (breakdown of scoring)
        """
        if config is None:
            config = DEFAULT_CONFIG

        grant = db.session.get(Grant, grant_id)
        if not grant:
            raise ValueError(f"Grant {grant_id} not found")

        # Layer 1: Extract financial data.
        financial_data = TonyScorer.extract_financial_snapshot(org_id)
        if not financial_data.get("revenue"):
            raise ValueError(f"Insufficient financial data for organization {org_id}")

        # Layer 2: Calculate features.
        features = TonyScorer.calculate_features(financial_data)

        # Layer 3: Calculate risk components.
        administrative_cost_ratio = _safe_float(features.get("admin_expense_ratio", financial_data.get("admin_expense_ratio", 0.25)), 0.25)
        base_prob = TonyScorer.tuckman_chang_vulnerability(
            equity=financial_data.get("unrestricted_net_assets", 0.0),
            revenue=financial_data.get("revenue", 0.0),
            administrative_cost_ratio=administrative_cost_ratio,
        )
        z_score, altman_zone = TonyScorer.calculate_altman_zscore(financial_data)
        org_health = TonyScorer.calculate_organizational_health(org_id)
        trussel_greenlee = TonyScorer.trussel_greenlee_vulnerability(
            features.get("current_ratio"),
            features.get("operating_reserves_days"),
            features.get("debt_to_assets", features.get("liabilities_to_assets", 0.0)),
        )

        preset_weights = config["scoring_presets"].get(preset, config["scoring_presets"]["balanced"])
        calibration = TonyScorer.calibrate_model_from_history(org_id, config=config, feature_vector=features)

        ratio_scores = {
            "program_expense_efficiency": TonyScorer.program_expense_efficiency_score(features.get("program_expense_ratio", 0.0)),
            "current_ratio": TonyScorer.current_ratio_score(
                financial_data.get("current_assets", financial_data.get("assets", 0.0)),
                financial_data.get("current_liabilities", financial_data.get("liabilities", 0.0)),
            ),
            "operating_reserves": TonyScorer.operating_reserves_score(features.get("operating_reserves_days", 0.0)),
            "debt_to_assets": TonyScorer.debt_to_assets_score(features.get("debt_to_assets", features.get("liabilities_to_assets", 0.0))),
        }
        ratio_risk = float(np.mean(list(ratio_scores.values())))
        capacity_risk = 1.0 - org_health["score"]

        hybrid_probability = (
            preset_weights.get("base_score", 0.50) * base_prob
            + preset_weights.get("financial_ratios", 0.30) * ratio_risk
            + preset_weights.get("organizational_health", 0.20) * capacity_risk
        )
        calibrated_probability = calibration.get("calibrated_probability")
        final_prob = hybrid_probability if calibrated_probability is None else (0.8 * hybrid_probability) + (0.2 * calibrated_probability)
        final_prob = float(_clamp(final_prob))

        thresholds = config["thresholds"]
        reserve_days = features.get("operating_reserves_days", 0.0)
        if reserve_days >= thresholds["continuity_low"] and final_prob < thresholds["risk_probability_moderate"]:
            descriptor = "Low Risk (Excellent)"
        elif reserve_days >= thresholds["continuity_moderate"] and final_prob < thresholds["risk_probability_high"]:
            descriptor = "Moderate Risk (Acceptable)"
        else:
            descriptor = "High Risk (Insufficient)"

        recommendation = TonyScorer._generate_recommendation(
            risk_probability=final_prob,
            continuity=reserve_days,
            operating_margin=features.get("operating_margin", 0.0),
            leverage=features.get("debt_to_assets", features.get("liabilities_to_assets", 0.0)),
            altman_zone=altman_zone,
            org_health_score=org_health["score"],
        )

        risk_factors = list(recommendation.get("risk_factors", []))
        if administrative_cost_ratio >= 0.35:
            risk_factors.append("high_administrative_cost_ratio")
        if financial_data.get("unrestricted_net_assets", 0.0) <= 0:
            risk_factors.append("insufficient_equity")
        if ratio_scores["current_ratio"] >= 0.75:
            risk_factors.append("weak_liquidity")
        if ratio_scores["operating_reserves"] >= 0.75:
            risk_factors.append("thin_operating_reserves")

        if trussel_greenlee >= 0.7:
            risk_factors.append("trussel_greenlee_flag")

        risk_factors = list(dict.fromkeys(risk_factors))

        return {
            "grant_id": grant_id,
            "grant_name": getattr(grant, "name", None) or getattr(grant, "title", ""),
            "org_id": org_id,
            "scored_at": _utcnow_naive().isoformat(),
            "preset": preset,
            "features": features,
            "base_risk_probability": round(float(base_prob), 4),
            "final_risk_probability": round(float(final_prob), 4),
            "risk_descriptor": descriptor,
            "altman_zscore": z_score,
            "altman_zone": altman_zone,
            "organizational_health": org_health["score"],
            "capacity_risk": round(float(capacity_risk), 4),
            "tuckman_chang_vulnerability": round(float(base_prob), 4),
            "trussel_greenlee_vulnerability": round(float(trussel_greenlee), 4),
            "financial_ratio_scores": ratio_scores,
            "hybrid_weights": preset_weights,
            "calibration": calibration,
            "grant_recommendation": recommendation,
            "adjustments": {
                "base_score": round(float(base_prob), 4),
                "financial_ratios": round(float(ratio_risk), 4),
                "organizational_health": round(float(capacity_risk), 4),
                "calibration_probability": calibrated_probability,
                "trussel_greenlee_secondary_check": round(float(trussel_greenlee), 4),
            },
            "financial_snapshot": {
                "revenue": round(financial_data.get("revenue", 0.0), 2),
                "expenses": round(financial_data.get("expenses", 0.0), 2),
                "assets": round(financial_data.get("assets", 0.0), 2),
                "liabilities": round(financial_data.get("liabilities", 0.0), 2),
                "unrestricted_net": round(financial_data.get("unrestricted_net_assets", 0.0), 2),
                "admin_expense_ratio": round(float(administrative_cost_ratio), 4),
                "current_ratio": round(float(financial_data.get("current_ratio", 0.0)), 4),
                "operating_reserves_days": round(float(features.get("operating_reserves_days", 0.0)), 2),
                "debt_to_assets": round(float(features.get("debt_to_assets", 0.0)), 4),
            },
            "risk_factors": risk_factors,
        }

    @staticmethod
    def _generate_recommendation(
        risk_probability: float,
        continuity: float,
        operating_margin: float,
        leverage: float,
        altman_zone: str | None = None,
        org_health_score: float = 0.5,
    ) -> dict[str, Any]:
        """Generate grant recommendation based on risk factors."""
        reasons: list[str] = []

        if continuity < 90:
            reasons.append("low_operating_reserves")
        if operating_margin < 0:
            reasons.append("negative_operating_margin")
        if leverage > 1.0:
            reasons.append("high_leverage")
        if altman_zone == "distress":
            reasons.append("altman_distress_zone")
        elif altman_zone == "grey":
            reasons.append("altman_grey_zone")
        if org_health_score < 0.45:
            reasons.append("weak_organizational_health")

        if risk_probability >= 0.65 or altman_zone == "distress" or len(reasons) >= 2:
            label = "Elevated Risk"
            recommendation = "CAUTION: Elevated risk detected. Recommend conditional approval with monitoring."
        elif risk_probability >= 0.4 or len(reasons) == 1:
            label = "Conditional"
            recommendation = "CONDITIONAL: Approved pending additional documentation or milestones."
        else:
            label = "Standard"
            recommendation = "APPROVED: Standard approval pathway. Monitor per normal procedures."

        return {
            "label": label,
            "recommendation": recommendation,
            "risk_factors": reasons,
            "risk_probability": round(risk_probability, 4),
        }

    @staticmethod
    def score_organization_grants(
        org_id: str,
        preset: str = "balanced",
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Score all active grants for an organization.
        
        Returns sorted list of grant scores (highest risk first).
        """
        grants = db.session.execute(
            select(Grant).where(
                (Grant.organization_id == org_id)
                & (Grant.status.in_(["active", "pending", "submitted"]))
            )
        ).scalars().all()

        if limit:
            grants = grants[:limit]

        scores = []
        for grant in grants:
            try:
                score = TonyScorer.score_grant(grant.id, org_id, preset)
                scores.append(score)
            except Exception as e:
                logger.warning(f"Failed to score grant {grant.id}: {e}")

        # Sort by risk probability (descending)
        scores.sort(key=lambda x: x["final_risk_probability"], reverse=True)
        return scores


class TonyScoringService:
    """High-level service for TONY scoring operations."""

    @staticmethod
    def run_organization_audit(org_id: str, preset: str = "balanced") -> dict[str, Any]:
        """Run full TONY scoring audit for organization."""
        try:
            grant_scores = TonyScorer.score_organization_grants(org_id, preset)

            # Calculate aggregates
            if grant_scores:
                avg_risk = np.mean([s["final_risk_probability"] for s in grant_scores])
                high_risk_count = len([s for s in grant_scores if s["risk_descriptor"] == "High Risk (Insufficient)"])
                moderate_risk_count = len([s for s in grant_scores if s["risk_descriptor"] == "Moderate Risk (Acceptable)"])
                low_risk_count = len([s for s in grant_scores if s["risk_descriptor"] == "Low Risk (Excellent)"])
            else:
                avg_risk = 0.5
                high_risk_count = 0
                moderate_risk_count = 0
                low_risk_count = 0

            return {
                "organization_id": org_id,
                "audited_at": _utcnow_naive().isoformat(),
                "preset": preset,
                "grant_scores": grant_scores,
                "summary": {
                    "total_grants_scored": len(grant_scores),
                    "average_risk_probability": round(float(avg_risk), 4),
                    "high_risk_count": high_risk_count,
                    "moderate_risk_count": moderate_risk_count,
                    "low_risk_count": low_risk_count,
                    "status": (
                        "Critical" if high_risk_count > 0
                        else "Moderate" if moderate_risk_count > 0
                        else "Healthy"
                    ),
                },
                "recommendations": [
                    s["grant_recommendation"]
                    for s in grant_scores
                    if s["risk_descriptor"] != "Low Risk (Excellent)"
                ],
            }
        except Exception as e:
            logger.error(f"TONY audit failed for org {org_id}: {e}")
            raise
