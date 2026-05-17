"""Fraud detection for P2P fundraising pages.

Detects suspicious patterns in P2P donations including:
- Rapid transaction sequences
- Duplicate/near-duplicate donations
- Unusual amount patterns
- Suspicious donor patterns
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from sqlalchemy import func, select

from ngo_homesuite.models.core import Donation, P2PPage, db


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass
class FraudAlert:
    """Represents a potential fraud indicator."""
    alert_type: str  # e.g., "rapid_sequence", "duplicate_donation", "unusual_amount"
    risk_level: str  # "low", "medium", "high", "critical"
    message: str
    affected_donation_ids: list[int]
    metadata: dict[str, Any] | None = None
    recommendation: str | None = None


class P2PFraudDetector:
    """Detects fraud patterns in P2P donations."""
    
    @staticmethod
    def analyze_p2p_page(page: P2PPage) -> list[FraudAlert]:
        """
        Comprehensive fraud analysis of a P2P page.
        Returns list of fraud alerts, empty if clean.
        """
        alerts = []
        
        # Get all donations for this P2P page
        donations = db.session.execute(
            select(Donation).where(Donation.organization_id == page.organization_id)
        ).scalars().all()
        
        if not donations:
            return alerts
        
        # Check 1: Rapid transaction sequence
        rapid_alerts = P2PFraudDetector._detect_rapid_sequences(page.id, donations)
        alerts.extend(rapid_alerts)
        
        # Check 2: Duplicate/near-duplicate donations
        dup_alerts = P2PFraudDetector._detect_duplicates(donations)
        alerts.extend(dup_alerts)
        
        # Check 3: Unusual amount patterns
        amount_alerts = P2PFraudDetector._detect_unusual_amounts(donations)
        alerts.extend(amount_alerts)
        
        # Check 4: Suspicious donor patterns
        donor_alerts = P2PFraudDetector._detect_suspicious_donors(donations)
        alerts.extend(donor_alerts)
        
        # Check 5: Page-level anomalies
        page_alerts = P2PFraudDetector._detect_page_anomalies(page, donations)
        alerts.extend(page_alerts)
        
        return alerts
    
    @staticmethod
    def _detect_rapid_sequences(page_id: int, donations: list[Donation]) -> list[FraudAlert]:
        """Detect rapid transaction sequences (suspicious pattern)."""
        alerts = []
        
        # Sort by date
        sorted_donations = sorted(donations, key=lambda d: d.donation_date)
        
        # Look for clusters of donations within 1 hour
        hour_window = timedelta(hours=1)
        for i, donation in enumerate(sorted_donations):
            cluster = [d for d in sorted_donations 
                      if donation.donation_date <= d.donation_date <= donation.donation_date + hour_window]
            
            if len(cluster) >= 5:
                alerts.append(FraudAlert(
                    alert_type="rapid_sequence",
                    risk_level="high",
                    message=f"{len(cluster)} donations within 1 hour (starting {donation.donation_date})",
                    affected_donation_ids=[d.id for d in cluster],
                    metadata={"cluster_size": len(cluster), "time_window_hours": 1},
                    recommendation="Verify donations are legitimate. May indicate script/bot activity."
                ))
                break  # Only report once per cluster
        
        # Look for donations within same minute (extremely suspicious)
        minute_clusters = {}
        for donation in sorted_donations:
            minute_key = donation.donation_date.strftime("%Y-%m-%d %H:%M")
            if minute_key not in minute_clusters:
                minute_clusters[minute_key] = []
            minute_clusters[minute_key].append(donation)
        
        for minute_key, cluster in minute_clusters.items():
            if len(cluster) >= 3:
                alerts.append(FraudAlert(
                    alert_type="suspicious_timing",
                    risk_level="critical",
                    message=f"{len(cluster)} donations in same minute ({minute_key})",
                    affected_donation_ids=[d.id for d in cluster],
                    metadata={"cluster_size": len(cluster)},
                    recommendation="URGENT: Likely bot/script attack. Review and flag donations."
                ))
        
        return alerts
    
    @staticmethod
    def _detect_duplicates(donations: list[Donation]) -> list[FraudAlert]:
        """Detect duplicate or near-duplicate donations."""
        alerts = []
        
        # Check for exact duplicates (same donor, same amount, within 1 hour)
        seen = {}
        for donation in donations:
            if not donation.donor_id:
                continue
            
            key = (donation.donor_id, donation.amount)
            if key not in seen:
                seen[key] = []
            seen[key].append(donation)
        
        for (donor_id, amount), group in seen.items():
            if len(group) >= 2:
                # Check if within time window
                time_range = max(d.donation_date for d in group) - min(d.donation_date for d in group)
                if time_range <= timedelta(hours=1):
                    alerts.append(FraudAlert(
                        alert_type="duplicate_donation",
                        risk_level="high",
                        message=f"Donor {donor_id} gave ${amount} multiple times within 1 hour",
                        affected_donation_ids=[d.id for d in group],
                        metadata={"donor_id": donor_id, "amount": amount, "count": len(group)},
                        recommendation="Contact donor to confirm legitimacy. May be accidental duplicate charge."
                    ))
        
        return alerts
    
    @staticmethod
    def _detect_unusual_amounts(donations: list[Donation]) -> list[FraudAlert]:
        """Detect unusual donation amounts."""
        alerts = []
        
        if not donations:
            return alerts
        
        amounts = [float(d.amount) for d in donations if float(d.amount) > 0]
        if not amounts:
            return alerts
        
        avg_amount = sum(amounts) / len(amounts)
        max_amount = max(amounts)
        
        for donation in donations:
            # Flag donations > 10x average
            donation_amount = float(donation.amount)
            if donation_amount > avg_amount * 10:
                alerts.append(FraudAlert(
                    alert_type="unusual_amount",
                    risk_level="medium",
                    message=f"Donation ${donation_amount} is {donation_amount/avg_amount:.1f}x page average",
                    affected_donation_ids=[donation.id],
                    metadata={"amount": donation_amount, "page_average": avg_amount},
                    recommendation="Verify donor identity and large gift process followed."
                ))
            
            # Flag round-number donations (potential testing)
            if donation_amount % 100 == 0 and donation_amount < 100:
                alerts.append(FraudAlert(
                    alert_type="round_number_test",
                    risk_level="low",
                    message=f"Small round-number donation ${donation_amount} (potential test)",
                    affected_donation_ids=[donation.id],
                    metadata={"amount": donation_amount},
                    recommendation="Likely legitimate, but monitor for follow-up."
                ))
        
        return alerts
    
    @staticmethod
    def _detect_suspicious_donors(donations: list[Donation]) -> list[FraudAlert]:
        """Detect suspicious donor patterns."""
        alerts = []
        
        # Donors with many donations on this page only (potential fraud network)
        donor_counts = {}
        for donation in donations:
            if donation.donor_id:
                if donation.donor_id not in donor_counts:
                    donor_counts[donation.donor_id] = 0
                donor_counts[donation.donor_id] += 1
        
        for donor_id, count in donor_counts.items():
            if count >= 10:
                alerts.append(FraudAlert(
                    alert_type="high_donation_count_per_donor",
                    risk_level="medium",
                    message=f"Donor {donor_id} has {count} donations on this page",
                    affected_donation_ids=[d.id for d in donations if d.donor_id == donor_id],
                    metadata={"donor_id": donor_id, "donation_count": count},
                    recommendation="Verify donor is legitimate person/entity, not fraud ring."
                ))
        
        return alerts
    
    @staticmethod
    def _detect_page_anomalies(page: P2PPage, donations: list[Donation]) -> list[FraudAlert]:
        """Detect page-level anomalies."""
        alerts = []
        
        if not donations:
            return alerts
        
        total_raised = sum(float(d.amount) for d in donations)
        
        # Page raised money before being activated
        if page.status != "active" and total_raised > 0:
            alerts.append(FraudAlert(
                alert_type="inactive_page_with_donations",
                risk_level="high",
                message=f"Inactive page has ${total_raised} in donations",
                affected_donation_ids=[d.id for d in donations],
                metadata={"page_status": page.status, "total_raised": total_raised},
                recommendation="Verify page status. Activate or investigate donations."
            ))
        
        # Unusually fast fundraising
        page_goal = getattr(page, "goal", None) or getattr(page, "goal_amount", None)
        if page_goal and total_raised >= page_goal:
            now = _utcnow_naive()
            days_active = (now - (page.created_at or now)).days
            if days_active <= 1:
                alerts.append(FraudAlert(
                    alert_type="goal_reached_too_fast",
                    risk_level="medium",
                    message=f"Page reached ${page_goal} goal in {days_active} day(s)",
                    affected_donation_ids=[d.id for d in donations],
                    metadata={"goal": page_goal, "days": days_active},
                    recommendation="Verify donations are legitimate. Unusually fast fundraising."
                ))
        
        return alerts
    
    @staticmethod
    def get_fraud_score(page: P2PPage) -> dict[str, Any]:
        """
        Get overall fraud risk score (0-100) for a P2P page.
        """
        alerts = P2PFraudDetector.analyze_p2p_page(page)
        
        if not alerts:
            return {
                "fraud_score": 0,
                "risk_level": "low",
                "alert_count": 0,
                "alerts": []
            }
        
        critical_count = sum(1 for a in alerts if a.risk_level == "critical")
        high_count = sum(1 for a in alerts if a.risk_level == "high")
        medium_count = sum(1 for a in alerts if a.risk_level == "medium")
        
        # Score calculation
        score = (critical_count * 30) + (high_count * 15) + (medium_count * 5)
        score = min(100, score)
        
        if critical_count > 0:
            risk_level = "critical"
        elif high_count > 1:
            risk_level = "high"
        elif high_count == 1 or medium_count > 2:
            risk_level = "medium"
        else:
            risk_level = "low"
        
        return {
            "fraud_score": score,
            "risk_level": risk_level,
            "alert_count": len(alerts),
            "critical_alerts": critical_count,
            "high_alerts": high_count,
            "medium_alerts": medium_count,
            "alerts": [
                {
                    "alert_type": a.alert_type,
                    "risk_level": a.risk_level,
                    "message": a.message,
                    "affected_donation_ids": a.affected_donation_ids,
                    "recommendation": a.recommendation,
                }
                for a in alerts
            ]
        }
