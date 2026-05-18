"""Admin routes for grant budget management and tracking.

All endpoints require the 'admin' role.
Handles grant budget line creation, editing, and budget variance reporting.
"""
from __future__ import annotations

import csv
from io import BytesIO, StringIO

from flask import Blueprint, jsonify, render_template, request, send_file
from flask_login import current_user, login_required
from sqlalchemy import select

from ngo_homesuite.models.core import db
from ngo_homesuite.grants.models import Grant, GrantBudgetLine, GrantExpenseAllocation
from ngo_homesuite.web.rbac import roles_required

grant_admin_bp = Blueprint("grant_admin", __name__, url_prefix="/admin/grants")


def _build_variance_report_csv(report_data: dict) -> bytes:
    stream = StringIO()
    writer = csv.writer(stream)
    writer.writerow([
        "category",
        "line_name",
        "allocated",
        "committed",
        "reconciled",
        "spent",
        "actual_spent",
        "remaining",
        "variance",
        "variance_pct",
        "utilization_pct",
        "alert_status",
    ])
    for line in report_data["lines"]:
        writer.writerow([
            line["category"],
            line["line_name"],
            line["allocated"],
            line["committed"],
            line["reconciled"],
            line["spent"],
            line["actual_spent"],
            line["remaining"],
            line["variance"],
            line["variance_pct"],
            line["utilization_pct"],
            line["alert_status"],
        ])
    return stream.getvalue().encode("utf-8-sig")


def _org_id() -> int:
    """Get current user's organization ID."""
    return int(current_user.organization_id)


@grant_admin_bp.route("/<int:grant_id>/budget", methods=["GET"])
@login_required
@roles_required("admin")
def get_grant_budget(grant_id: int):
    """Get grant budget overview and line items."""
    org_id = _org_id()

    # Fetch grant
    stmt = select(Grant).where(Grant.id == grant_id, Grant.organization_id == org_id)
    grant = db.session.scalar(stmt)
    if not grant:
        return jsonify({"error": "Grant not found"}), 404

    # Fetch budget lines
    stmt = select(GrantBudgetLine).where(
        GrantBudgetLine.grant_id == grant_id,
        GrantBudgetLine.organization_id == org_id
    )
    budget_lines = db.session.scalars(stmt).all()

    # Calculate totals per line (committed + spent)
    lines_data = []
    for line in budget_lines:
        stmt = select(db.func.sum(GrantExpenseAllocation.amount)).where(
            GrantExpenseAllocation.budget_line_id == line.id
        )
        spent = db.session.scalar(stmt) or 0.0

        variance = line.allocated_amount - spent
        variance_pct = (variance / line.allocated_amount * 100) if line.allocated_amount > 0 else 0

        lines_data.append({
            "id": line.id,
            "category": line.category,
            "line_name": line.line_name,
            "allocated_amount": line.allocated_amount,
            "spent_amount": spent,
            "remaining": variance,
            "variance_pct": round(variance_pct, 2),
            "notes": line.notes
        })

    return jsonify({
        "grant_id": grant_id,
        "grant_title": grant.title,
        "total_awarded": grant.amount_awarded,
        "budget_lines": lines_data
    })


@grant_admin_bp.route("/<int:grant_id>/budget/lines", methods=["POST"])
@login_required
@roles_required("admin")
def create_budget_line(grant_id: int):
    """Create a new budget line for a grant."""
    org_id = _org_id()

    # Verify grant exists
    stmt = select(Grant).where(Grant.id == grant_id, Grant.organization_id == org_id)
    grant = db.session.scalar(stmt)
    if not grant:
        return jsonify({"error": "Grant not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    # Validate required fields
    errors = []
    category = data.get("category", "").strip()
    line_name = data.get("line_name", "").strip()
    allocated_amount = data.get("allocated_amount")

    if not category:
        errors.append("category is required")
    if not line_name:
        errors.append("line_name is required")
    if allocated_amount is None:
        errors.append("allocated_amount is required")
    elif not isinstance(allocated_amount, (int, float)) or allocated_amount < 0:
        errors.append("allocated_amount must be a non-negative number")

    if errors:
        return jsonify({"errors": errors}), 400

    # Check for duplicate category per grant
    stmt = select(GrantBudgetLine).where(
        GrantBudgetLine.grant_id == grant_id,
        GrantBudgetLine.category == category
    )
    existing = db.session.scalar(stmt)
    if existing:
        return jsonify({"error": f"Budget line for category '{category}' already exists"}), 409

    # Validate total doesn't exceed award
    stmt = select(db.func.sum(GrantBudgetLine.allocated_amount)).where(
        GrantBudgetLine.grant_id == grant_id
    )
    current_total = db.session.scalar(stmt) or 0.0

    if current_total + allocated_amount > (grant.amount_awarded or 0):
        return jsonify({
            "error": f"Total budget lines ({current_total + allocated_amount}) exceed grant award ({grant.amount_awarded})"
        }), 400

    # Create line
    line = GrantBudgetLine(
        grant_id=grant_id,
        organization_id=org_id,
        category=category,
        line_name=line_name,
        allocated_amount=float(allocated_amount),
        notes=data.get("notes", "").strip() or None
    )
    db.session.add(line)
    db.session.commit()

    return jsonify({
        "id": line.id,
        "grant_id": line.grant_id,
        "category": line.category,
        "line_name": line.line_name,
        "allocated_amount": line.allocated_amount
    }), 201


@grant_admin_bp.route("/<int:grant_id>/budget/lines/<int:line_id>", methods=["PUT"])
@login_required
@roles_required("admin")
def update_budget_line(grant_id: int, line_id: int):
    """Update an existing budget line."""
    org_id = _org_id()

    # Verify grant exists
    stmt = select(Grant).where(Grant.id == grant_id, Grant.organization_id == org_id)
    grant = db.session.scalar(stmt)
    if not grant:
        return jsonify({"error": "Grant not found"}), 404

    # Fetch line
    stmt = select(GrantBudgetLine).where(
        GrantBudgetLine.id == line_id,
        GrantBudgetLine.grant_id == grant_id,
        GrantBudgetLine.organization_id == org_id
    )
    line = db.session.scalar(stmt)
    if not line:
        return jsonify({"error": "Budget line not found"}), 404

    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body required"}), 400

    # Update allowed fields
    if "line_name" in data:
        line.line_name = data["line_name"].strip()
    if "allocated_amount" in data:
        amount = data["allocated_amount"]
        if not isinstance(amount, (int, float)) or amount < 0:
            return jsonify({"error": "allocated_amount must be a non-negative number"}), 400
        line.allocated_amount = float(amount)
    if "notes" in data:
        line.notes = data["notes"].strip() or None

    db.session.commit()

    return jsonify({
        "id": line.id,
        "grant_id": line.grant_id,
        "category": line.category,
        "line_name": line.line_name,
        "allocated_amount": line.allocated_amount
    })


@grant_admin_bp.route("/<int:grant_id>/budget/lines/<int:line_id>", methods=["DELETE"])
@login_required
@roles_required("admin")
def delete_budget_line(grant_id: int, line_id: int):
    """Delete a budget line (only if no allocations exist)."""
    org_id = _org_id()

    # Verify grant exists
    stmt = select(Grant).where(Grant.id == grant_id, Grant.organization_id == org_id)
    grant = db.session.scalar(stmt)
    if not grant:
        return jsonify({"error": "Grant not found"}), 404

    # Fetch line
    stmt = select(GrantBudgetLine).where(
        GrantBudgetLine.id == line_id,
        GrantBudgetLine.grant_id == grant_id,
        GrantBudgetLine.organization_id == org_id
    )
    line = db.session.scalar(stmt)
    if not line:
        return jsonify({"error": "Budget line not found"}), 404

    # Check for allocations
    stmt = select(db.func.count(GrantExpenseAllocation.id)).where(
        GrantExpenseAllocation.budget_line_id == line_id
    )
    allocation_count = db.session.scalar(stmt) or 0

    if allocation_count > 0:
        return jsonify({
            "error": f"Cannot delete budget line with {allocation_count} expense allocation(s). Delete allocations first."
        }), 409

    db.session.delete(line)
    db.session.commit()

    return "", 204


@grant_admin_bp.route("/<int:grant_id>/budget/variance-report", methods=["GET"])
@login_required
@roles_required("admin")
def get_budget_variance_report(grant_id: int):
    """Get budget variance report for a grant, or export it as CSV."""
    from ngo_homesuite.grants.services import lifecycle as grant_svc
    from ngo_homesuite.grants.exceptions import GrantNotFound

    org_id = _org_id()
    try:
        summary = grant_svc.get_grant_budget_summary(grant_id, org_id)
    except GrantNotFound:
        return jsonify({"error": "Grant not found"}), 404

    report_data = {
        "grant_id": summary["grant_id"],
        "grant_title": summary["title"],
        "total_awarded": summary["amount_awarded"],
        "lines": summary["lines"],
        "summary": {
            "total_allocated": summary["total_allocated"],
            "total_committed": summary["total_committed"],
            "total_reconciled": summary["total_reconciled"],
            "total_spent": summary["total_spent"],
            "total_actual_spent": summary["total_actual_spent"],
            "total_remaining": summary["total_remaining"],
            "total_variance": summary["total_variance"],
            "utilization_pct": summary["utilization_pct"],
        },
    }

    if request.args.get("format") == "csv":
        csv_bytes = _build_variance_report_csv(report_data)
        return send_file(
            BytesIO(csv_bytes),
            mimetype="text/csv",
            as_attachment=True,
            download_name=f"grant-{grant_id}-variance-report.csv",
        )

    return jsonify(report_data)
