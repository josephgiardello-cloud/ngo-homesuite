"""Beneficiary management service with organization-scoped CRUD operations."""
from __future__ import annotations

from typing import Dict, List, Optional

from sqlalchemy import func, select
from werkzeug.exceptions import NotFound

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
    return db.session.scalars(
        select(Beneficiary).where(Beneficiary.id == beneficiary_id, Beneficiary.organization_id == organization_id).limit(1)
    ).first()


def list_beneficiaries(
    organization_id: int,
    *,
    program: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Beneficiary]:
    stmt = select(Beneficiary).where(Beneficiary.organization_id == organization_id)
    if program:
        stmt = stmt.where(Beneficiary.program == program)
    if status:
        stmt = stmt.where(Beneficiary.status == status)
    stmt = stmt.order_by(Beneficiary.created_at.desc())
    return list(db.session.scalars(stmt))


def update_beneficiary(beneficiary_id: int, organization_id: int, **fields) -> Beneficiary:
    beneficiary = db.session.scalars(
        select(Beneficiary).where(Beneficiary.id == beneficiary_id, Beneficiary.organization_id == organization_id).limit(1)
    ).first()
    if beneficiary is None:
        raise NotFound()
    allowed = {
        "first_name", "last_name", "email", "phone", "country", "city", "address", "program", "status", "notes"
    }
    for key, value in fields.items():
        if key in allowed:
            setattr(beneficiary, key, value)
    db.session.commit()
    return beneficiary


def delete_beneficiary(beneficiary_id: int, organization_id: int) -> None:
    beneficiary = db.session.scalars(
        select(Beneficiary).where(Beneficiary.id == beneficiary_id, Beneficiary.organization_id == organization_id).limit(1)
    ).first()
    if beneficiary is None:
        raise NotFound()
    db.session.delete(beneficiary)
    db.session.commit()


def beneficiary_program_summary(organization_id: int) -> Dict[str, int]:
    rows = db.session.connection().exec_driver_sql(
        str(select(
            func.coalesce(Beneficiary.program, "Unassigned").label("program"),
            func.count(Beneficiary.id).label("count"),
        ).where(Beneficiary.organization_id == organization_id).group_by(func.coalesce(Beneficiary.program, "Unassigned")).compile(compile_kwargs={"literal_binds": True}))
    ).all()
    return {row.program: int(row.count) for row in rows}
