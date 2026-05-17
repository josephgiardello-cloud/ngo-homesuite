from __future__ import annotations

from config.email import EmailSettings


def test_email_settings_import_and_defaults():
    settings = EmailSettings()
    assert settings.MAIL_SERVER
    assert settings.MAIL_PORT > 0
