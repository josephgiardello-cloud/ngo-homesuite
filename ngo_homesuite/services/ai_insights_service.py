from __future__ import annotations

import re
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.inspection import inspect

from ngo_homesuite.compliance.tony_scoring import TonyScorer
from ngo_homesuite.grants.models import GrantOpportunity, GrantSearchAlert
from ngo_homesuite.models.core import (
    Campaign,
    CampaignEmailBatch,
    CampaignEmailDelivery,
    CollaborationPresence,
    Donation,
    Donor,
    DonorEngagementScore,
    Expense,
    GrantApprovalDecision,
    GrantApprovalRequest,
    GrantOutcomeRecord,
    MembershipRecord,
    ProgramCase,
    RecurringDonationPlan,
    Task,
    User,
    Volunteer,
    VolunteerShift,
    VolunteerTraining,
    db,
)


_POSITIVE_WORDS = {
    "thanks",
    "thank",
    "great",
    "excellent",
    "love",
    "happy",
    "excited",
    "interested",
    "support",
    "supportive",
    "donate",
    "giving",
    "engaged",
    "renew",
    "renewal",
    "yes",
    "yes!",
}
_NEGATIVE_WORDS = {
    "frustrated",
    "angry",
    "annoyed",
    "nightmare",
    "unhappy",
    "disappointed",
    "not sure",
    "won't",
    "wont",
    "stop",
    "cancel",
    "problem",
    "issue",
    "complaint",
    "late",
    "risk",
    "hard",
}


@dataclass
class NaturalLanguageSection:
    title: str
    summary: str
    items: list[dict[str, Any]]


class AIInsightsService:
    @staticmethod
    def natural_language_report(org_id: int, query: str, *, limit: int = 10, project_root: str | None = None) -> dict[str, Any]:
        text = str(query or "").strip()
        normalized = text.lower()
        sections: list[NaturalLanguageSection] = []

        if any(word in normalized for word in ["donor", "retention", "churn", "lapsed", "lifetime value", "ltv"]):
            sections.append(AIInsightsService.predict_donor_retention(org_id, limit=limit))
        if any(word in normalized for word in ["grant", "opportunity", "funding", "match", "apply"]):
            sections.append(AIInsightsService.match_grant_opportunities(org_id, limit=limit))
        if any(word in normalized for word in ["compliance", "audit", "evidence", "release", "rotation", "drill"]):
            sections.append(AIInsightsService.synthesize_compliance_evidence(org_id, limit=limit))
        if any(word in normalized for word in ["volunteer", "shift", "training"]):
            sections.append(AIInsightsService.match_volunteer_shifts(org_id, limit=limit))
        if any(word in normalized for word in ["queue", "priority", "retry", "pending", "failed"]):
            sections.append(AIInsightsService.prioritize_queues(org_id, limit=limit))
        if any(word in normalized for word in ["sentiment", "intent", "message", "notes", "communication"]):
            sections.append(AIInsightsService.analyze_sentiment_and_intent(org_id, limit=limit))
        if any(word in normalized for word in ["campaign", "copy", "subject", "subject line", "goal", "thermometer"]):
            sections.append(AIInsightsService.generate_campaign_content(org_id, query=text, limit=limit))
        if any(word in normalized for word in ["anomaly", "fraud", "guardrail", "unusual", "suspicious"]):
            sections.append(AIInsightsService.detect_financial_anomalies(org_id, limit=limit))
        if any(word in normalized for word in ["release", "deployment", "rollback", "readiness"]):
            sections.append(AIInsightsService.predict_release_readiness(project_root=project_root))

        if not sections:
            sections = [
                AIInsightsService.predict_donor_retention(org_id, limit=limit),
                AIInsightsService.match_grant_opportunities(org_id, limit=limit),
                AIInsightsService.prioritize_queues(org_id, limit=limit),
            ]

        return {
            "query": text,
            "sections": [
                {
                    "title": section.title,
                    "summary": section.summary,
                    "items": section.items,
                }
                for section in sections
            ],
        }

    @staticmethod
    def predict_donor_retention(org_id: int, *, limit: int = 10) -> NaturalLanguageSection:
        scores = list(
            db.session.scalars(
                select(DonorEngagementScore)
                .where(DonorEngagementScore.organization_id == org_id)
                .order_by(DonorEngagementScore.score.asc())
                .limit(limit)
            )
        )
        if not scores:
            donors = list(
                db.session.scalars(
                    select(Donor)
                    .where(Donor.organization_id == org_id)
                    .order_by(Donor.created_at.desc())
                    .limit(limit)
                )
            )
            items = []
            for donor in donors:
                computed = AIInsightsService._donor_ltv_snapshot(org_id, donor)
                items.append(computed)
            return NaturalLanguageSection(
                title="Predictive donor churn and lifetime value",
                summary="Computed from recent donations, engagement signals, membership, tasks, and soft credits.",
                items=items,
            )

        items: list[dict[str, Any]] = []
        for score in scores:
            donor = db.session.get(Donor, score.donor_id)
            if donor is None:
                continue
            snapshot = AIInsightsService._donor_ltv_snapshot(org_id, donor)
            snapshot.update(
                {
                    "engagement_score": round(float(score.score or 0.0), 2),
                    "segment": score.segment,
                    "cultivation_priority": score.cultivation_priority,
                    "explanation": score.explanation,
                }
            )
            items.append(snapshot)

        items.sort(key=lambda row: (row.get("churn_risk", 0.0), row.get("lifetime_value_estimate", 0.0)), reverse=True)
        return NaturalLanguageSection(
            title="Predictive donor churn and lifetime value",
            summary="Donors ranked by churn risk and estimated lifetime value so staff can intervene early.",
            items=items[:limit],
        )

    @staticmethod
    def _donor_ltv_snapshot(org_id: int, donor: Donor) -> dict[str, Any]:
        donations = list(
            db.session.scalars(
                select(Donation)
                .where(Donation.organization_id == org_id, Donation.donor_id == donor.id)
                .order_by(Donation.donation_date.asc())
            )
        )
        today = datetime.now(timezone.utc).date()
        last_gift = donations[-1].donation_date.date() if donations and donations[-1].donation_date else None
        recency_days = (today - last_gift).days if last_gift else 9999
        last_12m_cutoff = datetime.combine(today - timedelta(days=365), datetime.min.time())
        donations_12m = [d for d in donations if d.donation_date and d.donation_date >= last_12m_cutoff]
        lifetime_total = sum(float(d.amount or 0.0) for d in donations)
        avg_gift = lifetime_total / len(donations) if donations else 0.0
        annualized_value = sum(float(d.amount or 0.0) for d in donations_12m)
        membership = db.session.scalars(
            select(MembershipRecord).where(
                MembershipRecord.organization_id == org_id,
                MembershipRecord.donor_id == donor.id,
                MembershipRecord.status == "active",
            ).limit(1)
        ).first()
        open_tasks = db.session.scalar(
            select(func.count(Task.id)).where(
                Task.organization_id == org_id,
                Task.donor_id == donor.id,
                Task.status.in_(["open", "in_progress"]),
            )
        ) or 0
        completed_tasks = db.session.scalar(
            select(func.count(Task.id)).where(
                Task.organization_id == org_id,
                Task.donor_id == donor.id,
                Task.status == "done",
            )
        ) or 0
        soft_credit_total = db.session.scalar(
            select(func.coalesce(func.sum(GrantOutcomeRecord.current_value), 0.0)).where(
                GrantOutcomeRecord.organization_id == org_id,
                GrantOutcomeRecord.program_case_id.isnot(None),
            )
        ) or 0.0
        journey_events = db.session.scalar(
            select(func.count()).select_from(GrantApprovalDecision).where(GrantApprovalDecision.organization_id == org_id)
        ) or 0

        engagement_boost = 0.0
        if membership is not None:
            engagement_boost += 0.20
        engagement_boost += min(open_tasks * 0.04, 0.16)
        engagement_boost += min(completed_tasks * 0.02, 0.10)

        churn_risk = min(1.0, round((recency_days / 365.0) * 0.45 + max(0.0, 0.45 - engagement_boost), 4))
        lifetime_value_estimate = max(
            lifetime_total * (1.0 + (1.0 - churn_risk)),
            annualized_value * 2.0,
            avg_gift * max(1.0, 1.0 + (12.0 - min(recency_days / 30.0, 12.0)) / 12.0),
        )
        priority = "high" if churn_risk >= 0.65 else "medium" if churn_risk >= 0.35 else "low"

        return {
            "donor_id": donor.id,
            "donor_name": donor.name,
            "email": donor.email,
            "last_gift_at": last_gift.isoformat() if last_gift else None,
            "recency_days": recency_days,
            "gifts_12m": len(donations_12m),
            "lifetime_total": round(lifetime_total, 2),
            "annualized_value": round(annualized_value, 2),
            "avg_gift": round(avg_gift, 2),
            "membership_active": bool(membership),
            "open_tasks": int(open_tasks),
            "completed_tasks": int(completed_tasks),
            "soft_credit_total": round(float(soft_credit_total), 2),
            "journey_events": int(journey_events),
            "churn_risk": round(churn_risk, 4),
            "lifetime_value_estimate": round(float(lifetime_value_estimate), 2),
            "next_action": (
                "Schedule stewardship outreach"
                if priority == "high"
                else "Send impact update"
                if priority == "medium"
                else "Keep warm with periodic stewardship"
            ),
            "priority": priority,
        }

    @staticmethod
    def match_grant_opportunities(org_id: int, *, limit: int = 5) -> NaturalLanguageSection:
        snapshot = TonyScorer.extract_financial_snapshot(str(org_id))
        features = TonyScorer.calculate_features(snapshot) if snapshot else {}
        org_health = TonyScorer.calculate_organizational_health(str(org_id))
        opportunities = list(
            db.session.scalars(
                select(GrantOpportunity)
                .where(GrantOpportunity.organization_id == org_id)
                .order_by(GrantOpportunity.created_at.desc())
                .limit(max(limit * 4, 10))
            )
        )

        scored: list[dict[str, Any]] = []
        for opp in opportunities:
            fit = AIInsightsService._grant_fit_score(snapshot, features, org_health, opp)
            scored.append(fit)

        scored.sort(key=lambda row: row["match_score"], reverse=True)
        return NaturalLanguageSection(
            title="Intelligent grant-opportunity matching",
            summary="Opportunities ranked by organizational readiness, requested amount fit, deadline pressure, and program alignment.",
            items=scored[:limit],
        )

    @staticmethod
    def _grant_fit_score(snapshot: dict[str, Any], features: dict[str, Any], org_health: dict[str, Any], opp: GrantOpportunity) -> dict[str, Any]:
        amount_min = float(opp.amount_min or 0.0)
        amount_max = float(opp.amount_max or amount_min or 0.0)
        target_amount = amount_max if amount_max > 0 else amount_min
        liquidity = float(features.get("operating_reserves_days", 0.0) or 0.0)
        program_efficiency = float(features.get("program_expense_ratio", 0.0) or 0.0)
        capacity = float(org_health.get("score", 0.5) or 0.5)
        deadline_days = (opp.deadline - datetime.now(timezone.utc).date()).days if opp.deadline else 180

        amount_fit = 0.0
        if target_amount > 0 and snapshot.get("revenue"):
            ratio = float(snapshot.get("revenue", 0.0)) / max(target_amount, 1.0)
            amount_fit = 1.0 if 0.25 <= ratio <= 8.0 else 0.7 if 0.1 <= ratio < 0.25 or 8.0 < ratio <= 12.0 else 0.35
        program_fit = 1.0 if program_efficiency >= 0.75 else 0.75 if program_efficiency >= 0.6 else 0.45
        readiness_fit = 1.0 if capacity >= 0.7 else 0.75 if capacity >= 0.5 else 0.35
        timing_fit = 1.0 if deadline_days >= 30 else 0.7 if deadline_days >= 14 else 0.35
        liquidity_fit = 1.0 if liquidity >= 90 else 0.75 if liquidity >= 45 else 0.35
        match_score = round((0.30 * readiness_fit + 0.25 * amount_fit + 0.20 * program_fit + 0.15 * timing_fit + 0.10 * liquidity_fit) * 100.0, 2)

        return {
            "grant_opportunity_id": opp.id,
            "title": opp.title,
            "funder_name": opp.funder_name,
            "program_name": opp.program_name,
            "deadline": opp.deadline.isoformat() if opp.deadline else None,
            "amount_min": amount_min,
            "amount_max": amount_max,
            "external_url": opp.external_url,
            "match_score": match_score,
            "match_reasons": [
                "readiness" if readiness_fit >= 0.75 else "capacity_gap",
                "budget_fit" if amount_fit >= 0.7 else "budget_mismatch",
                "timing" if timing_fit >= 0.7 else "deadline_pressure",
            ],
        }

    @staticmethod
    def synthesize_compliance_evidence(org_id: int, *, limit: int = 10) -> NaturalLanguageSection:
        table_names = set(inspect(db.engine).get_table_names())
        events: list[dict[str, Any]] = []
        if "security_audit_events" in table_names:
            from ngo_homesuite.audit.security_events import SecurityAuditEvent

            rows = list(
                db.session.scalars(
                    select(SecurityAuditEvent)
                    .where(SecurityAuditEvent.resource_org_id == org_id)
                    .order_by(SecurityAuditEvent.created_at.desc())
                    .limit(limit * 3)
                )
            )
            for row in rows:
                events.append(
                    {
                        "when": row.created_at.isoformat() if row.created_at else None,
                        "event_type": row.event_type,
                        "action": row.action,
                        "result": row.result,
                        "resource_type": row.resource_type,
                        "resource_id": row.resource_id,
                    }
                )
        else:
            rows = list(
                db.session.query(
                    GrantApprovalRequest.action_type,
                    GrantApprovalRequest.status,
                    GrantApprovalRequest.created_at,
                )
                .filter(GrantApprovalRequest.organization_id == org_id)
                .order_by(GrantApprovalRequest.created_at.desc())
                .limit(limit)
                .all()
            )
            for action_type, status, created_at in rows:
                events.append(
                    {
                        "when": created_at.isoformat() if created_at else None,
                        "event_type": "grant_approval",
                        "action": action_type,
                        "result": status,
                    }
                )

        narrative_lines: list[str] = []
        type_counts = Counter(str(item.get("event_type")) for item in events)
        if type_counts:
            most_common = ", ".join(f"{name} ({count})" for name, count in type_counts.most_common(3))
            narrative_lines.append(f"Recent evidence is dominated by {most_common}.")
        if any(item.get("result") == "denied" for item in events):
            narrative_lines.append("Denied actions were recorded and can be cited as control enforcement evidence.")
        if any("rotation" in str(item.get("action", "")).lower() for item in events):
            narrative_lines.append("Key rotation and related drills appear in the audit trail.")
        if not narrative_lines:
            narrative_lines.append("No recent evidence events were found for this organization.")

        return NaturalLanguageSection(
            title="Automated compliance evidence synthesis",
            summary="Narrative proof points distilled from security and approval events.",
            items=[
                {
                    "summary": line,
                    "events": events[:limit],
                }
                for line in narrative_lines[:limit]
            ],
        )

    @staticmethod
    def match_volunteer_shifts(org_id: int, *, limit: int = 10) -> NaturalLanguageSection:
        shifts = list(
            db.session.scalars(
                select(VolunteerShift)
                .where(VolunteerShift.organization_id == org_id)
                .order_by(VolunteerShift.shift_date.asc())
                .limit(limit * 3)
            )
        )
        items: list[dict[str, Any]] = []
        for shift in shifts:
            volunteer = db.session.get(Volunteer, shift.volunteer_id)
            if volunteer is None:
                continue
            trainings = list(
                db.session.scalars(
                    select(VolunteerTraining)
                    .where(
                        VolunteerTraining.organization_id == org_id,
                        VolunteerTraining.volunteer_id == volunteer.id,
                    )
                )
            )
            completed_trainings = [t for t in trainings if t.status == "completed"]
            required_training_score = min(len(completed_trainings) * 0.25, 1.0)
            attendance_score = 1.0 if volunteer.hours_logged and volunteer.hours_logged >= 10 else 0.7 if volunteer.hours_logged else 0.35
            location_score = 0.85 if shift.location else 0.6
            match_score = round((0.5 * required_training_score + 0.3 * attendance_score + 0.2 * location_score) * 100.0, 2)
            items.append(
                {
                    "shift_id": shift.id,
                    "title": shift.title,
                    "shift_date": shift.shift_date.isoformat() if shift.shift_date else None,
                    "volunteer_id": volunteer.id,
                    "volunteer_name": volunteer.name,
                    "hours_logged": float(volunteer.hours_logged or 0.0),
                    "training_completed": len(completed_trainings),
                    "match_score": match_score,
                    "no_show_risk": round(1.0 - min(match_score / 100.0, 1.0), 4),
                    "recommended_action": (
                        "Confirm assignment and send reminder"
                        if match_score < 60
                        else "Assign directly"
                    ),
                }
            )
        items.sort(key=lambda row: row["match_score"], reverse=True)
        return NaturalLanguageSection(
            title="Smart volunteer shift matching",
            summary="Matches are based on hours logged, training completion, and whether the shift has enough context to plan confidently.",
            items=items[:limit],
        )

    @staticmethod
    def analyze_sentiment_and_intent(org_id: int, *, limit: int = 10) -> NaturalLanguageSection:
        candidate_texts: list[tuple[str, str, str | None]] = []
        donor_notes = (
            db.session.query(Donor.name, Donor.notes)
            .filter(Donor.organization_id == org_id, Donor.notes.isnot(None))
            .limit(limit * 2)
            .all()
        )
        candidate_texts.extend(("donor_note", str(name), str(notes)) for name, notes in donor_notes if notes)

        task_notes = (
            db.session.query(Task.title, Task.description)
            .filter(Task.organization_id == org_id, Task.description.isnot(None))
            .limit(limit * 2)
            .all()
        )
        candidate_texts.extend(("task_note", str(title), str(description)) for title, description in task_notes if description)

        campaign_notes = (
            db.session.query(Campaign.name, Campaign.notes)
            .filter(Campaign.organization_id == org_id, Campaign.notes.isnot(None))
            .limit(limit)
            .all()
        )
        candidate_texts.extend(("campaign_note", str(name), str(notes)) for name, notes in campaign_notes if notes)

        presence_rows = (
            db.session.query(User.username, CollaborationPresence.status_message)
            .join(User, User.id == CollaborationPresence.user_id)
            .filter(CollaborationPresence.organization_id == org_id)
            .limit(limit)
            .all()
        )
        candidate_texts.extend(("presence", str(username), str(status_message)) for username, status_message in presence_rows if status_message)

        scored: list[dict[str, Any]] = []
        for source_type, label, content in candidate_texts:
            if not content:
                continue
            lowered = content.lower()
            pos = sum(1 for word in _POSITIVE_WORDS if word in lowered)
            neg = sum(1 for word in _NEGATIVE_WORDS if word in lowered)
            intent = "positive_engagement" if pos > neg else "risk_or_complaint" if neg > pos else "neutral"
            score = max(-1.0, min(1.0, (pos - neg) / max(1, pos + neg + 1)))
            scored.append(
                {
                    "source_type": source_type,
                    "source_label": label,
                    "excerpt": (content[:220] + "...") if len(content) > 220 else content,
                    "sentiment_score": round(score, 4),
                    "intent": intent,
                }
            )

        scored.sort(key=lambda row: row["sentiment_score"])
        return NaturalLanguageSection(
            title="Sentiment and intent signals",
            summary="Heuristic NLP over donor notes, tasks, campaign notes, and presence messages.",
            items=scored[:limit],
        )

    @staticmethod
    def generate_campaign_content(org_id: int, *, query: str, limit: int = 5) -> NaturalLanguageSection:
        campaign = db.session.scalars(
            select(Campaign).where(Campaign.organization_id == org_id).order_by(Campaign.updated_at.desc()).limit(1)
        ).first()
        donor = db.session.scalars(
            select(Donor).where(Donor.organization_id == org_id).order_by(Donor.created_at.desc()).limit(1)
        ).first()
        ask_amount = None
        m = re.search(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)", query)
        if m:
            try:
                ask_amount = float(m.group(1).replace(",", ""))
            except ValueError:
                ask_amount = None

        if campaign is None:
            title = "AI-generated campaign content"
            body = "No active campaign was found. Use the most recent fundraising theme and ask for a warm, impact-driven appeal."
        else:
            title = f"AI-generated copy for {campaign.name}"
            body = campaign.description or campaign.notes or "Use concise, impact-first messaging that names the campaign goal early."

        items = [
            {
                "subject_line": f"{campaign.name if campaign else 'Your support matters'}: a timely update from our team",
                "copy": body,
                "call_to_action": "Give now" if ask_amount is None else f"Give ${ask_amount:,.0f}" if ask_amount is not None else "Give now",
            },
            {
                "subject_line": "A small update with a big impact",
                "copy": "Lead with one concrete outcome, one urgent need, and one simple next step.",
                "call_to_action": "Learn more",
            },
        ]
        if donor is not None:
            items.append(
                {
                    "subject_line": f"{donor.name}, here's the difference your support makes",
                    "copy": f"Reference the donor by name, connect to current program impact, and invite a next action aligned to their recent giving.",
                    "call_to_action": "Respond to this appeal",
                }
            )
        return NaturalLanguageSection(title=title, summary="Draft copy and optimization hints for campaign teams.", items=items[:limit])

    @staticmethod
    def prioritize_queues(org_id: int, *, limit: int = 10) -> NaturalLanguageSection:
        task_rows = list(
            db.session.scalars(
                select(Task)
                .where(Task.organization_id == org_id, Task.status.in_(["open", "in_progress"]))
                .order_by(Task.priority.desc(), Task.due_date.asc().nulls_last())
                .limit(limit * 2)
            )
        )
        delivery_rows = list(
            db.session.scalars(
                select(CampaignEmailDelivery)
                .where(CampaignEmailDelivery.organization_id == org_id)
                .order_by(CampaignEmailDelivery.created_at.desc())
                .limit(limit * 2)
            )
        )
        recurring_rows = list(
            db.session.scalars(
                select(RecurringDonationPlan)
                .where(RecurringDonationPlan.organization_id == org_id)
                .order_by(RecurringDonationPlan.fail_count.desc(), RecurringDonationPlan.next_charge_date.asc())
                .limit(limit * 2)
            )
        )

        items: list[dict[str, Any]] = []
        for task in task_rows:
            urgency = 0.0
            if task.priority == "urgent":
                urgency += 0.5
            elif task.priority == "high":
                urgency += 0.35
            if task.due_date:
                overdue_days = max((datetime.now(timezone.utc).replace(tzinfo=None) - task.due_date).days, 0)
                urgency += min(overdue_days / 30.0, 0.5)
            items.append(
                {
                    "kind": "task",
                    "id": task.id,
                    "title": task.title,
                    "priority": task.priority,
                    "status": task.status,
                    "score": round(min(1.0, urgency), 4),
                    "why": "Overdue or high-priority staff task",
                }
            )
        for delivery in delivery_rows:
            score = 0.0
            if delivery.delivery_status == "failed":
                score += 0.65
            if delivery.open_count == 0 and delivery.click_count == 0:
                score += 0.15
            items.append(
                {
                    "kind": "campaign_delivery",
                    "id": delivery.id,
                    "title": f"Campaign delivery to {delivery.recipient_email}",
                    "priority": delivery.delivery_status,
                    "status": delivery.delivery_status,
                    "score": round(min(1.0, score), 4),
                    "why": "Failed or unread campaign delivery",
                }
            )
        for plan in recurring_rows:
            score = min(1.0, 0.3 + 0.15 * float(plan.fail_count or 0))
            items.append(
                {
                    "kind": "recurring_donation",
                    "id": plan.id,
                    "title": f"Recurring plan for donor {plan.donor_id}",
                    "priority": plan.status,
                    "status": plan.status,
                    "score": round(score, 4),
                    "why": "Failed recurring donation or near-term charge",
                }
            )

        items.sort(key=lambda row: row["score"], reverse=True)
        return NaturalLanguageSection(
            title="Intelligent queue prioritization",
            summary="Mixed priority view across tasks, campaign deliveries, and recurring donation plans.",
            items=items[:limit],
        )

    @staticmethod
    def detect_financial_anomalies(org_id: int, *, limit: int = 10) -> NaturalLanguageSection:
        donations = list(
            db.session.scalars(
                select(Donation).where(Donation.organization_id == org_id, Donation.amount.isnot(None)).order_by(Donation.donation_date.desc()).limit(200)
            )
        )
        expenses = list(
            db.session.scalars(
                select(Expense).where(Expense.organization_id == org_id, Expense.amount.isnot(None)).order_by(Expense.created_at.desc()).limit(200)
            )
        )

        donation_scores = AIInsightsService._outlier_scores_records(donations, value_getter=lambda d: float(d.amount or 0.0))
        expense_scores = AIInsightsService._outlier_scores_records(expenses, value_getter=lambda e: float(e.amount or 0.0))

        items: list[dict[str, Any]] = []
        for donation, score in donation_scores[:limit]:
            items.append(
                {
                    "kind": "donation",
                    "id": donation.id,
                    "amount": round(float(donation.amount or 0.0), 2),
                    "date": donation.donation_date.isoformat() if donation.donation_date else None,
                    "anomaly_score": round(score, 4),
                    "why": "Donation amount is unusually far from the org's donation distribution",
                }
            )
        for expense, score in expense_scores[:limit]:
            items.append(
                {
                    "kind": "expense",
                    "id": expense.id,
                    "amount": round(float(expense.amount or 0.0), 2),
                    "date": expense.created_at.isoformat() if expense.created_at else None,
                    "anomaly_score": round(score, 4),
                    "why": "Expense amount is unusually far from the org's expense distribution",
                }
            )
        items.sort(key=lambda row: row["anomaly_score"], reverse=True)
        return NaturalLanguageSection(
            title="Anomaly detection in financial guardrails",
            summary="Heuristic outlier detection across donations and expenses.",
            items=items[:limit],
        )

    @staticmethod
    def _outlier_scores_records(records: list[Any], *, value_getter) -> list[tuple[Any, float]]:
        if not records:
            return []
        values = [float(value_getter(record)) for record in records]
        paired = list(values)
        med = statistics.median(paired)
        abs_devs = [abs(value - med) for value in paired]
        mad = statistics.median(abs_devs) or 1.0
        scored: list[tuple[Any, float]] = []
        for index, value in enumerate(paired):
            modified_z = 0.6745 * abs(value - med) / mad
            scored.append((records[index], min(1.0, modified_z / 6.0)))
        scored.sort(key=lambda row: row[1], reverse=True)
        return scored

    @staticmethod
    def predict_release_readiness(*, project_root: str | None) -> NaturalLanguageSection:
        root = Path(project_root or Path(__file__).resolve().parents[2])
        artifacts = root / "artifacts"
        coverage_file = root / "coverage.xml"
        evidence_file = artifacts / "release-evidence-bundle.json"
        current_state_file = artifacts / "current_state.json"

        signals: list[dict[str, Any]] = []
        risk = 0.35
        if evidence_file.exists():
            signals.append({"signal": "release_evidence_bundle", "status": "present"})
            risk -= 0.08
        else:
            signals.append({"signal": "release_evidence_bundle", "status": "missing"})
            risk += 0.12

        if coverage_file.exists():
            try:
                import xml.etree.ElementTree as ET

                root_xml = ET.parse(coverage_file).getroot()
                line_rate = float(root_xml.attrib.get("line-rate", "0.0"))
                branch_rate = float(root_xml.attrib.get("branch-rate", "0.0"))
                signals.append({"signal": "coverage", "line_rate": line_rate, "branch_rate": branch_rate})
                risk -= 0.20 * max(0.0, min(1.0, line_rate))
                risk -= 0.10 * max(0.0, min(1.0, branch_rate))
            except Exception:
                signals.append({"signal": "coverage", "status": "unreadable"})
                risk += 0.05
        else:
            signals.append({"signal": "coverage", "status": "missing"})
            risk += 0.08

        if current_state_file.exists():
            signals.append({"signal": "current_state", "status": "present"})
            risk -= 0.03
        else:
            signals.append({"signal": "current_state", "status": "missing"})
            risk += 0.03

        state = "ready" if risk < 0.25 else "watch" if risk < 0.45 else "at_risk"
        items = [
            {
                "release_risk": round(max(0.0, min(1.0, risk)), 4),
                "state": state,
                "recommended_action": (
                    "Proceed with normal release checks"
                    if state == "ready"
                    else "Run one more targeted verification pass"
                    if state == "watch"
                    else "Delay release and address missing evidence"
                ),
            },
            *signals,
        ]
        return NaturalLanguageSection(
            title="Proactive release-readiness intelligence",
            summary="Repo artifacts and coverage signals are used to estimate release risk.",
            items=items,
        )

    @staticmethod
    def summarize_grant_match_alerts(org_id: int, *, limit: int = 10) -> NaturalLanguageSection:
        alerts = list(
            db.session.scalars(
                select(GrantSearchAlert)
                .where(GrantSearchAlert.organization_id == org_id)
                .order_by(GrantSearchAlert.matched_at.desc())
                .limit(limit)
            )
        )
        items = [
            {
                "alert_id": alert.id,
                "title": alert.title,
                "source": alert.external_source,
                "status": alert.status,
                "matched_at": alert.matched_at.isoformat() if alert.matched_at else None,
            }
            for alert in alerts
        ]
        return NaturalLanguageSection(
            title="Grant search alert intelligence",
            summary="Recently matched external opportunities from saved grant search profiles.",
            items=items,
        )

    @staticmethod
    def summarize_campaign_signal(org_id: int, *, limit: int = 10) -> NaturalLanguageSection:
        batches = list(
            db.session.scalars(
                select(CampaignEmailBatch)
                .where(CampaignEmailBatch.organization_id == org_id)
                .order_by(CampaignEmailBatch.created_at.desc())
                .limit(limit)
            )
        )
        items = [
            {
                "batch_id": batch.id,
                "campaign_id": batch.campaign_id,
                "status": batch.status,
                "sent_count": batch.sent_count,
                "failed_count": batch.failed_count,
                "scheduled_at": batch.scheduled_at.isoformat() if batch.scheduled_at else None,
            }
            for batch in batches
        ]
        return NaturalLanguageSection(
            title="Campaign queue and delivery signals",
            summary="Most recent bulk email batches and delivery outcomes.",
            items=items,
        )

    @staticmethod
    def summarize_program_impact(org_id: int, *, limit: int = 10) -> NaturalLanguageSection:
        cases = list(
            db.session.scalars(
                select(ProgramCase)
                .where(ProgramCase.organization_id == org_id)
                .order_by(ProgramCase.updated_at.desc())
                .limit(limit)
            )
        )
        items = []
        for case in cases:
            items.append(
                {
                    "case_id": case.id,
                    "title": case.title,
                    "status": case.status,
                    "risk_level": case.risk_level,
                    "progress_percent": round(float(case.progress_percent or 0.0), 2),
                    "outcome_metric": case.outcome_metric,
                    "outcome_value": case.outcome_value,
                }
            )
        return NaturalLanguageSection(
            title="Program impact summary",
            summary="Active cases and outcome progress that can be narrated into compliance or board reporting.",
            items=items,
        )
