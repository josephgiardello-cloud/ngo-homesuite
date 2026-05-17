"""Continuous compliance monitoring service.

Runs periodic checks to detect compliance drift, flag issues, and generate alerts.
Can be run as a background task or APScheduler job.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from ngo_homesuite.models.core import Organization, Donation, Fund, Grant, Expense, Project, db
from ngo_homesuite.compliance.gaap_validator import GaapComplianceValidator
from ngo_homesuite.compliance.risk_scoring import RiskScoringEngine
from ngo_homesuite.compliance.grant_validator import GrantPreSubmissionValidator
from ngo_homesuite.compliance.p2p_fraud_detector import P2PFraudDetector

logger = logging.getLogger(__name__)


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _grant_deadline(grant: Grant):
    return getattr(grant, "deadline", None) or getattr(grant, "application_deadline", None)


def _fund_balance(fund: Fund) -> float:
    value = getattr(fund, "balance", None)
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _fund_is_restricted(fund: Fund) -> bool:
    return bool(getattr(fund, "is_restricted", False))


class ComplianceMonitoringService:
    """Service for continuous compliance monitoring and alerts."""
    
    @staticmethod
    def run_full_compliance_audit(organization_id: int) -> dict[str, Any]:
        """
        Run comprehensive compliance audit for an organization.
        Returns summary of findings and recommended actions.
        """
        findings = {
            "organization_id": organization_id,
            "timestamp": _utcnow_naive().isoformat(),
            "audit_items": {
                "gaap_violations": [],
                "high_risk_donations": [],
                "high_risk_grants": [],
                "overdue_grants": [],
                "unallocated_funds": [],
                "fraud_alerts": [],
                "summary": {}
            }
        }
        
        # 1. GAAP Compliance
        gaap_violations = GaapComplianceValidator.validate_organization(organization_id)
        findings["audit_items"]["gaap_violations"] = [
            {
                "severity": v.severity,
                "code": v.code,
                "message": v.message,
                "entity_type": v.entity_type,
                "entity_id": v.entity_id,
                "remediation": v.remediation,
            }
            for v in gaap_violations
        ]
        
        # 2. High-risk donations
        donations = db.session.execute(
            select(Donation).where(Donation.organization_id == organization_id)
        ).scalars().all()
        
        high_risk_donations = []
        for donation in donations:
            assessment = RiskScoringEngine.assess_donation(donation)
            if assessment.overall_score >= 40:
                high_risk_donations.append({
                    "donation_id": donation.id,
                    "risk_score": assessment.overall_score,
                    "severity": assessment.severity,
                    "factors": [
                        {"factor": f.factor, "score": f.score, "reason": f.reason}
                        for f in assessment.factors
                    ],
                    "recommendation": assessment.recommendation,
                })
        
        findings["audit_items"]["high_risk_donations"] = high_risk_donations[:20]  # Limit to 20
        
        # 3. High-risk grants
        grants = db.session.execute(
            select(Grant).where(Grant.organization_id == organization_id)
        ).scalars().all()
        
        high_risk_grants = []
        overdue_grants = []
        
        for grant in grants:
            assessment = RiskScoringEngine.assess_grant(grant)
            
            if assessment.overall_score >= 40:
                high_risk_grants.append({
                    "grant_id": grant.id,
                    "title": grant.title,
                    "risk_score": assessment.overall_score,
                    "severity": assessment.severity,
                    "factors": [
                        {"factor": f.factor, "score": f.score, "reason": f.reason}
                        for f in assessment.factors
                    ],
                    "recommendation": assessment.recommendation,
                })
            
            # Track overdue grants
            deadline = _grant_deadline(grant)
            if deadline and deadline < _utcnow_naive().date():
                if grant.status not in ["completed", "closed"]:
                    overdue_grants.append({
                        "grant_id": grant.id,
                        "title": grant.title,
                        "deadline": deadline.isoformat(),
                        "days_overdue": (_utcnow_naive().date() - deadline).days,
                        "status": grant.status,
                    })
        
        findings["audit_items"]["high_risk_grants"] = high_risk_grants[:10]
        findings["audit_items"]["overdue_grants"] = overdue_grants
        
        # 4. Unallocated funds (not invested/spent)
        funds = db.session.execute(
            select(Fund).where(Fund.organization_id == organization_id)
        ).scalars().all()
        unallocated = [f for f in funds if _fund_balance(f) > 100]
        
        findings["audit_items"]["unallocated_funds"] = [
            {
                "fund_id": f.id,
                "name": f.name,
                "balance": _fund_balance(f),
                "status": "unallocated" if _fund_balance(f) > 100 else "allocated",
            }
            for f in unallocated
        ]
        
        # 5. Fraud alerts on P2P pages
        from ngo_homesuite.models.core import P2PPage
        p2p_pages = db.session.execute(
            select(P2PPage).where(P2PPage.organization_id == organization_id)
        ).scalars().all()
        
        fraud_alerts = []
        for page in p2p_pages:
            fraud_score_data = P2PFraudDetector.get_fraud_score(page)
            if fraud_score_data["fraud_score"] > 20:
                fraud_alerts.append({
                    "page_id": page.id,
                    "page_title": page.title,
                    "fraud_score": fraud_score_data["fraud_score"],
                    "risk_level": fraud_score_data["risk_level"],
                    "alert_count": fraud_score_data["alert_count"],
                })
        
        findings["audit_items"]["fraud_alerts"] = fraud_alerts
        
        # 6. Summary
        summary = {
            "total_donations": len(donations),
            "high_risk_donation_count": len(high_risk_donations),
            "total_grants": len(grants),
            "high_risk_grant_count": len(high_risk_grants),
            "overdue_grant_count": len(overdue_grants),
            "gaap_violation_count": len(gaap_violations),
            "unallocated_fund_count": len(unallocated),
            "fraud_alert_count": len(fraud_alerts),
            "overall_compliance_status": ComplianceMonitoringService._determine_status(
                len(gaap_violations),
                len(high_risk_donations),
                len(high_risk_grants),
                len(overdue_grants),
                len(fraud_alerts)
            ),
        }
        
        findings["audit_items"]["summary"] = summary
        
        return findings
    
    @staticmethod
    def check_grant_deadlines(organization_id: int) -> dict[str, Any]:
        """Check for approaching or overdue grant deadlines."""
        alerts = {
            "critical": [],  # Overdue or due today
            "urgent": [],    # Due within 7 days
            "warning": [],   # Due within 30 days
        }
        
        grants = db.session.execute(
            select(Grant).where(Grant.organization_id == organization_id)
        ).scalars().all()
        
        now = _utcnow_naive().date()
        
        for grant in grants:
            deadline = _grant_deadline(grant)
            if not deadline or grant.status in ["completed", "closed"]:
                continue
            
            days_remaining = (deadline - now).days
            
            if days_remaining < 0:
                alerts["critical"].append({
                    "grant_id": grant.id,
                    "title": grant.title,
                    "days_overdue": abs(days_remaining),
                    "status": grant.status,
                })
            elif days_remaining <= 7:
                alerts["urgent"].append({
                    "grant_id": grant.id,
                    "title": grant.title,
                    "days_remaining": days_remaining,
                    "status": grant.status,
                })
            elif days_remaining <= 30:
                alerts["warning"].append({
                    "grant_id": grant.id,
                    "title": grant.title,
                    "days_remaining": days_remaining,
                    "status": grant.status,
                })
        
        return alerts
    
    @staticmethod
    def detect_compliance_drift(organization_id: int) -> dict[str, Any]:
        """
        Detect compliance drift by comparing current state to expected state.
        """
        drift_indicators = {
            "timestamp": _utcnow_naive().isoformat(),
            "organization_id": organization_id,
            "drift_detected": False,
            "indicators": []
        }
        
        # Check 1: Sudden spike in transaction volume
        last_30_days = _utcnow_naive() - timedelta(days=30)
        recent_donations = db.session.execute(
            select(func.count(Donation.id)).where(
                Donation.organization_id == organization_id,
                Donation.donation_date >= last_30_days
            )
        ).scalar() or 0
        
        last_60_to_30_days = db.session.execute(
            select(func.count(Donation.id)).where(
                Donation.organization_id == organization_id,
                Donation.donation_date >= _utcnow_naive() - timedelta(days=60),
                Donation.donation_date < last_30_days
            )
        ).scalar() or 0
        
        if last_60_to_30_days > 0:
            volume_change = ((recent_donations - last_60_to_30_days) / last_60_to_30_days) * 100
            if volume_change > 100:  # More than doubled
                drift_indicators["drift_detected"] = True
                drift_indicators["indicators"].append({
                    "type": "transaction_volume_spike",
                    "severity": "medium",
                    "message": f"Donation volume increased {volume_change:.0f}% in last 30 days",
                    "recommendation": "Verify surge is legitimate (fundraising campaign, etc.)",
                })
        
        # Check 2: Increasing unallocated funds
        org_funds = db.session.execute(
            select(Fund).where(Fund.organization_id == organization_id)
        ).scalars().all()
        unallocated_total = sum(_fund_balance(f) for f in org_funds if not _fund_is_restricted(f))
        
        if unallocated_total > 100000:
            drift_indicators["drift_detected"] = True
            drift_indicators["indicators"].append({
                "type": "large_unallocated_balance",
                "severity": "high",
                "message": f"Unrestricted funds balance is ${unallocated_total:,.2f}",
                "recommendation": "Plan program spending or restricted fund allocations",
            })
        
        # Check 3: High proportion of unallocated donations
        all_donations = db.session.execute(
            select(func.count(Donation.id)).where(
                Donation.organization_id == organization_id
            )
        ).scalar() or 0
        
        allocated_donations = db.session.execute(
            select(func.count(Donation.id)).where(
                Donation.organization_id == organization_id,
                Donation.fund_id != None
            )
        ).scalar() or 0
        
        if all_donations > 0:
            allocation_rate = (allocated_donations / all_donations) * 100
            if allocation_rate < 50:
                drift_indicators["drift_detected"] = True
                drift_indicators["indicators"].append({
                    "type": "low_fund_allocation",
                    "severity": "medium",
                    "message": f"Only {allocation_rate:.0f}% of donations are fund-allocated",
                    "recommendation": "Improve fund allocation process for better fund accounting",
                })
        
        return drift_indicators
    
    @staticmethod
    def _determine_status(gaap_count: int, high_risk_donations: int, 
                         high_risk_grants: int, overdue_grants: int, fraud_alerts: int) -> str:
        """Determine overall compliance status based on findings."""
        total_issues = gaap_count + high_risk_donations + high_risk_grants + overdue_grants + fraud_alerts
        
        if total_issues == 0:
            return "compliant"
        elif total_issues <= 5:
            return "minor_issues"
        elif total_issues <= 15:
            return "moderate_issues"
        else:
            return "critical_issues"
