"""Service helpers for organization lookups."""

from __future__ import annotations

from typing import Optional
from sqlalchemy import select

from ngo_homesuite.models.core import Organization, db


def get_first_active_organization() -> Optional[Organization]:
    stmt = select(Organization).where(Organization.is_active == True).order_by(Organization.id.asc()).limit(1)
    return db.session.scalars(stmt).first()
