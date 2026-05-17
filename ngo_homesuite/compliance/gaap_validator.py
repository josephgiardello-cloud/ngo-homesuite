"""GAAP compliance validator for nonprofit accounting standards.

Validates that donations, expenses, funds, and projects adhere to GAAP (Generally Accepted Accounting Principles)
for nonprofits, including fund accounting principles and restricted/unrestricted fund separation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from datetime import datetime, timezone

from sqlalchemy import func, select
from ngo_homesuite.models.core import Donation, Expense, Fund, Project, db


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _fund_is_restricted(fund: Fund | None) -> bool:
    return bool(getattr(fund, "is_restricted", False)) if fund is not None else False


def _donation_is_restricted(donation: Donation) -> bool:
    return bool(getattr(donation, "is_restricted", False))


def _fund_balance(fund: Fund | None) -> float:
    if fund is None:
        return 0.0
    value = getattr(fund, "balance", None)
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _expense_date(expense: Expense) -> datetime:
    return getattr(expense, "expense_date", None) or getattr(expense, "paid_at", _utcnow_naive())


@dataclass
class ComplianceViolation:
    """Represents a single compliance violation."""
    severity: str  # "error", "warning", "info"
    code: str  # e.g., "GAAP_RESTRICTED_FUND_MISMATCH"
    message: str
    entity_type: str
    entity_id: int | None = None
    remediation: str | None = None


class GaapComplianceValidator:
    """Validates GAAP compliance for nonprofit accounting."""
    
    @staticmethod
    def validate_donation(donation: Donation) -> list[ComplianceViolation]:
        """Validate a single donation for GAAP compliance."""
        violations = []
        
        # Violation 1: Restricted donation must have a fund allocation
        if _donation_is_restricted(donation) and not donation.fund_id:
            violations.append(ComplianceViolation(
                severity="error",
                code="GAAP_RESTRICTED_FUND_REQUIRED",
                message="Restricted donations must be allocated to a specific fund",
                entity_type="donation",
                entity_id=donation.id,
                remediation="Allocate this donation to a fund matching the donor's restriction"
            ))
        
        # Violation 2: Fund-allocated donation must match fund's restriction status
        if donation.fund_id:
            fund = db.session.get(Fund, donation.fund_id)
            if fund and not _fund_is_restricted(fund) and _donation_is_restricted(donation):
                violations.append(ComplianceViolation(
                    severity="error",
                    code="GAAP_FUND_RESTRICTION_MISMATCH",
                    message=f"Donation marked as restricted but fund '{fund.name}' is unrestricted",
                    entity_type="donation",
                    entity_id=donation.id,
                    remediation=f"Either unmark donation as restricted, or allocate to a restricted fund"
                ))
        
        # Violation 3: Donation amount must be positive
        if donation.amount <= 0:
            violations.append(ComplianceViolation(
                severity="error",
                code="GAAP_INVALID_AMOUNT",
                message="Donation amount must be positive",
                entity_type="donation",
                entity_id=donation.id
            ))
        
        # Violation 4: Future-dated donation (red flag)
        if donation.donation_date > _utcnow_naive():
            violations.append(ComplianceViolation(
                severity="warning",
                code="GAAP_FUTURE_DATED_DONATION",
                message="Donation is dated in the future",
                entity_type="donation",
                entity_id=donation.id,
                remediation="Verify donation date is accurate"
            ))
        
        return violations
    
    @staticmethod
    def validate_expense(expense: Expense) -> list[ComplianceViolation]:
        """Validate a single expense for GAAP compliance."""
        violations = []
        
        # Violation 1: Expense must have a fund or project allocation
        if not expense.fund_id and not expense.project_id:
            violations.append(ComplianceViolation(
                severity="error",
                code="GAAP_EXPENSE_ALLOCATION_REQUIRED",
                message="Expense must be allocated to at least one fund or project",
                entity_type="expense",
                entity_id=expense.id,
                remediation="Allocate this expense to a fund or project"
            ))
        
        # Violation 2: Expense amount must be positive
        if expense.amount <= 0:
            violations.append(ComplianceViolation(
                severity="error",
                code="GAAP_INVALID_AMOUNT",
                message="Expense amount must be positive",
                entity_type="expense",
                entity_id=expense.id
            ))
        
        # Violation 3: Future-dated expense (red flag)
        if _expense_date(expense) > _utcnow_naive():
            violations.append(ComplianceViolation(
                severity="warning",
                code="GAAP_FUTURE_DATED_EXPENSE",
                message="Expense is dated in the future",
                entity_type="expense",
                entity_id=expense.id,
                remediation="Verify expense date is accurate"
            ))
        
        # Violation 4: Restricted fund expense must be used for allowed purposes
        if expense.fund_id:
            fund = db.session.get(Fund, expense.fund_id)
            if fund and _fund_is_restricted(fund) and not getattr(fund, "purpose", None):
                violations.append(ComplianceViolation(
                    severity="warning",
                    code="GAAP_RESTRICTED_FUND_PURPOSE_MISSING",
                    message=f"Restricted fund '{fund.name}' has no documented purpose",
                    entity_type="expense",
                    entity_id=expense.id,
                    remediation="Document the fund's restriction and allowed use"
                ))
        
        return violations
    
    @staticmethod
    def validate_fund(fund: Fund) -> list[ComplianceViolation]:
        """Validate a single fund for GAAP compliance."""
        violations = []
        
        # Violation 1: Fund must have a name
        if not fund.name or not fund.name.strip():
            violations.append(ComplianceViolation(
                severity="error",
                code="GAAP_FUND_NAME_REQUIRED",
                message="Fund must have a name",
                entity_type="fund",
                entity_id=fund.id
            ))
        
        # Violation 2: Restricted fund must document its restriction
        if _fund_is_restricted(fund) and not getattr(fund, "purpose", None):
            violations.append(ComplianceViolation(
                severity="warning",
                code="GAAP_RESTRICTED_FUND_PURPOSE_REQUIRED",
                message="Restricted funds should document their donor restriction/purpose",
                entity_type="fund",
                entity_id=fund.id,
                remediation="Add a purpose statement describing the fund's restriction"
            ))
        
        # Violation 3: Fund balance integrity check (inflows - outflows should match balance)
        balance_calc = GaapComplianceValidator._calculate_fund_balance(fund.id)
        if abs(balance_calc - _fund_balance(fund)) > 0.01:  # Allow for rounding
            violations.append(ComplianceViolation(
                severity="error",
                code="GAAP_FUND_BALANCE_MISMATCH",
                message=f"Fund balance ({_fund_balance(fund)}) does not match calculated total ({balance_calc})",
                entity_type="fund",
                entity_id=fund.id,
                remediation="Run a reconciliation to verify fund balance"
            ))
        
        return violations
    
    @staticmethod
    def validate_organization(organization_id: int) -> list[ComplianceViolation]:
        """Validate overall organization-level GAAP compliance."""
        violations = []
        
        # Check 1: Fund accounting separation
        funds = db.session.execute(
            select(Fund).where(Fund.organization_id == organization_id)
        ).scalars().all()

        restricted = sum(_fund_balance(f) for f in funds if _fund_is_restricted(f))
        unrestricted = sum(_fund_balance(f) for f in funds if not _fund_is_restricted(f))
        
        # Check 2: Total donations should roughly match allocated donations
        total_donations = db.session.execute(
            select(func.sum(Donation.amount)).where(
                Donation.organization_id == organization_id
            )
        ).scalar() or 0
        
        allocated_donations = db.session.execute(
            select(func.sum(Donation.amount)).where(
                Donation.organization_id == organization_id,
                Donation.fund_id != None
            )
        ).scalar() or 0
        
        if total_donations > 0:
            allocation_pct = (allocated_donations / total_donations) * 100
            if allocation_pct < 80:
                violations.append(ComplianceViolation(
                    severity="warning",
                    code="GAAP_LOW_DONATION_ALLOCATION",
                    message=f"Only {allocation_pct:.1f}% of donations are fund-allocated (recommended: >80%)",
                    entity_type="organization",
                    entity_id=organization_id,
                    remediation="Allocate unallocated donations to appropriate funds"
                ))
        
        # Check 3: Ensure at least one unrestricted fund exists
        unrestricted_count = sum(1 for f in funds if not _fund_is_restricted(f))
        
        if unrestricted_count == 0:
            violations.append(ComplianceViolation(
                severity="warning",
                code="GAAP_NO_UNRESTRICTED_FUND",
                message="Organization should have at least one unrestricted (general operating) fund",
                entity_type="organization",
                entity_id=organization_id,
                remediation="Create a general operating fund"
            ))
        
        return violations
    
    @staticmethod
    def _calculate_fund_balance(fund_id: int) -> float:
        """Calculate fund balance from inflows and outflows."""
        inflow = db.session.execute(
            select(func.sum(Donation.amount)).where(
                Donation.fund_id == fund_id
            )
        ).scalar() or 0
        
        outflow = db.session.execute(
            select(func.sum(Expense.amount)).where(
                Expense.fund_id == fund_id
            )
        ).scalar() or 0
        
        return inflow - outflow
