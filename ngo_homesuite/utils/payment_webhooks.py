import hmac
import hashlib
import json
from flask import request

def verify_stripe_signature(payload, sig_header, secret):
    import stripe
    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, secret
        )
        return event
    except Exception:
        return None

def handle_paypal_webhook(payload, secret):
    # Basic HMAC verification
    expected_sig = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    # You'd compare this to a header from PayPal
    return expected_sig

# Example Flask route for Stripe
# @app.route('/webhook/stripe', methods=['POST'])
# def stripe_webhook():
#     event = verify_stripe_signature(request.data, request.headers.get('Stripe-Signature'), STRIPE_SECRET)
#     if event:
#         # Process donation
#         pass
#     return '', 200
