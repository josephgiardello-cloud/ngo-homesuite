"""Volunteer scheduling and training service layer."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Dict, List, Optional

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

def get_volunteer(volunteer_id: int, organization_id: int) -> Optional[Volunteer]:
    return Volunteer.query.filter_by(
        id=volunteer_id, organization_id=organization_id
    ).first()


def list_volunteers(organization_id: int, *, status: Optional[str] = None) -> List[Volunteer]:
    q = Volunteer.query.filter_by(organization_id=organization_id)
    if status:
        q = q.filter_by(status=status)
    return q.order_by(Volunteer.name.asc()).all()


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
    vol = Volunteer.query.filter_by(
        id=volunteer_id, organization_id=organization_id
    ).first_or_404()

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
    q = VolunteerShift.query.filter_by(organization_id=organization_id)
    if volunteer_id:
        q = q.filter_by(volunteer_id=volunteer_id)
    if project_id:
        q = q.filter_by(project_id=project_id)
    if status:
        q = q.filter_by(status=status)
    return q.order_by(VolunteerShift.shift_date.asc()).all()


def update_shift(
    shift_id: int,
    organization_id: int,
    **fields: Any,
) -> VolunteerShift:
    shift = VolunteerShift.query.filter_by(
        id=shift_id, organization_id=organization_id
    ).first_or_404()
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
    shift = VolunteerShift.query.filter_by(
        id=shift_id, organization_id=organization_id
    ).first_or_404()
    shift.status = 'completed'
    shift.hours = hours
    db.session.commit()

    # Update volunteer hours total
    vol = Volunteer.query.get(shift.volunteer_id)
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
    from sqlalchemy import func
    q = (
        db.session.query(
            Volunteer.id,
            Volunteer.name,
            Volunteer.email,
            func.coalesce(func.sum(VolunteerShift.hours), 0.0).label('shift_hours'),
            func.count(VolunteerShift.id).label('shift_count'),
        )
        .outerjoin(
            VolunteerShift,
            (VolunteerShift.volunteer_id == Volunteer.id)
            & (VolunteerShift.status == 'completed'),
        )
        .filter(Volunteer.organization_id == organization_id)
    )
    if volunteer_id:
        q = q.filter(Volunteer.id == volunteer_id)
    rows = q.group_by(Volunteer.id).order_by(Volunteer.name.asc()).all()
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
    q = TrainingCourse.query.filter_by(organization_id=organization_id)
    if category:
        q = q.filter_by(category=category)
    if is_required is not None:
        q = q.filter_by(is_required=is_required)
    return q.order_by(TrainingCourse.name.asc()).all()


def update_training_course(
    course_id: int,
    organization_id: int,
    **fields: Any,
) -> TrainingCourse:
    course = TrainingCourse.query.filter_by(
        id=course_id, organization_id=organization_id
    ).first_or_404()
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
    course = TrainingCourse.query.filter_by(
        id=course_id, organization_id=organization_id
    ).first_or_404()
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
    Volunteer.query.filter_by(
        id=volunteer_id, organization_id=organization_id
    ).first_or_404()
    TrainingCourse.query.filter_by(
        id=course_id, organization_id=organization_id
    ).first_or_404()

    # Idempotent: if pending already exists, return it
    existing = VolunteerTraining.query.filter_by(
        volunteer_id=volunteer_id,
        course_id=course_id,
        organization_id=organization_id,
        status='pending',
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
    training = VolunteerTraining.query.filter_by(
        id=training_id, organization_id=organization_id
    ).first_or_404()
    training.status = 'completed'
    training.completed_at = datetime.now(UTC).replace(tzinfo=None)
    if score is not None:
        training.score = score
    if notes is not None:
        training.notes = notes

    # Compute expiry date if course has expires_after_days
    course = TrainingCourse.query.get(training.course_id)
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
    q = VolunteerTraining.query.filter_by(organization_id=organization_id)
    if volunteer_id:
        q = q.filter_by(volunteer_id=volunteer_id)
    if course_id:
        q = q.filter_by(course_id=course_id)
    if status:
        q = q.filter_by(status=status)
    return q.order_by(VolunteerTraining.assigned_at.desc()).all()


def training_compliance_report(organization_id: int) -> Dict[str, Any]:
    """Return compliance summary: how many volunteers have completed each required course."""
    required_courses = TrainingCourse.query.filter_by(
        organization_id=organization_id, is_required=True
    ).all()
    total_volunteers = Volunteer.query.filter_by(
        organization_id=organization_id, status='active'
    ).count()

    report = []
    for course in required_courses:
        completed = VolunteerTraining.query.filter_by(
            organization_id=organization_id,
            course_id=course.id,
            status='completed',
        ).count()
        report.append({
            'course_id': course.id,
            'course_name': course.name,
            'category': course.category,
            'total_active_volunteers': total_volunteers,
            'completed_count': completed,
            'compliance_pct': round(completed / total_volunteers * 100, 1) if total_volunteers else 0.0,
        })
    return {'required_courses': report, 'total_active_volunteers': total_volunteers}
