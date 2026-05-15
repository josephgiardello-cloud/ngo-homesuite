"""PII pre-redaction hook for AI prompts.

Replaces common PII patterns with placeholder tokens before sending
text to an external AI provider.  The redaction is intentionally
conservative — it is better to redact a false-positive than to leak
real data.
"""

from __future__ import annotations

import logging
import re
from typing import Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Redaction patterns
# Each entry is (name, compiled_pattern, replacement_token)
# ---------------------------------------------------------------------------

_PATTERNS: list[Tuple[str, re.Pattern[str], str]] = [
    # Email addresses
    (
        "email",
        re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
    # US/international phone numbers (various formats)
    (
        "phone",
        re.compile(
            r"""
            (?:
                \+?1[-.\s]?        # optional country code
            )?
            (?:\(\d{3}\)|\d{3})   # area code
            [-.\s]?
            \d{3}
            [-.\s]?
            \d{4}
            \b
            """,
            re.VERBOSE,
        ),
        "[REDACTED_PHONE]",
    ),
    # US Social Security Numbers (123-45-6789, 123456789)
    (
        "ssn",
        re.compile(r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b"),
        "[REDACTED_SSN]",
    ),
    # Credit card numbers (13–19 digits, optionally separated by spaces/dashes)
    (
        "credit_card",
        re.compile(r"\b(?:\d[ -]?){13,19}\b"),
        "[REDACTED_CC]",
    ),
    # US ZIP codes (5-digit + optional dash-4)
    (
        "zip_code",
        re.compile(r"\b\d{5}(?:-\d{4})?\b"),
        "[REDACTED_ZIP]",
    ),
]


def redact_pii(text: str) -> Tuple[str, int]:
    """Redact PII from *text*.

    Returns:
        A tuple of (redacted_text, count_of_replacements_made).
    """
    total_replacements = 0
    result = text
    for name, pattern, token in _PATTERNS:
        new_result, n = pattern.subn(token, result)
        if n:
            logger.info("PII redaction: replaced %d %s instance(s)", n, name)
            total_replacements += n
        result = new_result
    return result, total_replacements
