import datetime
from io import BytesIO
import warnings


def _build_canvas(output):
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"ast\.NameConstant is deprecated and will be removed in Python 3\.14; use ast\.Constant instead",
            category=DeprecationWarning,
        )
        from reportlab.lib.pagesizes import LETTER
        from reportlab.pdfgen import canvas

    return canvas.Canvas(output, pagesize=LETTER)

def generate_receipt_pdf(donation: dict, donor: dict, output_path: str):
    """
    Generate a simple tax-compliant donation receipt PDF.
    donation: dict with amount_cents, currency, received_at, etc.
    donor: dict with name, address, etc.
    """
    c = _build_canvas(output_path)
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, f"Donation Receipt")
    c.drawString(72, 700, f"Date: {donation.get('received_at', datetime.date.today())}")
    c.drawString(72, 680, f"Donor: {donor.get('name', '')}")
    c.drawString(72, 660, f"Address: {donor.get('address', '')}")
    c.drawString(72, 640, f"Amount: {donation.get('amount_cents', 0)/100:.2f} {donation.get('currency', 'USD')}")
    c.drawString(72, 620, "Thank you for your support! This receipt may be used for tax purposes.")
    c.showPage()
    c.save()


def generate_receipt_pdf_bytes(donation: dict, donor: dict) -> bytes:
    """Generate a donation receipt PDF and return raw bytes for HTTP responses."""

    buffer = BytesIO()
    c = _build_canvas(buffer)
    c.setFont("Helvetica", 12)
    c.drawString(72, 720, "Donation Receipt")
    c.drawString(72, 700, f"Date: {donation.get('received_at', datetime.date.today())}")
    c.drawString(72, 680, f"Donor: {donor.get('name', '')}")
    c.drawString(72, 660, f"Address: {donor.get('address', '')}")
    c.drawString(72, 640, f"Amount: {donation.get('amount_cents', 0)/100:.2f} {donation.get('currency', 'USD')}")
    c.drawString(72, 620, "Thank you for your support! This receipt may be used for tax purposes.")
    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.read()
