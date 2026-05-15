"""Accounting integration service: QuickBooks Online and Xero.

Architecture
------------
Both providers use OAuth 2.0 authorization_code flow. Token pairs (access +
refresh) are stored per-organization in app.config extension storage; in
production these should be persisted to the database or a secrets store.

This module provides:
  - OAuth helpers (exchange code → tokens, refresh)
  - Push helpers (create invoice/payment/contact)
  - Sync wrappers that record results in AccountingSyncLog
  - A unified push_donation / push_expense interface

External dependencies (optional, graceful degradation):
  - requests (always present in requirements.txt)
  - Config keys: QUICKBOOKS_CLIENT_ID, QUICKBOOKS_CLIENT_SECRET,
                 QUICKBOOKS_REALM_ID (company ID),
                 XERO_CLIENT_ID, XERO_CLIENT_SECRET, XERO_TENANT_ID
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

from ngo_homesuite.models.core import AccountingSyncLog, Donation, Expense, db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_QBO_BASE = "https://quickbooks.api.intuit.com/v3/company"
_XERO_BASE = "https://api.xero.com/api.xro/2.0"
_QBO_TOKEN_URL = "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"
_XERO_TOKEN_URL = "https://identity.xero.com/connect/token"


def _cfg(key: str) -> Optional[str]:
    return os.environ.get(key)


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Token management (in-process cache; swap for DB/secrets in production)
# ---------------------------------------------------------------------------

_token_cache: Dict[str, Dict[str, str]] = {}


def store_tokens(provider: str, org_id: int, tokens: Dict[str, str]) -> None:
    _token_cache[f"{provider}:{org_id}"] = tokens


def get_tokens(provider: str, org_id: int) -> Optional[Dict[str, str]]:
    return _token_cache.get(f"{provider}:{org_id}")


# ---------------------------------------------------------------------------
# QuickBooks OAuth
# ---------------------------------------------------------------------------

def quickbooks_exchange_code(
    org_id: int, code: str, redirect_uri: str
) -> Dict[str, Any]:
    """Exchange OAuth2 auth code for QBO tokens. Store and return token dict."""
    client_id = _cfg("QUICKBOOKS_CLIENT_ID")
    client_secret = _cfg("QUICKBOOKS_CLIENT_SECRET")
    if not client_id or not client_secret:
        return {"error": "QUICKBOOKS_CLIENT_ID/SECRET not configured"}

    resp = requests.post(
        _QBO_TOKEN_URL,
        auth=(client_id, client_secret),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    if not resp.ok:
        return {"error": resp.text}
    tokens = resp.json()
    store_tokens("quickbooks", org_id, tokens)
    return tokens


def _quickbooks_refresh(org_id: int) -> Optional[str]:
    tokens = get_tokens("quickbooks", org_id)
    if not tokens or "refresh_token" not in tokens:
        return None
    client_id = _cfg("QUICKBOOKS_CLIENT_ID")
    client_secret = _cfg("QUICKBOOKS_CLIENT_SECRET")
    resp = requests.post(
        _QBO_TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
        timeout=15,
    )
    if not resp.ok:
        return None
    new_tokens = resp.json()
    store_tokens("quickbooks", org_id, new_tokens)
    return new_tokens.get("access_token")


def _qbo_headers(org_id: int) -> Optional[Dict[str, str]]:
    tokens = get_tokens("quickbooks", org_id)
    if not tokens:
        return None
    return {
        "Authorization": f"Bearer {tokens.get('access_token', '')}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# QuickBooks push helpers
# ---------------------------------------------------------------------------

def _qbo_create_customer(org_id: int, name: str, email: Optional[str]) -> Optional[str]:
    """Create or find a QBO Customer. Returns ExternalId or None on failure."""
    realm = _cfg("QUICKBOOKS_REALM_ID")
    headers = _qbo_headers(org_id)
    if not realm or not headers:
        return None

    payload = {"DisplayName": name[:100]}
    if email:
        payload["PrimaryEmailAddr"] = {"Address": email}

    resp = requests.post(
        f"{_QBO_BASE}/{realm}/customer",
        headers=headers,
        json=payload,
        timeout=15,
    )
    if resp.ok:
        return str(resp.json().get("Customer", {}).get("Id", ""))
    return None


def push_donation_to_quickbooks(
    org_id: int,
    donation_id: int,
) -> Dict[str, Any]:
    """Push a Donation as a Sales Receipt to QuickBooks Online."""
    donation = Donation.query.filter_by(id=donation_id, organization_id=org_id).first()
    if not donation:
        return {"error": "Donation not found"}

    realm = _cfg("QUICKBOOKS_REALM_ID")
    headers = _qbo_headers(org_id)
    if not realm or not headers:
        return _record_sync(org_id, "quickbooks", "donation", donation_id, "skipped", error="QBO not configured")

    donor_name = "Anonymous Donor"
    donor_email: Optional[str] = None
    if hasattr(donation, 'donor') and donation.donor:
        donor_name = getattr(donation.donor, 'name', donor_name)
        donor_email = getattr(donation.donor, 'email', None)

    customer_id = _qbo_create_customer(org_id, donor_name, donor_email)

    receipt_payload: Dict[str, Any] = {
        "TotalAmt": float(donation.amount),
        "CurrencyRef": {"value": getattr(donation, 'currency', 'USD')},
        "Line": [
            {
                "Amount": float(donation.amount),
                "DetailType": "SalesItemLineDetail",
                "SalesItemLineDetail": {
                    "ItemRef": {"value": "1", "name": "Donation"},
                    "Qty": 1,
                    "UnitPrice": float(donation.amount),
                },
            }
        ],
    }
    if customer_id:
        receipt_payload["CustomerRef"] = {"value": customer_id}

    resp = requests.post(
        f"{_QBO_BASE}/{realm}/salesreceipt",
        headers=headers,
        json=receipt_payload,
        timeout=15,
    )
    if resp.ok:
        ext_id = str(resp.json().get("SalesReceipt", {}).get("Id", ""))
        return _record_sync(org_id, "quickbooks", "donation", donation_id, "synced", external_id=ext_id)
    return _record_sync(org_id, "quickbooks", "donation", donation_id, "failed", error=resp.text[:500])


def push_expense_to_quickbooks(
    org_id: int,
    expense_id: int,
) -> Dict[str, Any]:
    """Push an Expense as a Purchase to QuickBooks Online."""
    expense = Expense.query.filter_by(id=expense_id, organization_id=org_id).first()
    if not expense:
        return {"error": "Expense not found"}

    realm = _cfg("QUICKBOOKS_REALM_ID")
    headers = _qbo_headers(org_id)
    if not realm or not headers:
        return _record_sync(org_id, "quickbooks", "expense", expense_id, "skipped", error="QBO not configured")

    purchase_payload = {
        "AccountRef": {"value": "1"},
        "PaymentType": "Cash",
        "TotalAmt": float(expense.amount),
        "Line": [
            {
                "Amount": float(expense.amount),
                "DetailType": "AccountBasedExpenseLineDetail",
                "AccountBasedExpenseLineDetail": {
                    "AccountRef": {"value": "1"},
                },
                "Description": expense.description or "",
            }
        ],
        "EntityRef": {"value": "1", "type": "Vendor"},
    }

    resp = requests.post(
        f"{_QBO_BASE}/{realm}/purchase",
        headers=headers,
        json=purchase_payload,
        timeout=15,
    )
    if resp.ok:
        ext_id = str(resp.json().get("Purchase", {}).get("Id", ""))
        return _record_sync(org_id, "quickbooks", "expense", expense_id, "synced", external_id=ext_id)
    return _record_sync(org_id, "quickbooks", "expense", expense_id, "failed", error=resp.text[:500])


# ---------------------------------------------------------------------------
# Xero OAuth
# ---------------------------------------------------------------------------

def xero_exchange_code(
    org_id: int, code: str, redirect_uri: str
) -> Dict[str, Any]:
    """Exchange OAuth2 auth code for Xero tokens."""
    client_id = _cfg("XERO_CLIENT_ID")
    client_secret = _cfg("XERO_CLIENT_SECRET")
    if not client_id or not client_secret:
        return {"error": "XERO_CLIENT_ID/SECRET not configured"}

    resp = requests.post(
        _XERO_TOKEN_URL,
        auth=(client_id, client_secret),
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    if not resp.ok:
        return {"error": resp.text}
    tokens = resp.json()
    store_tokens("xero", org_id, tokens)
    return tokens


def _xero_refresh(org_id: int) -> Optional[str]:
    tokens = get_tokens("xero", org_id)
    if not tokens or "refresh_token" not in tokens:
        return None
    client_id = _cfg("XERO_CLIENT_ID")
    client_secret = _cfg("XERO_CLIENT_SECRET")
    resp = requests.post(
        _XERO_TOKEN_URL,
        auth=(client_id, client_secret),
        data={"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
        timeout=15,
    )
    if not resp.ok:
        return None
    new_tokens = resp.json()
    store_tokens("xero", org_id, new_tokens)
    return new_tokens.get("access_token")


def _xero_headers(org_id: int) -> Optional[Dict[str, str]]:
    tokens = get_tokens("xero", org_id)
    tenant_id = _cfg("XERO_TENANT_ID")
    if not tokens or not tenant_id:
        return None
    return {
        "Authorization": f"Bearer {tokens.get('access_token', '')}",
        "Xero-Tenant-Id": tenant_id,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Xero push helpers
# ---------------------------------------------------------------------------

def push_donation_to_xero(
    org_id: int,
    donation_id: int,
) -> Dict[str, Any]:
    """Push a Donation as a Xero Invoice (ACCREC)."""
    donation = Donation.query.filter_by(id=donation_id, organization_id=org_id).first()
    if not donation:
        return {"error": "Donation not found"}

    headers = _xero_headers(org_id)
    if not headers:
        return _record_sync(org_id, "xero", "donation", donation_id, "skipped", error="Xero not configured")

    donor_name = "Anonymous Donor"
    if hasattr(donation, 'donor') and donation.donor:
        donor_name = getattr(donation.donor, 'name', donor_name)

    payload = {
        "Type": "ACCREC",
        "Contact": {"Name": donor_name[:255]},
        "LineAmountTypes": "Exclusive",
        "LineItems": [
            {
                "Description": "Donation",
                "Quantity": 1.0,
                "UnitAmount": float(donation.amount),
                "AccountCode": "200",
            }
        ],
        "CurrencyCode": getattr(donation, 'currency', 'USD'),
    }

    resp = requests.post(
        f"{_XERO_BASE}/Invoices",
        headers=headers,
        json={"Invoices": [payload]},
        timeout=15,
    )
    if resp.ok:
        invoices = resp.json().get("Invoices", [])
        ext_id = invoices[0].get("InvoiceID", "") if invoices else ""
        ref = invoices[0].get("InvoiceNumber", "") if invoices else ""
        return _record_sync(
            org_id, "xero", "donation", donation_id, "synced",
            external_id=ext_id, external_ref=ref,
        )
    return _record_sync(org_id, "xero", "donation", donation_id, "failed", error=resp.text[:500])


def push_expense_to_xero(
    org_id: int,
    expense_id: int,
) -> Dict[str, Any]:
    """Push an Expense as a Xero BankTransaction (SPEND)."""
    expense = Expense.query.filter_by(id=expense_id, organization_id=org_id).first()
    if not expense:
        return {"error": "Expense not found"}

    headers = _xero_headers(org_id)
    if not headers:
        return _record_sync(org_id, "xero", "expense", expense_id, "skipped", error="Xero not configured")

    payload = {
        "Type": "SPEND",
        "Contact": {"Name": expense.payee or "Vendor"},
        "BankAccount": {"Code": "090"},
        "LineAmountTypes": "Inclusive",
        "LineItems": [
            {
                "Description": expense.description or "Expense",
                "Quantity": 1.0,
                "UnitAmount": float(expense.amount),
                "AccountCode": "429",
            }
        ],
        "CurrencyCode": getattr(expense, 'currency', 'USD'),
    }

    resp = requests.post(
        f"{_XERO_BASE}/BankTransactions",
        headers=headers,
        json={"BankTransactions": [payload]},
        timeout=15,
    )
    if resp.ok:
        txns = resp.json().get("BankTransactions", [])
        ext_id = txns[0].get("BankTransactionID", "") if txns else ""
        return _record_sync(org_id, "xero", "expense", expense_id, "synced", external_id=ext_id)
    return _record_sync(org_id, "xero", "expense", expense_id, "failed", error=resp.text[:500])


# ---------------------------------------------------------------------------
# Unified sync log helper
# ---------------------------------------------------------------------------

def _record_sync(
    org_id: int,
    provider: str,
    sync_type: str,
    internal_id: int,
    status: str,
    *,
    external_id: Optional[str] = None,
    external_ref: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    log = AccountingSyncLog(
        organization_id=org_id,
        provider=provider,
        sync_type=sync_type,
        internal_id=internal_id,
        external_id=external_id,
        external_ref=external_ref,
        status=status,
        error_message=error,
        synced_at=_now() if status == "synced" else None,
    )
    db.session.add(log)
    db.session.commit()
    return {
        "provider": provider,
        "sync_type": sync_type,
        "internal_id": internal_id,
        "status": status,
        "external_id": external_id,
        "external_ref": external_ref,
        "error": error,
    }


def list_sync_logs(
    org_id: int,
    *,
    provider: Optional[str] = None,
    sync_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 100,
) -> List[AccountingSyncLog]:
    q = AccountingSyncLog.query.filter_by(organization_id=org_id)
    if provider:
        q = q.filter_by(provider=provider)
    if sync_type:
        q = q.filter_by(sync_type=sync_type)
    if status:
        q = q.filter_by(status=status)
    return q.order_by(AccountingSyncLog.created_at.desc()).limit(limit).all()
