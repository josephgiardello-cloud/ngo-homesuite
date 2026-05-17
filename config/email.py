from __future__ import annotations

import os

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class EmailSettings(BaseSettings):
        MAIL_SERVER: str = "smtp.sendgrid.net"
        MAIL_PORT: int = 587
        MAIL_USE_TLS: bool = True
        MAIL_USERNAME: str = ""
        MAIL_PASSWORD: str = ""
        MAIL_DEFAULT_SENDER: str = "noreply@ngohomesuite.local"

        model_config = SettingsConfigDict(env_prefix="MAIL_")

except Exception:  # pragma: no cover
    class EmailSettings:
        """Lightweight fallback when pydantic-settings is unavailable."""

        def __init__(self) -> None:
            self.MAIL_SERVER = os.environ.get("MAIL_SERVER", "smtp.sendgrid.net")
            self.MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
            self.MAIL_USE_TLS = os.environ.get("MAIL_USE_TLS", "true").strip().lower() in {"1", "true", "yes", "on"}
            self.MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
            self.MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
            self.MAIL_DEFAULT_SENDER = os.environ.get("MAIL_DEFAULT_SENDER", "noreply@ngohomesuite.local")
