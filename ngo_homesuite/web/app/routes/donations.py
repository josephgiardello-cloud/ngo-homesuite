from ngo_homesuite.utils.payment_service import record_donation
from ngo_homesuite.utils.email_service import send_receipt

# Example Flask route for donation insert
from flask import Blueprint, request, jsonify

donations_bp = Blueprint('donations', __name__)

@donations_bp.route('/donate', methods=['POST'])
def donate():
    data = request.json
    donor_id = data['donor_id']
    campaign_id = data['campaign_id']
    amount_cents = data['amount_cents']
    currency = data['currency']
    donor_email = data['donor_email']
    donor_name = data['donor_name']
    # Record donation in DB
    record_donation(donor_id, campaign_id, amount_cents, currency)
    # Send thank-you/receipt email
    send_receipt(donor_email, donor_name, amount_cents, currency)
    return jsonify({'status': 'success'})
