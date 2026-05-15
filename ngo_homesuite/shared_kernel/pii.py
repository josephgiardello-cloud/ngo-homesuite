from __future__ import annotations

from typing import Any


_SENSITIVE_KEYS = {
    "email",
    "phone",
    "ssn",
    "social_security_number",
    "tax_id",
    "address",
    "street",
    "dob",
    "date_of_birth",
    "bank_account",
    "card_number",
}


def redact_value(value: Any) -> str:
    text = str(value)
    if not text:
        return "[REDACTED]"
    if len(text) <= 4:
        return "[REDACTED]"
    return f"[REDACTED:{text[-4:]}]"


def redact_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        redacted: dict[str, Any] = {}
        for key, value in payload.items():
            if str(key).lower() in _SENSITIVE_KEYS:
                redacted[key] = redact_value(value)
            else:
                redacted[key] = redact_payload(value)
        return redacted
    if isinstance(payload, list):
        return [redact_payload(item) for item in payload]
    return payload
