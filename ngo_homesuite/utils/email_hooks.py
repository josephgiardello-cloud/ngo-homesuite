import smtplib
from email.message import EmailMessage

def send_thank_you_email(donor_email: str, donor_name: str, donation_amount: float, currency: str, smtp_config: dict):
    msg = EmailMessage()
    msg['Subject'] = 'Thank You for Your Donation'
    msg['From'] = smtp_config['from']
    msg['To'] = donor_email
    msg.set_content(f"Dear {donor_name},\n\nThank you for your generous donation of {donation_amount:.2f} {currency}.\nWe appreciate your support!\n\nBest regards,\nYour NGO Team")
    with smtplib.SMTP(smtp_config['host'], smtp_config.get('port', 587)) as server:
        server.starttls()
        server.login(smtp_config['user'], smtp_config['password'])
        server.send_message(msg)
