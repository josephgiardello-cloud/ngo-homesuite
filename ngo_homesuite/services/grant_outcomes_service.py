from __future__ import annotations

import os
import warnings
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select

from ngo_homesuite.db.utils import audit
from ngo_homesuite.models.core import Grant, GrantOutcomeRecord, GrantOutcomeTemplate, ProgramCase, db


if os.getenv("NGOHS_WARN_DIRECT_GRANT_SERVICE_IMPORTS", "0") == "1":
    warnings.warn(
        "Direct import of grant_outcomes_service is deprecated. Use ngo_homesuite.grants.facade.GrantsFacade instead.",
        DeprecationWarning,
        stacklevel=2,
    )


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _get_grant(grant_id: int, organization_id: int) -> Grant:
    grant = db.session.scalars(
        select(Grant).where(Grant.id == grant_id, Grant.organization_id == organization_id).limit(1)
    ).first()
    if grant is None:
        raise ValueError("grant not found for organization")
    return grant


def define_outcome_template(
    grant_id: int,
    organization_id: int,
    *,
    metric_name: str,
    target_value: float,
    unit: Optional[str] = None,
    baseline_value: Optional[float] = None,
    program_case_type: Optional[str] = None,
    notes: Optional[str] = None,
) -> GrantOutcomeTemplate:
    _get_grant(grant_id, organization_id)
    clean_metric = (metric_name or "").strip()
    if not clean_metric:
        raise ValueError("metric_name is required")
    if float(target_value) <= 0:
        raise ValueError("target_value must be positive")

    existing = db.session.scalars(
        select(GrantOutcomeTemplate).where(
            GrantOutcomeTemplate.grant_id == grant_id,
            GrantOutcomeTemplate.organization_id == organization_id,
            GrantOutcomeTemplate.metric_name == clean_metric,
        ).limit(1)
    ).first()
    if existing is not None:
        raise ValueError("outcome template metric already exists for this grant")

    template = GrantOutcomeTemplate(
        grant_id=grant_id,
        organization_id=organization_id,
        metric_name=clean_metric,
        unit=(unit or "").strip() or None,
        target_value=float(target_value),
        baseline_value=float(baseline_value) if baseline_value is not None else None,
        program_case_type=(program_case_type or "").strip() or None,
        notes=(notes or "").strip() or None,
        is_active=True,
    )
    db.session.add(template)
    db.session.commit()
    audit(
        "grant.outcome.template.create",
        entity_type="grant",
        entity_id=int(grant_id),
        details={
            "organization_id": int(organization_id),
            "template_id": int(template.id),
            "after": {
                "metric_name": template.metric_name,
                "target_value": float(template.target_value),
                "program_case_type": template.program_case_type,
            },
        },
    )
    return template


def record_outcome(
    grant_id: int,
    organization_id: int,
    *,
    template_id: int,
    current_value: float,
    program_case_id: Optional[int] = None,
    note: Optional[str] = None,
    source: str = "manual",
) -> GrantOutcomeRecord:
    _get_grant(grant_id, organization_id)
    if float(current_value) < 0:
        raise ValueError("current_value cannot be negative")

    template = db.session.scalars(
        select(GrantOutcomeTemplate).where(
            GrantOutcomeTemplate.id == template_id,
            GrantOutcomeTemplate.grant_id == grant_id,
            GrantOutcomeTemplate.organization_id == organization_id,
            GrantOutcomeTemplate.is_active == True,
        ).limit(1)
    ).first()
    if template is None:
        raise ValueError("template not found/active for grant")

    if program_case_id is not None:
        case = db.session.scalars(
            select(ProgramCase).where(
                ProgramCase.id == program_case_id,
                ProgramCase.organization_id == organization_id,
                ProgramCase.grant_id == grant_id,
            ).limit(1)
        ).first()
        if case is None:
            raise ValueError("program_case_id must reference a case tied to this grant")

    record = GrantOutcomeRecord(
        grant_id=grant_id,
        template_id=template_id,
        organization_id=organization_id,
        program_case_id=program_case_id,
        current_value=float(current_value),
        note=(note or "").strip() or None,
        source=(source or "manual").strip() or "manual",
        recorded_at=_utcnow_naive(),
    )
    db.session.add(record)
    db.session.commit()
    audit(
        "grant.outcome.record.create",
        entity_type="grant",
        entity_id=int(grant_id),
        details={
            "organization_id": int(organization_id),
            "record_id": int(record.id),
            "template_id": int(template_id),
            "current_value": float(record.current_value),
        },
    )
    return record


def outcome_summary(grant_id: int, organization_id: int) -> dict:
    _get_grant(grant_id, organization_id)
    templates = list(
        db.session.scalars(
            select(GrantOutcomeTemplate)
            .where(
                GrantOutcomeTemplate.grant_id == grant_id,
                GrantOutcomeTemplate.organization_id == organization_id,
                GrantOutcomeTemplate.is_active == True,
            )
            .order_by(GrantOutcomeTemplate.metric_name.asc())
        )
    )

    rows = []
    for template in templates:
        latest = db.session.scalars(
            select(GrantOutcomeRecord)
            .where(
                GrantOutcomeRecord.template_id == template.id,
                GrantOutcomeRecord.organization_id == organization_id,
            )
            .order_by(GrantOutcomeRecord.recorded_at.desc(), GrantOutcomeRecord.id.desc())
            .limit(1)
        ).first()

        current = float(latest.current_value) if latest is not None else (float(template.baseline_value) if template.baseline_value is not None else 0.0)
        target = float(template.target_value)
        variance = round(current - target, 2)
        progress_pct = 0.0 if target <= 0 else round(max(0.0, min(100.0, (current / target) * 100.0)), 2)

        rows.append(
            {
                "template_id": int(template.id),
                "metric_name": template.metric_name,
                "unit": template.unit,
                "baseline_value": float(template.baseline_value) if template.baseline_value is not None else None,
                "target_value": target,
                "current_value": current,
                "variance": variance,
                "progress_percent": progress_pct,
                "last_recorded_at": latest.recorded_at.isoformat() if latest is not None else None,
            }
        )

    return {
        "grant_id": int(grant_id),
        "metric_count": len(rows),
        "metrics": rows,
    }


def grant_variance_report(organization_id: int) -> dict:
    grants = list(
        db.session.scalars(
            select(Grant)
            .where(Grant.organization_id == organization_id)
            .order_by(Grant.created_at.desc())
        )
    )

    by_grant = []
    for grant in grants:
        summary = outcome_summary(int(grant.id), organization_id)
        if summary["metric_count"] == 0:
            continue

        total_target = sum(float(item["target_value"] or 0) for item in summary["metrics"])
        total_current = sum(float(item["current_value"] or 0) for item in summary["metrics"])
        by_grant.append(
            {
                "grant_id": int(grant.id),
                "title": grant.title,
                "metric_count": int(summary["metric_count"]),
                "total_target": round(total_target, 2),
                "total_current": round(total_current, 2),
                "total_variance": round(total_current - total_target, 2),
            }
        )

    return {
        "grant_count": len(by_grant),
        "grants": by_grant,
    }
