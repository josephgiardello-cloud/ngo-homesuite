import smtplib
from email.message import EmailMessage

from ngo_homesuite.config import get_runtime_settings

def send_receipt(donor_email, donor_name, amount_cents, currency):
    settings = get_runtime_settings()
    msg = EmailMessage()
    msg['Subject'] = 'Thank You for Your Donation'
    msg['From'] = settings.default_mail_sender
    msg['To'] = donor_email
    amount = amount_cents / 100.0
    msg.set_content(f"Dear {donor_name},\n\nThank you for your generous donation of ${amount:.2f} {currency}.\nA receipt is attached.\n\nBest regards,\nYour NGO Team")
    with smtplib.SMTP(settings.mail_server, int(settings.mail_port)) as server:
        if settings.mail_use_tls:
            server.starttls()
        if settings.mail_username and settings.mail_password:
            server.login(settings.mail_username, settings.mail_password)
        server.send_message(msg)
