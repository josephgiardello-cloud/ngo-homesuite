from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from ngo_homesuite.ai import rag_index
from ngo_homesuite.utils import email_service, mailchimp_service


def test_rag_default_index_dir_uses_runtime_settings(monkeypatch):
    monkeypatch.setattr(
        rag_index,
        "get_runtime_settings",
        lambda: SimpleNamespace(copilot_index_dir="data/from-runtime-settings"),
    )

    assert rag_index.default_index_dir() == "data/from-runtime-settings"


def test_mailchimp_api_root_uses_runtime_settings(monkeypatch):
    monkeypatch.setattr(
        mailchimp_service,
        "get_runtime_settings",
        lambda: SimpleNamespace(mailchimp_api_key="abc-us7", mailchimp_list_id="list123"),
    )

    assert mailchimp_service._mailchimp_api_root() == "https://us7.api.mailchimp.com/3.0"


def test_email_service_send_receipt_uses_runtime_settings(monkeypatch):
    fake_settings = SimpleNamespace(
        default_mail_sender="noreply@test.local",
        mail_server="smtp.test.local",
        mail_port=2525,
        mail_use_tls=True,
        mail_username="smtp-user",
        mail_password="smtp-pass",
    )
    monkeypatch.setattr(email_service, "get_runtime_settings", lambda: fake_settings)

    smtp_instance = MagicMock()
    smtp_context = MagicMock()
    smtp_context.__enter__.return_value = smtp_instance
    smtp_context.__exit__.return_value = None

    monkeypatch.setattr(email_service.smtplib, "SMTP", MagicMock(return_value=smtp_context))

    email_service.send_receipt(
        donor_email="donor@example.org",
        donor_name="Donor Person",
        amount_cents=2500,
        currency="USD",
    )

    email_service.smtplib.SMTP.assert_called_once_with("smtp.test.local", 2525)
    smtp_instance.starttls.assert_called_once()
    smtp_instance.login.assert_called_once_with("smtp-user", "smtp-pass")
    smtp_instance.send_message.assert_called_once()
