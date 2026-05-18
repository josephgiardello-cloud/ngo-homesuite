"""Admin routes for grant budget transaction management (B-2: Commitments & Reconciliation)."""
from flask import Blueprint, request, jsonify
from flask_login import login_required, current_user
from ngo_homesuite.web.rbac import roles_required
from ngo_homesuite.models.core import db, _utcnow_naive
from ngo_homesuite.grants.models import (
    Grant,
    GrantBudgetLine,
    GrantBudgetTransaction,
)

budget_transactions_bp = Blueprint("budget_transactions", __name__, url_prefix="/admin/grants")


def _org_id():
    """Extract current user's organization for RLS."""
    return current_user.organization_id


@budget_transactions_bp.route("/<int:grant_id>/budget/lines/<int:line_id>/commit", methods=["POST"])
@login_required
@roles_required("admin")
def commit_budget_line(grant_id, line_id):
    """
    Commit funds for a budget line (pledge without spending).
    Body: {"amount": float, "description": str}
    Returns: {"success": bool, "transaction_id": int, "committed_total": float, "remaining": float}
    """
    org_id = _org_id()
    data = request.get_json() or {}

    # Validate request
    if not data.get("amount"):
        return jsonify({"error": "amount required"}), 400
    
    try:
        commit_amount = float(data["amount"])
    except (ValueError, TypeError):
        return jsonify({"error": "amount must be numeric"}), 400
    
    if commit_amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400

    # Fetch grant & budget line
    grant = Grant.query.filter_by(id=grant_id, organization_id=org_id).first()
    if not grant:
        return jsonify({"error": "grant not found"}), 404

    line = GrantBudgetLine.query.filter_by(id=line_id, grant_id=grant_id, organization_id=org_id).first()
    if not line:
        return jsonify({"error": "budget line not found"}), 404

    # Check: committed + amount <= allocated
    if line.committed_amount + commit_amount > line.allocated_amount:
        return jsonify({"error": "commitment exceeds allocated amount"}), 400

    # Create transaction record
    txn = GrantBudgetTransaction(
        budget_line_id=line_id,
        grant_id=grant_id,
        organization_id=org_id,
        transaction_type="commit",
        amount=commit_amount,
        description=data.get("description", f"Commitment of ${commit_amount:.2f}"),
        created_by_user_id=current_user.id,
    )
    db.session.add(txn)

    # Update line committed_amount
    line.committed_amount += commit_amount
    line.updated_at = _utcnow_naive()
    db.session.commit()

    remaining = line.allocated_amount - line.committed_amount - line.reconciled_amount
    return jsonify({
        "success": True,
        "transaction_id": txn.id,
        "committed_total": line.committed_amount,
        "reconciled_total": line.reconciled_amount,
        "remaining": remaining,
    }), 201


@budget_transactions_bp.route("/<int:grant_id>/budget/lines/<int:line_id>/reconcile", methods=["POST"])
@login_required
@roles_required("admin")
def reconcile_budget_line(grant_id, line_id):
    """
    Mark expense as reconciled (confirmed spent from budget line).
    Body: {"amount": float, "expense_id": int, "description": str}
    Returns: {"success": bool, "transaction_id": int, "reconciled_total": float, "remaining": float}
    """
    org_id = _org_id()
    data = request.get_json() or {}

    # Validate request
    if not data.get("amount"):
        return jsonify({"error": "amount required"}), 400
    
    try:
        reconcile_amount = float(data["amount"])
    except (ValueError, TypeError):
        return jsonify({"error": "amount must be numeric"}), 400
    
    if reconcile_amount <= 0:
        return jsonify({"error": "amount must be positive"}), 400

    # Fetch grant & budget line
    grant = Grant.query.filter_by(id=grant_id, organization_id=org_id).first()
    if not grant:
        return jsonify({"error": "grant not found"}), 404

    line = GrantBudgetLine.query.filter_by(id=line_id, grant_id=grant_id, organization_id=org_id).first()
    if not line:
        return jsonify({"error": "budget line not found"}), 404

    # Check: reconciled + amount <= allocated
    if line.reconciled_amount + reconcile_amount > line.allocated_amount:
        return jsonify({"error": "reconciliation exceeds allocated amount"}), 400

    # Create transaction record
    txn = GrantBudgetTransaction(
        budget_line_id=line_id,
        grant_id=grant_id,
        organization_id=org_id,
        transaction_type="reconcile",
        amount=reconcile_amount,
        description=data.get("description", f"Reconciliation of ${reconcile_amount:.2f}"),
        reference_type="expense" if data.get("expense_id") else "manual",
        reference_id=data.get("expense_id"),
        created_by_user_id=current_user.id,
    )
    db.session.add(txn)

    # Update line reconciled_amount
    line.reconciled_amount += reconcile_amount
    line.updated_at = _utcnow_naive()
    db.session.commit()

    remaining = line.allocated_amount - line.committed_amount - line.reconciled_amount
    return jsonify({
        "success": True,
        "transaction_id": txn.id,
        "committed_total": line.committed_amount,
        "reconciled_total": line.reconciled_amount,
        "remaining": remaining,
    }), 201


@budget_transactions_bp.route("/<int:grant_id>/budget/lines/<int:line_id>/status", methods=["GET"])
@login_required
@roles_required("admin")
def get_line_status(grant_id, line_id):
    """
    Get detailed status of a budget line with variance metrics.
    Returns: {"line_id": int, "allocated": float, "committed": float, "reconciled": float, 
              "remaining": float, "status": str, "variance_pct": float, "variance_status": str}
    """
    org_id = _org_id()

    grant = Grant.query.filter_by(id=grant_id, organization_id=org_id).first()
    if not grant:
        return jsonify({"error": "grant not found"}), 404

    line = GrantBudgetLine.query.filter_by(id=line_id, grant_id=grant_id, organization_id=org_id).first()
    if not line:
        return jsonify({"error": "budget line not found"}), 404

    remaining = line.allocated_amount - line.committed_amount - line.reconciled_amount
    variance = line.committed_amount - line.reconciled_amount
    variance_pct = (variance / line.allocated_amount * 100) if line.allocated_amount > 0 else 0

    # Determine status: pending (no commits), active (commits but not closed), closed (100% reconciled)
    if line.reconciled_amount >= line.allocated_amount:
        variance_status = "closed"
    elif variance_pct > 10:
        variance_status = "over-committed"
    elif variance_pct < -20:
        variance_status = "under-committed"
    else:
        variance_status = "on-track"

    return jsonify({
        "line_id": line_id,
        "category": line.category,
        "line_name": line.line_name,
        "allocated": line.allocated_amount,
        "committed": line.committed_amount,
        "reconciled": line.reconciled_amount,
        "remaining": remaining,
        "status": line.status,
        "variance": variance,
        "variance_pct": round(variance_pct, 2),
        "variance_status": variance_status,
    }), 200


@budget_transactions_bp.route("/<int:grant_id>/budget/variance-alerts", methods=["GET"])
@login_required
@roles_required("admin")
def get_variance_alerts(grant_id):
    """
    Get variance alerts for all budget lines in a grant.
    Alerts: >10% over-committed, >20% under-committed, approaching limit.
    Returns: {"alerts": [{"line_id": int, "category": str, "type": str, "variance_pct": float}]}
    """
    org_id = _org_id()

    grant = Grant.query.filter_by(id=grant_id, organization_id=org_id).first()
    if not grant:
        return jsonify({"error": "grant not found"}), 404

    lines = GrantBudgetLine.query.filter_by(grant_id=grant_id, organization_id=org_id).all()
    alerts = []

    for line in lines:
        remaining = line.allocated_amount - line.committed_amount - line.reconciled_amount
        variance = line.committed_amount - line.reconciled_amount
        variance_pct = (variance / line.allocated_amount * 100) if line.allocated_amount > 0 else 0
        utilization_pct = ((line.committed_amount + line.reconciled_amount) / line.allocated_amount * 100) if line.allocated_amount > 0 else 0

        # Alert if over-committed (>10%)
        if variance_pct > 10:
            alerts.append({
                "line_id": line.id,
                "category": line.category,
                "type": "over-committed",
                "variance_pct": round(variance_pct, 2),
                "message": f"{line.category}: committed ${line.committed_amount:.2f}, only ${line.allocated_amount:.2f} allocated",
            })

        # Alert if under-committed (>20% under)
        if variance_pct < -20 and line.reconciled_amount > 0:
            alerts.append({
                "line_id": line.id,
                "category": line.category,
                "type": "under-committed",
                "variance_pct": round(variance_pct, 2),
                "message": f"{line.category}: only ${line.committed_amount:.2f} committed of ${line.allocated_amount:.2f}",
            })

        # Alert if approaching limit (>90% utilization)
        if utilization_pct > 90:
            alerts.append({
                "line_id": line.id,
                "category": line.category,
                "type": "approaching-limit",
                "variance_pct": round(utilization_pct, 2),
                "message": f"{line.category}: ${utilization_pct:.0f}% of budget used, ${remaining:.2f} remaining",
            })

    return jsonify({
        "grant_id": grant_id,
        "alert_count": len(alerts),
        "alerts": alerts,
    }), 200


@budget_transactions_bp.route("/<int:grant_id>/budget/lines/<int:line_id>/transactions", methods=["GET"])
@login_required
@roles_required("admin")
def list_line_transactions(grant_id, line_id):
    """
    Get transaction history for a budget line.
    Returns: {"transactions": [{"id": int, "type": str, "amount": float, "description": str, "created_at": str}]}
    """
    org_id = _org_id()

    grant = Grant.query.filter_by(id=grant_id, organization_id=org_id).first()
    if not grant:
        return jsonify({"error": "grant not found"}), 404

    line = GrantBudgetLine.query.filter_by(id=line_id, grant_id=grant_id, organization_id=org_id).first()
    if not line:
        return jsonify({"error": "budget line not found"}), 404

    transactions = GrantBudgetTransaction.query.filter_by(budget_line_id=line_id).order_by(
        GrantBudgetTransaction.created_at.desc()
    ).all()

    return jsonify({
        "line_id": line_id,
        "category": line.category,
        "transaction_count": len(transactions),
        "transactions": [
            {
                "id": txn.id,
                "type": txn.transaction_type,
                "amount": txn.amount,
                "description": txn.description,
                "reference_type": txn.reference_type,
                "reference_id": txn.reference_id,
                "created_at": txn.created_at.isoformat(),
            }
            for txn in transactions
        ],
    }), 200
