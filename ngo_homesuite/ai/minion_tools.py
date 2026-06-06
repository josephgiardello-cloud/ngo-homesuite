from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from dataclasses import dataclass
from typing import Any, Callable

from sqlalchemy import bindparam, func, inspect, text

from ngo_homesuite.models.core import Donation, Donor, Expense, Organization, db
from ngo_homesuite.services.bank_reconciliation_service import BankReconciliationService
from ngo_homesuite.services.opinionated_workflows import run_donation_receipt_followup_workflow
from ngo_homesuite.services.reporting_service import ReportingService


@dataclass
class MinionTool:
    name: str
    description: str
    schema: dict[str, Any]
    handler: Callable[[dict[str, Any], dict[str, Any]], Any]
    requires_approval: bool = False
    mutates_state: bool = False


class MinionToolRegistry:
    def __init__(self) -> None:
        self.reporting_service = ReportingService()
        self.bank_reconciliation_service = BankReconciliationService()
        self._relationship_schema_cache: dict[str, bool] | None = None
        self._tools = {
            "list_recent_donations": MinionTool(
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
            "search_donors": MinionTool(
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
            "donor_profile_insights": MinionTool(
                name="donor_profile_insights",
                description="Build a donor intelligence snapshot with RFM-based predictive scoring and next-step guidance.",
                schema={
                    "type": "object",
                    "properties": {
                        "donor_id": {"type": "integer", "minimum": 1},
                    },
                    "required": ["donor_id"],
                },
                handler=self._donor_profile_insights,
            ),
            "summarize_donor": MinionTool(
                name="summarize_donor",
                description="Generate a natural-language donor summary with risk flags and next best action.",
                schema={
                    "type": "object",
                    "properties": {
                        "donor_id": {"type": "integer", "minimum": 1},
                    },
                    "required": ["donor_id"],
                },
                handler=self._summarize_donor,
            ),
            "summarize_activity_timeline": MinionTool(
                name="summarize_activity_timeline",
                description="Summarize a unified activity feed and recommend next actions for staff.",
                schema={
                    "type": "object",
                    "properties": {
                        "entity_type": {"type": "string", "enum": ["donor", "beneficiary", "volunteer", "organization"]},
                        "entity_id": {"type": "integer", "minimum": 1},
                        "activity_type": {"type": "string"},
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 40},
                    },
                },
                handler=self._summarize_activity_timeline,
            ),
            "find_similar_donors": MinionTool(
                name="find_similar_donors",
                description="Find donors with similar giving behavior and profile signals to a reference donor.",
                schema={
                    "type": "object",
                    "properties": {
                        "donor_id": {"type": "integer", "minimum": 1},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5},
                    },
                    "required": ["donor_id"],
                },
                handler=self._find_similar_donors,
            ),
            "rank_donors_for_outreach": MinionTool(
                name="rank_donors_for_outreach",
                description="Return next donors to call ranked by predicted value and risk.",
                schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
                    },
                },
                handler=self._rank_donors_for_outreach,
            ),
            "suggest_outreach_targets": MinionTool(
                name="suggest_outreach_targets",
                description="Suggest top donor outreach targets with concise rationale and next-step recommendations.",
                schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 25, "default": 10},
                    },
                },
                handler=self._suggest_outreach_targets,
            ),
            "draft_personalized_appeal": MinionTool(
                name="draft_personalized_appeal",
                description="Draft a personalized donor outreach email grounded in giving history and risk signals.",
                schema={
                    "type": "object",
                    "properties": {
                        "donor_id": {"type": "integer", "minimum": 1},
                        "campaign_name": {"type": "string"},
                        "ask_amount": {"type": "number", "minimum": 0},
                    },
                    "required": ["donor_id"],
                },
                handler=self._draft_personalized_appeal,
            ),
            "organization_financial_summary": MinionTool(
                name="organization_financial_summary",
                description="Return a quick financial summary for the current organization.",
                schema={"type": "object", "properties": {}},
                handler=self._organization_financial_summary,
            ),
            "generate_report": MinionTool(
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
            "generate_grant_report_draft": MinionTool(
                name="generate_grant_report_draft",
                description="Generate a grant report draft payload and a short narrative summary.",
                schema={
                    "type": "object",
                    "properties": {
                        "report_type": {"type": "string", "default": "grant_pipeline"},
                        "params": {"type": "object"},
                    },
                },
                handler=self._generate_grant_report_draft,
            ),
            "create_donor": MinionTool(
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
            "export_donors_snapshot": MinionTool(
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
            "run_reconciliation": MinionTool(
                name="run_reconciliation",
                description="Run a reconciliation workflow between a bank statement reference and ledger reference.",
                schema={
                    "type": "object",
                    "properties": {
                        "bank_statement_ref": {"type": "string"},
                        "ledger_ref": {"type": "string"},
                    },
                    "required": ["bank_statement_ref", "ledger_ref"],
                },
                handler=self._run_reconciliation,
                requires_approval=True,
                mutates_state=True,
            ),
            "execute_donation_followup_workflow": MinionTool(
                name="execute_donation_followup_workflow",
                description="Execute donation receipt and follow-up workflow for a donation.",
                schema={
                    "type": "object",
                    "properties": {
                        "donation_id": {"type": "integer", "minimum": 1},
                    },
                    "required": ["donation_id"],
                },
                handler=self._execute_donation_followup_workflow,
                requires_approval=True,
                mutates_state=True,
            ),
            # ---- Engagement Scoring & AI Donor Insights ----
            "get_donor_engagement_score": MinionTool(
                name="get_donor_engagement_score",
                description=(
                    "Return the persisted engagement score (0â€“100) for a donor, broken down into "
                    "recency / frequency / monetary / engagement dimensions with segment and "
                    "cultivation priority. Computes a fresh score if none exists."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "donor_id": {"type": "integer", "minimum": 1},
                    },
                    "required": ["donor_id"],
                },
                handler=self._get_donor_engagement_score,
            ),
            "get_ai_donor_recommendations": MinionTool(
                name="get_ai_donor_recommendations",
                description=(
                    "Generate AI-powered next-step recommendations for a specific donor using their "
                    "engagement score, RFM signals, and giving history. Returns suggested outreach "
                    "channel, ask amount, message tone, and timing â€” all computed locally with Ollama."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "donor_id": {"type": "integer", "minimum": 1},
                    },
                    "required": ["donor_id"],
                },
                handler=self._get_ai_donor_recommendations,
            ),
            "list_at_risk_donors": MinionTool(
                name="list_at_risk_donors",
                description=(
                    "Return lapsed and at-risk donors ranked by engagement score descending "
                    "(highest-value donors to reactivate first), with recommended next action."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 20},
                    },
                },
                handler=self._list_at_risk_donors,
            ),
            "evaluate_smart_group": MinionTool(
                name="evaluate_smart_group",
                description=(
                    "Evaluate a saved Smart Group / Dynamic Audience and return current matching "
                    "donors with key stats. Pass group_id to query a saved group."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "group_id": {"type": "integer", "minimum": 1},
                    },
                    "required": ["group_id"],
                },
                handler=self._evaluate_smart_group,
            ),
            "summarize_program_impact": MinionTool(
                name="summarize_program_impact",
                description=(
                    "Return a program impact summary across all cases, with outcome metric "
                    "averages, case counts by status, and narrative context."
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "case_type": {"type": "string"},
                        "program_name": {"type": "string"},
                    },
                },
                handler=self._summarize_program_impact,
            ),
        }

    def _relationship_schema(self) -> dict[str, bool]:
        if self._relationship_schema_cache is not None:
            return self._relationship_schema_cache

        inspector = inspect(db.engine)
        table_names = set(inspector.get_table_names())
        schema: dict[str, bool] = {}
        for table_name in ("interactions", "pledges", "registrations"):
            if table_name not in table_names:
                continue
            columns = {str(col.get("name")) for col in inspector.get_columns(table_name)}
            schema[table_name] = "organization_id" in columns

        self._relationship_schema_cache = schema
        return schema

    def _bulk_donor_relationship_counts(self, donor_ids: list[int], org_id: int) -> dict[int, dict[str, int]]:
        unique_ids = [donor_id for donor_id in dict.fromkeys(int(d) for d in donor_ids) if donor_id > 0]
        counts = {donor_id: {"interactions": 0, "pledges": 0, "events": 0} for donor_id in unique_ids}
        if not unique_ids:
            return {}

        schema = self._relationship_schema()
        table_to_metric = {
            "interactions": "interactions",
            "pledges": "pledges",
            "registrations": "events",
        }

        for table_name, metric_name in table_to_metric.items():
            if table_name not in schema:
                continue

            query = f"SELECT donor_id, COUNT(*) AS count FROM {table_name} WHERE donor_id IN :donor_ids"
            params: dict[str, Any] = {"donor_ids": unique_ids}
            if schema[table_name]:
                query += " AND organization_id = :org_id"
                params["org_id"] = org_id
            query += " GROUP BY donor_id"

            stmt = text(query).bindparams(bindparam("donor_ids", expanding=True))
            for donor_id, count in db.session.execute(stmt, params):
                donor_key = int(donor_id)
                if donor_key in counts:
                    counts[donor_key][metric_name] = int(count or 0)

        return counts

    def _optional_donor_relationship_counts(self, donor_id: int, org_id: int) -> dict[str, int]:
        return self._bulk_donor_relationship_counts([int(donor_id)], org_id).get(
            int(donor_id),
            {"interactions": 0, "pledges": 0, "events": 0},
        )

    def _compute_rfm_signals(self, donor: Donor, org_id: int) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        lookback = now - timedelta(days=365)
        rows = (
            Donation.query.filter_by(organization_id=org_id, donor_id=donor.id)
            .order_by(Donation.donation_date.desc())
            .all()
        )

        latest = rows[0].donation_date if rows else None
        recency_days = 9999 if latest is None else max(0, (now - latest).days)
        in_year = [d for d in rows if d.donation_date and d.donation_date >= lookback]
        frequency_12m = len(in_year)
        monetary_12m = float(sum(float(d.amount or 0.0) for d in in_year))
        lifetime_total = float(sum(float(d.amount or 0.0) for d in rows))
        max_single_gift = float(max((float(d.amount or 0.0) for d in rows), default=0.0))

        if recency_days <= 30:
            recency_score = 100
        elif recency_days <= 90:
            recency_score = 75
        elif recency_days <= 180:
            recency_score = 50
        elif recency_days <= 365:
            recency_score = 25
        else:
            recency_score = 10

        if frequency_12m >= 12:
            frequency_score = 100
        elif frequency_12m >= 6:
            frequency_score = 80
        elif frequency_12m >= 3:
            frequency_score = 60
        elif frequency_12m >= 1:
            frequency_score = 35
        else:
            frequency_score = 10

        if monetary_12m >= 10000:
            monetary_score = 100
        elif monetary_12m >= 5000:
            monetary_score = 85
        elif monetary_12m >= 2500:
            monetary_score = 70
        elif monetary_12m >= 1000:
            monetary_score = 55
        elif monetary_12m > 0:
            monetary_score = 35
        else:
            monetary_score = 10

        giving_likelihood = round((0.4 * recency_score) + (0.3 * frequency_score) + (0.3 * monetary_score), 1)
        churn_risk = round(100 - (0.6 * recency_score + 0.25 * frequency_score + 0.15 * monetary_score), 1)
        major_gift_potential = round(min(100.0, (0.6 * monetary_score) + (0.4 * min(100.0, max_single_gift / 100))), 1)

        return {
            "last_donation_at": latest.isoformat() if latest else None,
            "recency_days": recency_days,
            "frequency_12m": frequency_12m,
            "monetary_12m": round(monetary_12m, 2),
            "lifetime_total": round(lifetime_total, 2),
            "max_single_gift": round(max_single_gift, 2),
            "rfm": {
                "recency_score": recency_score,
                "frequency_score": frequency_score,
                "monetary_score": monetary_score,
            },
            "predictions": {
                "giving_likelihood": giving_likelihood,
                "churn_risk": churn_risk,
                "major_gift_potential": major_gift_potential,
            },
        }

    def _insight_summary_text(self, donor: Donor, metrics: dict[str, Any], related: dict[str, int]) -> str:
        recency = metrics["recency_days"]
        likelihood = metrics["predictions"]["giving_likelihood"]
        churn = metrics["predictions"]["churn_risk"]
        major = metrics["predictions"]["major_gift_potential"]
        lifetime = metrics["lifetime_total"]

        if recency > 180 and lifetime >= 1000:
            status = "lapsed donor with strong reactivation potential"
        elif churn >= 65:
            status = "at-risk donor requiring quick follow-up"
        elif likelihood >= 70:
            status = "high-probability near-term donor"
        else:
            status = "steady donor with moderate opportunity"

        return (
            f"{donor.name} appears to be a {status}. "
            f"Last gift was {recency} days ago, 12-month giving is ${metrics['monetary_12m']:.2f} across "
            f"{metrics['frequency_12m']} gifts, and lifetime giving is ${lifetime:.2f}. "
            f"Model signals: give likelihood {likelihood}/100, churn risk {churn}/100, major gift potential {major}/100. "
            f"Related activity counts: interactions={related['interactions']}, pledges={related['pledges']}, events={related['events']}."
        )

    def _recommended_actions(self, metrics: dict[str, Any], related: dict[str, int]) -> list[str]:
        recency = metrics["recency_days"]
        churn = metrics["predictions"]["churn_risk"]
        major = metrics["predictions"]["major_gift_potential"]
        actions: list[str] = []

        if recency > 120 or churn >= 60:
            actions.append("Schedule a personal check-in call within 7 days.")
        if related["interactions"] == 0:
            actions.append("Log a new interaction after outreach to maintain relationship context.")
        if major >= 70:
            actions.append("Prepare a tailored major-gift ask anchored to recent impact outcomes.")
        if metrics["frequency_12m"] <= 1:
            actions.append("Offer a recurring giving option with a low-friction monthly amount.")
        if not actions:
            actions.append("Send a stewardship update and confirm next engagement milestone.")
        return actions

    def _risk_flags(self, metrics: dict[str, Any], related: dict[str, int]) -> list[str]:
        flags: list[str] = []
        if metrics["recency_days"] > 180:
            flags.append("No gift in over 180 days")
        if metrics["predictions"]["churn_risk"] >= 65:
            flags.append("Elevated churn risk")
        if metrics["frequency_12m"] <= 1:
            flags.append("Low giving frequency in last 12 months")
        if related["interactions"] == 0:
            flags.append("No recorded interactions")
        return flags

    def _similarity_score(
        self,
        anchor: Donor,
        anchor_metrics: dict[str, Any],
        candidate: Donor,
        candidate_metrics: dict[str, Any],
    ) -> float:
        recency_delta = abs(anchor_metrics["recency_days"] - candidate_metrics["recency_days"])
        frequency_delta = abs(anchor_metrics["frequency_12m"] - candidate_metrics["frequency_12m"])
        monetary_delta = abs(anchor_metrics["monetary_12m"] - candidate_metrics["monetary_12m"])
        type_bonus = 8.0 if (anchor.donor_type or "") == (candidate.donor_type or "") else 0.0

        recency_component = max(0.0, 35.0 - min(35.0, recency_delta / 5.0))
        frequency_component = max(0.0, 30.0 - min(30.0, frequency_delta * 4.0))
        monetary_component = max(0.0, 27.0 - min(27.0, monetary_delta / 150.0))
        return round(min(100.0, recency_component + frequency_component + monetary_component + type_bonus), 1)

    def list_tools(self) -> list[MinionTool]:
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

    def get_tool(self, name: str) -> MinionTool | None:
        return self._tools.get(name)

    def parse_tool_list(
        self,
        raw: str | list[str] | tuple[str, ...] | set[str] | None,
        *,
        default_all: bool = True,
    ) -> set[str]:
        if raw is None:
            return set(self._tools.keys()) if default_all else set()
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

    def _donor_profile_insights(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        donor_id = int(args.get("donor_id", 0) or 0)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}
        if donor_id <= 0:
            return {"error": "donor_id is required"}

        donor = Donor.query.filter_by(organization_id=org_id, id=donor_id).first()
        if donor is None:
            return {"error": f"donor {donor_id} not found"}

        metrics = self._compute_rfm_signals(donor, org_id)
        related = self._optional_donor_relationship_counts(donor.id, org_id)
        summary = self._insight_summary_text(donor, metrics, related)

        return {
            "donor": {
                "id": donor.id,
                "name": donor.name,
                "email": donor.email,
                "phone": donor.phone,
                "donor_type": donor.donor_type,
            },
            "metrics": metrics,
            "activity": related,
            "recommended_actions": self._recommended_actions(metrics, related),
            "insight_summary": summary,
        }

    def _summarize_donor(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        profile = self._donor_profile_insights(args, runtime_ctx)
        if isinstance(profile, dict) and profile.get("error"):
            return profile

        metrics = profile["metrics"]
        related = profile["activity"]
        actions = profile["recommended_actions"]
        risk_flags = self._risk_flags(metrics, related)
        return {
            "donor": profile["donor"],
            "summary": profile["insight_summary"],
            "risk_flags": risk_flags,
            "next_best_action": actions[0] if actions else "Send a stewardship update and confirm next engagement milestone.",
            "recommended_actions": actions,
            "signals": metrics["predictions"],
        }

    def _summarize_activity_timeline(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}

        from ngo_homesuite.services.activity_timeline_service import ActivityTimelineService

        entity_type = (str(args.get("entity_type") or "").strip().lower() or None)
        entity_id = int(args.get("entity_id", 0) or 0)
        activity_type = (str(args.get("activity_type") or "").strip().lower() or None)
        query = (str(args.get("query") or "").strip() or None)
        limit = max(1, min(int(args.get("limit", 40) or 40), 100))

        if entity_type == "donor" and entity_id > 0:
            items = ActivityTimelineService.get_donor_timeline(
                org_id,
                entity_id,
                limit=limit,
                offset=0,
                search_query=query,
            )
        elif entity_type == "beneficiary" and entity_id > 0:
            items = ActivityTimelineService.get_beneficiary_timeline(
                org_id,
                entity_id,
                limit=limit,
                offset=0,
                search_query=query,
            )
        else:
            items = ActivityTimelineService.get_organization_activity(
                org_id,
                limit=limit,
                offset=0,
                entity_type_filter=entity_type,
                activity_type_filter=activity_type,
                search_query=query,
            )

        by_type: dict[str, int] = {}
        by_entity: dict[str, int] = {}
        overdue_tasks = 0
        open_followups = 0
        now = datetime.now(timezone.utc).date().isoformat()

        for item in items:
            by_type[item.activity_type] = by_type.get(item.activity_type, 0) + 1
            by_entity[item.entity_type] = by_entity.get(item.entity_type, 0) + 1

            if item.activity_type == "task":
                status = str((item.metadata or {}).get("status") or "").lower()
                due_date = str((item.metadata or {}).get("due_date") or "")
                if status not in {"done", "cancelled"} and due_date and due_date[:10] < now:
                    overdue_tasks += 1

            if item.activity_type == "interaction":
                completed = bool((item.metadata or {}).get("completed"))
                follow_up_due = str((item.metadata or {}).get("follow_up_due") or "")
                if not completed and follow_up_due and follow_up_due[:10] < now:
                    open_followups += 1

        recommendations: list[str] = []
        if overdue_tasks > 0:
            recommendations.append(f"Resolve {overdue_tasks} overdue task(s) first to prevent service and stewardship slippage.")
        if open_followups > 0:
            recommendations.append(f"Close or reschedule {open_followups} overdue follow-up interaction(s).")
        if by_type.get("donation", 0) > 0 and by_type.get("interaction", 0) == 0:
            recommendations.append("Add stewardship interactions after recent donations to preserve relationship momentum.")
        if not recommendations:
            recommendations.append("Timeline is healthy; continue logging interactions and keep task due dates current.")

        summary = (
            f"Reviewed {len(items)} activity item(s). Top categories: "
            + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items(), key=lambda pair: pair[1], reverse=True)[:4])
        )

        return {
            "summary": summary,
            "recommended_actions": recommendations,
            "next_best_action": recommendations[0],
            "activity_totals": {
                "total": len(items),
                "by_type": by_type,
                "by_entity": by_entity,
                "overdue_tasks": overdue_tasks,
                "overdue_followups": open_followups,
            },
            "scope": {
                "entity_type": entity_type,
                "entity_id": entity_id if entity_id > 0 else None,
                "activity_type": activity_type,
                "query": query,
            },
        }

    def _find_similar_donors(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        donor_id = int(args.get("donor_id", 0) or 0)
        limit = max(1, min(int(args.get("limit", 5) or 5), 20))
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}
        if donor_id <= 0:
            return {"error": "donor_id is required"}

        anchor = Donor.query.filter_by(organization_id=org_id, id=donor_id).first()
        if anchor is None:
            return {"error": f"donor {donor_id} not found"}

        anchor_metrics = self._compute_rfm_signals(anchor, org_id)
        candidates = (
            Donor.query.filter_by(organization_id=org_id)
            .filter(Donor.id != anchor.id)
            .order_by(Donor.created_at.asc())
            .all()
        )

        scored: list[dict[str, Any]] = []
        for candidate in candidates:
            candidate_metrics = self._compute_rfm_signals(candidate, org_id)
            score = self._similarity_score(anchor, anchor_metrics, candidate, candidate_metrics)
            scored.append(
                {
                    "donor_id": candidate.id,
                    "donor_name": candidate.name,
                    "donor_type": candidate.donor_type,
                    "similarity_score": score,
                    "last_donation_at": candidate_metrics["last_donation_at"],
                    "lifetime_total": candidate_metrics["lifetime_total"],
                    "frequency_12m": candidate_metrics["frequency_12m"],
                }
            )

        scored.sort(key=lambda item: item["similarity_score"], reverse=True)
        return {
            "anchor_donor": {"id": anchor.id, "name": anchor.name},
            "matches": scored[:limit],
            "count": min(limit, len(scored)),
        }

    def _rank_donors_for_outreach(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}

        limit = max(1, min(int(args.get("limit", 10) or 10), 25))
        donors = Donor.query.filter_by(organization_id=org_id).order_by(Donor.created_at.asc()).all()
        related_counts = self._bulk_donor_relationship_counts([donor.id for donor in donors], org_id)

        ranked: list[dict[str, Any]] = []
        for donor in donors:
            metrics = self._compute_rfm_signals(donor, org_id)
            related = related_counts.get(donor.id, {"interactions": 0, "pledges": 0, "events": 0})
            score = round(
                (0.5 * metrics["predictions"]["giving_likelihood"]) +
                (0.3 * metrics["predictions"]["major_gift_potential"]) +
                (0.2 * metrics["predictions"]["churn_risk"]),
                1,
            )
            ranked.append(
                {
                    "donor_id": donor.id,
                    "donor_name": donor.name,
                    "priority_score": score,
                    "giving_likelihood": metrics["predictions"]["giving_likelihood"],
                    "churn_risk": metrics["predictions"]["churn_risk"],
                    "major_gift_potential": metrics["predictions"]["major_gift_potential"],
                    "last_donation_at": metrics["last_donation_at"],
                    "suggested_next_action": self._recommended_actions(metrics, related)[0],
                }
            )

        ranked.sort(key=lambda item: item["priority_score"], reverse=True)
        return {"recommended": ranked[:limit], "count": min(limit, len(ranked))}

    def _suggest_outreach_targets(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        ranking = self._rank_donors_for_outreach(args, runtime_ctx)
        if isinstance(ranking, dict) and ranking.get("error"):
            return ranking

        targets: list[dict[str, Any]] = []
        for item in ranking["recommended"]:
            rationale_parts: list[str] = []
            if item["giving_likelihood"] >= 70:
                rationale_parts.append("high near-term giving likelihood")
            if item["churn_risk"] >= 60:
                rationale_parts.append("elevated churn risk")
            if item["major_gift_potential"] >= 70:
                rationale_parts.append("major-gift potential")
            if not rationale_parts:
                rationale_parts.append("balanced engagement opportunity")

            targets.append(
                {
                    **item,
                    "rationale": ", ".join(rationale_parts),
                }
            )

        return {
            "targets": targets,
            "count": ranking["count"],
            "summary": f"Prepared {ranking['count']} outreach targets ranked by value, urgency, and relationship momentum.",
        }

    def _draft_personalized_appeal(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        donor_id = int(args.get("donor_id", 0) or 0)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}
        if donor_id <= 0:
            return {"error": "donor_id is required"}

        donor = Donor.query.filter_by(organization_id=org_id, id=donor_id).first()
        org = Organization.query.filter_by(id=org_id).first()
        if donor is None:
            return {"error": f"donor {donor_id} not found"}

        metrics = self._compute_rfm_signals(donor, org_id)
        campaign_name = str(args.get("campaign_name", "Community Impact Fund")).strip() or "Community Impact Fund"
        ask_amount = float(args.get("ask_amount") or max(50.0, round(metrics["max_single_gift"] * 0.6, 2) or 100.0))

        subject = f"{donor.name}, your support can accelerate {campaign_name}"
        body = (
            f"Hi {donor.name},\n\n"
            f"Thank you for your continued support of {(org.name if org else 'our nonprofit')}. "
            "In the last year, your contributions helped sustain critical programs for families we serve.\n\n"
            f"We are currently advancing {campaign_name}, and a gift of ${ask_amount:,.2f} would directly expand this work."
            f" Based on your past support, we believe this is a meaningful way to deepen your impact.\n\n"
            "Would you be open to a short call this week so we can share progress and next steps?\n\n"
            "With gratitude,\n"
            "Fundraising Team"
        )

        return {
            "donor_id": donor.id,
            "donor_name": donor.name,
            "campaign_name": campaign_name,
            "ask_amount": round(ask_amount, 2),
            "subject": subject,
            "body": body,
            "signals": metrics["predictions"],
        }

    def _generate_report(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        report_type = str(args.get("report_type", "")).strip()
        params = args.get("params") if isinstance(args.get("params"), dict) else {}
        if not report_type:
            return {"error": "report_type is required"}
        org_id = self._org_filter(runtime_ctx)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}

        actor = runtime_ctx.get("actor") or "minion"
        try:
            return self.reporting_service.generate_report(
                report_type,
                params=params,
                actor=actor,
                organization_id=org_id,
            )
        except Exception as exc:
            return {"error": str(exc), "report_type": report_type}

    def _generate_grant_report_draft(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        report_type = str(args.get("report_type", "grant_pipeline")).strip() or "grant_pipeline"
        params = args.get("params") if isinstance(args.get("params"), dict) else {}
        actor = runtime_ctx.get("actor") or "minion"
        org_id = self._org_filter(runtime_ctx)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}

        try:
            rows = self.reporting_service.generate_report(
                report_type,
                params=params,
                actor=actor,
                organization_id=org_id,
            )
        except Exception as exc:
            return {"error": str(exc), "report_type": report_type}

        return {
            "report_type": report_type,
            "row_ids": rows,
            "summary": f"Prepared grant report draft '{report_type}' with {len(rows)} rows.",
            "next_steps": [
                "Validate grant milestones and outcomes before submission.",
                "Attach supporting evidence from compliance exports.",
            ],
        }

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

    def _run_reconciliation(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        bank_statement_ref = str(args.get("bank_statement_ref", "")).strip()
        ledger_ref = str(args.get("ledger_ref", "")).strip()
        actor = str(runtime_ctx.get("actor") or "minion")
        if not bank_statement_ref or not ledger_ref:
            return {"error": "bank_statement_ref and ledger_ref are required"}

        result = self.bank_reconciliation_service.reconcile(
            bank_statement=bank_statement_ref,
            ledger=ledger_ref,
            actor=actor,
        )
        if isinstance(result, dict):
            status = str(result.get("status") or "reconciliation_completed")
        else:
            status = "reconciliation_started" if result is None else "reconciliation_completed"
        return {
            "ok": True,
            "status": status,
            "bank_statement_ref": bank_statement_ref,
            "ledger_ref": ledger_ref,
            "result": result,
        }

    def _execute_donation_followup_workflow(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        donation_id = int(args.get("donation_id", 0) or 0)
        actor = str(runtime_ctx.get("actor") or "minion")
        org_id = self._org_filter(runtime_ctx)
        if donation_id <= 0:
            return {"error": "donation_id is required"}
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}

        return run_donation_receipt_followup_workflow(
            donation_id=donation_id,
            actor=actor,
            organization_id=org_id,
        )

    # ------------------------------------------------------------------ #
    # Engagement Scoring & AI Donor Insights handlers
    # ------------------------------------------------------------------ #

    def _get_donor_engagement_score(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        donor_id = int(args.get("donor_id", 0) or 0)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}
        if donor_id <= 0:
            return {"error": "donor_id is required"}

        try:
            from ngo_homesuite.services.engagement_scoring_service import compute_score, get_score

            rec = get_score(org_id, donor_id) or compute_score(org_id, donor_id)
            return {
                "donor_id": rec.donor_id,
                "score": float(rec.score),
                "segment": rec.segment,
                "cultivation_priority": rec.cultivation_priority,
                "breakdown": {
                    "recency": float(rec.recency_score),
                    "frequency": float(rec.frequency_score),
                    "monetary": float(rec.monetary_score),
                    "engagement": float(rec.engagement_score),
                },
                "explanation": rec.explanation,
                "computed_at": rec.computed_at.isoformat() if rec.computed_at else None,
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _get_ai_donor_recommendations(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        """Generate Ollama-powered contextual recommendations for a donor."""
        org_id = self._org_filter(runtime_ctx)
        donor_id = int(args.get("donor_id", 0) or 0)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}
        if donor_id <= 0:
            return {"error": "donor_id is required"}

        donor = Donor.query.filter_by(organization_id=org_id, id=donor_id).first()
        if donor is None:
            return {"error": f"donor {donor_id} not found"}

        rfm = self._compute_rfm_signals(donor, org_id)
        related = self._optional_donor_relationship_counts(donor.id, org_id)

        # Enrich with persisted engagement score if available
        try:
            from ngo_homesuite.services.engagement_scoring_service import get_score

            score_rec = get_score(org_id, donor_id)
            score_block = (
                f"Engagement Score: {score_rec.score}/100 (segment: {score_rec.segment}, "
                f"priority: {score_rec.cultivation_priority})"
                if score_rec
                else "Engagement Score: not yet computed"
            )
        except Exception:  # noqa: BLE001
            score_block = "Engagement Score: unavailable"

        prompt = (
            f"You are a nonprofit fundraising advisor. Based on the following donor profile, "
            f"provide specific, actionable outreach recommendations. Be concise and concrete.\n\n"
            f"Donor: {donor.name} | Type: {donor.donor_type or 'individual'}\n"
            f"{score_block}\n"
            f"Last gift: {rfm['recency_days']} days ago | 12-month gifts: {rfm['frequency_12m']} "
            f"(${rfm['monetary_12m']:,.2f}) | Lifetime: ${rfm['lifetime_total']:,.2f}\n"
            f"Interactions: {related['interactions']} | Pledges: {related['pledges']} | Events: {related['events']}\n"
            f"Churn risk: {rfm['predictions']['churn_risk']}/100 | "
            f"Giving likelihood: {rfm['predictions']['giving_likelihood']}/100 | "
            f"Major gift potential: {rfm['predictions']['major_gift_potential']}/100\n\n"
            "Please provide:\n"
            "1. Recommended outreach channel (call/email/letter/visit) and timing\n"
            "2. Suggested ask amount with rationale\n"
            "3. Key talking points personalized to this donor\n"
            "4. Any retention red flags to address\n"
        )

        # Attempt Ollama generation; fall back to rule-based if unavailable
        try:
            import importlib
            ollama = importlib.import_module("ollama")
            model = runtime_ctx.get("ollama_model", "llama3.2")
            response = ollama.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
            )
            ai_text = response["message"]["content"] if isinstance(response, dict) else str(response)
        except Exception:  # noqa: BLE001
            # Graceful fallback to rule-based summary
            actions = self._recommended_actions(rfm, related)
            ai_text = (
                f"[Offline / Ollama unavailable â€” rule-based fallback]\n\n"
                + "\n".join(f"â€¢ {a}" for a in actions)
            )

        return {
            "donor_id": donor.id,
            "donor_name": donor.name,
            "signals": rfm["predictions"],
            "recommendations": ai_text,
            "prompt_used": prompt,
        }

    def _list_at_risk_donors(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}
        limit = max(1, min(int(args.get("limit", 20) or 20), 50))

        try:
            from ngo_homesuite.services.engagement_scoring_service import high_priority_lapsed

            records = high_priority_lapsed(org_id, limit=limit)
            related_counts = self._bulk_donor_relationship_counts([int(rec.donor_id) for rec in records], org_id)
            result = []
            for rec in records:
                donor = Donor.query.filter_by(id=rec.donor_id, organization_id=org_id).first()
                rfm = self._compute_rfm_signals(donor, org_id) if donor else {}
                related = related_counts.get(int(rec.donor_id), {"interactions": 0, "pledges": 0, "events": 0})
                actions = self._recommended_actions(rfm, related) if rfm else []
                result.append(
                    {
                        "donor_id": rec.donor_id,
                        "donor_name": donor.name if donor else "Unknown",
                        "email": donor.email if donor else None,
                        "score": float(rec.score),
                        "segment": rec.segment,
                        "cultivation_priority": rec.cultivation_priority,
                        "suggested_action": actions[0] if actions else "Contact donor",
                    }
                )
            return {"donors": result, "count": len(result)}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _evaluate_smart_group(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        group_id = int(args.get("group_id", 0) or 0)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}
        if group_id <= 0:
            return {"error": "group_id is required"}

        try:
            from ngo_homesuite.services.smart_groups_service import evaluate_group

            members = evaluate_group(group_id, org_id)
            return {
                "group_id": group_id,
                "count": len(members),
                "members": members[:50],  # cap response size
                "truncated": len(members) > 50,
            }
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    def _summarize_program_impact(self, args: dict[str, Any], runtime_ctx: dict[str, Any]) -> Any:
        org_id = self._org_filter(runtime_ctx)
        if org_id is None:
            return {"error": "organization_id is required in runtime context"}
        case_type = str(args.get("case_type", "") or "").strip() or None
        if not case_type:
            case_type = str(args.get("program_name", "") or "").strip() or None

        try:
            from ngo_homesuite.services.program_impact_service import impact_report

            return impact_report(org_id, case_type=case_type)
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

