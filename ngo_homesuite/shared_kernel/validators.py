"""Shared validation utilities for NGO HomeSuite.

Ported and adapted from the TONY diagnostic tool (josephgiardello-cloud/Tony).
Provides EIN format validation for NGO organization and funder data.
"""

from __future__ import annotations

import re
from typing import Iterable, Set


_EIN_RE = re.compile(r"^\d{2}-\d{7}$")


class ValidationError(ValueError):
    """Raised when input fails domain validation."""


def validate_ein(ein: str) -> None:
    """Validate US Employer Identification Number format (NN-NNNNNNN).

    Raises:
        ValidationError: if the EIN does not match the expected format.
    """
    if not _EIN_RE.match(ein):
        raise ValidationError(
            f"Invalid EIN format: {ein!r} — expected XX-XXXXXXX (e.g. 12-3456789)"
        )
