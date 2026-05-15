import stripe
from flask import Flask, request, jsonify
import os
import sqlite3
from stripe import SignatureVerificationError
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
import logging
from logging.handlers import RotatingFileHandler

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')
app = Flask(__name__)
csrf = CSRFProtect(app)
limiter = Limiter(app, key_func=lambda: request.remote_addr)

# Setup rotating logs for production
if os.getenv('ENV', 'development') == 'production':
    handler = RotatingFileHandler('webhook_events.log', maxBytes=1000000, backupCount=5)
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(message)s')
else:
    logging.basicConfig(level=logging.INFO, filename='webhook_events.log', format='%(asctime)s %(message)s')

# Create Stripe checkout session
@app.route('/create-checkout-session', methods=['POST'])
@limiter.limit('5 per minute')
def create_checkout_session():
    data = request.json
    currency = data.get('currency', 'USD').upper()
    allowed_currencies = {'USD', 'EUR', 'GBP', 'INR'}
    if currency not in allowed_currencies:
        return jsonify({'error': 'Unsupported currency'}), 400
    try:
        amount_cents = int(data['amount_cents'])
    except (ValueError, TypeError):
        return jsonify({'error': 'Invalid amount'}), 400
    session = stripe.checkout.Session.create(
        payment_method_types=['card'],
        line_items=[{
            'price_data': {
                'currency': currency,
                'product_data': {
                    'name': data['campaign_name'],
                },
                'unit_amount': amount_cents,
            },
            'quantity': 1,
        }],
        mode='payment',
        success_url=data['success_url'],
        cancel_url=data['cancel_url'],
        metadata={
            'donor_id': str(data['donor_id']),
            'campaign_id': str(data['campaign_id'])
        }
    )
    return jsonify({'checkout_url': session.url})

# Stripe webhook endpoint
@app.route('/webhook/stripe', methods=['POST'])
def stripe_webhook():
    payload = request.data
    sig_header = request.headers.get('Stripe-Signature')
    endpoint_secret = os.getenv('STRIPE_WEBHOOK_SECRET')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except SignatureVerificationError as e:
        logging.info(f"Invalid signature: {e}")
        return jsonify({'error': 'Invalid signature'}), 400
    except ValueError as e:
        logging.info(f"Invalid payload: {e}")
        return jsonify({'error': 'Invalid payload'}), 400
    logging.info(f"Received event: {event.get('type')} session_id: {event['data']['object'].get('id')}")
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        if session.get('payment_status') != 'paid':
            logging.info(f"Session {session.get('id')} not paid yet.")
            return jsonify({'status': 'pending', 'session_id': session.get('id')}), 200
        try:
            donor_id = int(session['metadata']['donor_id'])
            campaign_id = int(session['metadata']['campaign_id'])
        except (ValueError, TypeError, KeyError):
            logging.error("Invalid donor_id or campaign_id in metadata")
            return jsonify({'error': 'Invalid donor_id or campaign_id', 'session_id': session.get('id')}), 400
        amount_cents = session['amount_total']
        currency = session['currency']
        session_id = session['id']
        payment_intent = session.get('payment_intent')
        bank_account_id = None  # For Stripe, can be None or a virtual account
        note = f"session:{session_id}|intent:{payment_intent}" if payment_intent else session_id
        with sqlite3.connect(os.getenv('DB_PATH')) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM donations WHERE source = 'stripe' AND note = ?", (note,))
            if cur.fetchone():
                return jsonify({'status': 'already_processed', 'session_id': session_id}), 200
            try:
                cur.execute("INSERT INTO donations (donor_id, campaign_id, amount_cents, currency, received_at, source, note, bank_account_id) VALUES (?, ?, ?, ?, datetime('now'), 'stripe', ?, ?)", (donor_id, campaign_id, amount_cents, currency, note, bank_account_id))
                conn.commit()
            except Exception as e:
                conn.rollback()
                logging.error(f"DB error: {e}")
                return jsonify({'error': 'DB error', 'session_id': session_id}), 500
        # Optionally call email_service.send_receipt here
        return jsonify({'status': 'success', 'session_id': session_id, 'payment_intent': payment_intent}), 200
    return jsonify({'status': 'ignored'}), 200

def record_donation(donor_id, campaign_id, amount_cents, currency, source='manual', note=None, bank_account_id=None):
    with sqlite3.connect(os.getenv('DB_PATH')) as conn:
        cur = conn.cursor()
        cur.execute("INSERT INTO donations (donor_id, campaign_id, amount_cents, currency, received_at, source, note, bank_account_id) VALUES (?, ?, ?, ?, datetime('now'), ?, ?, ?)", (donor_id, campaign_id, amount_cents, currency, source, note, bank_account_id))
        conn.commit()

