import smtplib
from email.message import EmailMessage
import os

def send_receipt(donor_email, donor_name, amount_cents, currency):
    msg = EmailMessage()
    msg['Subject'] = 'Thank You for Your Donation'
    msg['From'] = os.getenv('EMAIL_FROM')
    msg['To'] = donor_email
    amount = amount_cents / 100.0
    msg.set_content(f"Dear {donor_name},\n\nThank you for your generous donation of ${amount:.2f} {currency}.\nA receipt is attached.\n\nBest regards,\nYour NGO Team")
    with smtplib.SMTP(os.getenv('SMTP_HOST'), int(os.getenv('SMTP_PORT', 587))) as server:
        server.starttls()
        server.login(os.getenv('SMTP_USER'), os.getenv('SMTP_PASSWORD'))
        server.send_message(msg)
