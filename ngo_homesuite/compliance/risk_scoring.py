"""Risk scoring engine for donations, grants, projects, and other entities.

Provides a unified risk assessment framework to identify high-risk transactions and entities.
Risk scores range from 0 (low risk) to 100 (high risk).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy import func, select

from ngo_homesuite.models.core import Donation, Donor, Grant, Project, Expense, db


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _grant_deadline(grant: Grant):
    return getattr(grant, "deadline", None) or getattr(grant, "application_deadline", None)


def _grant_total_amount(grant: Grant) -> float | None:
    return getattr(grant, "total_amount", None) or getattr(grant, "amount_awarded", None) or getattr(grant, "amount_requested", None)


def _grant_reporting_requirements(grant: Grant):
    return getattr(grant, "reporting_requirements", None) or getattr(grant, "requirements", None)


def _donation_is_restricted(donation: Donation) -> bool:
    return bool(getattr(donation, "is_restricted", False))


def _donation_metadata(donation: Donation):
    return getattr(donation, "metadata", None) or {}


@dataclass
class RiskFactor:
    """Represents a single risk factor contributing to overall score."""
    factor: str
    score: int  # 0-100
    reason: str
    severity: str  # "low", "medium", "high"


@dataclass
class RiskAssessment:
    """Complete risk assessment with score and factors."""
    overall_score: int  # 0-100
    severity: str  # "low", "medium", "high", "critical"
    factors: list[RiskFactor]
    recommendation: str
    
    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_score": self.overall_score,
            "severity": self.severity,
            "factors": [
                {"factor": f.factor, "score": f.score, "reason": f.reason, "severity": f.severity}
                for f in self.factors
            ],
            "recommendation": self.recommendation,
        }


class RiskScoringEngine:
    """Unified risk assessment across organization entities."""
    
    @staticmethod
    def assess_donation(donation: Donation) -> RiskAssessment:
        """Assess risk profile of a single donation."""
        factors = []
        
        # Risk 1: Unusual donation amount (very large or very small)
        org_donations = db.session.execute(
            select(func.avg(Donation.amount)).where(
                Donation.organization_id == donation.organization_id,
                Donation.amount > 0
            )
        ).scalar() or 100
        
        if donation.amount > org_donations * 5:
            factors.append(RiskFactor(
                factor="unusual_amount",
                score=25,
                reason=f"Amount ${donation.amount} is 5x+ organization average",
                severity="medium"
            ))
        elif donation.amount < 1:
            factors.append(RiskFactor(
                factor="micro_donation",
                score=10,
                reason="Very small donation amount",
                severity="low"
            ))
        
        # Risk 2: Restricted donation without fund allocation
        if _donation_is_restricted(donation) and not donation.fund_id:
            factors.append(RiskFactor(
                factor="restricted_unallocated",
                score=35,
                reason="Restricted donation not allocated to specific fund",
                severity="high"
            ))
        
        # Risk 3: Donor is new and large donation
        if donation.donor_id:
            donor = db.session.get(Donor, donation.donor_id)
            if donor:
                donation_count = db.session.execute(
                    select(func.count(Donation.id)).where(
                        Donation.donor_id == donation.donor_id
                    )
                ).scalar() or 0
                
                if donation_count <= 1 and donation.amount > org_donations * 2:
                    factors.append(RiskFactor(
                        factor="new_large_donor",
                        score=20,
                        reason="First-time or new donor giving unusually large amount",
                        severity="medium"
                    ))
        
        # Risk 4: Anomalous frequency (rapid back-to-back donations)
        if donation.donor_id:
            recent_count = db.session.execute(
                select(func.count(Donation.id)).where(
                    Donation.donor_id == donation.donor_id,
                    Donation.donation_date >= _utcnow_naive() - timedelta(days=1)
                )
            ).scalar() or 0
            
            if recent_count > 3:
                factors.append(RiskFactor(
                    factor="rapid_donations",
                    score=30,
                    reason="Multiple donations from same donor in last 24 hours",
                    severity="medium"
                ))
        
        # Risk 5: Missing donor information
        if not donation.donor_id:
            factors.append(RiskFactor(
                factor="anonymous_donation",
                score=15,
                reason="Anonymous or unattributed donation",
                severity="low"
            ))
        
        # Risk 6: Suspicious payment method or other flags
        donation_metadata = _donation_metadata(donation)
        if isinstance(donation_metadata, dict):
            if donation_metadata.get("flagged_for_review"):
                factors.append(RiskFactor(
                    factor="manual_flag",
                    score=50,
                    reason="Donation manually flagged for review",
                    severity="high"
                ))
        
        overall_score = int(sum(f.score for f in factors) / max(1, len(factors))) if factors else 0
        overall_score = min(100, overall_score)
        
        if overall_score >= 60:
            severity = "critical"
        elif overall_score >= 40:
            severity = "high"
        elif overall_score >= 20:
            severity = "medium"
        else:
            severity = "low"
        
        recommendation = RiskScoringEngine._recommendation_for_donation(severity, factors)
        
        return RiskAssessment(
            overall_score=overall_score,
            severity=severity,
            factors=factors,
            recommendation=recommendation
        )
    
    @staticmethod
    def assess_grant(grant: Grant) -> RiskAssessment:
        """Assess risk profile of a grant."""
        factors = []
        
        # Risk 1: Grant past deadline without completion
        deadline = _grant_deadline(grant)
        if deadline and deadline < _utcnow_naive().date():
            if grant.status not in ["completed", "closed"]:
                factors.append(RiskFactor(
                    factor="overdue_grant",
                    score=50,
                    reason="Grant past deadline without completion",
                    severity="high"
                ))
        
        # Risk 2: High amount with no project linkage
        total_amount = _grant_total_amount(grant)
        if not grant.project_id and total_amount and total_amount > 50000:
            factors.append(RiskFactor(
                factor="large_unlinked_grant",
                score=30,
                reason="Large grant amount not linked to project",
                severity="medium"
            ))
        
        # Risk 3: Grant status unclear or inconsistent
        if not grant.status or grant.status not in ["draft", "submitted", "awarded", "in_progress", "completed", "closed"]:
            factors.append(RiskFactor(
                factor="invalid_status",
                score=40,
                reason="Grant has invalid or unclear status",
                severity="high"
            ))
        
        # Risk 4: Missing reporting requirements
        if grant.status == "awarded" and not _grant_reporting_requirements(grant):
            factors.append(RiskFactor(
                factor="missing_requirements",
                score=35,
                reason="Awarded grant missing reporting requirements",
                severity="medium"
            ))
        
        # Risk 5: Very tight reporting timeline
        if deadline:
            days_remaining = (deadline - _utcnow_naive().date()).days
            if 0 < days_remaining < 30 and grant.status not in ["completed", "closed"]:
                factors.append(RiskFactor(
                    factor="tight_deadline",
                    score=25,
                    reason=f"Grant deadline in {days_remaining} days",
                    severity="medium"
                ))
        
        overall_score = int(sum(f.score for f in factors) / max(1, len(factors))) if factors else 0
        overall_score = min(100, overall_score)
        
        if overall_score >= 60:
            severity = "critical"
        elif overall_score >= 40:
            severity = "high"
        elif overall_score >= 20:
            severity = "medium"
        else:
            severity = "low"
        
        recommendation = RiskScoringEngine._recommendation_for_grant(severity, factors)
        
        return RiskAssessment(
            overall_score=overall_score,
            severity=severity,
            factors=factors,
            recommendation=recommendation
        )
    
    @staticmethod
    def _recommendation_for_donation(severity: str, factors: list[RiskFactor]) -> str:
        """Generate recommendation for donation based on risk."""
        if severity == "critical":
            return "HOLD: Flag for manual review before processing. Verify donor identity and donation legitimacy."
        elif severity == "high":
            return "CAUTION: Review donation details carefully. Request additional verification if needed."
        elif severity == "medium":
            return "Monitor: Process normally but track for compliance audit."
        else:
            return "Clear: Process normally."
    
    @staticmethod
    def _recommendation_for_grant(severity: str, factors: list[RiskFactor]) -> str:
        """Generate recommendation for grant based on risk."""
        if severity == "critical":
            return "URGENT: Immediate action required. Address deadline or status issues."
        elif severity == "high":
            return "ACTION REQUIRED: Resolve outstanding issues to maintain grant compliance."
        elif severity == "medium":
            return "REVIEW: Schedule review of grant status and requirements."
        else:
            return "On Track: Continue normal grant management."
