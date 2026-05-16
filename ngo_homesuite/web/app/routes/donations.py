"""Donation blueprint — direct (non-Stripe) donation recording.

For online Stripe-based donations see ngo_homesuite/web/integrations_routes.py
(POST /integrations/webhooks/stripe).
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ngo_homesuite.services.donation_service import DonationConcurrencyError, DonationNotFound, DonationService
from ngo_homesuite.web.rbac import roles_required

donations_bp = Blueprint("donations", __name__, url_prefix="/donations")
_svc = DonationService()


@donations_bp.post("/")
@login_required
@roles_required("admin", "staff")
def create_donation():
    """Record a direct (cash/bank-transfer/cheque) donation."""
    data = request.get_json(silent=True) or {}
    required = ["donor_name", "amount"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return jsonify({"error": f"Missing required fields: {missing}"}), 400

    try:
        amount = float(data["amount"])
    except (ValueError, TypeError):
        return jsonify({"error": "amount must be a number"}), 400

    org_id = current_user.organization_id
    if not org_id:
        return jsonify({"error": "User has no associated organisation"}), 403

    try:
        donation = _svc.create_donation(
            org_id=org_id,
            donor_name=data["donor_name"],
            amount=amount,
            currency=data.get("currency", "USD"),
            donor_email=data.get("donor_email"),
            donor_phone=data.get("donor_phone"),
            donor_id=data.get("donor_id"),
            project_id=data.get("project_id"),
            fund_id=data.get("fund_id"),
            payment_method=data.get("payment_method", "cash"),
            reference_number=data.get("reference_number"),
            purpose=data.get("purpose"),
            notes=data.get("notes"),
            actor_id=current_user.id,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except DonationConcurrencyError as exc:
        return jsonify({"error": str(exc)}), 409

    return jsonify({
        "id": donation.id,
        "donor_name": donation.donor_name,
        "amount": donation.amount,
        "currency": donation.currency,
        "status": donation.status,
        "donation_date": donation.donation_date.isoformat(),
    }), 201


@donations_bp.get("/")
@login_required
@roles_required("admin", "staff", "viewer")
def list_donations():
    """Paginated donation list for the current user's organisation."""
    org_id = current_user.organization_id
    if not org_id:
        return jsonify({"error": "User has no associated organisation"}), 403

    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(200, max(1, request.args.get("per_page", 50, type=int)))
    result = _svc.list_donations(
        org_id=org_id,
        donor_id=request.args.get("donor_id", type=int),
        project_id=request.args.get("project_id", type=int),
        fund_id=request.args.get("fund_id", type=int),
        status=request.args.get("status"),
        page=page,
        per_page=per_page,
    )
    return jsonify({
        "items": [
            {
                "id": d.id,
                "donor_name": d.donor_name,
                "amount": d.amount,
                "currency": d.currency,
                "status": d.status,
                "payment_method": d.payment_method,
                "donation_date": d.donation_date.isoformat(),
            }
            for d in result["items"]
        ],
        "total": result["total"],
        "page": result["page"],
        "per_page": result["per_page"],
        "pages": result["pages"],
    })


@donations_bp.get("/<int:donation_id>")
@login_required
@roles_required("admin", "staff", "viewer")
def get_donation(donation_id: int):
    org_id = current_user.organization_id
    if not org_id:
        return jsonify({"error": "User has no associated organisation"}), 403
    try:
        donation = _svc.get_donation(donation_id, org_id)
    except DonationNotFound:
        return jsonify({"error": "Donation not found"}), 404
    return jsonify({
        "id": donation.id,
        "donor_name": donation.donor_name,
        "donor_email": donation.donor_email,
        "amount": donation.amount,
        "currency": donation.currency,
        "status": donation.status,
        "payment_method": donation.payment_method,
        "reference_number": donation.reference_number,
        "purpose": donation.purpose,
        "notes": donation.notes,
        "project_id": donation.project_id,
        "fund_id": donation.fund_id,
        "donation_date": donation.donation_date.isoformat(),
        "created_at": donation.created_at.isoformat(),
    })


@donations_bp.patch("/<int:donation_id>/status")
@login_required
@roles_required("admin", "staff")
def update_status(donation_id: int):
    """Advance donation status along the defined lifecycle."""
    org_id = current_user.organization_id
    if not org_id:
        return jsonify({"error": "User has no associated organisation"}), 403
    data = request.get_json(silent=True) or {}
    new_status = data.get("status")
    if not new_status:
        return jsonify({"error": "'status' field is required"}), 400
    from ngo_homesuite.services.donation_service import InvalidStatusTransition
    try:
        donation = _svc.update_status(donation_id, org_id, new_status, actor_id=current_user.id)
    except DonationNotFound:
        return jsonify({"error": "Donation not found"}), 404
    except InvalidStatusTransition as exc:
        return jsonify({"error": str(exc)}), 422
    except DonationConcurrencyError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({"id": donation.id, "status": donation.status})


@donations_bp.post("/<int:donation_id>/receipt")
@login_required
@roles_required("admin", "staff")
def generate_receipt(donation_id: int):
    """Generate a tax receipt for a processed donation."""
    org_id = current_user.organization_id
    if not org_id:
        return jsonify({"error": "User has no associated organisation"}), 403
    try:
        receipt = _svc.generate_receipt(donation_id, org_id)
    except DonationNotFound:
        return jsonify({"error": "Donation not found"}), 404
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 422
    except DonationConcurrencyError as exc:
        return jsonify({"error": str(exc)}), 409
    return jsonify({
        "receipt_number": receipt.receipt_number,
        "status": receipt.status,
        "sent_to_email": receipt.sent_to_email,
        "created_at": receipt.created_at.isoformat(),
    }), 201
