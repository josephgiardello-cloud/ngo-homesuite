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
from sqlalchemy import func, select

from ngo_homesuite.models.core import (
    Organization, Grant, Project, Fund, Donation, Expense, db
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
        "continuity_low": 8.0,
        "continuity_moderate": 4.0,
        "risk_probability_moderate": 0.35,
        "risk_probability_high": 0.6,
    },
    "altman_z": {
        "safe_threshold": 2.6,
        "distress_threshold": 1.1,
    },
    "weights": {
        "benchmark_gap": 0.25,
        "confidence_penalty": 0.20,
        "donor_penalty": 0.15,
        "cashflow_penalty": 0.15,
        "compliance_penalty": 0.10,
        "altman_penalty": 0.20,
        "trend_relief": 0.20,
    },
    "scoring_presets": {
        "conservative": {
            "benchmark_gap": 0.30,
            "confidence_penalty": 0.25,
            "donor_penalty": 0.20,
            "cashflow_penalty": 0.20,
            "compliance_penalty": 0.15,
            "altman_penalty": 0.25,
            "trend_relief": 0.10,
        },
        "balanced": {
            "benchmark_gap": 0.25,
            "confidence_penalty": 0.20,
            "donor_penalty": 0.15,
            "cashflow_penalty": 0.15,
            "compliance_penalty": 0.10,
            "altman_penalty": 0.20,
            "trend_relief": 0.20,
        },
        "lenient": {
            "benchmark_gap": 0.20,
            "confidence_penalty": 0.15,
            "donor_penalty": 0.10,
            "cashflow_penalty": 0.10,
            "compliance_penalty": 0.05,
            "altman_penalty": 0.15,
            "trend_relief": 0.30,
        },
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

        # Get latest year's donations/expenses by fund
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

        # Program expenses (expenses tagged to projects)
        program_expenses = db.session.execute(
            select(func.sum(Expense.amount)).where(
                (Expense.organization_id == org_id)
                & (Expense.project_id.isnot(None))
                & (Expense.created_at >= year_ago)
            )
        ).scalar() or 0.0

        # Fund balances = assets (unrestricted) and liabilities estimate
        funds = db.session.execute(
            select(Fund).where(Fund.organization_id == org_id)
        ).scalars().all()
        unrestricted_funds = sum(_fund_balance(f) for f in funds if not _fund_is_restricted(f))
        restricted_funds = sum(_fund_balance(f) for f in funds if _fund_is_restricted(f))

        total_assets = max(unrestricted_funds + restricted_funds, 0.0)
        # Estimate liabilities as percentage of assets (or from expenses * 0.2 as proxy)
        estimated_liabilities = expenses * 0.15 if expenses > 0 else 0.0

        return {
            "revenue": donations,
            "expenses": expenses,
            "program_expenses": program_expenses,
            "assets": total_assets,
            "liabilities": estimated_liabilities,
            "unrestricted_net_assets": unrestricted_funds,
            "restricted_net_assets": restricted_funds,
        }

    @staticmethod
    def calculate_features(financial_data: dict[str, float]) -> dict[str, float]:
        """Calculate core risk features from financial snapshot."""
        revenue = financial_data.get("revenue", 0.0)
        expenses = financial_data.get("expenses", 0.0)
        program_expenses = financial_data.get("program_expenses", 0.0)
        assets = financial_data.get("assets", 0.0)
        liabilities = financial_data.get("liabilities", 0.0)
        unrestricted_net = financial_data.get("unrestricted_net_assets", 0.0)

        # Continuity months: runway = unrestricted_net / (monthly_burn)
        monthly_burn = expenses / 12.0 if expenses > 0 else 1.0
        continuity_months = unrestricted_net / monthly_burn if monthly_burn > 0 else 0.0
        continuity_months = max(-24, min(120, continuity_months))  # Cap for stability

        # Operating margin: (revenue - expenses) / revenue
        operating_margin = (revenue - expenses) / revenue if revenue > 0 else -1.0

        # Program expense ratio: program_expenses / total_expenses
        program_expense_ratio = program_expenses / expenses if expenses > 0 else 0.0

        # Liabilities to assets
        liabilities_to_assets = liabilities / assets if assets > 0 else 1.0

        # Revenue volatility (proxy: assume stable if no history)
        revenue_volatility = 0.1  # Conservative default

        return {
            "continuity_months": continuity_months,
            "operating_margin": operating_margin,
            "program_expense_ratio": program_expense_ratio,
            "liabilities_to_assets": liabilities_to_assets,
            "revenue_volatility": revenue_volatility,
        }

    @staticmethod
    def calculate_base_risk_probability(features: dict[str, float]) -> float:
        """Calculate base risk probability (0-1) from features.
        
        Uses weighted combination of risk factors.
        """
        continuity = features.get("continuity_months", 0.0)
        margin = features.get("operating_margin", 0.0)
        program_ratio = features.get("program_expense_ratio", 0.0)
        leverage = features.get("liabilities_to_assets", 0.0)
        volatility = features.get("revenue_volatility", 0.0)

        # Risk components (higher = more risk)
        continuity_risk = 1.0 / (1.0 + np.exp(continuity / 4.0))  # Sigmoid
        margin_risk = 1.0 if margin < 0 else max(0.0, -margin * 2.0)
        program_risk = 1.0 if program_ratio < 0.5 else 0.0
        leverage_risk = min(leverage, 1.0) if leverage > 0 else 0.0
        volatility_risk = min(volatility * 2.0, 1.0)

        # Weighted average
        base_probability = (
            continuity_risk * 0.4
            + margin_risk * 0.25
            + program_risk * 0.15
            + leverage_risk * 0.15
            + volatility_risk * 0.05
        )

        return float(np.clip(base_probability, 0.0, 1.0))

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
    def calculate_organizational_health(org_id: str) -> dict[str, float]:
        """Calculate organizational health score (0-1).
        
        Components: governance, program impact, operational resilience, market position.
        """
        org = db.session.get(Organization, org_id)
        if not org:
            return {"score": 0.5, "components": {}}

        # Count grants (program impact proxy)
        grant_count = db.session.execute(
            select(func.count(Grant.id)).where(Grant.organization_id == org_id)
        ).scalar() or 0

        # Count projects (operational resilience proxy)
        project_count = db.session.execute(
            select(func.count(Project.id)).where(Project.organization_id == org_id)
        ).scalar() or 0

        # Funding diversity (market position proxy)
        fund_count = db.session.execute(
            select(func.count(Fund.id)).where(Fund.organization_id == org_id)
        ).scalar() or 0

        # Score components (0-1)
        program_score = min(grant_count / 10.0, 1.0)  # Expect ~10 active grants
        operations_score = min(project_count / 15.0, 1.0)  # Expect ~15 projects
        market_score = min(fund_count / 5.0, 1.0)  # Expect ~5 funds

        overall = np.mean([program_score, operations_score, market_score])

        return {
            "score": round(float(overall), 4),
            "components": {
                "program_impact": round(float(program_score), 4),
                "operational_resilience": round(float(operations_score), 4),
                "market_position": round(float(market_score), 4),
            },
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

        # Layer 1: Extract financial data
        financial_data = TonyScorer.extract_financial_snapshot(org_id)
        if not financial_data.get("revenue"):
            raise ValueError(f"Insufficient financial data for organization {org_id}")

        # Layer 2: Calculate features
        features = TonyScorer.calculate_features(financial_data)

        # Layer 3: Calculate risk components
        base_prob = TonyScorer.calculate_base_risk_probability(features)
        z_score, altman_zone = TonyScorer.calculate_altman_zscore(financial_data)
        org_health = TonyScorer.calculate_organizational_health(org_id)

        # Adjust base probability using preset weights
        preset_weights = config["scoring_presets"].get(preset, config["scoring_presets"]["balanced"])
        thresholds = config["thresholds"]

        # Penalties and reliefs
        altman_penalty = 1.0 if altman_zone == "distress" else (0.4 if altman_zone == "grey" else 0.0)

        # Apply adjustments
        adjustments = {
            "altman_penalty": altman_penalty,
            "org_health_relief": org_health["score"],  # Health score reduces risk
            "leverage_penalty": min(features.get("liabilities_to_assets", 0), 1.0),
        }

        # Calculate final probability
        penalty_total = (
            preset_weights.get("altman_penalty", 0.2) * altman_penalty
            + preset_weights.get("confidence_penalty", 0.2) * (1.0 - org_health["score"])
        )
        relief_total = preset_weights.get("trend_relief", 0.2) * org_health["score"]

        final_prob = base_prob + penalty_total - relief_total
        final_prob = float(np.clip(final_prob, 0.0, 1.0))

        # Determine risk descriptor
        continuity_raw = features.get("continuity_months", 0.0)
        if continuity_raw >= thresholds["continuity_low"] and final_prob < thresholds["risk_probability_moderate"]:
            descriptor = "Low Risk (Excellent)"
        elif continuity_raw >= thresholds["continuity_moderate"] and final_prob < thresholds["risk_probability_high"]:
            descriptor = "Moderate Risk (Acceptable)"
        else:
            descriptor = "High Risk (Insufficient)"

        # Generate grant recommendation
        recommendation = TonyScorer._generate_recommendation(
            risk_probability=final_prob,
            continuity=continuity_raw,
            operating_margin=features.get("operating_margin", 0.0),
            leverage=features.get("liabilities_to_assets", 0.0),
            altman_zone=altman_zone,
            org_health_score=org_health["score"],
        )

        return {
            "grant_id": grant_id,
            "grant_name": getattr(grant, "name", None) or getattr(grant, "title", ""),
            "org_id": org_id,
            "scored_at": _utcnow_naive().isoformat(),
            "preset": preset,
            # Layer outputs
            "features": features,
            "base_risk_probability": round(float(base_prob), 4),
            "final_risk_probability": round(float(final_prob), 4),
            "risk_descriptor": descriptor,
            "altman_zscore": z_score,
            "altman_zone": altman_zone,
            "organizational_health": org_health["score"],
            "grant_recommendation": recommendation,
            # Components breakdown
            "adjustments": adjustments,
            "financial_snapshot": {
                "revenue": round(financial_data.get("revenue", 0.0), 2),
                "expenses": round(financial_data.get("expenses", 0.0), 2),
                "assets": round(financial_data.get("assets", 0.0), 2),
                "liabilities": round(financial_data.get("liabilities", 0.0), 2),
                "unrestricted_net": round(financial_data.get("unrestricted_net_assets", 0.0), 2),
            },
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

        if continuity < 3:
            reasons.append("low_reserve_months")
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
