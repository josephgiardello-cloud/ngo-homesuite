"""Service layer for project operations."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import or_, select

from ngo_homesuite.models.core import Project, db


class ProjectNotFound(Exception):
    pass


class ProjectService:
    def list_all_projects(
        self,
        org_id: int,
        *,
        search: Optional[str] = None,
        status: Optional[str] = None,
    ) -> list[Project]:
        stmt = select(Project).where(Project.organization_id == org_id)
        if search:
            like_term = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Project.name.ilike(like_term),
                    Project.program.ilike(like_term),
                    Project.description.ilike(like_term),
                )
            )
        if status:
            stmt = stmt.where(Project.status == status)
        stmt = stmt.order_by(Project.name.asc(), Project.id.asc())
        return list(db.session.scalars(stmt))

    def get_project(self, project_id: int, org_id: int) -> Project:
        stmt = select(Project).where(Project.id == project_id, Project.organization_id == org_id).limit(1)
        project = db.session.scalars(stmt).first()
        if project is None:
            raise ProjectNotFound(f"Project {project_id} not found for org {org_id}")
        return project

    def create_project(
        self,
        org_id: int,
        *,
        name: str,
        description: Optional[str],
        program: Optional[str],
        budget: float,
        spent: float,
        currency: str,
        status: str,
    ) -> Project:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ValueError("Project name is required")

        project = Project(
            organization_id=org_id,
            name=clean_name,
            description=(description or "").strip() or None,
            program=(program or "").strip() or None,
            budget=float(budget),
            spent=float(spent),
            currency=(currency or "USD").upper(),
            status=status,
        )
        db.session.add(project)
        db.session.commit()
        return project

    def update_project(
        self,
        project_id: int,
        org_id: int,
        **fields,
    ) -> Project:
        project = self.get_project(project_id, org_id)
        mutable = {"name", "description", "program", "budget", "spent", "currency", "status"}
        for key, value in fields.items():
            if key not in mutable:
                raise ValueError(f"Field {key!r} is not updatable via this method")
            if key == "name":
                value = (value or "").strip()
                if not value:
                    raise ValueError("Project name cannot be blank")
            if key == "currency":
                value = (value or "USD").upper()
            setattr(project, key, value)
        db.session.commit()
        return project
