"""Volunteer scheduling and training service layer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select
from werkzeug.exceptions import NotFound

from ngo_homesuite.models.core import (
    TrainingCourse,
    Volunteer,
    VolunteerShift,
    VolunteerTraining,
    db,
)


# ---------------------------------------------------------------------------
# Volunteer CRUD
# ---------------------------------------------------------------------------


def _get_volunteer_or_404(volunteer_id: int, organization_id: int) -> Volunteer:
    volunteer = db.session.scalars(
        select(Volunteer).where(
            Volunteer.id == volunteer_id,
            Volunteer.organization_id == organization_id,
        ).limit(1)
    ).first()
    if volunteer is None:
        raise NotFound()
    return volunteer


def _get_shift_or_404(shift_id: int, organization_id: int) -> VolunteerShift:
    shift = db.session.scalars(
        select(VolunteerShift).where(
            VolunteerShift.id == shift_id,
            VolunteerShift.organization_id == organization_id,
        ).limit(1)
    ).first()
    if shift is None:
        raise NotFound()
    return shift


def _get_course_or_404(course_id: int, organization_id: int) -> TrainingCourse:
    course = db.session.scalars(
        select(TrainingCourse).where(
            TrainingCourse.id == course_id,
            TrainingCourse.organization_id == organization_id,
        ).limit(1)
    ).first()
    if course is None:
        raise NotFound()
    return course


def _get_training_or_404(training_id: int, organization_id: int) -> VolunteerTraining:
    training = db.session.scalars(
        select(VolunteerTraining).where(
            VolunteerTraining.id == training_id,
            VolunteerTraining.organization_id == organization_id,
        ).limit(1)
    ).first()
    if training is None:
        raise NotFound()
    return training

def get_volunteer(volunteer_id: int, organization_id: int) -> Optional[Volunteer]:
    return db.session.scalars(
        select(Volunteer).where(
            Volunteer.id == volunteer_id,
            Volunteer.organization_id == organization_id,
        ).limit(1)
    ).first()


def list_volunteers(
    organization_id: int,
    *,
    status: Optional[str] = None,
    search_query: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[Volunteer]:
    stmt = select(Volunteer).where(Volunteer.organization_id == organization_id)
    if status:
        stmt = stmt.where(Volunteer.status == status)
    if search_query:
        like = f"%{str(search_query).strip()}%"
        stmt = stmt.where(
            (Volunteer.name.ilike(like))
            | (Volunteer.email.ilike(like))
            | (Volunteer.phone.ilike(like))
        )
    stmt = stmt.order_by(Volunteer.name.asc())
    if limit is not None:
        stmt = stmt.limit(max(1, min(int(limit), 200))).offset(max(0, int(offset)))
    return list(db.session.scalars(stmt))


def list_recent_volunteers(organization_id: int, *, limit: int = 8) -> List[Volunteer]:
    stmt = (
        select(Volunteer)
        .where(Volunteer.organization_id == organization_id)
        .order_by(Volunteer.created_at.desc())
        .limit(limit)
    )
    return list(db.session.scalars(stmt))


def create_volunteer(
    organization_id: int,
    name: str,
    *,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    status: str = 'active',
) -> Volunteer:
    clean_name = (name or '').strip()
    if not clean_name:
        raise ValueError('Volunteer name is required')

    volunteer = Volunteer(
        organization_id=organization_id,
        name=clean_name,
        email=(email or '').strip() or None,
        phone=(phone or '').strip() or None,
        status=(status or 'active').strip() or 'active',
        hours_logged=0.0,
    )
    db.session.add(volunteer)
    db.session.commit()
    return volunteer


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------

def create_shift(
    organization_id: int,
    volunteer_id: int,
    title: str,
    shift_date: Any,           # date or ISO string
    *,
    project_id: Optional[int] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    hours: Optional[float] = None,
    location: Optional[str] = None,
    notes: Optional[str] = None,
) -> VolunteerShift:
    """Create a volunteer shift. `shift_date` may be a date object or YYYY-MM-DD string."""
    from datetime import date as _date
    if isinstance(shift_date, str):
        shift_date = _date.fromisoformat(shift_date)

    # Guard cross-tenant
    vol = _get_volunteer_or_404(volunteer_id, organization_id)

    shift = VolunteerShift(
        organization_id=organization_id,
        volunteer_id=vol.id,
        project_id=project_id,
        title=title,
        shift_date=shift_date,
        start_time=start_time,
        end_time=end_time,
        hours=hours,
        location=location,
        status='scheduled',
        notes=notes,
    )
    db.session.add(shift)
    db.session.commit()
    return shift


def list_shifts(
    organization_id: int,
    *,
    volunteer_id: Optional[int] = None,
    project_id: Optional[int] = None,
    status: Optional[str] = None,
) -> List[VolunteerShift]:
    stmt = select(VolunteerShift).where(VolunteerShift.organization_id == organization_id)
    if volunteer_id:
        stmt = stmt.where(VolunteerShift.volunteer_id == volunteer_id)
    if project_id:
        stmt = stmt.where(VolunteerShift.project_id == project_id)
    if status:
        stmt = stmt.where(VolunteerShift.status == status)
    stmt = stmt.order_by(VolunteerShift.shift_date.asc())
    return list(db.session.scalars(stmt))


def get_shift(shift_id: int, organization_id: int) -> Optional[VolunteerShift]:
    shift = db.session.get(VolunteerShift, shift_id)
    if shift is None or shift.organization_id != organization_id:
        return None
    return shift


def update_shift(
    shift_id: int,
    organization_id: int,
    **fields: Any,
) -> VolunteerShift:
    shift = _get_shift_or_404(shift_id, organization_id)
    allowed = {
        'title', 'shift_date', 'start_time', 'end_time', 'hours',
        'location', 'status', 'notes', 'project_id',
    }
    from datetime import date as _date
    for k, v in fields.items():
        if k in allowed:
            if k == 'shift_date' and isinstance(v, str):
                v = _date.fromisoformat(v)
            setattr(shift, k, v)
    db.session.commit()
    return shift


def complete_shift(
    shift_id: int,
    organization_id: int,
    hours: float,
) -> VolunteerShift:
    """Mark a shift as completed and update the volunteer's cumulative hours."""
    shift = _get_shift_or_404(shift_id, organization_id)
    shift.status = 'completed'
    shift.hours = hours
    db.session.commit()

    # Update volunteer hours total
    vol = db.session.get(Volunteer, shift.volunteer_id)
    if vol:
        vol.hours_logged = (vol.hours_logged or 0.0) + hours
        db.session.commit()
    return shift


def volunteer_hours_summary(
    organization_id: int,
    *,
    volunteer_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return per-volunteer hours totals from completed shifts."""
    stmt = select(
        Volunteer.id,
        Volunteer.name,
        Volunteer.email,
        func.coalesce(func.sum(VolunteerShift.hours), 0.0).label('shift_hours'),
        func.count(VolunteerShift.id).label('shift_count'),
    ).outerjoin(
        VolunteerShift,
        (VolunteerShift.volunteer_id == Volunteer.id)
        & (VolunteerShift.status == 'completed'),
    ).where(Volunteer.organization_id == organization_id)
    if volunteer_id:
        stmt = stmt.where(Volunteer.id == volunteer_id)
    rows = db.session.connection().exec_driver_sql(str(stmt.group_by(Volunteer.id).order_by(Volunteer.name.asc()).compile(compile_kwargs={"literal_binds": True}))).all()
    return [
        {
            'volunteer_id': r.id,
            'name': r.name,
            'email': r.email,
            'shift_hours': round(float(r.shift_hours), 2),
            'shift_count': r.shift_count,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Training courses
# ---------------------------------------------------------------------------

def create_training_course(
    organization_id: int,
    name: str,
    *,
    description: Optional[str] = None,
    category: str = 'orientation',
    duration_hours: Optional[float] = None,
    is_required: bool = False,
    expires_after_days: Optional[int] = None,
    created_by_id: Optional[int] = None,
) -> TrainingCourse:
    course = TrainingCourse(
        organization_id=organization_id,
        name=name,
        description=description,
        category=category,
        duration_hours=duration_hours,
        is_required=is_required,
        expires_after_days=expires_after_days,
        created_by_id=created_by_id,
    )
    db.session.add(course)
    db.session.commit()
    return course


def list_training_courses(
    organization_id: int,
    *,
    category: Optional[str] = None,
    is_required: Optional[bool] = None,
) -> List[TrainingCourse]:
    stmt = select(TrainingCourse).where(TrainingCourse.organization_id == organization_id)
    if category:
        stmt = stmt.where(TrainingCourse.category == category)
    if is_required is not None:
        stmt = stmt.where(TrainingCourse.is_required == is_required)
    stmt = stmt.order_by(TrainingCourse.name.asc())
    return list(db.session.scalars(stmt))


def update_training_course(
    course_id: int,
    organization_id: int,
    **fields: Any,
) -> TrainingCourse:
    course = _get_course_or_404(course_id, organization_id)
    allowed = {
        'name', 'description', 'category', 'duration_hours',
        'is_required', 'expires_after_days',
    }
    for k, v in fields.items():
        if k in allowed:
            setattr(course, k, v)
    db.session.commit()
    return course


def delete_training_course(course_id: int, organization_id: int) -> None:
    course = _get_course_or_404(course_id, organization_id)
    db.session.delete(course)
    db.session.commit()


# ---------------------------------------------------------------------------
# Volunteer training assignments
# ---------------------------------------------------------------------------

def assign_training(
    organization_id: int,
    volunteer_id: int,
    course_id: int,
    *,
    notes: Optional[str] = None,
) -> VolunteerTraining:
    """Assign a training course to a volunteer (idempotent on pending)."""
    # Guard cross-tenant
    _get_volunteer_or_404(volunteer_id, organization_id)
    _get_course_or_404(course_id, organization_id)

    # Idempotent: if pending already exists, return it
    existing = db.session.scalars(
        select(VolunteerTraining).where(
            VolunteerTraining.volunteer_id == volunteer_id,
            VolunteerTraining.course_id == course_id,
            VolunteerTraining.organization_id == organization_id,
            VolunteerTraining.status == 'pending',
        ).limit(1)
    ).first()
    if existing:
        return existing

    training = VolunteerTraining(
        organization_id=organization_id,
        volunteer_id=volunteer_id,
        course_id=course_id,
        status='pending',
        notes=notes,
    )
    db.session.add(training)
    db.session.commit()
    return training


def complete_training(
    training_id: int,
    organization_id: int,
    *,
    score: Optional[float] = None,
    notes: Optional[str] = None,
) -> VolunteerTraining:
    training = _get_training_or_404(training_id, organization_id)
    training.status = 'completed'
    training.completed_at = datetime.now(UTC).replace(tzinfo=None)
    if score is not None:
        training.score = score
    if notes is not None:
        training.notes = notes

    # Compute expiry date if course has expires_after_days
    course = db.session.get(TrainingCourse, training.course_id)
    if course and course.expires_after_days:
        training.expires_at = training.completed_at + timedelta(days=course.expires_after_days)

    db.session.commit()
    return training


def list_volunteer_trainings(
    organization_id: int,
    *,
    volunteer_id: Optional[int] = None,
    course_id: Optional[int] = None,
    status: Optional[str] = None,
) -> List[VolunteerTraining]:
    stmt = select(VolunteerTraining).where(VolunteerTraining.organization_id == organization_id)
    if volunteer_id:
        stmt = stmt.where(VolunteerTraining.volunteer_id == volunteer_id)
    if course_id:
        stmt = stmt.where(VolunteerTraining.course_id == course_id)
    if status:
        stmt = stmt.where(VolunteerTraining.status == status)
    stmt = stmt.order_by(VolunteerTraining.assigned_at.desc())
    return list(db.session.scalars(stmt))


def training_compliance_report(organization_id: int) -> Dict[str, Any]:
    """Return compliance summary: how many volunteers have completed each required course."""
    required_courses = list(
        db.session.scalars(
            select(TrainingCourse).where(
                TrainingCourse.organization_id == organization_id,
                TrainingCourse.is_required == True,
            )
        )
    )
    total_volunteers = db.session.scalar(
        select(func.count(Volunteer.id)).where(
            Volunteer.organization_id == organization_id,
            Volunteer.status == 'active',
        )
    ) or 0

    report = []
    for course in required_courses:
        completed = db.session.scalar(
            select(func.count(VolunteerTraining.id)).where(
                VolunteerTraining.organization_id == organization_id,
                VolunteerTraining.course_id == course.id,
                VolunteerTraining.status == 'completed',
            )
        ) or 0
        report.append({
            'course_id': course.id,
            'course_name': course.name,
            'category': course.category,
            'total_active_volunteers': total_volunteers,
            'completed_count': completed,
            'compliance_pct': round(completed / total_volunteers * 100, 1) if total_volunteers else 0.0,
        })
    return {'required_courses': report, 'total_active_volunteers': total_volunteers}
