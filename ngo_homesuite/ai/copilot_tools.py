from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import func

from ngo_homesuite.models.core import Donation, Donor, Expense, Organization, db
from ngo_homesuite.services.reporting_service import ReportingService


@dataclass
class CopilotTool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any], dict[str, Any]], Any]
    requires_approval: bool = False
    mutates_state: bool = False


class CopilotToolRegistry:
    def __init__(self) -> None:
        self.reporting_service = ReportingService()
        self._tools = {
            "list_recent_donations": CopilotTool(
                name="list_recent_donations",
                description="List recent donations for the current organization.",
                schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10}
                    },
                },
                handler=self._list_recent_donations,
            ),
            "search_donors": CopilotTool(
                name="search_donors",
                description="Search donors by name/email/phone.",
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 10},
                    },
                    "required": ["query"],
                },
                handler=self._search_donors,
            ),
            "organization_financial_summary": CopilotTool(
                name="organization_financial_summary",
                description="Return a quick financial summary for the current organization.",
                schema={"type": "object", "properties": {}},
                handler=self._organization_financial_summary,
            ),
            "generate_report": CopilotTool(
                name="generate_report",
                description="Generate a report payload via the reporting service.",
                schema={
                    "type": "object",
                    "properties": {
                        "report_type": {"type": "string"},
                        "params": {"type": "object"},
                    },
                    "required": ["report_type"],
                },
                handler=self._generate_report,
            ),
            "create_donor": CopilotTool(
                name="create_donor",
                description="Create a donor profile in the current organization.",
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "email": {"type": "string"},
                        "phone": {"type": "string"},
                        "donor_type": {
                            "type": "string",
                            "enum": ["individual", "corporate", "foundation", "anonymous"],
                            "default": "individual",
                        },
                        "notes": {"type": "string"},
                    },
                    "required": ["name"],
                },
                handler=self._create_donor,
                requires_approval=True,
                mutates_state=True,
            ),
            "export_donors_snapshot": CopilotTool(
                name="export_donors_snapshot",
                description="Prepare a CSV snapshot payload of donors in the current organization.",
                schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}
                    },
                },
                handler=self._export_donors_snapshot,
                requires_approval=True,
                mutates_state=False,
            ),
        }

    def list_tools(self) -> list[CopilotTool]:
        return list(self._tools.values())

    def get_ollama_tool_specs(self, allowlist: set[str] | None = None) -> list[dict[str, Any]]:
        specs = []
        allowed = set(allowlist or self._tools.keys())
        for tool in self._tools.values():
            if tool.name not in allowed:
                continue
            specs.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.schema,
                    },
                }
            )
        return specs

    def get_tool(self, name: str) -> CopilotTool | None:
        return self._tools.get(name)

    def parse_tool_list(self, raw: str | list[str] | tuple[str, ...] | set[str] | None) -> set[str]:
        if raw is None:
            return set(self._tools.keys())
        if isinstance(raw, str):
            candidates = [p.strip() for p in raw.split(",") if p.strip()]
        else:
            candidates = [str(p).strip() for p in raw if str(p).strip()]
        return {name for name in candidates if name in self._tools}

    def execute(self, name: str, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")
        return tool.handler(args, runtime_ctx)

    def _org_filter(self, runtime_ctx: dict[str, Any]):
        org_id = runtime_ctx.get("organization_id")
        if org_id is None:
            return None
        return int(org_id)

    def _list_recent_donations(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        limit = int(args.get("limit", 10))
        org_id = self._org_filter(runtime_ctx)
        if org_id is None:
            return []
        rows = (
            Donation.query.filter_by(organization_id=org_id)
            .order_by(Donation.donation_date.desc())
            .limit(max(1, min(limit, 50)))
            .all()
        )
        return [
            {
                "id": d.id,
                "donor_name": d.donor_name,
                "amount": float(d.amount or 0),
                "currency": d.currency,
                "date": d.donation_date.isoformat() if d.donation_date else None,
                "status": d.status,
            }
            for d in rows
        ]

    def _search_donors(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        q = str(args.get("query", "")).strip()
        limit = int(args.get("limit", 10))
        org_id = self._org_filter(runtime_ctx)
        if not q or org_id is None:
            return []

        like = f"%{q}%"
        rows = (
            Donor.query.filter_by(organization_id=org_id)
            .filter((Donor.name.ilike(like)) | (Donor.email.ilike(like)) | (Donor.phone.ilike(like)))
            .order_by(Donor.name.asc())
            .limit(max(1, min(limit, 50)))
            .all()
        )
        return [
            {
                "id": d.id,
                "name": d.name,
                "email": d.email,
                "phone": d.phone,
                "donor_type": d.donor_type,
            }
            for d in rows
        ]

    def _organization_financial_summary(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        if org_id is None:
            return {}

        org = Organization.query.filter_by(id=org_id).first()
        total_donations = db.session.query(func.sum(Donation.amount)).filter_by(organization_id=org_id).scalar() or 0
        total_expenses = db.session.query(func.sum(Expense.amount)).filter_by(organization_id=org_id).scalar() or 0
        donor_count = Donor.query.filter_by(organization_id=org_id).count()

        return {
            "organization": org.name if org else None,
            "total_donations": float(total_donations),
            "total_expenses": float(total_expenses),
            "net": float(total_donations - total_expenses),
            "donor_count": donor_count,
        }

    def _generate_report(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        report_type = str(args.get("report_type", "")).strip()
        params = args.get("params") if isinstance(args.get("params"), dict) else {}
        if not report_type:
            return {"error": "report_type is required"}

        actor = runtime_ctx.get("actor") or "copilot"
        try:
            return self.reporting_service.generate_report(report_type, params=params, actor=actor)
        except Exception as exc:
            return {"error": str(exc), "report_type": report_type}

    def _create_donor(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}

        name = str(args.get("name", "")).strip()
        if not name:
            return {"error": "name is required"}

        donor = Donor(
            organization_id=org_id,
            name=name,
            email=(str(args.get("email", "")).strip() or None),
            phone=(str(args.get("phone", "")).strip() or None),
            donor_type=str(args.get("donor_type", "individual")).strip() or "individual",
            notes=(str(args.get("notes", "")).strip() or None),
        )
        db.session.add(donor)
        db.session.commit()

        return {
            "id": donor.id,
            "name": donor.name,
            "email": donor.email,
            "phone": donor.phone,
            "donor_type": donor.donor_type,
            "created": True,
        }

    def _export_donors_snapshot(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}

        limit = max(1, min(int(args.get("limit", 100)), 500))
        rows = (
            Donor.query.filter_by(organization_id=org_id)
            .order_by(Donor.name.asc())
            .limit(limit)
            .all()
        )

        header = "id,name,email,phone,donor_type"
        body = [
            f'{d.id},"{(d.name or "").replace("\"", "\"\"")}","{(d.email or "").replace("\"", "\"\"")}","{(d.phone or "").replace("\"", "\"\"")}","{(d.donor_type or "").replace("\"", "\"\"")}"'
            for d in rows
        ]
        csv_payload = "\n".join([header] + body)
        return {
            "row_count": len(rows),
            "content_type": "text/csv",
            "filename": "donors_snapshot.csv",
            "csv": csv_payload,
        }
