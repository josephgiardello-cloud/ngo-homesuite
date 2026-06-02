"""Service layer for unified constituent activity timelines.

Provides chronological feeds of all interactions (notes, calls, emails, donations,
tasks, case notes, workflow events) across constituent types (donor, volunteer,
beneficiary) for both profile-scoped and organization-wide views.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import select, text

from ngo_homesuite.models.core import User, db


ActivityType = Literal[
    "interaction",      # donor call/email/meeting/note
    "donation",         # donation recorded
    "pledge",           # pledge created/updated
    "email_delivery",   # campaign email delivery/open/click
    "event_attendance", # event registration/attendance
    "soft_credit",      # influenced donation credit
    "case_note",        # beneficiary case note
    "service_log",      # program service log
    "task",             # workflow task/action
    "workflow_event",   # workflow execution
    "volunteer_hour",   # volunteer time logged
]


@dataclass(frozen=True)
class ActivityFeedItem:
    """Unified activity timeline item across all entity types."""

    activity_id: str          # Unique ID within org scope (type:table_id)
    activity_type: ActivityType
    occurred_at: str          # ISO 8601 datetime (UTC)
    entity_type: str          # "donor" | "beneficiary" | "volunteer" | "grant"
    entity_id: int            # FK to entity
    actor_id: int | None      # User who created/modified
    actor_name: str | None    # User display name
    summary: str              # One-line description for UI
    metadata: dict[str, Any]  # Type-specific fields (channel, status, etc.)

    def to_dict(self) -> dict[str, Any]:
        return {
            "activity_id": self.activity_id,
            "activity_type": self.activity_type,
            "occurred_at": self.occurred_at,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
            "summary": self.summary,
            "metadata": self.metadata,
        }


class ActivityTimelineService:
    """Unified timeline across all constituent activity types."""

    _table_exists_cache: dict[str, bool] = {}

    @staticmethod
    def _activity_numeric_id(activity_id: str) -> int | None:
        try:
            return int(str(activity_id).split(":", 1)[1])
        except Exception:
            return None

    @staticmethod
    def _normalize_text(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().lower()

    @staticmethod
    def _item_matches_query(item: ActivityFeedItem, search_query: str | None) -> bool:
        query = ActivityTimelineService._normalize_text(search_query)
        if not query:
            return True

        haystack_parts = [
            item.summary,
            item.activity_type,
            item.entity_type,
            item.actor_name or "",
        ]
        for key, value in (item.metadata or {}).items():
            haystack_parts.append(str(key))
            haystack_parts.append(str(value))

        haystack = " ".join(ActivityTimelineService._normalize_text(part) for part in haystack_parts)
        return query in haystack

    @staticmethod
    def _table_exists(table_name: str) -> bool:
        key = str(table_name or "").strip().lower()
        if not key:
            return False
        cached = ActivityTimelineService._table_exists_cache.get(key)
        if cached is not None:
            return cached
        row = db.session.execute(
            text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND lower(name) = :name LIMIT 1"),
            {"name": key},
        ).first()
        exists = row is not None
        ActivityTimelineService._table_exists_cache[key] = exists
        return exists

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    @staticmethod
    def _user_name(user_id: int | None) -> str | None:
        """Fetch user display name."""
        if user_id is None:
            return None
        user = db.session.scalars(
            select(User).where(User.id == user_id).limit(1)
        ).first()
        if user is None:
            return None
        if user.first_name or user.last_name:
            return f"{user.first_name} {user.last_name}".strip()
        return user.username

    @staticmethod
    def get_donor_timeline(
        organization_id: int,
        donor_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
        search_query: str | None = None,
    ) -> list[ActivityFeedItem]:
        """Fetch unified timeline for a donor.

        Includes:
        - Donor interactions (calls, emails, meetings, notes)
        - Donations
        - Pledges
        - Related beneficiary connections (if any)
        - Workflow events referencing this donor

        Args:
            organization_id: Org scope
            donor_id: Target donor
            limit: Max items to return
            offset: Pagination offset

        Returns:
            Chronological feed (newest first), limited to offset+limit items
        """
        items: list[ActivityFeedItem] = []

        donor_row = db.session.execute(
            text(
                """
                SELECT id, name, email
                FROM donors
                WHERE id = :donor_id
                  AND organization_id = :org_id
                LIMIT 1
                """
            ),
            {"donor_id": donor_id, "org_id": organization_id},
        ).mappings().first()
        if donor_row is None:
            return []

        donor_name = str(donor_row.get("name") or "").strip()
        donor_email = str(donor_row.get("email") or "").strip().lower()

        # Query 1: Donor interactions
        donor_interactions: list[tuple[Any, ...]] = []
        try:
            donor_interactions = db.session.execute(
                text("""
                    SELECT
                        'interaction:' || di.id AS activity_id,
                        di.occurred_at,
                        di.channel,
                        di.summary,
                        di.next_action,
                        di.follow_up_due,
                        di.completed_at,
                        di.created_by_user_id
                    FROM donor_interactions di
                    JOIN donors d ON d.id = di.donor_id
                    WHERE di.donor_id = :donor_id
                      AND d.organization_id = :org_id
                    ORDER BY di.occurred_at DESC
                """),
                {"donor_id": donor_id, "org_id": organization_id},
            ).fetchall()
        except Exception:
            # Legacy fallback schema uses `interactions` instead of `donor_interactions`.
            try:
                donor_interactions = db.session.execute(
                    text("""
                        SELECT
                            'interaction:' || i.id AS activity_id,
                            COALESCE(i.updated_at, i.created_at) AS occurred_at,
                            COALESCE(i.type, 'other') AS channel,
                            COALESCE(i.subject, i.notes, 'Interaction logged') AS summary,
                            NULL AS next_action,
                            i.due_date AS follow_up_due,
                            i.completed_at,
                            NULL AS created_by_user_id
                        FROM interactions i
                        JOIN donors d ON d.id = i.donor_id
                        WHERE i.donor_id = :donor_id
                          AND d.organization_id = :org_id
                        ORDER BY COALESCE(i.updated_at, i.created_at) DESC
                    """),
                    {"donor_id": donor_id, "org_id": organization_id},
                ).fetchall()
            except Exception:
                donor_interactions = []

        for row in donor_interactions:
            activity_id, occurred_at, channel, summary, next_action, follow_up_due, completed_at, created_by = row
            actor_name = ActivityTimelineService._user_name(created_by)
            summary_text = f"{channel.title()} — {summary}"
            if completed_at:
                summary_text += " ✓"

            items.append(
                ActivityFeedItem(
                    activity_id=activity_id,
                    activity_type="interaction",
                    occurred_at=occurred_at,
                    entity_type="donor",
                    entity_id=donor_id,
                    actor_id=created_by,
                    actor_name=actor_name,
                    summary=summary_text,
                    metadata={
                        "channel": channel,
                        "next_action": next_action,
                        "follow_up_due": follow_up_due,
                        "completed": completed_at is not None,
                    },
                )
            )

        # Query 2: Donations
        donations = db.session.execute(
            text("""
                SELECT
                    'donation:' || don.id AS activity_id,
                    don.donation_date,
                    don.amount,
                    COALESCE(don.fund_id, 0) AS fund_id,
                    COALESCE(don.project_id, 0) AS project_id
                FROM donations don
                WHERE don.donor_id = :donor_id
                  AND don.organization_id = :org_id
                ORDER BY don.donation_date DESC
            """),
            {"donor_id": donor_id, "org_id": organization_id},
        ).fetchall()

        for row in donations:
            activity_id, donation_date, amount, fund_id, project_id = row
            amount_dollars = f"${float(amount or 0):,.2f}"
            summary_text = f"Donation received — {amount_dollars}"

            items.append(
                ActivityFeedItem(
                    activity_id=activity_id,
                    activity_type="donation",
                    occurred_at=donation_date,
                    entity_type="donor",
                    entity_id=donor_id,
                    actor_id=None,
                    actor_name=None,
                    summary=summary_text,
                    metadata={
                        "amount": amount,
                        "amount_formatted": amount_dollars,
                        "fund_id": fund_id if fund_id else None,
                        "project_id": project_id if project_id else None,
                    },
                )
            )

        # Query 2b: Pledges
        try:
            pledge_rows = db.session.execute(
                text(
                    """
                    SELECT
                        'pledge:' || p.id AS activity_id,
                        COALESCE(p.updated_at, p.created_at, p.start_date) AS occurred_at,
                        COALESCE(p.amount, p.amount_cents / 100.0, 0.0) AS amount_value,
                        COALESCE(p.frequency, 'monthly') AS frequency,
                        COALESCE(p.status, CASE WHEN COALESCE(p.active, 1) = 1 THEN 'active' ELSE 'inactive' END) AS pledge_status,
                        p.campaign_id,
                        p.start_date,
                        p.end_date
                    FROM pledges p
                    WHERE p.donor_id = :donor_id
                    ORDER BY COALESCE(p.updated_at, p.created_at, p.start_date) DESC
                    """
                ),
                {"donor_id": donor_id},
            ).fetchall()
            for row in pledge_rows:
                (
                    activity_id,
                    occurred_at,
                    amount_value,
                    frequency,
                    pledge_status,
                    campaign_id,
                    start_date,
                    end_date,
                ) = row
                amount_dollars = f"${ActivityTimelineService._coerce_float(amount_value):,.2f}"
                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="pledge",
                        occurred_at=occurred_at,
                        entity_type="donor",
                        entity_id=donor_id,
                        actor_id=None,
                        actor_name=None,
                        summary=f"Pledge ({pledge_status}) — {amount_dollars} {frequency}",
                        metadata={
                            "amount": ActivityTimelineService._coerce_float(amount_value),
                            "frequency": frequency,
                            "status": pledge_status,
                            "campaign_id": campaign_id,
                            "start_date": start_date,
                            "end_date": end_date,
                        },
                    )
                )
        except Exception:
            pass

        # Query 3: Donor-linked tasks
        try:
            donor_tasks = db.session.execute(
                text("""
                    SELECT
                        'task:' || t.id AS activity_id,
                        COALESCE(t.completed_at, t.due_date, t.created_at) AS occurred_at,
                        t.title,
                        t.status,
                        t.priority,
                        t.due_date,
                        t.completed_at,
                        t.assigned_to_id
                    FROM tasks t
                    WHERE t.organization_id = :org_id
                      AND t.donor_id = :donor_id
                    ORDER BY COALESCE(t.completed_at, t.due_date, t.created_at) DESC
                """),
                {"org_id": organization_id, "donor_id": donor_id},
            ).fetchall()

            for row in donor_tasks:
                (
                    activity_id,
                    occurred_at,
                    title,
                    status,
                    priority,
                    due_date,
                    completed_at,
                    assigned_to_id,
                ) = row
                actor_name = ActivityTimelineService._user_name(assigned_to_id)
                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="task",
                        occurred_at=occurred_at,
                        entity_type="donor",
                        entity_id=donor_id,
                        actor_id=assigned_to_id,
                        actor_name=actor_name,
                        summary=f"Task ({status}) — {title}",
                        metadata={
                            "status": status,
                            "priority": priority,
                            "due_date": due_date,
                            "completed": completed_at is not None,
                        },
                    )
                )
        except Exception:
            pass

        # Query 4: Campaign email touchpoints
        try:
            email_touch_rows = db.session.execute(
                text(
                    """
                    SELECT
                        'email_delivery:' || ced.id AS activity_id,
                        COALESCE(ced.last_clicked_at, ced.last_opened_at, ced.sent_at, ced.created_at) AS occurred_at,
                        ced.delivery_status,
                        ced.recipient_email,
                        ced.open_count,
                        ced.click_count,
                        ced.sent_at,
                        ced.last_opened_at,
                        ced.last_clicked_at,
                        ced.campaign_id
                    FROM campaign_email_deliveries ced
                    WHERE ced.organization_id = :org_id
                      AND ced.donor_id = :donor_id
                    ORDER BY COALESCE(ced.last_clicked_at, ced.last_opened_at, ced.sent_at, ced.created_at) DESC
                    """
                ),
                {"org_id": organization_id, "donor_id": donor_id},
            ).fetchall()

            for row in email_touch_rows:
                (
                    activity_id,
                    occurred_at,
                    delivery_status,
                    recipient_email,
                    open_count,
                    click_count,
                    sent_at,
                    last_opened_at,
                    last_clicked_at,
                    campaign_id,
                ) = row
                summary = f"Campaign email {delivery_status}"
                if ActivityTimelineService._coerce_float(click_count) > 0:
                    summary = "Campaign email clicked"
                elif ActivityTimelineService._coerce_float(open_count) > 0:
                    summary = "Campaign email opened"

                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="email_delivery",
                        occurred_at=occurred_at,
                        entity_type="donor",
                        entity_id=donor_id,
                        actor_id=None,
                        actor_name=None,
                        summary=summary,
                        metadata={
                            "delivery_status": delivery_status,
                            "recipient_email": recipient_email,
                            "open_count": int(open_count or 0),
                            "click_count": int(click_count or 0),
                            "sent_at": sent_at,
                            "last_opened_at": last_opened_at,
                            "last_clicked_at": last_clicked_at,
                            "campaign_id": campaign_id,
                        },
                    )
                )
        except Exception:
            pass

        # Query 5: Event registrations / attendance
        try:
            event_rows = db.session.execute(
                text(
                    """
                    SELECT
                        'event_attendance:' || r.id AS activity_id,
                        COALESCE(r.attended_at, r.registered_at, r.created_at, e.start_datetime, e.date) AS occurred_at,
                        COALESCE(e.title, e.name, 'Event') AS event_title,
                        COALESCE(r.status, CASE WHEN r.attended_at IS NOT NULL THEN 'attended' ELSE 'registered' END) AS registration_status,
                        COALESCE(r.amount_paid, 0.0) AS amount_paid,
                        r.event_id
                    FROM registrations r
                    LEFT JOIN events e ON e.id = r.event_id
                    WHERE r.donor_id = :donor_id
                    ORDER BY COALESCE(r.attended_at, r.registered_at, r.created_at, e.start_datetime, e.date) DESC
                    """
                ),
                {"donor_id": donor_id},
            ).fetchall()

            for row in event_rows:
                activity_id, occurred_at, event_title, registration_status, amount_paid, event_id = row
                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="event_attendance",
                        occurred_at=occurred_at,
                        entity_type="donor",
                        entity_id=donor_id,
                        actor_id=None,
                        actor_name=None,
                        summary=f"Event {registration_status} — {event_title}",
                        metadata={
                            "event_id": event_id,
                            "event_title": event_title,
                            "status": registration_status,
                            "amount_paid": ActivityTimelineService._coerce_float(amount_paid),
                        },
                    )
                )
        except Exception:
            pass

        # Query 6: Volunteer hour touchpoints (identity match on donor email/name)
        try:
            if donor_email or donor_name:
                volunteer_rows = db.session.execute(
                    text(
                        """
                        SELECT
                            'volunteer_shift:' || vs.id AS activity_id,
                            COALESCE(vs.shift_date, vs.created_at) AS occurred_at,
                            COALESCE(vs.title, 'Volunteer shift') AS shift_title,
                            COALESCE(vs.hours, 0.0) AS shift_hours,
                            COALESCE(vs.status, 'scheduled') AS shift_status,
                            v.id AS volunteer_id,
                            v.name AS volunteer_name
                        FROM volunteer_shifts vs
                        JOIN volunteers v ON v.id = vs.volunteer_id
                        WHERE vs.organization_id = :org_id
                          AND (
                            (:donor_email <> '' AND lower(trim(COALESCE(v.email, ''))) = :donor_email)
                            OR (:donor_name <> '' AND lower(trim(COALESCE(v.name, ''))) = lower(trim(:donor_name)))
                          )
                        ORDER BY COALESCE(vs.shift_date, vs.created_at) DESC
                        """
                    ),
                    {
                        "org_id": organization_id,
                        "donor_email": donor_email,
                        "donor_name": donor_name,
                    },
                ).fetchall()

                for row in volunteer_rows:
                    (
                        activity_id,
                        occurred_at,
                        shift_title,
                        shift_hours,
                        shift_status,
                        volunteer_id,
                        volunteer_name,
                    ) = row
                    items.append(
                        ActivityFeedItem(
                            activity_id=activity_id,
                            activity_type="volunteer_hour",
                            occurred_at=occurred_at,
                            entity_type="donor",
                            entity_id=donor_id,
                            actor_id=None,
                            actor_name=None,
                            summary=f"Volunteer shift ({shift_status}) — {shift_title}",
                            metadata={
                                "volunteer_id": volunteer_id,
                                "volunteer_name": volunteer_name,
                                "hours": ActivityTimelineService._coerce_float(shift_hours),
                                "status": shift_status,
                            },
                        )
                    )
        except Exception:
            pass

        # Query 7: Explicit soft-credit attribution records
        try:
            soft_credit_rows = db.session.execute(
                text(
                    """
                    SELECT
                        'soft_credit:' || sc.id AS activity_id,
                        COALESCE(sc.created_at, don.donation_date) AS occurred_at,
                        sc.role,
                        sc.credited_amount,
                        sc.credit_weight,
                        sc.rationale,
                        sc.donation_id,
                        don.donor_id AS hard_credit_donor_id,
                        COALESCE(hd.name, don.donor_name, 'Unknown donor') AS hard_credit_donor_name,
                        don.amount AS donation_amount,
                        don.currency AS donation_currency
                    FROM donor_soft_credits sc
                    JOIN donations don ON don.id = sc.donation_id
                    LEFT JOIN donors hd ON hd.id = don.donor_id
                    WHERE sc.organization_id = :org_id
                      AND sc.donor_id = :donor_id
                    ORDER BY COALESCE(sc.created_at, don.donation_date) DESC
                    """
                ),
                {"org_id": organization_id, "donor_id": donor_id},
            ).fetchall()

            for row in soft_credit_rows:
                (
                    activity_id,
                    occurred_at,
                    role,
                    credited_amount,
                    credit_weight,
                    rationale,
                    donation_id,
                    hard_credit_donor_id,
                    hard_credit_donor_name,
                    donation_amount,
                    donation_currency,
                ) = row
                amount_value = ActivityTimelineService._coerce_float(credited_amount)
                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="soft_credit",
                        occurred_at=occurred_at,
                        entity_type="donor",
                        entity_id=donor_id,
                        actor_id=None,
                        actor_name=None,
                        summary=(
                            f"Soft credit ({role}) — influenced {donation_currency} {amount_value:,.2f} "
                            f"for {hard_credit_donor_name}"
                        ),
                        metadata={
                            "donation_id": donation_id,
                            "role": role,
                            "credited_amount": amount_value,
                            "credit_weight": ActivityTimelineService._coerce_float(credit_weight, 1.0),
                            "hard_credit_donor_id": hard_credit_donor_id,
                            "hard_credit_donor_name": hard_credit_donor_name,
                            "donation_amount": ActivityTimelineService._coerce_float(donation_amount),
                            "donation_currency": donation_currency,
                            "rationale": rationale,
                        },
                    )
                )
        except Exception:
            pass

        # Query 8: Legacy soft-credit string fallback
        try:
            if donor_name:
                legacy_soft_credit_rows = db.session.execute(
                    text(
                        """
                        SELECT
                            'soft_credit_legacy:' || don.id AS activity_id,
                            COALESCE(don.donation_date, don.created_at) AS occurred_at,
                            don.id AS donation_id,
                            COALESCE(don.amount, 0.0) AS donation_amount,
                            don.currency,
                            COALESCE(don.donor_name, 'Unknown donor') AS hard_credit_donor_name
                        FROM donations don
                        WHERE don.organization_id = :org_id
                          AND lower(trim(COALESCE(don.soft_credit_name, ''))) = lower(trim(:donor_name))
                        ORDER BY COALESCE(don.donation_date, don.created_at) DESC
                        """
                    ),
                    {"org_id": organization_id, "donor_name": donor_name},
                ).fetchall()

                for row in legacy_soft_credit_rows:
                    activity_id, occurred_at, donation_id, donation_amount, currency, hard_credit_donor_name = row
                    amount_value = ActivityTimelineService._coerce_float(donation_amount)
                    items.append(
                        ActivityFeedItem(
                            activity_id=activity_id,
                            activity_type="soft_credit",
                            occurred_at=occurred_at,
                            entity_type="donor",
                            entity_id=donor_id,
                            actor_id=None,
                            actor_name=None,
                            summary=(
                                f"Soft credit (legacy) — influenced {currency} {amount_value:,.2f} "
                                f"for {hard_credit_donor_name}"
                            ),
                            metadata={
                                "donation_id": donation_id,
                                "role": "influencer",
                                "credited_amount": amount_value,
                                "legacy_source": "donations.soft_credit_name",
                                "hard_credit_donor_name": hard_credit_donor_name,
                            },
                        )
                    )
        except Exception:
            pass

        # Query 9: Workflow events referencing this donor
        try:
            workflow_rows = db.session.execute(
                text(
                    """
                    SELECT
                        'workflow_event:' || event_id AS activity_id,
                        occurred_at,
                        event_type,
                        actor_id,
                        aggregate_type,
                        aggregate_id
                    FROM workflow_events_v2
                    WHERE org_id = :org_id
                      AND (
                        payload_json LIKE :needle_a
                        OR payload_json LIKE :needle_b
                      )
                    ORDER BY occurred_at DESC
                    """
                ),
                {
                    "org_id": str(organization_id),
                    "needle_a": f'%"donor_id": {int(donor_id)}%',
                    "needle_b": f'%"donor_id":"{int(donor_id)}"%',
                },
            ).fetchall()

            for row in workflow_rows:
                activity_id, occurred_at, event_type, actor_id, aggregate_type, aggregate_id = row
                actor_name = ActivityTimelineService._user_name(int(actor_id)) if actor_id else None
                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="workflow_event",
                        occurred_at=occurred_at,
                        entity_type="donor",
                        entity_id=donor_id,
                        actor_id=int(actor_id) if actor_id else None,
                        actor_name=actor_name,
                        summary=f"Workflow event — {event_type}",
                        metadata={
                            "event_type": event_type,
                            "aggregate_type": aggregate_type,
                            "aggregate_id": aggregate_id,
                        },
                    )
                )
        except Exception:
            pass

        # Sort by occurred_at descending (newest first)
        items.sort(key=lambda x: str(x.occurred_at or ""), reverse=True)

        # Optional lightweight search for profile timelines
        if search_query:
            items = [item for item in items if ActivityTimelineService._item_matches_query(item, search_query)]

        # Apply pagination
        paginated = items[offset : offset + limit]

        return paginated

    @staticmethod
    def get_donor_journey(
        organization_id: int,
        donor_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
        search_query: str | None = None,
    ) -> dict[str, Any] | None:
        """Return a 360-degree donor journey view with summary metrics and timeline."""
        donor = db.session.execute(
            text(
                """
                SELECT
                    id,
                    name,
                    email,
                    phone,
                    donor_type,
                    status,
                    created_at
                FROM donors
                WHERE organization_id = :org_id
                  AND id = :donor_id
                LIMIT 1
                """
            ),
            {"org_id": organization_id, "donor_id": donor_id},
        ).mappings().first()
        if donor is None:
            return None

        donation_stats = db.session.execute(
            text(
                """
                SELECT
                    COALESCE(COUNT(*), 0) AS donation_count,
                    COALESCE(SUM(amount), 0.0) AS donation_total,
                    MIN(donation_date) AS first_gift_at,
                    MAX(donation_date) AS last_gift_at
                FROM donations
                WHERE organization_id = :org_id
                  AND donor_id = :donor_id
                """
            ),
            {"org_id": organization_id, "donor_id": donor_id},
        ).mappings().first() or {}

        soft_credit_stats = {"soft_credit_count": 0, "soft_credit_total": 0.0}
        try:
            row = db.session.execute(
                text(
                    """
                    SELECT
                        COALESCE(COUNT(*), 0) AS soft_credit_count,
                        COALESCE(SUM(credited_amount), 0.0) AS soft_credit_total
                    FROM donor_soft_credits
                    WHERE organization_id = :org_id
                      AND donor_id = :donor_id
                    """
                ),
                {"org_id": organization_id, "donor_id": donor_id},
            ).mappings().first()
            if row:
                soft_credit_stats = {
                    "soft_credit_count": int(row.get("soft_credit_count") or 0),
                    "soft_credit_total": ActivityTimelineService._coerce_float(row.get("soft_credit_total")),
                }
        except Exception:
            pass

        timeline = ActivityTimelineService.get_donor_timeline(
            organization_id,
            donor_id,
            limit=limit,
            offset=offset,
            search_query=search_query,
        )
        timeline_dicts = [item.to_dict() for item in timeline]

        journey = {
            "donor": {
                "id": int(donor.get("id") or 0),
                "name": donor.get("name"),
                "email": donor.get("email"),
                "phone": donor.get("phone"),
                "donor_type": donor.get("donor_type"),
                "status": donor.get("status"),
                "created_at": donor.get("created_at"),
            },
            "summary": {
                "donation_count": int(donation_stats.get("donation_count") or 0),
                "donation_total": ActivityTimelineService._coerce_float(donation_stats.get("donation_total")),
                "first_gift_at": donation_stats.get("first_gift_at"),
                "last_gift_at": donation_stats.get("last_gift_at"),
                "soft_credit_count": int(soft_credit_stats.get("soft_credit_count") or 0),
                "soft_credit_total": ActivityTimelineService._coerce_float(soft_credit_stats.get("soft_credit_total")),
                "touchpoints_returned": len(timeline_dicts),
                "last_engaged_at": timeline_dicts[0]["occurred_at"] if timeline_dicts else None,
            },
            "timeline": timeline_dicts,
            "pagination": {
                "limit": int(limit),
                "offset": int(offset),
                "returned": len(timeline_dicts),
                "query": search_query,
            },
        }
        return journey

    @staticmethod
    def get_beneficiary_timeline(
        organization_id: int,
        beneficiary_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
        search_query: str | None = None,
    ) -> list[ActivityFeedItem]:
        """Fetch unified timeline for a beneficiary.

        Includes:
        - Case notes / service logs
        - Case status changes
        - Program appointments
        - Related donor interactions (if linked)

        Args:
            organization_id: Org scope
            beneficiary_id: Target beneficiary
            limit: Max items to return
            offset: Pagination offset

        Returns:
            Chronological feed (newest first), limited to offset+limit items
        """
        items: list[ActivityFeedItem] = []

        # Query 1: Structured beneficiary service logs
        try:
            service_logs = db.session.execute(
                text("""
                    SELECT
                        'service_log:' || bsl.id AS activity_id,
                        bsl.service_date,
                        bsl.service_type,
                        bsl.outcome_note,
                        bsl.staff_user_id
                    FROM beneficiary_service_logs bsl
                    WHERE bsl.organization_id = :org_id
                      AND bsl.beneficiary_id = :beneficiary_id
                    ORDER BY bsl.service_date DESC
                """),
                {"beneficiary_id": beneficiary_id, "org_id": organization_id},
            ).fetchall()

            for row in service_logs:
                activity_id, service_date, service_type, outcome_note, staff_user_id = row
                actor_name = ActivityTimelineService._user_name(staff_user_id)
                detail = outcome_note or "Service log entry"

                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="service_log",
                        occurred_at=service_date,
                        entity_type="beneficiary",
                        entity_id=beneficiary_id,
                        actor_id=staff_user_id,
                        actor_name=actor_name,
                        summary=f"{service_type} — {detail}",
                        metadata={"service_type": service_type},
                    )
                )
        except Exception:
            pass

        # Query 2: Case activity stream (notes, status changes, calls, emails)
        try:
            case_events = db.session.execute(
                text("""
                    SELECT
                        'case_activity:' || ca.id AS activity_id,
                        ca.created_at,
                        ca.activity_type,
                        COALESCE(ca.content, ''),
                        ca.previous_status,
                        ca.new_status,
                        ca.actor_id
                    FROM case_activities ca
                    JOIN program_cases pc ON pc.id = ca.case_id
                    WHERE pc.organization_id = :org_id
                      AND pc.beneficiary_id = :beneficiary_id
                    ORDER BY ca.created_at DESC
                """),
                {"beneficiary_id": beneficiary_id, "org_id": organization_id},
            ).fetchall()

            for row in case_events:
                activity_id, created_at, activity_type, content, previous_status, new_status, actor_id = row
                actor_name = ActivityTimelineService._user_name(actor_id)

                summary = content or f"Case activity: {activity_type}"
                if activity_type == "status_change":
                    summary = f"Case status changed: {previous_status or '-'} -> {new_status or '-'}"

                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="case_note",
                        occurred_at=created_at,
                        entity_type="beneficiary",
                        entity_id=beneficiary_id,
                        actor_id=actor_id,
                        actor_name=actor_name,
                        summary=summary,
                        metadata={
                            "case_activity_type": activity_type,
                            "previous_status": previous_status,
                            "new_status": new_status,
                        },
                    )
                )
        except Exception:
            pass

        # Query 3: Program case tasks linked to beneficiary
        try:
            case_tasks = db.session.execute(
                text("""
                    SELECT
                        'program_case_task:' || pct.id AS activity_id,
                        COALESCE(pct.completed_at, pct.due_date, pct.created_at) AS occurred_at,
                        pct.title,
                        pct.status,
                        pct.priority,
                        pct.due_date,
                        pct.assigned_to_user_id
                    FROM program_case_tasks pct
                    JOIN program_cases pc ON pc.id = pct.case_id
                    WHERE pct.organization_id = :org_id
                      AND pc.beneficiary_id = :beneficiary_id
                    ORDER BY COALESCE(pct.completed_at, pct.due_date, pct.created_at) DESC
                """),
                {"org_id": organization_id, "beneficiary_id": beneficiary_id},
            ).fetchall()

            for row in case_tasks:
                activity_id, occurred_at, title, status, priority, due_date, assigned_to_user_id = row
                actor_name = ActivityTimelineService._user_name(assigned_to_user_id)

                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="task",
                        occurred_at=occurred_at,
                        entity_type="beneficiary",
                        entity_id=beneficiary_id,
                        actor_id=assigned_to_user_id,
                        actor_name=actor_name,
                        summary=f"Case task ({status}) — {title}",
                        metadata={
                            "status": status,
                            "priority": priority,
                            "due_date": due_date,
                        },
                    )
                )
        except Exception:
            pass

        # Sort by occurred_at descending (newest first)
        items.sort(key=lambda x: x.occurred_at, reverse=True)

        if search_query:
            items = [item for item in items if ActivityTimelineService._item_matches_query(item, search_query)]

        # Apply pagination
        paginated = items[offset : offset + limit]

        return paginated

    @staticmethod
    def get_organization_activity(
        organization_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        entity_type_filter: str | None = None,
        activity_type_filter: str | None = None,
        search_query: str | None = None,
    ) -> list[ActivityFeedItem]:
        """Fetch organization-wide recent activity feed.

        Includes all interactions, donations, and key events across all
        constituents. Used for dashboard/activity feeds.

        Args:
            organization_id: Org scope
            limit: Max items to return
            offset: Pagination offset
            entity_type_filter: Optional filter to only show "donor", "beneficiary", etc.

        Returns:
            Chronological feed (newest first)
        """
        items: list[ActivityFeedItem] = []

        # Query 1: Recent interactions
        recent_interactions: list[tuple[Any, ...]] = []
        try:
            recent_interactions = db.session.execute(
                text("""
                    SELECT
                        'interaction:' || di.id AS activity_id,
                        di.occurred_at,
                        di.donor_id,
                        di.channel,
                        di.summary,
                        di.created_by_user_id,
                        d.name
                    FROM donor_interactions di
                    JOIN donors d ON d.id = di.donor_id
                    WHERE d.organization_id = :org_id
                    ORDER BY di.occurred_at DESC
                    LIMIT :limit
                """),
                {"org_id": organization_id, "limit": limit},
            ).fetchall()
        except Exception:
            try:
                recent_interactions = db.session.execute(
                    text("""
                        SELECT
                            'interaction:' || i.id AS activity_id,
                            COALESCE(i.updated_at, i.created_at) AS occurred_at,
                            i.donor_id,
                            COALESCE(i.type, 'other') AS channel,
                            COALESCE(i.subject, i.notes, 'Interaction logged') AS summary,
                            NULL AS created_by_user_id,
                            d.name
                        FROM interactions i
                        JOIN donors d ON d.id = i.donor_id
                        WHERE d.organization_id = :org_id
                        ORDER BY COALESCE(i.updated_at, i.created_at) DESC
                        LIMIT :limit
                    """),
                    {"org_id": organization_id, "limit": limit},
                ).fetchall()
            except Exception:
                recent_interactions = []

        for row in recent_interactions:
            (
                activity_id,
                occurred_at,
                donor_id,
                channel,
                summary,
                created_by,
                donor_name,
            ) = row
            if entity_type_filter and entity_type_filter != "donor":
                continue

            actor_name = ActivityTimelineService._user_name(created_by)
            summary_text = f"{donor_name}: {channel.title()} — {summary}"

            items.append(
                ActivityFeedItem(
                    activity_id=activity_id,
                    activity_type="interaction",
                    occurred_at=occurred_at,
                    entity_type="donor",
                    entity_id=donor_id,
                    actor_id=created_by,
                    actor_name=actor_name,
                    summary=summary_text,
                    metadata={
                        "channel": channel,
                        "donor_name": donor_name,
                        "interaction_id": ActivityTimelineService._activity_numeric_id(activity_id),
                    },
                )
            )

        # Query 1b: Recent donor-linked tasks
        try:
            recent_tasks = db.session.execute(
                text("""
                    SELECT
                        'task:' || t.id AS activity_id,
                        COALESCE(t.completed_at, t.due_date, t.created_at) AS occurred_at,
                        t.donor_id,
                        t.title,
                        t.status,
                        t.priority,
                        t.due_date,
                        t.completed_at,
                        t.assigned_to_id,
                        d.name
                    FROM tasks t
                    LEFT JOIN donors d ON d.id = t.donor_id
                    WHERE t.organization_id = :org_id
                    ORDER BY COALESCE(t.completed_at, t.due_date, t.created_at) DESC
                    LIMIT :limit
                """),
                {"org_id": organization_id, "limit": limit},
            ).fetchall()

            for row in recent_tasks:
                (
                    activity_id,
                    occurred_at,
                    donor_id,
                    title,
                    status,
                    priority,
                    due_date,
                    completed_at,
                    assigned_to_id,
                    donor_name,
                ) = row

                entity_type = "donor" if donor_id else "organization"
                entity_id = int(donor_id or 0)
                if entity_type_filter and entity_type_filter != entity_type:
                    continue

                actor_name = ActivityTimelineService._user_name(assigned_to_id)
                lead = donor_name if donor_name else "Org"
                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="task",
                        occurred_at=occurred_at,
                        entity_type=entity_type,
                        entity_id=entity_id,
                        actor_id=assigned_to_id,
                        actor_name=actor_name,
                        summary=f"{lead}: Task ({status}) — {title}",
                        metadata={
                            "task_id": ActivityTimelineService._activity_numeric_id(activity_id),
                            "donor_name": donor_name,
                            "status": status,
                            "priority": priority,
                            "due_date": due_date,
                            "completed": completed_at is not None,
                        },
                    )
                )
        except Exception:
            pass

        # Query 2: Recent donations
        recent_donations = db.session.execute(
            text("""
                SELECT
                    'donation:' || don.id AS activity_id,
                    don.donation_date,
                    don.donor_id,
                    don.amount,
                    d.name
                FROM donations don
                JOIN donors d ON d.id = don.donor_id
                WHERE d.organization_id = :org_id
                ORDER BY don.donation_date DESC
                LIMIT :limit
            """),
            {"org_id": organization_id, "limit": limit},
        ).fetchall()

        for row in recent_donations:
            activity_id, donation_date, donor_id, amount, donor_name = row
            if entity_type_filter and entity_type_filter != "donor":
                continue

            amount_dollars = f"${float(amount or 0):,.2f}"
            summary_text = f"{donor_name}: Donation received — {amount_dollars}"

            items.append(
                ActivityFeedItem(
                    activity_id=activity_id,
                    activity_type="donation",
                    occurred_at=donation_date,
                    entity_type="donor",
                    entity_id=donor_id,
                    actor_id=None,
                    actor_name=None,
                    summary=summary_text,
                    metadata={
                        "donor_name": donor_name,
                        "donation_id": ActivityTimelineService._activity_numeric_id(activity_id),
                        "amount": amount,
                        "amount_formatted": amount_dollars,
                    },
                )
            )

        # Query 3: Recent case activities tied to beneficiaries
        try:
            recent_case_activity = db.session.execute(
                text("""
                    SELECT
                        'case_activity:' || ca.id AS activity_id,
                        ca.created_at,
                        pc.beneficiary_id,
                        b.first_name,
                        b.last_name,
                        ca.activity_type,
                        COALESCE(ca.content, ''),
                        ca.actor_id
                    FROM case_activities ca
                    JOIN program_cases pc ON pc.id = ca.case_id
                    LEFT JOIN beneficiaries b ON b.id = pc.beneficiary_id
                    WHERE ca.organization_id = :org_id
                    ORDER BY ca.created_at DESC
                    LIMIT :limit
                """),
                {"org_id": organization_id, "limit": limit},
            ).fetchall()

            for row in recent_case_activity:
                activity_id, created_at, beneficiary_id, first_name, last_name, case_activity_type, content, actor_id = row
                if entity_type_filter and entity_type_filter != "beneficiary":
                    continue
                beneficiary_name = f"{(first_name or '').strip()} {(last_name or '').strip()}".strip() or "Beneficiary"
                actor_name = ActivityTimelineService._user_name(actor_id)
                summary_text = content or f"{beneficiary_name}: Case activity ({case_activity_type})"
                items.append(
                    ActivityFeedItem(
                        activity_id=activity_id,
                        activity_type="case_note",
                        occurred_at=created_at,
                        entity_type="beneficiary",
                        entity_id=int(beneficiary_id or 0),
                        actor_id=actor_id,
                        actor_name=actor_name,
                        summary=summary_text,
                        metadata={
                            "beneficiary_name": beneficiary_name,
                            "case_activity_id": ActivityTimelineService._activity_numeric_id(activity_id),
                            "case_activity_type": case_activity_type,
                        },
                    )
                )
        except Exception:
            pass

        if activity_type_filter:
            items = [item for item in items if item.activity_type == activity_type_filter]

        if search_query:
            items = [item for item in items if ActivityTimelineService._item_matches_query(item, search_query)]

        # Sort by occurred_at descending (newest first) and apply pagination
        items.sort(key=lambda x: x.occurred_at, reverse=True)
        paginated = items[offset : offset + limit]

        return paginated
