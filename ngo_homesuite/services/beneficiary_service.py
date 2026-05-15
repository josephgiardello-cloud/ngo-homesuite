"""Beneficiary management service with organization-scoped CRUD operations."""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import func

from ngo_homesuite.models.core import Beneficiary, db


def create_beneficiary(
    organization_id: int,
    first_name: str,
    last_name: str,
    *,
    email: Optional[str] = None,
    phone: Optional[str] = None,
    country: Optional[str] = None,
    city: Optional[str] = None,
    address: Optional[str] = None,
    program: Optional[str] = None,
    status: str = "active",
    notes: Optional[str] = None,
) -> Beneficiary:
    beneficiary = Beneficiary(
        organization_id=organization_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        phone=phone,
        country=country,
        city=city,
        address=address,
        program=program,
        status=status,
        notes=notes,
    )
    db.session.add(beneficiary)
    db.session.commit()
    return beneficiary


def get_beneficiary(beneficiary_id: int, organization_id: int) -> Optional[Beneficiary]:
    return Beneficiary.query.filter_by(id=beneficiary_id, organization_id=organization_id).first()


def list_beneficiaries(
    organization_id: int,
    *,
    program: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Beneficiary]:
    q = Beneficiary.query.filter_by(organization_id=organization_id)
    if program:
        q = q.filter_by(program=program)
    if status:
        q = q.filter_by(status=status)
    return q.order_by(Beneficiary.created_at.desc()).all()


def update_beneficiary(beneficiary_id: int, organization_id: int, **fields) -> Beneficiary:
    beneficiary = Beneficiary.query.filter_by(id=beneficiary_id, organization_id=organization_id).first_or_404()
    allowed = {
        "first_name", "last_name", "email", "phone", "country", "city", "address", "program", "status", "notes"
    }
    for key, value in fields.items():
        if key in allowed:
            setattr(beneficiary, key, value)
    db.session.commit()
    return beneficiary


def delete_beneficiary(beneficiary_id: int, organization_id: int) -> None:
    beneficiary = Beneficiary.query.filter_by(id=beneficiary_id, organization_id=organization_id).first_or_404()
    db.session.delete(beneficiary)
    db.session.commit()


def beneficiary_program_summary(organization_id: int) -> Dict[str, int]:
    rows = (
        db.session.query(
            func.coalesce(Beneficiary.program, "Unassigned").label("program"),
            func.count(Beneficiary.id).label("count"),
        )
        .filter(Beneficiary.organization_id == organization_id)
        .group_by(func.coalesce(Beneficiary.program, "Unassigned"))
        .all()
    )
    return {row.program: int(row.count) for row in rows}
