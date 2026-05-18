from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizedIdentity:
    provider: str
    provider_user_id: str
    email: str
    email_normalized: str
    display_name: str

    @classmethod
    def from_oauth(
        cls,
        *,
        provider: str,
        provider_user_id: str,
        email: str,
        display_name: str | None = None,
    ) -> "NormalizedIdentity":
        normalized_provider = str(provider or "").strip().lower()
        normalized_uid = str(provider_user_id or "").strip()
        normalized_email = str(email or "").strip().lower()
        normalized_name = str(display_name or "").strip()

        if not normalized_provider:
            raise ValueError("provider is required")
        if not normalized_uid:
            raise ValueError("provider_user_id is required")
        if not normalized_email:
            raise ValueError("email is required")

        return cls(
            provider=normalized_provider,
            provider_user_id=normalized_uid,
            email=normalized_email,
            email_normalized=normalized_email,
            display_name=normalized_name,
        )
