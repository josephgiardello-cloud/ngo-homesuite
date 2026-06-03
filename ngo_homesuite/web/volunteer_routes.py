"""Volunteer management routes: shifts, training courses, training assignments."""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import login_required

from ngo_homesuite.web.auth_routes import require_step_up_auth
from ngo_homesuite.web.rbac import roles_required

volunteer_bp = Blueprint('volunteer', __name__)


def _org_id():
    from flask_login import current_user
    return current_user.organization_id


# ---------------------------------------------------------------------------
# Volunteers list
# ---------------------------------------------------------------------------

@volunteer_bp.get('/volunteers')
@login_required
def list_volunteers_route():
    from ngo_homesuite.services.volunteer_service import list_volunteers
    status = request.args.get('status')
    q = request.args.get('q')
    limit = request.args.get('limit', type=int)
    offset = request.args.get('offset', default=0, type=int)
    vols = list_volunteers(
        _org_id(),
        status=status,
        search_query=q,
        limit=limit,
        offset=offset,
    )
    return jsonify([
        {
            'id': v.id,
            'name': v.name,
            'email': v.email,
            'phone': v.phone,
            'hours_logged': v.hours_logged,
            'status': v.status,
        }
        for v in vols
    ])


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------

@volunteer_bp.post('/volunteers/shifts')
@login_required
@roles_required('admin', 'staff')
def create_shift_route():
    from ngo_homesuite.services.volunteer_service import create_shift
    data = request.get_json(force=True) or {}
    volunteer_id = data.get('volunteer_id')
    title = data.get('title')
    shift_date = data.get('shift_date')
    if not volunteer_id or not title or not shift_date:
        return jsonify({'error': 'volunteer_id, title and shift_date are required'}), 400

    shift = create_shift(
        _org_id(),
        volunteer_id,
        title,
        shift_date,
        project_id=data.get('project_id'),
        start_time=data.get('start_time'),
        end_time=data.get('end_time'),
        hours=data.get('hours'),
        location=data.get('location'),
        notes=data.get('notes'),
    )
    return jsonify(_shift_dict(shift)), 201


@volunteer_bp.get('/volunteers/shifts')
@login_required
def list_shifts_route():
    from ngo_homesuite.services.volunteer_service import list_shifts
    volunteer_id = request.args.get('volunteer_id', type=int)
    project_id = request.args.get('project_id', type=int)
    status = request.args.get('status')
    shifts = list_shifts(_org_id(), volunteer_id=volunteer_id, project_id=project_id, status=status)
    return jsonify([_shift_dict(s) for s in shifts])


@volunteer_bp.get('/volunteers/shifts/<int:shift_id>')
@login_required
def get_shift_route(shift_id: int):
    from ngo_homesuite.services.volunteer_service import get_shift
    shift = get_shift(shift_id, _org_id())
    if shift is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(_shift_dict(shift))


@volunteer_bp.patch('/volunteers/shifts/<int:shift_id>')
@login_required
@roles_required('admin', 'staff')
def update_shift_route(shift_id: int):
    from ngo_homesuite.services.volunteer_service import update_shift
    data = request.get_json(force=True) or {}
    shift = update_shift(shift_id, _org_id(), **data)
    return jsonify(_shift_dict(shift))


@volunteer_bp.post('/volunteers/shifts/<int:shift_id>/complete')
@login_required
@roles_required('admin', 'staff')
def complete_shift_route(shift_id: int):
    from ngo_homesuite.services.volunteer_service import complete_shift
    data = request.get_json(force=True) or {}
    hours = data.get('hours')
    if hours is None:
        return jsonify({'error': 'hours is required'}), 400
    shift = complete_shift(shift_id, _org_id(), float(hours))
    return jsonify(_shift_dict(shift))


@volunteer_bp.get('/volunteers/hours')
@login_required
def hours_summary_route():
    from ngo_homesuite.services.volunteer_service import volunteer_hours_summary
    volunteer_id = request.args.get('volunteer_id', type=int)
    rows = volunteer_hours_summary(_org_id(), volunteer_id=volunteer_id)
    return jsonify(rows)


# ---------------------------------------------------------------------------
# Training courses
# ---------------------------------------------------------------------------

@volunteer_bp.post('/volunteers/training/courses')
@login_required
@roles_required('admin')
def create_course_route():
    from ngo_homesuite.services.volunteer_service import create_training_course
    from flask_login import current_user
    data = request.get_json(force=True) or {}
    name = data.get('name')
    if not name:
        return jsonify({'error': 'name is required'}), 400

    course = create_training_course(
        _org_id(),
        name,
        description=data.get('description'),
        category=data.get('category', 'orientation'),
        duration_hours=data.get('duration_hours'),
        is_required=bool(data.get('is_required', False)),
        expires_after_days=data.get('expires_after_days'),
        created_by_id=current_user.id,
    )
    return jsonify(_course_dict(course)), 201


@volunteer_bp.get('/volunteers/training/courses')
@login_required
def list_courses_route():
    from ngo_homesuite.services.volunteer_service import list_training_courses
    category = request.args.get('category')
    is_required_param = request.args.get('is_required')
    is_required = None
    if is_required_param is not None:
        is_required = is_required_param.lower() in ('1', 'true', 'yes')
    courses = list_training_courses(_org_id(), category=category, is_required=is_required)
    return jsonify([_course_dict(c) for c in courses])


@volunteer_bp.patch('/volunteers/training/courses/<int:course_id>')
@login_required
@roles_required('admin')
def update_course_route(course_id: int):
    from ngo_homesuite.services.volunteer_service import update_training_course
    data = request.get_json(force=True) or {}
    course = update_training_course(course_id, _org_id(), **data)
    return jsonify(_course_dict(course))


@volunteer_bp.delete('/volunteers/training/courses/<int:course_id>')
@login_required
@roles_required('admin')
@require_step_up_auth
def delete_course_route(course_id: int):
    from ngo_homesuite.services.volunteer_service import delete_training_course
    delete_training_course(course_id, _org_id())
    return '', 204


# ---------------------------------------------------------------------------
# Training assignments
# ---------------------------------------------------------------------------

@volunteer_bp.post('/volunteers/<int:volunteer_id>/training')
@login_required
@roles_required('admin', 'staff')
def assign_training_route(volunteer_id: int):
    from ngo_homesuite.services.volunteer_service import assign_training
    data = request.get_json(force=True) or {}
    course_id = data.get('course_id')
    if not course_id:
        return jsonify({'error': 'course_id is required'}), 400
    training = assign_training(_org_id(), volunteer_id, course_id, notes=data.get('notes'))
    return jsonify(_training_dict(training)), 201


@volunteer_bp.get('/volunteers/<int:volunteer_id>/training')
@login_required
def list_volunteer_trainings_route(volunteer_id: int):
    from ngo_homesuite.services.volunteer_service import list_volunteer_trainings
    status = request.args.get('status')
    items = list_volunteer_trainings(_org_id(), volunteer_id=volunteer_id, status=status)
    return jsonify([_training_dict(t) for t in items])


@volunteer_bp.post('/volunteers/training/<int:training_id>/complete')
@login_required
@roles_required('admin', 'staff')
def complete_training_route(training_id: int):
    from ngo_homesuite.services.volunteer_service import complete_training
    data = request.get_json(force=True) or {}
    training = complete_training(
        training_id, _org_id(),
        score=data.get('score'),
        notes=data.get('notes'),
    )
    return jsonify(_training_dict(training))


@volunteer_bp.get('/volunteers/training/compliance')
@login_required
@roles_required('admin', 'staff')
def training_compliance_route():
    from ngo_homesuite.services.volunteer_service import training_compliance_report
    return jsonify(training_compliance_report(_org_id()))


# ---------------------------------------------------------------------------
# Serializers
# ---------------------------------------------------------------------------

def _shift_dict(s) -> dict:
    return {
        'id': s.id,
        'volunteer_id': s.volunteer_id,
        'project_id': s.project_id,
        'title': s.title,
        'shift_date': s.shift_date.isoformat() if s.shift_date else None,
        'start_time': s.start_time,
        'end_time': s.end_time,
        'hours': s.hours,
        'location': s.location,
        'status': s.status,
        'notes': s.notes,
    }


def _course_dict(c) -> dict:
    return {
        'id': c.id,
        'name': c.name,
        'description': c.description,
        'category': c.category,
        'duration_hours': c.duration_hours,
        'is_required': c.is_required,
        'expires_after_days': c.expires_after_days,
    }


def _training_dict(t) -> dict:
    return {
        'id': t.id,
        'volunteer_id': t.volunteer_id,
        'course_id': t.course_id,
        'status': t.status,
        'score': t.score,
        'assigned_at': t.assigned_at.isoformat() if t.assigned_at else None,
        'completed_at': t.completed_at.isoformat() if t.completed_at else None,
        'expires_at': t.expires_at.isoformat() if t.expires_at else None,
        'notes': t.notes,
    }
