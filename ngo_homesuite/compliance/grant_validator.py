"""Grant pre-submission validator for common funder requirements.

Validates grant proposals against standard requirements before submission
to catch issues early and improve approval odds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from datetime import datetime, timezone

from ngo_homesuite.models.core import Grant, Project, Organization, db


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _grant_funder(grant: Grant):
    return getattr(grant, "funder", None) or getattr(grant, "funder_name", None)


def _grant_total_amount(grant: Grant):
    return getattr(grant, "total_amount", None) or getattr(grant, "amount_requested", None) or getattr(grant, "amount_awarded", None)


def _grant_deadline(grant: Grant):
    return getattr(grant, "deadline", None) or getattr(grant, "application_deadline", None)


def _grant_award_start(grant: Grant):
    return getattr(grant, "award_start_date", None) or getattr(grant, "start_date", None)


def _grant_award_end(grant: Grant):
    return getattr(grant, "award_end_date", None) or getattr(grant, "end_date", None)


def _grant_reporting_requirements(grant: Grant):
    return getattr(grant, "reporting_requirements", None) or getattr(grant, "requirements", None)


@dataclass
class ValidationError:
    """Represents a validation error in grant submission."""
    field: str
    severity: str  # "error" (blocks submission), "warning" (should fix), "info" (nice to have)
    message: str
    remediation: str | None = None


class GrantPreSubmissionValidator:
    """Validates grant proposals before submission."""
    
    @staticmethod
    def validate_grant_for_submission(grant: Grant) -> list[ValidationError]:
        """
        Comprehensive pre-submission validation.
        Returns list of errors/warnings. Empty list = ready to submit.
        """
        errors = []
        
        # === CRITICAL FIELDS ===
        
        if not grant.title or not grant.title.strip():
            errors.append(ValidationError(
                field="title",
                severity="error",
                message="Grant title is required",
                remediation="Provide a clear, compelling grant title (recommended: 50-100 characters)"
            ))
        
        funder = _grant_funder(grant)
        if not funder or not str(funder).strip():
            errors.append(ValidationError(
                field="funder",
                severity="error",
                message="Funder name is required",
                remediation="Specify the grant funder organization"
            ))
        
        if not grant.description or len(grant.description.strip()) < 100:
            errors.append(ValidationError(
                field="description",
                severity="error",
                message="Grant description must be at least 100 characters",
                remediation="Provide a detailed description of the grant opportunity and funding purpose"
            ))
        
        total_amount = _grant_total_amount(grant)
        if not total_amount or total_amount <= 0:
            errors.append(ValidationError(
                field="total_amount",
                severity="error",
                message="Grant amount must be specified and positive",
                remediation="Enter the total grant amount requested"
            ))
        
        # === DATE VALIDATION ===
        
        deadline = _grant_deadline(grant)
        if not deadline:
            errors.append(ValidationError(
                field="deadline",
                severity="error",
                message="Grant deadline is required",
                remediation="Enter the submission deadline date"
            ))
        else:
            if deadline <= _utcnow_naive().date():
                errors.append(ValidationError(
                    field="deadline",
                    severity="error",
                    message="Grant deadline has already passed",
                    remediation="Correct the deadline date"
                ))
            
            days_until = (deadline - _utcnow_naive().date()).days
            if days_until < 7:
                errors.append(ValidationError(
                    field="deadline",
                    severity="warning",
                    message=f"Grant deadline is only {days_until} days away",
                    remediation="Ensure you have sufficient time to complete and submit the proposal"
                ))
        
        award_start_date = _grant_award_start(grant)
        if not award_start_date:
            errors.append(ValidationError(
                field="award_start_date",
                severity="warning",
                message="Award start date should be specified",
                remediation="Provide the expected project start date if awarded"
            ))
        else:
            if award_start_date < _utcnow_naive().date():
                errors.append(ValidationError(
                    field="award_start_date",
                    severity="error",
                    message="Award start date cannot be in the past",
                    remediation="Set a future start date"
                ))
        
        award_end_date = _grant_award_end(grant)
        if not award_end_date:
            errors.append(ValidationError(
                field="award_end_date",
                severity="warning",
                message="Award end date should be specified",
                remediation="Provide the expected project end date"
            ))
        elif award_start_date:
            if award_end_date <= award_start_date:
                errors.append(ValidationError(
                    field="award_end_date",
                    severity="error",
                    message="Award end date must be after start date",
                    remediation="Correct the end date to be after the start date"
                ))
        
        # === PROJECT LINKAGE ===
        
        if not grant.project_id:
            errors.append(ValidationError(
                field="project_id",
                severity="warning",
                message="Grant is not linked to a project",
                remediation="Link this grant to the project it will fund"
            ))
        else:
            project = db.session.get(Project, grant.project_id)
            if not project:
                errors.append(ValidationError(
                    field="project_id",
                    severity="error",
                    message="Linked project does not exist",
                    remediation="Select a valid project"
                ))
            elif not project.description or len(project.description.strip()) < 100:
                errors.append(ValidationError(
                    field="project",
                    severity="warning",
                    message="Linked project lacks detailed description",
                    remediation="Add a detailed project description (100+ chars)"
                ))
        
        # === FUNDER REQUIREMENTS ===
        
        reporting_requirements = _grant_reporting_requirements(grant)
        if not reporting_requirements or not str(reporting_requirements).strip():
            errors.append(ValidationError(
                field="reporting_requirements",
                severity="warning",
                message="Funder reporting requirements not documented",
                remediation="Document what reports/outcomes the funder requires"
            ))
        
        compliance_requirements = getattr(grant, "compliance_requirements", None)
        if compliance_requirements is not None and not str(compliance_requirements).strip():
            errors.append(ValidationError(
                field="compliance_requirements",
                severity="warning",
                message="Grant compliance requirements not documented",
                remediation="List any compliance/audit requirements from the funder"
            ))
        
        # === BUDGET VALIDATION ===
        
        budget_narrative = getattr(grant, "budget_narrative", None)
        if budget_narrative and len(str(budget_narrative).strip()) < 50:
            errors.append(ValidationError(
                field="budget_narrative",
                severity="warning",
                message="Budget narrative is minimal or missing",
                remediation="Provide detailed budget justification (50+ characters)"
            ))
        
        # === FUNDER ELIGIBILITY CHECK ===
        
        funder_eligibility_confirmed = getattr(grant, "funder_eligibility_confirmed", None)
        if funder_eligibility_confirmed is not None and not bool(funder_eligibility_confirmed):
            errors.append(ValidationError(
                field="funder_eligibility_confirmed",
                severity="warning",
                message="Funder eligibility not confirmed",
                remediation="Verify your organization meets funder eligibility requirements"
            ))
        
        # === OUTCOME METRICS ===
        
        expected_outcomes = getattr(grant, "expected_outcomes", None)
        if expected_outcomes is not None and not str(expected_outcomes).strip():
            errors.append(ValidationError(
                field="expected_outcomes",
                severity="warning",
                message="Expected outcomes not specified",
                remediation="Document measurable outcomes and success metrics"
            ))
        
        # === FINAL READINESS CHECK ===
        
        has_blocking_errors = any(e.severity == "error" for e in errors)
        
        if not has_blocking_errors:
            # Additional soft checks
            warning_count = sum(1 for e in errors if e.severity == "warning")
            if warning_count > 5:
                errors.append(ValidationError(
                    field="overall",
                    severity="info",
                    message=f"Grant has {warning_count} warnings. Consider addressing before submission.",
                    remediation="Review and address warnings to strengthen proposal"
                ))
        
        return errors
    
    @staticmethod
    def get_readiness_score(grant: Grant) -> dict[str, Any]:
        """
        Return readiness score 0-100 for grant submission.
        """
        errors = GrantPreSubmissionValidator.validate_grant_for_submission(grant)
        
        blocking = sum(1 for e in errors if e.severity == "error")
        warnings = sum(1 for e in errors if e.severity == "warning")
        
        if blocking > 0:
            score = 0
            status = "NOT_READY"
            reason = f"{blocking} critical issue(s) must be resolved before submission"
        elif warnings > 3:
            score = 50
            status = "CONDITIONAL"
            reason = f"Multiple warnings ({warnings}). Consider addressing."
        elif warnings > 0:
            score = 75
            status = "READY_WITH_NOTES"
            reason = f"Ready to submit. {warnings} minor issue(s) to consider."
        else:
            score = 100
            status = "READY"
            reason = "Proposal is complete and ready for submission"
        
        return {
            "readiness_score": score,
            "status": status,
            "reason": reason,
            "blocking_issues": blocking,
            "warnings": warnings,
            "errors": [
                {
                    "field": e.field,
                    "severity": e.severity,
                    "message": e.message,
                    "remediation": e.remediation,
                }
                for e in errors
            ]
        }
