from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas
import datetime

def generate_receipt_pdf(donation: dict, donor: dict, output_path: str):
    """
    Generate a simple tax-compliant donation receipt PDF.
    donation: dict with amount_cents, currency, received_at, etc.
    donor: dict with name, address, etc.
    """
    c = canvas.Canvas(output_path, pagesize=LETTER)
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, f"Donation Receipt")
    c.drawString(72, 700, f"Date: {donation.get('received_at', datetime.date.today())}")
    c.drawString(72, 680, f"Donor: {donor.get('name', '')}")
    c.drawString(72, 660, f"Address: {donor.get('address', '')}")
    c.drawString(72, 640, f"Amount: {donation.get('amount_cents', 0)/100:.2f} {donation.get('currency', 'USD')}")
    c.drawString(72, 620, "Thank you for your support! This receipt may be used for tax purposes.")
    c.showPage()
    c.save()
