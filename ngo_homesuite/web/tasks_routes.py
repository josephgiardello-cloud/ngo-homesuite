from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ngo_homesuite.web.rbac import roles_required


tasks_bp = Blueprint("tasks", __name__, url_prefix="/tasks")


def _org_id() -> int:
    return int(current_user.organization_id)


@tasks_bp.get("/")
@login_required
def list_tasks_route():
    from ngo_homesuite.services.task_service import list_tasks

    tasks = list_tasks(
        _org_id(),
        donor_id=request.args.get("donor_id", type=int),
        grant_id=request.args.get("grant_id", type=int),
        status=request.args.get("status"),
        priority=request.args.get("priority"),
        overdue_only=request.args.get("overdue") == "1",
    )
    return jsonify([
        {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "priority": t.priority,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "donor_id": t.donor_id,
            "grant_id": t.grant_id,
        }
        for t in tasks
    ])


@tasks_bp.post("/")
@login_required
@roles_required("admin", "staff")
def create_task_route():
    from ngo_homesuite.services.task_service import create_task

    data = request.get_json(silent=True) or {}
    title = data.get("title")
    if not title:
        return jsonify({"error": "title is required"}), 400

    task = create_task(_org_id(), title, **{k: v for k, v in data.items() if k != "title"})
    return jsonify({"id": task.id, "status": task.status}), 201


@tasks_bp.post("/<int:task_id>/complete")
@login_required
@roles_required("admin", "staff")
def complete_task_route(task_id: int):
    from ngo_homesuite.services.task_service import complete_task

    data = request.get_json(silent=True) or {}
    task = complete_task(task_id, _org_id(), notes=data.get("notes"))
    return jsonify({"id": task.id, "status": task.status})


@tasks_bp.get("/overdue")
@login_required
def overdue_route():
    from ngo_homesuite.services.task_service import overdue_task_summary

    return jsonify(overdue_task_summary(_org_id()))
