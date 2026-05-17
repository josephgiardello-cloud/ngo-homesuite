"""Main web routes, dashboards, and Phase 1 CRUD interfaces."""

import csv
import json
import time
import uuid
from ngo_homesuite.ai.copilot_tools import CopilotToolRegistry
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional as TypingOptional

from flask import Blueprint, render_template, redirect, url_for, request, flash, send_file, current_app, Response, session, abort
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from werkzeug.exceptions import NotFound
from wtforms import BooleanField, DateField, FloatField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Optional as WTOptional, NumberRange, Email
from io import BytesIO
from openpyxl import Workbook, load_workbook

from ngo_homesuite.models.core import (
    Organization, Beneficiary, Project, Donation, Donor, Fund, Expense, DonationReceipt, P2PPage, Volunteer, db
)
from ngo_homesuite.services.beneficiary_service import create_beneficiary, get_beneficiary, list_beneficiaries, update_beneficiary
from ngo_homesuite.services.program_impact_service import list_cases
from ngo_homesuite.services.donation_service import DonationConcurrencyError, DonationNotFound, DonationService
from ngo_homesuite.services.donor_service import DonorNotFound, DonorService
from ngo_homesuite.services.expense_service import ExpenseService
from ngo_homesuite.services.fund_service import FundConcurrencyError, FundNotFound, FundService
from ngo_homesuite.services.organization_service import get_first_active_organization
from ngo_homesuite.services.project_service import ProjectNotFound, ProjectService
from ngo_homesuite.services.reporting_service import ReportingService
from ngo_homesuite.services.volunteer_service import create_volunteer, list_recent_volunteers
from ngo_homesuite.domain import (
    BeneficiaryEntity,
    CampaignEntity,
    DomainRegistry,
    DonorEntity,
    GrantEntity,
    LifecycleState,
    OutcomeEntity,
    ProgramEntity,
)
from ngo_homesuite.ai.semantic_memory import SemanticMemoryLayer
from ngo_homesuite.services.opinionated_workflows import (
    run_donation_receipt_followup_workflow,
    run_grant_tracking_reporting_workflow,
    run_program_tracking_impact_workflow,
)
from sqlalchemy import func
from ngo_homesuite.web.rbac import roles_required
from ngo_homesuite.utils.receipt_pdf import generate_receipt_pdf_bytes
from ngo_homesuite.compliance.evidence_pack import build_compliance_evidence

main_bp = Blueprint('main', __name__)

_SUPPORTED_LOCALES = {'en', 'es', 'fr'}
_DONOR_IMPORT_ALLOWED_EXTENSIONS = {'.csv', '.xlsx'}
_DONOR_IMPORT_MAX_ROWS = 2500
_DONOR_IMPORT_VALID_TYPES = {'individual', 'corporate', 'foundation', 'anonymous'}

# Track process start time for uptime calculation.
_PROCESS_START = time.monotonic()


@main_bp.route('/health', methods=['GET'])
def health() -> Response:
    """Structured health probe: DB reachability, migration version, uptime."""
    from ngo_homesuite.models.core import db as _db

    # DB reachability
    db_ok = False
    db_error: str | None = None
    try:
        _db.session.scalar(_db.text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # pragma: no cover
        db_error = str(exc)

    # Latest applied migration version (from schema_version table)
    migration_version: int | None = None
    try:
        if db_ok:
            raw = _db.session.scalar(_db.text("SELECT MAX(version) FROM schema_version"))
            migration_version = int(raw) if raw is not None else None
    except Exception:  # pragma: no cover
        pass

    # Expected version = highest numbered SQL migration file on disk
    from pathlib import Path as _Path
    _migrations_dir = _Path(__file__).parent.parent / "migrations"
    _sql_files = sorted(_migrations_dir.glob("[0-9]*.sql"))
    expected_version: int = 0
    if _sql_files:
        try:
            expected_version = int(_sql_files[-1].name.split("_")[0])
        except ValueError:  # pragma: no cover
            expected_version = 0

    payload: dict = {
        "status": "ok" if db_ok else "degraded",
        "db": "ok" if db_ok else f"error: {db_error}",
        "migration_version": migration_version,
        "expected_migration_version": expected_version,
        "migration_current": migration_version == expected_version,
        "uptime_seconds": round(time.monotonic() - _PROCESS_START, 1),
    }
    status_code = 200 if db_ok else 503
    return Response(
        json.dumps(payload),
        status=status_code,
        mimetype="application/json",
    )


@main_bp.route('/health/live', methods=['GET'])
def health_live() -> Response:
    """Kubernetes liveness probe — returns 200 as long as the process is running."""
    return Response(
        json.dumps({"status": "live"}),
        status=200,
        mimetype="application/json",
    )


@main_bp.route('/health/ready', methods=['GET'])
def health_ready() -> Response:
    """Kubernetes readiness probe — 200 only when DB is up and migrations are current."""
    from ngo_homesuite.models.core import db as _db
    from pathlib import Path as _Path

    db_ok = False
    try:
        _db.session.scalar(_db.text("SELECT 1"))
        db_ok = True
    except Exception:  # pragma: no cover
        pass

    migration_version: int | None = None
    try:
        if db_ok:
            raw = _db.session.scalar(_db.text("SELECT MAX(version) FROM schema_version"))
            migration_version = int(raw) if raw is not None else None
    except Exception:  # pragma: no cover
        pass

    _migrations_dir = _Path(__file__).parent.parent / "migrations"
    _sql_files = sorted(_migrations_dir.glob("[0-9]*.sql"))
    expected_version: int = 0
    if _sql_files:
        try:
            expected_version = int(_sql_files[-1].name.split("_")[0])
        except ValueError:  # pragma: no cover
            expected_version = 0

    # Ready only when DB is up and migrations are at the expected version (or no SQL migrations exist)
    migration_current = (migration_version == expected_version) or (expected_version == 0 and migration_version is None)
    ready = db_ok and migration_current
    payload = {
        "status": "ready" if ready else "not_ready",
        "db": "ok" if db_ok else "error",
        "migration_current": migration_current,
    }
    return Response(
        json.dumps(payload),
        status=200 if ready else 503,
        mimetype="application/json",
    )


class DonorForm(FlaskForm):
    name = StringField('Name', validators=[DataRequired()])
    email = StringField('Email', validators=[WTOptional(), Email()])
    phone = StringField('Phone', validators=[WTOptional()])
    donor_type = SelectField(
        'Donor Type',
        choices=[
            ('individual', 'Individual'),
            ('corporate', 'Corporate'),
            ('foundation', 'Foundation'),
            ('anonymous', 'Anonymous'),
        ],
        validators=[DataRequired()],
    )
    notes = TextAreaField('Notes', validators=[WTOptional()])
    submit = SubmitField('Save Donor')


class DonationForm(FlaskForm):
    donor_id = SelectField('Donor', coerce=int, validators=[DataRequired()])
    project_id = SelectField('Project', coerce=int, validators=[WTOptional()])
    fund_id = SelectField('Fund', coerce=int, validators=[WTOptional()])
    amount = FloatField('Amount', validators=[DataRequired(), NumberRange(min=0.01)])
    currency = SelectField('Currency', choices=[('USD', 'USD'), ('EUR', 'EUR'), ('GBP', 'GBP')], validators=[DataRequired()])
    payment_method = SelectField(
        'Payment Method',
        choices=[('cash', 'Cash'), ('bank_transfer', 'Bank Transfer'), ('credit_card', 'Credit Card')],
        validators=[DataRequired()],
    )
    purpose = StringField('Purpose', validators=[WTOptional()])
    reference_number = StringField('Reference Number', validators=[WTOptional()])
    notes = TextAreaField('Notes', validators=[WTOptional()])
    submit = SubmitField('Record Donation')


class ExpenseForm(FlaskForm):
    project_id = SelectField('Project', coerce=int, validators=[WTOptional()])
    fund_id = SelectField('Fund', coerce=int, validators=[WTOptional()])
    amount = FloatField('Amount', validators=[DataRequired(), NumberRange(min=0.01)])
    currency = SelectField('Currency', choices=[('USD', 'USD'), ('EUR', 'EUR'), ('GBP', 'GBP')], validators=[DataRequired()])
    payee = StringField('Payee', validators=[WTOptional()])
    description = TextAreaField('Description', validators=[WTOptional()])
    submit = SubmitField('Record Expense')


class ProjectForm(FlaskForm):
    name = StringField('Project Name', validators=[DataRequired()])
    description = TextAreaField('Description', validators=[WTOptional()])
    program = StringField('Program', validators=[WTOptional()])
    budget = FloatField('Budget', validators=[DataRequired(), NumberRange(min=0)])
    spent = FloatField('Spent', validators=[DataRequired(), NumberRange(min=0)])
    currency = SelectField('Currency', choices=[('USD', 'USD'), ('EUR', 'EUR'), ('GBP', 'GBP')], validators=[DataRequired()])
    status = SelectField(
        'Status',
        choices=[('planned', 'Planned'), ('active', 'Active'), ('paused', 'Paused'), ('completed', 'Completed')],
        validators=[DataRequired()],
    )
    submit = SubmitField('Save Project')


class FundForm(FlaskForm):
    name = StringField('Fund Name', validators=[DataRequired()])
    description = TextAreaField('Description', validators=[WTOptional()])
    is_active = SelectField(
        'Status',
        choices=[('true', 'Active'), ('false', 'Inactive')],
        validators=[DataRequired()],
    )
    submit = SubmitField('Save Fund')


class ConfirmDeleteForm(FlaskForm):
    submit = SubmitField('Delete')


class PublicDonationForm(FlaskForm):
    donor_name = StringField('Full Name', validators=[DataRequired()])
    donor_email = StringField('Email', validators=[WTOptional(), Email()])
    donor_phone = StringField('Phone', validators=[WTOptional()])
    amount = FloatField('Amount', validators=[DataRequired(), NumberRange(min=0.01)])
    currency = SelectField('Currency', choices=[('USD', 'USD'), ('EUR', 'EUR'), ('GBP', 'GBP')], validators=[DataRequired()])
    payment_method = SelectField(
        'Payment Method',
        choices=[('credit_card', 'Credit Card'), ('bank_transfer', 'Bank Transfer'), ('cash', 'Cash')],
        validators=[DataRequired()],
    )
    purpose = StringField('Purpose', validators=[WTOptional()])
    make_recurring = BooleanField('Make this a recurring donation')
    recurring_frequency = SelectField(
        'Recurring Frequency',
        choices=[('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('yearly', 'Yearly')],
        validators=[WTOptional()],
    )
    submit = SubmitField('Donate')


class P2PPageForm(FlaskForm):
    donor_id = SelectField('Fundraiser Owner', coerce=int, validators=[DataRequired()])
    title = StringField('Fundraiser Title', validators=[DataRequired()])
    goal_amount = FloatField('Goal Amount', validators=[WTOptional(), NumberRange(min=0)])
    story = TextAreaField('Story', validators=[WTOptional()])
    campaign_slug = StringField('Campaign Slug (optional)', validators=[WTOptional()])
    submit = SubmitField('Create Fundraiser')


class RecurringDonationForm(FlaskForm):
    donor_id = SelectField('Donor', coerce=int, validators=[DataRequired()])
    amount = FloatField('Amount', validators=[DataRequired(), NumberRange(min=0.01)])
    currency = SelectField('Currency', choices=[('USD', 'USD'), ('EUR', 'EUR'), ('GBP', 'GBP')], validators=[DataRequired()])
    payment_method = SelectField(
        'Payment Method',
        choices=[('credit_card', 'Credit Card'), ('bank_transfer', 'Bank Transfer'), ('cash', 'Cash')],
        validators=[DataRequired()],
    )
    purpose = StringField('Purpose', validators=[WTOptional()])
    frequency = SelectField(
        'Frequency',
        choices=[('monthly', 'Monthly'), ('quarterly', 'Quarterly'), ('yearly', 'Yearly')],
        validators=[DataRequired()],
    )
    submit = SubmitField('Create Recurring Plan')


class BeneficiaryIntakeForm(FlaskForm):
    first_name = StringField('First Name', validators=[DataRequired()])
    last_name = StringField('Last Name', validators=[WTOptional()])
    email = StringField('Email', validators=[WTOptional(), Email()])
    phone = StringField('Phone', validators=[WTOptional()])
    date_of_birth = DateField('Date of Birth', validators=[WTOptional()], format='%Y-%m-%d')
    gender = SelectField(
        'Gender',
        choices=[('', '-- Select --'), ('male', 'Male'), ('female', 'Female'), ('non_binary', 'Non-binary'), ('prefer_not_to_say', 'Prefer not to say')],
        validators=[WTOptional()],
    )
    program = StringField('Program / Service Area', validators=[WTOptional()])
    status = SelectField(
        'Status',
        choices=[('active', 'Active'), ('inactive', 'Inactive'), ('pending', 'Pending')],
        validators=[DataRequired()],
    )
    city = StringField('City', validators=[WTOptional()])
    country = StringField('Country', validators=[WTOptional()])
    notes = TextAreaField('Notes', validators=[WTOptional()])
    submit = SubmitField('Save Beneficiary')


def _current_org() -> TypingOptional[Organization]:
    """Pick assigned org first, then fallback to first active org for seeded demo users."""
    if current_user.organization:
        return current_user.organization
    return get_first_active_organization()


def _build_csv_bytes(headers, rows):
    import io
    text_stream = io.StringIO()
    writer = csv.writer(text_stream)
    writer.writerow(headers)
    writer.writerows(rows)
    return text_stream.getvalue().encode('utf-8-sig')


def _build_xlsx_bytes(sheet_name, headers, rows):
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = sheet_name
    worksheet.append(headers)
    for row in rows:
        worksheet.append(list(row))
    output = BytesIO()
    workbook.save(output)
    output.seek(0)
    return output.read()


def _build_iif_bytes(trns_type: str, rows):
    """Build QuickBooks IIF payload bytes for TRNS/SPL/ENDTRNS batches.

    Row schema: [txn_id, date_iso, name, amount, memo]
    """
    text_stream = []
    text_stream.append("!TRNS\tTRNSID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tMEMO")
    text_stream.append("!SPL\tSPLID\tTRNSTYPE\tDATE\tACCNT\tNAME\tAMOUNT\tMEMO")
    text_stream.append("!ENDTRNS")

    if trns_type == 'DEPOSIT':
        trns_account = 'Undeposited Funds'
        spl_account = 'Donations Income'
        trns_sign = 1.0
    else:
        trns_account = 'Operating Bank'
        spl_account = 'Program Expense'
        trns_sign = -1.0

    for txn_id, date_iso, name, amount, memo in rows:
        amount_val = float(amount or 0)
        trns_amt = trns_sign * amount_val
        spl_amt = -trns_amt
        date_value = (date_iso or '').strip() or datetime.now(timezone.utc).strftime('%Y-%m-%d')
        text_stream.append(
            f"TRNS\t{txn_id}\t{trns_type}\t{date_value}\t{trns_account}\t{name}\t{trns_amt:.2f}\t{memo}"
        )
        text_stream.append(
            f"SPL\t{txn_id}\t{trns_type}\t{date_value}\t{spl_account}\t{name}\t{spl_amt:.2f}\t{memo}"
        )
        text_stream.append("ENDTRNS")

    payload = "\n".join(text_stream) + "\n"
    return payload.encode('utf-8')


def _parse_float(value: str):
    value = (value or '').strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _normalize_text(value: str) -> str:
    return (value or '').strip().lower()


def _normalize_phone(value: str) -> str:
    return ''.join(ch for ch in (value or '') if ch.isdigit())


def _donor_import_cache_dir() -> Path:
    cache_dir = Path(current_app.instance_path) / 'donor_import_cache'
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _parse_donor_import_file(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    ext = path.suffix.lower()
    if ext not in _DONOR_IMPORT_ALLOWED_EXTENSIONS:
        raise ValueError('Only CSV and Excel (.xlsx) files are supported.')

    headers: list[str] = []
    rows: list[dict[str, str]] = []

    if ext == '.csv':
        try:
            with path.open('r', encoding='utf-8-sig', newline='') as fh:
                reader = csv.DictReader(fh)
                headers = [str(h or '').strip() for h in (reader.fieldnames or []) if str(h or '').strip()]
                for idx, row in enumerate(reader, start=1):
                    if idx > _DONOR_IMPORT_MAX_ROWS:
                        break
                    rows.append({h: str((row or {}).get(h, '') or '').strip() for h in headers})
        except UnicodeDecodeError:
            with path.open('r', encoding='latin-1', newline='') as fh:
                reader = csv.DictReader(fh)
                headers = [str(h or '').strip() for h in (reader.fieldnames or []) if str(h or '').strip()]
                for idx, row in enumerate(reader, start=1):
                    if idx > _DONOR_IMPORT_MAX_ROWS:
                        break
                    rows.append({h: str((row or {}).get(h, '') or '').strip() for h in headers})
    else:
        workbook = load_workbook(filename=path, read_only=True, data_only=True)
        ws = workbook.active
        sheet_rows = ws.iter_rows(values_only=True)
        raw_headers = next(sheet_rows, None)
        if raw_headers is None:
            return [], []

        headers = [str(c or '').strip() for c in raw_headers]
        headers = [h if h else f'column_{idx + 1}' for idx, h in enumerate(headers)]
        for idx, values in enumerate(sheet_rows, start=1):
            if idx > _DONOR_IMPORT_MAX_ROWS:
                break
            row_map: dict[str, str] = {}
            for col_idx, header in enumerate(headers):
                value = ''
                if values and col_idx < len(values):
                    value = str(values[col_idx] or '').strip()
                row_map[header] = value
            rows.append(row_map)

    return headers, rows


def _guess_donor_import_mapping(headers: list[str]) -> dict[str, str]:
    aliases = {
        'name': {'name', 'full name', 'donor', 'donor name'},
        'email': {'email', 'email address', 'e-mail'},
        'phone': {'phone', 'phone number', 'mobile', 'telephone'},
        'donor_type': {'type', 'donor type', 'category'},
        'notes': {'notes', 'note', 'comments', 'comment'},
    }

    mapping: dict[str, str] = {'name': '', 'email': '', 'phone': '', 'donor_type': '', 'notes': ''}
    normalized = {h: _normalize_text(h) for h in headers}
    for target, candidates in aliases.items():
        for header, normalized_header in normalized.items():
            if normalized_header in candidates and not mapping[target]:
                mapping[target] = header
    if not mapping['name'] and headers:
        mapping['name'] = headers[0]
    return mapping


def _extract_donor_import_mapping(form_data) -> dict[str, str]:
    mapping = {
        'name': (form_data.get('map_name') or '').strip(),
        'email': (form_data.get('map_email') or '').strip(),
        'phone': (form_data.get('map_phone') or '').strip(),
        'donor_type': (form_data.get('map_donor_type') or '').strip(),
        'notes': (form_data.get('map_notes') or '').strip(),
    }
    return mapping


def _build_donor_import_preview(org_id: int, rows: list[dict[str, str]], mapping: dict[str, str]) -> dict[str, object]:
    donors = DonorService().list_all_donors(org_id)
    existing_by_email: dict[str, Donor] = {}
    existing_by_name_phone: dict[str, Donor] = {}
    for donor in donors:
        email_key = _normalize_text(donor.email)
        if email_key:
            existing_by_email[email_key] = donor
        name_phone_key = f"{_normalize_text(donor.name)}::{_normalize_phone(donor.phone)}"
        if _normalize_phone(donor.phone):
            existing_by_name_phone[name_phone_key] = donor

    preview_rows: list[dict[str, object]] = []
    error_count = 0
    duplicate_count = 0
    ready_count = 0

    for idx, row in enumerate(rows, start=1):
        mapped_name = str(row.get(mapping['name'], '') if mapping.get('name') else '').strip()
        mapped_email = str(row.get(mapping['email'], '') if mapping.get('email') else '').strip().lower()
        mapped_phone = str(row.get(mapping['phone'], '') if mapping.get('phone') else '').strip()
        mapped_type = str(row.get(mapping['donor_type'], '') if mapping.get('donor_type') else '').strip().lower() or 'individual'
        mapped_notes = str(row.get(mapping['notes'], '') if mapping.get('notes') else '').strip()

        errors: list[str] = []
        warnings: list[str] = []

        if not mapped_name:
            errors.append('Missing donor name')
        if mapped_type not in _DONOR_IMPORT_VALID_TYPES:
            warnings.append(f"Unknown donor type '{mapped_type}' -> default to individual")
            mapped_type = 'individual'

        duplicate_match = None
        if mapped_email:
            duplicate_match = existing_by_email.get(mapped_email)
        if duplicate_match is None and _normalize_phone(mapped_phone):
            duplicate_match = existing_by_name_phone.get(f"{_normalize_text(mapped_name)}::{_normalize_phone(mapped_phone)}")

        status = 'ready'
        if errors:
            status = 'error'
            error_count += 1
        elif duplicate_match is not None:
            status = 'duplicate'
            duplicate_count += 1
        else:
            ready_count += 1

        preview_rows.append(
            {
                'row_number': idx,
                'name': mapped_name,
                'email': mapped_email,
                'phone': mapped_phone,
                'donor_type': mapped_type,
                'notes': mapped_notes,
                'status': status,
                'errors': errors,
                'warnings': warnings,
                'duplicate_match': duplicate_match,
            }
        )

    return {
        'rows': preview_rows,
        'summary': {
            'total': len(preview_rows),
            'ready': ready_count,
            'duplicates': duplicate_count,
            'errors': error_count,
        },
    }


def _apply_donor_import(org_id: int, preview_rows: list[dict[str, object]]) -> dict[str, int]:
    created = 0
    skipped_duplicates = 0
    skipped_errors = 0
    for item in preview_rows:
        status = str(item.get('status') or '')
        if status == 'error':
            skipped_errors += 1
            continue
        if status == 'duplicate':
            skipped_duplicates += 1
            continue

        DonorService().create_donor(
            org_id,
            str(item.get('name') or '').strip(),
            email=(str(item.get('email') or '').strip() or None),
            phone=(str(item.get('phone') or '').strip() or None),
            donor_type=str(item.get('donor_type') or 'individual').strip() or 'individual',
            notes=(str(item.get('notes') or '').strip() or None),
        )
        created += 1

    return {
        'created': created,
        'skipped_duplicates': skipped_duplicates,
        'skipped_errors': skipped_errors,
    }


def _next_charge_date(current: date, frequency: str) -> date:
    if frequency == 'quarterly':
        return current + timedelta(days=90)
    if frequency == 'yearly':
        return current + timedelta(days=365)
    return current + timedelta(days=30)


def _openapi_spec_path() -> Path:
    return Path(current_app.root_path).parent / 'docs' / 'openapi.yaml'


def _sqlite_db_file_path() -> str:
    uri = str(current_app.config.get('SQLALCHEMY_DATABASE_URI', 'sqlite:///ngo_homesuite.db'))
    if uri.startswith('sqlite:///'):
        return uri.replace('sqlite:///', '', 1)
    return 'ngo_homesuite.db'


def _build_domain_registry_for_org(org: Organization | None) -> DomainRegistry:
    registry = DomainRegistry()
    if org is None:
        return registry

    donor_svc = DonorService()
    project_svc = ProjectService()
    reporting_svc = ReportingService()

    donors = donor_svc.list_all_donors(org.id)
    projects = project_svc.list_all_projects(org.id)
    beneficiaries = list_beneficiaries(org.id)

    for donor in donors:
        donor_entity = DonorEntity(
            entity_id=f"donor:{donor.id}",
            name=donor.name,
            donor_type=donor.donor_type,
            email=donor.email,
            phone=donor.phone,
            lifecycle_state=LifecycleState.active,
        )
        donor_entity.transition(LifecycleState.active, actor='system', reason='ingested_for_domain_registry')
        registry.upsert(donor_entity)

    for project in projects:
        program = ProgramEntity(
            entity_id=f"program:{project.id}",
            name=project.name,
            lifecycle_state=LifecycleState.active if project.status in ('active', 'planned') else LifecycleState.paused,
        )
        program.transition(program.lifecycle_state, actor='system', reason='project_to_program_mapping')
        registry.upsert(program)

    for beneficiary in beneficiaries:
        beneficiary_entity = BeneficiaryEntity(
            entity_id=f"beneficiary:{beneficiary.id}",
            name=f"{beneficiary.first_name} {beneficiary.last_name}".strip(),
            lifecycle_state=LifecycleState.active if beneficiary.status == 'active' else LifecycleState.paused,
        )
        beneficiary_entity.transition(beneficiary_entity.lifecycle_state, actor='system', reason='beneficiary_ingested')
        registry.upsert(beneficiary_entity)

    purpose_sums = reporting_svc.donation_purpose_totals(org.id)
    for idx, (purpose, total) in enumerate(purpose_sums, start=1):
        campaign = CampaignEntity(
            entity_id=f"campaign:{idx}",
            name=str(purpose),
            lifecycle_state=LifecycleState.active,
            fundraising_goal=round(float(total) * 1.25, 2),
            raised_amount=float(total),
        )
        campaign.transition(LifecycleState.active, actor='system', reason='derived_from_donation_purpose')
        registry.upsert(campaign)

    foundation_totals = reporting_svc.foundation_donor_totals(org.id)
    for idx, (foundation_name, approved) in enumerate(foundation_totals, start=1):
        grant = GrantEntity(
            entity_id=f"grant:{idx}",
            name=f"{foundation_name} Grant",
            lifecycle_state=LifecycleState.active,
            requested_amount=round(float(approved) * 1.2, 2),
            approved_amount=float(approved),
            status_note='Derived from foundation donor history',
        )
        grant.transition(LifecycleState.active, actor='system', reason='derived_from_foundation_donations')
        registry.upsert(grant)

    project_donation_counts = reporting_svc.project_donation_counts(org.id)
    for project in projects:
        related_donations = project_donation_counts.get(project.id, 0)
        outcome = OutcomeEntity(
            entity_id=f"outcome:program:{project.id}",
            name=f"{project.name} Donor Reach",
            lifecycle_state=LifecycleState.active,
            metric_name='contributing_donations',
            metric_value=float(related_donations),
            program_id=f"program:{project.id}",
        )
        outcome.transition(LifecycleState.active, actor='system', reason='derived_outcome_metric')
        registry.upsert(outcome)
        registry.link(source_id=f"program:{project.id}", relation='has_outcome', target_id=outcome.entity_id, actor='system')

    # Lightweight relationship stitching for usability in semantic retrieval.
    for beneficiary in beneficiaries:
        beneficiary_id = f"beneficiary:{beneficiary.id}"
        for project in projects:
            if beneficiary.program and project.program and beneficiary.program.strip().lower() == project.program.strip().lower():
                registry.link(
                    source_id=beneficiary_id,
                    relation='enrolled_in_program',
                    target_id=f"program:{project.id}",
                    actor='system',
                )

    return registry


def _issue_receipt_for_donation(donation: Donation, recipient_email: str | None = None):
    """Delegate receipt generation to DonationService.generate_receipt()."""
    svc = DonationService()
    return svc.generate_receipt(
        donation_id=donation.id,
        org_id=donation.organization_id,
        sent_to_email=recipient_email,
    )


@main_bp.route('/api/openapi.yaml', methods=['GET'])
@login_required
@roles_required('admin', 'staff', 'viewer')
def api_openapi_spec():
    spec_path = _openapi_spec_path()
    if not spec_path.exists():
        return {'error': 'OpenAPI spec not found.'}, 404

    return Response(spec_path.read_text(encoding='utf-8'), mimetype='application/yaml')


@main_bp.route('/api/docs', methods=['GET'])
@login_required
@roles_required('admin', 'staff', 'viewer')
def api_docs_index():
    spec_url = url_for('main.api_openapi_spec')
    swagger_url = url_for('main.api_swagger_ui')
    html = (
        '<!doctype html>'
        '<html><head><meta charset="utf-8"><title>NGO HomeSuite API Docs</title>'
        '<style>body{font-family:Segoe UI,Arial,sans-serif;margin:2rem;line-height:1.45;}code{background:#f3f3f3;padding:0.15rem 0.35rem;border-radius:4px;}a{color:#0b5cab;}</style>'
        '</head><body>'
        '<h1>NGO HomeSuite API Docs</h1>'
        '<p>Starter API contract for beta integrations.</p>'
        f'<p>OpenAPI spec: <a href="{spec_url}">{spec_url}</a></p>'
        f'<p>Interactive Swagger UI: <a href="{swagger_url}">{swagger_url}</a></p>'
        '<p>Use this spec with Swagger Editor or Redoc for interactive review.</p>'
        '</body></html>'
    )
    return Response(html, mimetype='text/html')


@main_bp.route('/api/swagger', methods=['GET'])
@login_required
@roles_required('admin', 'staff', 'viewer')
def api_swagger_ui():
    spec_url = url_for('main.api_openapi_spec')
    html = f"""<!doctype html>
<html>
    <head>
        <meta charset=\"utf-8\" />
        <title>NGO HomeSuite Swagger UI</title>
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
        <link rel=\"stylesheet\" href=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui.css\" />
        <style>
            body {{ margin: 0; background: #f6f8fb; }}
            .topbar {{ display: none; }}
        </style>
    </head>
    <body>
        <div id=\"swagger-ui\"></div>
        <script src=\"https://unpkg.com/swagger-ui-dist@5/swagger-ui-bundle.js\"></script>
        <script>
            window.addEventListener('load', function() {{
                SwaggerUIBundle({{
                    url: '{spec_url}',
                    dom_id: '#swagger-ui',
                    deepLinking: true,
                    docExpansion: 'list',
                    defaultModelsExpandDepth: 1,
                }});
            }});
        </script>
    </body>
</html>"""
    return Response(html, mimetype='text/html')


@main_bp.route('/api/domain/snapshot', methods=['GET'])
@login_required
@roles_required('admin', 'staff', 'viewer')
def api_domain_snapshot():
    org = _current_org()
    registry = _build_domain_registry_for_org(org)
    return {'ok': True, 'organization': org.name if org else None, 'entities': registry.snapshot()}


@main_bp.route('/api/semantic/context', methods=['GET'])
@login_required
@roles_required('admin', 'staff', 'viewer')
def api_semantic_context():
    task = (request.args.get('task') or '').strip()
    if not task:
        return {'error': 'task query parameter is required.'}, 400

    org = _current_org()
    registry = _build_domain_registry_for_org(org)
    memory = SemanticMemoryLayer()
    org_id = org.id if org else None
    memory.index_registry(registry, organization_id=org_id)
    return {'ok': True, 'context': memory.assemble_context(task=task, limit=6, organization_id=org_id)}


@main_bp.route('/workflows', methods=['GET'])
@login_required
@roles_required('admin', 'staff', 'viewer')
def workflows_page():
    return render_template('workflows.html', active_page='workflows')


@main_bp.route('/workflows/donation', methods=['POST'])
@login_required
@roles_required('admin', 'staff')
def workflow_donation_route():
    donation_id = request.form.get('donation_id', type=int)
    if not donation_id:
        flash('Donation ID is required.', 'error')
        return redirect(url_for('main.workflows_page'))
    org = _current_org()
    if not org:
        flash('No organization is available.', 'error')
        return redirect(url_for('main.workflows_page'))

    result = run_donation_receipt_followup_workflow(
        donation_id=donation_id,
        actor=getattr(current_user, 'username', 'workflow'),
        organization_id=org.id,
        db_path=_sqlite_db_file_path(),
    )
    flash('Workflow completed.' if result.get('ok') else str(result.get('error')), 'success' if result.get('ok') else 'error')
    return redirect(url_for('main.workflows_page'))


@main_bp.route('/workflows/grant', methods=['POST'])
@login_required
@roles_required('admin', 'staff')
def workflow_grant_route():
    grant_name = (request.form.get('grant_name') or '').strip()
    requested_amount = request.form.get('requested_amount', type=float) or 0.0
    if not grant_name:
        flash('Grant name is required.', 'error')
        return redirect(url_for('main.workflows_page'))

    result = run_grant_tracking_reporting_workflow(
        grant_name=grant_name,
        requested_amount=requested_amount,
        actor=getattr(current_user, 'username', 'workflow'),
        db_path=_sqlite_db_file_path(),
    )
    flash('Grant workflow completed.' if result.get('ok') else 'Grant workflow failed.', 'success' if result.get('ok') else 'error')
    return redirect(url_for('main.workflows_page'))


@main_bp.route('/workflows/program-impact', methods=['POST'])
@login_required
@roles_required('admin', 'staff')
def workflow_program_route():
    program_name = (request.form.get('program_name') or '').strip()
    beneficiary_count = request.form.get('beneficiary_count', type=int) or 0
    if not program_name:
        flash('Program name is required.', 'error')
        return redirect(url_for('main.workflows_page'))

    result = run_program_tracking_impact_workflow(
        program_name=program_name,
        beneficiary_count=beneficiary_count,
        outcomes=[{'metric_name': 'beneficiary_engagement', 'metric_value': beneficiary_count}],
        actor=getattr(current_user, 'username', 'workflow'),
        db_path=_sqlite_db_file_path(),
    )
    flash('Program workflow completed.' if result.get('ok') else 'Program workflow failed.', 'success' if result.get('ok') else 'error')
    return redirect(url_for('main.workflows_page'))


@main_bp.route('/api/workflows/donation/<int:donation_id>/run', methods=['POST'])
@login_required
@roles_required('admin', 'staff')
def api_workflow_donation_run(donation_id: int):
    org = _current_org()
    if not org:
        return {'ok': False, 'error': 'No organization is available.'}, 400

    result = run_donation_receipt_followup_workflow(
        donation_id=donation_id,
        actor=getattr(current_user, 'username', 'workflow'),
        organization_id=org.id,
        db_path=_sqlite_db_file_path(),
    )
    return (result, 200) if result.get('ok') else (result, 404)


@main_bp.route('/api/workflows/grant/run', methods=['POST'])
@login_required
@roles_required('admin', 'staff')
def api_workflow_grant_run():
    payload = request.get_json(silent=True) or {}
    grant_name = str(payload.get('grant_name', '')).strip()
    requested_amount = float(payload.get('requested_amount', 0.0) or 0.0)
    if not grant_name:
        return {'error': 'grant_name is required.'}, 400
    return run_grant_tracking_reporting_workflow(
        grant_name=grant_name,
        requested_amount=requested_amount,
        actor=getattr(current_user, 'username', 'workflow'),
        db_path=_sqlite_db_file_path(),
    )


@main_bp.route('/api/workflows/program-impact/run', methods=['POST'])
@login_required
@roles_required('admin', 'staff')
def api_workflow_program_run():
    payload = request.get_json(silent=True) or {}
    program_name = str(payload.get('program_name', '')).strip()
    beneficiary_count = int(payload.get('beneficiary_count', 0) or 0)
    outcomes = payload.get('outcomes') if isinstance(payload.get('outcomes'), list) else []
    if not program_name:
        return {'error': 'program_name is required.'}, 400
    return run_program_tracking_impact_workflow(
        program_name=program_name,
        beneficiary_count=beneficiary_count,
        outcomes=outcomes,
        actor=getattr(current_user, 'username', 'workflow'),
        db_path=_sqlite_db_file_path(),
    )


@main_bp.route('/')
def index():
    """Home/landing page."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('index.html')


@main_bp.route('/locale/<string:lang>', methods=['POST'])
def set_locale(lang: str):
    normalized = (lang or '').strip().lower()
    if normalized in _SUPPORTED_LOCALES:
        session['lang'] = normalized

    next_path = (request.args.get('next') or request.form.get('next') or request.referrer or url_for('main.index')).strip()
    if not next_path.startswith('/'):
        next_path = url_for('main.index')
    return redirect(next_path)


@main_bp.route('/give', methods=['GET', 'POST'])
def public_give():
    """Public donation page for self-service online giving."""
    org = get_first_active_organization()
    if not org:
        flash('Donation portal is not available yet. Please contact the organization.', 'error')
        return redirect(url_for('main.index'))

    form = PublicDonationForm()
    if request.method == 'GET':
        purpose_prefill = (request.args.get('purpose') or '').strip()
        amount_prefill = (request.args.get('amount') or '').strip()
        if purpose_prefill and not form.purpose.data:
            form.purpose.data = purpose_prefill[:120]
        if amount_prefill and not form.amount.data:
            try:
                parsed_amount = float(amount_prefill)
            except ValueError:
                parsed_amount = 0.0
            if parsed_amount > 0:
                form.amount.data = round(parsed_amount, 2)

    if form.validate_on_submit():
        donor_email = (form.donor_email.data or '').strip() or None

        _donor_svc = DonorService()
        donor, _created = _donor_svc.find_or_create_by_email(
            org.id,
            donor_email,
            form.donor_name.data.strip(),
            phone=(form.donor_phone.data or '').strip() or None,
            donor_type='individual',
            notes='Created from public donation portal.',
        )

        _donation_svc = DonationService()
        donation = _donation_svc.create_donation(
            org_id=org.id,
            donor_name=donor.name,
            amount=form.amount.data,
            currency=form.currency.data,
            donor_email=donor.email,
            donor_phone=donor.phone,
            donor_id=donor.id,
            payment_method=form.payment_method.data,
            purpose=form.purpose.data or 'General Fund',
            notes='Public portal donation',
            status='received',
        )

        if form.make_recurring.data:
            if not donor.email:
                flash('Recurring donations require an email to process retries and receipts.', 'error')
                return render_template('public_donation_form.html', form=form, active_page='give')
            _donation_svc.create_recurring_plan(
                org.id,
                donor.id,
                amount=form.amount.data,
                currency=form.currency.data,
                payment_method=form.payment_method.data,
                purpose=form.purpose.data or 'General Fund',
                frequency=form.recurring_frequency.data or 'monthly',
                next_charge_date=_next_charge_date(date.today(), form.recurring_frequency.data or 'monthly'),
            )

        # Advance to 'processed' so generate_receipt() is satisfied, then issue.
        _donation_svc.update_status(donation.id, org.id, 'processed', actor_id=None)
        _issue_receipt_for_donation(donation, recipient_email=donor.email)

        flash('Thank you for your donation. Your receipt has been generated.', 'success')
        return redirect(url_for('main.public_give'))

    return render_template('public_donation_form.html', form=form, active_page='give')


@main_bp.route('/setup', methods=['GET', 'POST'])
@login_required
@roles_required('admin')
def org_setup():
    """First-time organization setup wizard."""
    from ngo_homesuite.models.core import Organization, User, db
    import re

    org = _current_org()
    if org:
        flash('Organization already configured. Use Settings to make changes.', 'info')
        return redirect(url_for('main.org_settings'))

    error = None
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip() or None
        phone = request.form.get('phone', '').strip() or None
        website = request.form.get('website', '').strip() or None
        country = request.form.get('country', '').strip() or None
        city = request.form.get('city', '').strip() or None
        mission = request.form.get('mission', '').strip() or None

        if not name:
            error = 'Organization name is required.'
        else:
            slug = re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-') or 'org'
            # ensure unique slug
            existing = db.session.query(Organization).filter_by(slug=slug).first()
            if existing:
                slug = f"{slug}-{existing.id + 1}"

            new_org = Organization(
                name=name, slug=slug, email=email, phone=phone,
                website=website, country=country, city=city,
                mission=mission, is_active=True,
            )
            db.session.add(new_org)
            db.session.flush()  # get new_org.id

            # Assign org to current user
            user = db.session.get(User, current_user.id)
            user.organization_id = new_org.id
            db.session.commit()
            flash(f'Organization "{name}" created successfully.', 'success')
            return redirect(url_for('main.dashboard'))

    return render_template('setup.html', error=error, active_page=None)


@main_bp.route('/settings', methods=['GET', 'POST'])
@login_required
@roles_required('admin')
def org_settings():
    """Organization settings page."""
    from ngo_homesuite.models.core import Organization, db

    org = _current_org()
    error = None
    success = False

    if request.method == 'POST' and org:
        import re
        name = request.form.get('name', '').strip()
        if not name:
            error = 'Organization name is required.'
        else:
            org.name = name
            org.email = request.form.get('email', '').strip() or None
            org.phone = request.form.get('phone', '').strip() or None
            org.website = request.form.get('website', '').strip() or None
            org.country = request.form.get('country', '').strip() or None
            org.city = request.form.get('city', '').strip() or None
            org.mission = request.form.get('mission', '').strip() or None
            db.session.commit()
            flash('Settings saved.', 'success')
            success = True

    return render_template('settings.html', org=org, error=error, success=success, active_page='settings')


@main_bp.route('/dashboard')
@login_required
def dashboard():
    """User dashboard with summary cards."""

    org = _current_org()
    if org:
        summary = ReportingService().organization_dashboard_summary(org.id, recent_donations_limit=5)
        stats = {
            'organization': org,
            **summary,
        }
    else:
        stats = {
            'organization': None,
            'beneficiary_count': 0,
            'project_count': 0,
            'donor_count': 0,
            'total_donations': 0,
            'total_budget': 0,
            'total_expenses': 0,
            'net_cashflow': 0,
            'total_funds': 0,
            'recent_donations': [],
        }
    
    ai_context = {
        'active_page': 'dashboard',
        'organization': org.name if org else None,
        'donor_count': stats['donor_count'],
        'total_donations': stats['total_donations'],
        'total_expenses': stats['total_expenses'],
        'project_count': stats['project_count'],
        'total_funds': stats['total_funds'],
    }
    return render_template('dashboard.html', stats=stats, active_page='dashboard', ai_context=ai_context)


@main_bp.route('/activity')
@login_required
def activity_feed():
    """Organization-wide activity feed dashboard."""
    org = _current_org()
    if not org:
        flash('No organization found for your account.', 'error')
        return redirect(url_for('main.dashboard'))

    return render_template(
        'activity_feed.html',
        active_page='activity',
        organization_name=org.name,
    )


@main_bp.route('/tony-scoring')
@login_required
@roles_required('admin', 'staff')
def tony_scoring():
    """TONY advanced grant scoring dashboard."""
    org = _current_org()
    if not org:
        flash('No organization found for your account.', 'error')
        return redirect(url_for('main.dashboard'))

    return render_template(
        'tony_scoring.html',
        active_page='tony_scoring',
        organization_name=org.name,
    )


@main_bp.route('/tasks/board')
@login_required
def task_board():
    """Operational task board powered by v2 task + reminder APIs."""
    org = _current_org()
    if not org:
        flash('No organization found for your account.', 'error')
        return redirect(url_for('main.dashboard'))

    ai_context = {
        'active_page': 'task_board',
        'organization': org.name,
    }
    return render_template(
        'task_board.html',
        active_page='task_board',
        organization_name=org.name,
        ai_context=ai_context,
    )


@main_bp.route('/mobile/intake', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff', 'volunteer')
def mobile_intake():
    org = _current_org()
    if not org:
        flash('No organization found for your account.', 'error')
        return redirect(url_for('main.dashboard'))

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip().lower()
        if action == 'beneficiary':
            first_name = (request.form.get('first_name') or '').strip()
            last_name = (request.form.get('last_name') or '').strip()
            if not first_name or not last_name:
                flash('Beneficiary first and last name are required.', 'error')
            else:
                create_beneficiary(
                    org.id,
                    first_name,
                    last_name,
                    phone=(request.form.get('phone') or '').strip() or None,
                    city=(request.form.get('city') or '').strip() or None,
                    program=(request.form.get('program') or '').strip() or None,
                    status=(request.form.get('status') or 'active').strip() or 'active',
                    notes=(request.form.get('notes') or '').strip() or None,
                )
                flash('Beneficiary quick intake captured.', 'success')
                return redirect(url_for('main.mobile_intake'))
        elif action == 'volunteer':
            name = (request.form.get('volunteer_name') or '').strip()
            if not name:
                flash('Volunteer name is required.', 'error')
            else:
                create_volunteer(
                    org.id,
                    name,
                    email=(request.form.get('volunteer_email') or '').strip() or None,
                    phone=(request.form.get('volunteer_phone') or '').strip() or None,
                    status=(request.form.get('volunteer_status') or 'active').strip() or 'active',
                )
                flash('Volunteer quick registration captured.', 'success')
                return redirect(url_for('main.mobile_intake'))
        else:
            flash('Unsupported intake action.', 'error')

    recent_beneficiaries = list_beneficiaries(org.id)[:8]
    recent_volunteers = list_recent_volunteers(org.id, limit=8)

    ai_context = {
        'active_page': 'mobile_intake',
        'organization': org.name,
        'recent_beneficiaries': len(recent_beneficiaries),
        'recent_volunteers': len(recent_volunteers),
    }
    return render_template(
        'mobile_intake.html',
        active_page='mobile_intake',
        beneficiaries=recent_beneficiaries,
        volunteers=recent_volunteers,
        ai_context=ai_context,
    )


@main_bp.route('/p2p/manage', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def p2p_manage():
    from ngo_homesuite.services.p2p_service import close_page, create_page, list_pages, publish_page

    org = _current_org()
    if not org:
        flash('No organization found for your account.', 'error')
        return redirect(url_for('main.dashboard'))

    form = P2PPageForm()
    donors = DonorService().list_all_donors(org.id)
    form.donor_id.choices = [(int(d.id), d.name) for d in donors]

    status_filter = (request.args.get('status') or '').strip().lower() or None

    if request.method == 'POST':
        action = (request.form.get('action') or '').strip().lower()

        if action in {'publish', 'close'}:
            page_id = request.form.get('page_id', type=int)
            if not page_id:
                flash('A valid fundraiser page id is required.', 'error')
                return redirect(url_for('main.p2p_manage'))

            try:
                if action == 'publish':
                    publish_page(page_id, org.id)
                    flash('Fundraiser published.', 'success')
                else:
                    close_page(page_id, org.id)
                    flash('Fundraiser closed.', 'success')
            except NotFound:
                flash('Fundraiser page not found for this organization.', 'error')
            except ValueError as exc:
                flash(str(exc), 'error')
            return redirect(url_for('main.p2p_manage'))

        if not donors:
            flash('Create at least one donor before creating a fundraiser page.', 'error')
            pages = list_pages(org.id, status=status_filter)
            return render_template('p2p_manage.html', form=form, pages=pages, active_page='p2p')

        if form.validate_on_submit():
            try:
                create_page(
                    organization_id=org.id,
                    donor_id=int(form.donor_id.data),
                    title=(form.title.data or '').strip(),
                    goal_amount=float(form.goal_amount.data or 0.0),
                    story=(form.story.data or '').strip() or None,
                    campaign_slug=(form.campaign_slug.data or '').strip() or None,
                )
                flash('Fundraiser page created.', 'success')
                return redirect(url_for('main.p2p_manage'))
            except ValueError:
                flash('Invalid fundraiser data.', 'error')
        else:
            flash('Please fix the highlighted form issues.', 'error')

    pages = list_pages(org.id, status=status_filter)
    ai_context = {
        'active_page': 'p2p',
        'organization': org.name,
        'p2p_page_count': len(pages),
    }
    return render_template('p2p_manage.html', form=form, pages=pages, active_page='p2p', ai_context=ai_context)


@main_bp.route('/donors')
@login_required
def donors_list():
    org = _current_org()
    query = request.args.get('q', '').strip()
    donor_type = request.args.get('donor_type', '').strip()

    donors = []
    if org:
        donors = DonorService().list_all_donors(org.id, donor_type=donor_type or None, search=query or None)
    delete_form = ConfirmDeleteForm()
    ai_context = {
        'active_page': 'donors',
        'organization': org.name if org else None,
        'donor_count': len(donors),
    }
    ctx = dict(
        donors=donors,
        delete_form=delete_form,
        active_page='donors',
        filter_q=query,
        filter_donor_type=donor_type,
        ai_context=ai_context,
    )
    if request.headers.get('HX-Request'):
        return render_template('_donors_rows.html', **ctx)
    return render_template('donors.html', **ctx)


@main_bp.route('/donors/<int:donor_id>')
@login_required
def donor_detail(donor_id: int):
    from ngo_homesuite.services.activity_timeline_service import ActivityTimelineService

    org = _current_org()
    donor_summary = ReportingService().donor_profile_summary(org.id, donor_id, recent_limit=10)
    donor = donor_summary['donor']

    ai_context = {
        'active_page': 'donors',
        'organization': org.name if org else None,
        'donor_id': donor.id,
        'donor_name': donor.name,
        'donation_count': donor_summary['donation_count'],
        'donation_total': donor_summary['donation_total'],
    }

    donor_ai_insights = None
    try:
        donor_ai_insights = CopilotToolRegistry().execute(
            "summarize_donor",
            {"donor_id": donor.id},
            {
                "organization_id": org.id,
                "actor": getattr(current_user, "username", "web"),
            },
        )
        if isinstance(donor_ai_insights, dict) and donor_ai_insights.get("error"):
            donor_ai_insights = None
    except Exception:
        donor_ai_insights = None

    activity_query = (request.args.get('activity_q') or '').strip()

    # Fetch unified activity timeline
    timeline_items = []
    try:
        timeline_items = ActivityTimelineService.get_donor_timeline(
            org.id,
            donor_id,
            limit=25,
            search_query=activity_query or None,
        )
    except Exception:
        # If timeline fetch fails, continue without it
        timeline_items = []

    return render_template(
        'donor_detail.html',
        donor=donor,
        donation_count=donor_summary['donation_count'],
        donation_total=donor_summary['donation_total'],
        first_gift_date=donor_summary['first_gift_date'],
        last_gift_date=donor_summary['last_gift_date'],
        active_recurring_plans=donor_summary['active_recurring_plans'],
        recent_donations=donor_summary['recent_donations'],
        recurring_plans=donor_summary['recurring_plans'],
        timeline_items=timeline_items,
        activity_query=activity_query,
        active_page='donors',
        ai_context=ai_context,
        donor_ai_insights=donor_ai_insights,
    )


@main_bp.route('/donors/export/<string:file_type>')
@login_required
def donors_export(file_type: str):
    org = _current_org()
    if not org:
        flash('No organization available for export.', 'error')
        return redirect(url_for('main.donors_list'))

    query = request.args.get('q', '').strip()
    donor_type = request.args.get('donor_type', '').strip()
    donors = DonorService().list_all_donors(org.id, donor_type=donor_type or None, search=query or None)
    headers = ['ID', 'Name', 'Email', 'Phone', 'Type', 'Created At']
    rows = [
        [d.id, d.name, d.email or '', d.phone or '', d.donor_type, d.created_at.strftime('%Y-%m-%d %H:%M:%S') if d.created_at else '']
        for d in donors
    ]

    if file_type == 'csv':
        data = _build_csv_bytes(headers, rows)
        return send_file(BytesIO(data), mimetype='text/csv', as_attachment=True, download_name='donors.csv')
    if file_type == 'xlsx':
        data = _build_xlsx_bytes('Donors', headers, rows)
        return send_file(
            BytesIO(data),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='donors.xlsx',
        )
    flash('Unsupported export type.', 'error')
    return redirect(url_for('main.donors_list'))


@main_bp.route('/donors/new', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def donor_create():
    org = _current_org()
    if not org:
        flash('No organization is available. Please seed data first.', 'error')
        return redirect(url_for('main.dashboard'))

    form = DonorForm()
    if form.validate_on_submit():
        DonorService().create_donor(
            org.id,
            form.name.data,
            email=form.email.data,
            phone=form.phone.data,
            donor_type=form.donor_type.data,
            notes=form.notes.data,
        )
        flash('Donor created successfully.', 'success')
        return redirect(url_for('main.donors_list'))

    return render_template('donor_form.html', form=form, is_edit=False, active_page='donors')


@main_bp.route('/donors/<int:donor_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def donor_edit(donor_id: int):
    org = _current_org()
    donor = DonorService().get_donor(donor_id, org.id)
    form = DonorForm(obj=donor)

    if form.validate_on_submit():
        DonorService().update_donor(
            donor.id,
            org.id,
            name=form.name.data,
            email=form.email.data,
            phone=form.phone.data,
            donor_type=form.donor_type.data,
            notes=form.notes.data,
        )
        flash('Donor updated successfully.', 'success')
        return redirect(url_for('main.donors_list'))

    return render_template('donor_form.html', form=form, is_edit=True, donor=donor, active_page='donors')


@main_bp.route('/donors/<int:donor_id>/delete', methods=['POST'])
@login_required
@roles_required('admin', 'staff')
def donor_delete(donor_id: int):
    form = ConfirmDeleteForm()
    if not form.validate_on_submit():
        flash('Invalid delete request.', 'error')
        return redirect(url_for('main.donors_list'))

    org = _current_org()
    try:
        DonorService().delete_donor(donor_id, org.id)
    except ValueError:
        flash('Cannot delete donor with existing donations. Edit donor instead.', 'error')
        return redirect(url_for('main.donors_list'))
    flash('Donor deleted successfully.', 'success')
    return redirect(url_for('main.donors_list'))


@main_bp.route('/donors/dedupe')
@login_required
@roles_required('admin', 'staff')
def donor_dedupe():
    """Surface probable duplicate donors for manual merge."""
    org = _current_org()
    if not org:
        flash('No organization is available.', 'error')
        return redirect(url_for('main.donors_list'))

    donors = DonorService().list_all_donors(org.id)
    seen_email = {}
    seen_name_phone = {}
    candidates = []
    pair_keys = set()

    for donor in donors:
        email_key = _normalize_text(donor.email)
        name_phone_key = f"{_normalize_text(donor.name)}::{_normalize_phone(donor.phone)}"

        if email_key:
            first = seen_email.get(email_key)
            if first and first.id != donor.id:
                key = tuple(sorted([first.id, donor.id]))
                if key not in pair_keys:
                    pair_keys.add(key)
                    candidates.append({'primary': first, 'duplicate': donor, 'reason': 'Matching email'})
            else:
                seen_email[email_key] = donor

        # Name + phone is a fallback when email is missing.
        if _normalize_phone(donor.phone):
            first = seen_name_phone.get(name_phone_key)
            if first and first.id != donor.id:
                key = tuple(sorted([first.id, donor.id]))
                if key not in pair_keys:
                    pair_keys.add(key)
                    candidates.append({'primary': first, 'duplicate': donor, 'reason': 'Matching name + phone'})
            else:
                seen_name_phone[name_phone_key] = donor

    return render_template('donor_dedupe.html', candidates=candidates, active_page='donors')


@main_bp.route('/donors/import', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def donor_import():
    """CSV/XLSX donor import with field mapping, preview, and duplicate suggestions."""
    org = _current_org()
    if not org:
        flash('No organization is available.', 'error')
        return redirect(url_for('main.donors_list'))

    headers: list[str] = []
    mapping = {'name': '', 'email': '', 'phone': '', 'donor_type': '', 'notes': ''}
    preview: dict[str, object] | None = None
    cache_id: str | None = None
    filename: str | None = None

    if request.method == 'POST':
        action = (request.form.get('action') or 'preview').strip().lower()

        if action == 'preview':
            uploaded = request.files.get('import_file')
            if uploaded is None or not uploaded.filename:
                flash('Please choose a CSV or XLSX file to preview.', 'error')
                return render_template('donor_import.html', active_page='donors')

            filename = uploaded.filename
            ext = Path(filename).suffix.lower()
            if ext not in _DONOR_IMPORT_ALLOWED_EXTENSIONS:
                flash('Unsupported file type. Use CSV or XLSX.', 'error')
                return render_template('donor_import.html', active_page='donors')

            cache_id = uuid.uuid4().hex
            cache_path = _donor_import_cache_dir() / f'{cache_id}{ext}'
            uploaded.save(cache_path)

            try:
                headers, rows = _parse_donor_import_file(cache_path)
            except Exception:
                cache_path.unlink(missing_ok=True)
                flash('Could not parse file. Please check the format and try again.', 'error')
                return render_template('donor_import.html', active_page='donors')

            if not headers:
                cache_path.unlink(missing_ok=True)
                flash('No header row found in file.', 'error')
                return render_template('donor_import.html', active_page='donors')

            if not rows:
                cache_path.unlink(missing_ok=True)
                flash('No data rows found in file.', 'error')
                return render_template('donor_import.html', active_page='donors')

            mapping = _extract_donor_import_mapping(request.form)
            guessed = _guess_donor_import_mapping(headers)
            for key, guessed_value in guessed.items():
                if not mapping.get(key):
                    mapping[key] = guessed_value

            preview = _build_donor_import_preview(org.id, rows, mapping)
            return render_template(
                'donor_import.html',
                active_page='donors',
                headers=headers,
                mapping=mapping,
                preview=preview,
                cache_id=cache_id,
                source_filename=filename,
            )

        if action == 'import':
            cache_id = (request.form.get('cache_id') or '').strip()
            if not cache_id:
                flash('Import preview expired. Please upload and preview again.', 'error')
                return redirect(url_for('main.donor_import'))

            cache_dir = _donor_import_cache_dir()
            candidates = list(cache_dir.glob(f'{cache_id}.*'))
            if not candidates:
                flash('Import preview cache not found. Please preview again.', 'error')
                return redirect(url_for('main.donor_import'))

            cache_path = candidates[0]
            filename = cache_path.name
            try:
                headers, rows = _parse_donor_import_file(cache_path)
            except Exception:
                cache_path.unlink(missing_ok=True)
                flash('Failed to read cached import file. Please preview again.', 'error')
                return redirect(url_for('main.donor_import'))

            mapping = _extract_donor_import_mapping(request.form)
            if not mapping.get('name'):
                flash('Please map a Name column before importing.', 'error')
                preview = _build_donor_import_preview(org.id, rows, mapping)
                return render_template(
                    'donor_import.html',
                    active_page='donors',
                    headers=headers,
                    mapping=mapping,
                    preview=preview,
                    cache_id=cache_id,
                    source_filename=filename,
                )

            preview = _build_donor_import_preview(org.id, rows, mapping)
            result = _apply_donor_import(org.id, preview['rows'])
            cache_path.unlink(missing_ok=True)

            flash(
                (
                    f"Import completed: created {result['created']}, "
                    f"skipped duplicates {result['skipped_duplicates']}, "
                    f"skipped errors {result['skipped_errors']}."
                ),
                'success',
            )
            return redirect(url_for('main.donors_list'))

    return render_template('donor_import.html', active_page='donors')


@main_bp.route('/donors/merge', methods=['POST'])
@login_required
@roles_required('admin', 'staff')
def donor_merge():
    """Merge a duplicate donor into a primary donor."""
    org = _current_org()
    if not org:
        flash('No organization is available.', 'error')
        return redirect(url_for('main.donors_list'))

    primary_id = request.form.get('primary_id', type=int)
    duplicate_id = request.form.get('duplicate_id', type=int)
    if not primary_id or not duplicate_id or primary_id == duplicate_id:
        flash('Invalid merge request.', 'error')
        return redirect(url_for('main.donor_dedupe'))

    try:
        DonorService().merge_donors(org.id, primary_id, duplicate_id)
    except ValueError:
        flash('Invalid merge request.', 'error')
        return redirect(url_for('main.donor_dedupe'))
    flash('Duplicate donor merged successfully.', 'success')
    return redirect(url_for('main.donors_list'))


@main_bp.route('/donations/new', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def donation_create():
    org = _current_org()
    if not org:
        flash('No organization is available. Please seed data first.', 'error')
        return redirect(url_for('main.dashboard'))

    donor_service = DonorService()
    donation_service = DonationService()
    donor_options = [(0, 'Select a donor')] + [(d.id, d.name) for d in donor_service.list_all_donors(org.id)]
    project_options = [(0, 'General / None')] + [(p.id, p.name) for p in ProjectService().list_all_projects(org.id)]
    fund_options = [(0, 'General / None')] + [
        (f.id, f.name)
        for f in FundService().list_funds(org.id, active_only=True, page=1, per_page=500)['items']
    ]

    form = DonationForm()
    form.donor_id.choices = donor_options
    form.project_id.choices = project_options
    form.fund_id.choices = fund_options

    if form.validate_on_submit():
        try:
            donor = donor_service.get_donor(form.donor_id.data, org.id)
        except DonorNotFound:
            donor = None
        if donor is None:
            flash('Please select a valid donor.', 'error')
            return render_template('donation_form.html', form=form, active_page='donations')

        try:
            donation = donation_service.create_donation(
                org_id=org.id,
                donor_name=donor.name,
                amount=form.amount.data,
                currency=form.currency.data,
                donor_email=donor.email,
                donor_phone=donor.phone,
                donor_id=donor.id,
                project_id=form.project_id.data or None,
                fund_id=form.fund_id.data or None,
                payment_method=form.payment_method.data,
                reference_number=form.reference_number.data or None,
                purpose=form.purpose.data,
                notes=form.notes.data,
                status='received',
            )
            donation_service.update_status(donation.id, org.id, 'processed', actor_id=getattr(current_user, 'id', None))
            _issue_receipt_for_donation(donation, recipient_email=donor.email)
        except ValueError as exc:
            flash(str(exc), 'error')
            return render_template('donation_form.html', form=form, active_page='donations')
        except DonationConcurrencyError:
            flash('This donation was updated by another user. Please reload and try again.', 'error')
            return render_template('donation_form.html', form=form, active_page='donations')
        flash('Donation recorded successfully and receipt generated.', 'success')
        return redirect(url_for('main.dashboard'))

    return render_template('donation_form.html', form=form, active_page='donations')


@main_bp.route('/donations/recurring', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def recurring_donations():
    """Create and view recurring donation plans."""
    org = _current_org()
    if not org:
        flash('No organization is available.', 'error')
        return redirect(url_for('main.dashboard'))

    donor_service = DonorService()
    donation_service = DonationService()
    donor_options = [(0, 'Select a donor')] + [
        (d.id, f"{d.name} ({d.email or 'no email'})")
        for d in donor_service.list_all_donors(org.id)
    ]

    form = RecurringDonationForm()
    form.donor_id.choices = donor_options

    if form.validate_on_submit():
        try:
            donor = donor_service.get_donor(form.donor_id.data, org.id)
        except DonorNotFound:
            donor = None
        if donor is None:
            flash('Please select a valid donor.', 'error')
            return render_template('recurring_donations.html', form=form, plans=[], active_page='donations')

        try:
            donation_service.create_recurring_plan(
                org.id,
                donor.id,
                amount=form.amount.data,
                currency=form.currency.data,
                payment_method=form.payment_method.data,
                purpose=form.purpose.data,
                frequency=form.frequency.data,
                next_charge_date=_next_charge_date(date.today(), form.frequency.data),
            )
        except ValueError as exc:
            flash(str(exc), 'error')
            plans = donation_service.list_recurring_plans(org.id)
            return render_template('recurring_donations.html', form=form, plans=plans, active_page='donations')
        except DonationConcurrencyError:
            flash('Recurring plan changed while saving. Please try again.', 'error')
            plans = donation_service.list_recurring_plans(org.id)
            return render_template('recurring_donations.html', form=form, plans=plans, active_page='donations')
        flash('Recurring donation plan created.', 'success')
        return redirect(url_for('main.recurring_donations'))

    plans = donation_service.list_recurring_plans(org.id)
    return render_template('recurring_donations.html', form=form, plans=plans, active_page='donations')


@main_bp.route('/donations/recurring/process', methods=['POST'])
@login_required
@roles_required('admin', 'staff')
def process_recurring_donations():
    """Run due recurring plans and handle failures for missing payment contact data."""
    org = _current_org()
    if not org:
        flash('No organization is available.', 'error')
        return redirect(url_for('main.dashboard'))

    result = DonationService().process_due_recurring_plans(org.id, run_date=date.today())
    processed = result.get('processed', 0)
    failed = result.get('failed', 0)
    flash(f'Recurring processing complete: {processed} processed, {failed} failed.', 'info')
    return redirect(url_for('main.recurring_donations'))


@main_bp.route('/donations')
@login_required
def donations_list():
    org = _current_org()
    query = request.args.get('q', '').strip()
    payment_method = request.args.get('payment_method', '').strip()
    status = request.args.get('status', '').strip()
    min_amount = _parse_float(request.args.get('min_amount', ''))
    max_amount = _parse_float(request.args.get('max_amount', ''))

    donations = []
    if org:
        donations = DonationService().list_filtered_donations(
            org.id,
            search=query or None,
            payment_method=payment_method or None,
            status=status or None,
            min_amount=min_amount,
            max_amount=max_amount,
        )
    ai_context = {
        'active_page': 'donations',
        'organization': org.name if org else None,
        'donation_count': len(donations),
        'total_donations': sum(float(d.amount or 0) for d in donations),
    }
    return render_template(
        'donations.html',
        donations=donations,
        active_page='donations',
        filter_q=query,
        filter_payment_method=payment_method,
        filter_status=status,
        filter_min_amount=request.args.get('min_amount', ''),
        filter_max_amount=request.args.get('max_amount', ''),
        ai_context=ai_context,
    )


@main_bp.route('/donations/export/<string:file_type>')
@login_required
def donations_export(file_type: str):
    org = _current_org()
    if not org:
        flash('No organization available for export.', 'error')
        return redirect(url_for('main.donations_list'))

    query = request.args.get('q', '').strip()
    payment_method = request.args.get('payment_method', '').strip()
    status = request.args.get('status', '').strip()
    min_amount = _parse_float(request.args.get('min_amount', ''))
    max_amount = _parse_float(request.args.get('max_amount', ''))

    donations = DonationService().list_filtered_donations(
        org.id,
        search=query or None,
        payment_method=payment_method or None,
        status=status or None,
        min_amount=min_amount,
        max_amount=max_amount,
    )
    headers = ['ID', 'Date', 'Donor', 'Email', 'Amount', 'Currency', 'Status', 'Payment Method', 'Purpose', 'Reference']
    rows = [
        [
            d.id,
            d.donation_date.strftime('%Y-%m-%d') if d.donation_date else '',
            d.donor_name,
            d.donor_email or '',
            d.amount,
            d.currency,
            d.status,
            d.payment_method or '',
            d.purpose or '',
            d.reference_number or '',
        ]
        for d in donations
    ]

    if file_type == 'csv':
        data = _build_csv_bytes(headers, rows)
        return send_file(BytesIO(data), mimetype='text/csv', as_attachment=True, download_name='donations.csv')
    if file_type == 'xlsx':
        data = _build_xlsx_bytes('Donations', headers, rows)
        return send_file(
            BytesIO(data),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='donations.xlsx',
        )
    if file_type == 'iif':
        iif_rows = [
            [
                d.id,
                d.donation_date.strftime('%Y-%m-%d') if d.donation_date else '',
                d.donor_name or 'Unknown Donor',
                d.amount,
                d.purpose or 'Donation',
            ]
            for d in donations
        ]
        data = _build_iif_bytes('DEPOSIT', iif_rows)
        return send_file(BytesIO(data), mimetype='text/plain', as_attachment=True, download_name='donations.iif')
    flash('Unsupported export type.', 'error')
    return redirect(url_for('main.donations_list'))


@main_bp.route('/donations/<int:donation_id>/receipt')
@login_required
def donation_receipt(donation_id: int):
    org = _current_org()
    try:
        donation = DonationService().get_donation(donation_id, org.id)
    except DonationNotFound:
        abort(404)
    donor = donation.donor

    donation_payload = {
        'amount_cents': int(round(float(donation.amount) * 100)),
        'currency': donation.currency,
        'received_at': donation.donation_date.strftime('%Y-%m-%d'),
    }
    donor_payload = {
        'name': donor.name if donor else donation.donor_name,
        'address': '',
    }

    pdf_data = generate_receipt_pdf_bytes(donation_payload, donor_payload)
    file_name = f"receipt-{donation.id}.pdf"
    return send_file(
        BytesIO(pdf_data),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=file_name,
    )


@main_bp.route('/expenses')
@login_required
def expenses_list():
    org = _current_org()
    query = request.args.get('q', '').strip()
    min_amount = _parse_float(request.args.get('min_amount', ''))
    max_amount = _parse_float(request.args.get('max_amount', ''))

    expenses = []
    if org:
        expenses = ExpenseService().list_filtered_expenses(
            org.id,
            search=query or None,
            min_amount=min_amount,
            max_amount=max_amount,
        )
    ai_context = {
        'active_page': 'expenses',
        'organization': org.name if org else None,
        'expense_count': len(expenses),
        'total_expenses': sum(float(e.amount or 0) for e in expenses),
    }
    return render_template(
        'expenses.html',
        expenses=expenses,
        active_page='expenses',
        filter_q=query,
        filter_min_amount=request.args.get('min_amount', ''),
        filter_max_amount=request.args.get('max_amount', ''),
        ai_context=ai_context,
    )


@main_bp.route('/expenses/new', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def expense_create():
    org = _current_org()
    if not org:
        flash('No organization is available. Please seed data first.', 'error')
        return redirect(url_for('main.dashboard'))

    project_options = [(0, 'General / None')] + [(p.id, p.name) for p in ProjectService().list_all_projects(org.id)]
    fund_options = [(0, 'General / None')] + [
        (f.id, f.name)
        for f in FundService().list_all_funds(org.id, active_only=True)
    ]

    form = ExpenseForm()
    form.project_id.choices = project_options
    form.fund_id.choices = fund_options

    if form.validate_on_submit():
        try:
            ExpenseService().create_expense(
                org.id,
                project_id=form.project_id.data or None,
                fund_id=form.fund_id.data or None,
                amount=form.amount.data,
                currency=form.currency.data,
                payee=form.payee.data,
                description=form.description.data,
            )
        except ValueError as exc:
            flash(f'Could not record expense: {exc}', 'error')
            return render_template('expense_form.html', form=form, active_page='expenses')
        flash('Expense recorded successfully.', 'success')
        return redirect(url_for('main.expenses_list'))

    return render_template('expense_form.html', form=form, active_page='expenses')


@main_bp.route('/expenses/export/<string:file_type>')
@login_required
def expenses_export(file_type: str):
    org = _current_org()
    if not org:
        flash('No organization available for export.', 'error')
        return redirect(url_for('main.expenses_list'))

    query = request.args.get('q', '').strip()
    min_amount = _parse_float(request.args.get('min_amount', ''))
    max_amount = _parse_float(request.args.get('max_amount', ''))

    expenses = ExpenseService().list_filtered_expenses(
        org.id,
        search=query or None,
        min_amount=min_amount,
        max_amount=max_amount,
    )
    headers = ['ID', 'Date', 'Payee', 'Amount', 'Currency', 'Project', 'Fund', 'Description']
    rows = [
        [
            e.id,
            e.paid_at.strftime('%Y-%m-%d') if e.paid_at else '',
            e.payee or '',
            e.amount,
            e.currency,
            e.project.name if e.project else '',
            e.fund.name if e.fund else '',
            e.description or '',
        ]
        for e in expenses
    ]

    if file_type == 'csv':
        data = _build_csv_bytes(headers, rows)
        return send_file(BytesIO(data), mimetype='text/csv', as_attachment=True, download_name='expenses.csv')
    if file_type == 'xlsx':
        data = _build_xlsx_bytes('Expenses', headers, rows)
        return send_file(
            BytesIO(data),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='expenses.xlsx',
        )
    if file_type == 'iif':
        iif_rows = [
            [
                e.id,
                e.paid_at.strftime('%Y-%m-%d') if e.paid_at else '',
                (e.payee or e.description or 'Expense'),
                e.amount,
                e.description or 'Expense',
            ]
            for e in expenses
        ]
        data = _build_iif_bytes('CHECK', iif_rows)
        return send_file(BytesIO(data), mimetype='text/plain', as_attachment=True, download_name='expenses.iif')
    flash('Unsupported export type.', 'error')
    return redirect(url_for('main.expenses_list'))


@main_bp.route('/projects')
@login_required
def projects_dashboard():
    org = _current_org()
    query = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()

    projects = []
    if org:
        projects = ProjectService().list_all_projects(org.id, search=query or None, status=status or None)
    ai_context = {
        'active_page': 'projects',
        'organization': org.name if org else None,
        'project_count': len(projects),
    }
    return render_template(
        'projects.html',
        projects=projects,
        active_page='projects',
        filter_q=query,
        filter_status=status,
        ai_context=ai_context,
    )


@main_bp.route('/projects/export/<string:file_type>')
@login_required
def projects_export(file_type: str):
    org = _current_org()
    if not org:
        flash('No organization available for export.', 'error')
        return redirect(url_for('main.projects_dashboard'))

    query = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()
    projects = ProjectService().list_all_projects(org.id, search=query or None, status=status or None)
    headers = ['ID', 'Name', 'Program', 'Status', 'Currency', 'Budget', 'Spent']
    rows = [[p.id, p.name, p.program or '', p.status, p.currency, p.budget, p.spent] for p in projects]

    if file_type == 'csv':
        data = _build_csv_bytes(headers, rows)
        return send_file(BytesIO(data), mimetype='text/csv', as_attachment=True, download_name='projects.csv')
    if file_type == 'xlsx':
        data = _build_xlsx_bytes('Projects', headers, rows)
        return send_file(
            BytesIO(data),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='projects.xlsx',
        )
    flash('Unsupported export type.', 'error')
    return redirect(url_for('main.projects_dashboard'))


@main_bp.route('/projects/new', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def project_create():
    org = _current_org()
    if not org:
        flash('No organization is available. Please seed data first.', 'error')
        return redirect(url_for('main.dashboard'))

    form = ProjectForm()
    if form.validate_on_submit():
        ProjectService().create_project(
            org.id,
            name=form.name.data,
            description=form.description.data,
            program=form.program.data,
            budget=form.budget.data,
            spent=form.spent.data,
            currency=form.currency.data,
            status=form.status.data,
        )
        flash('Project created successfully.', 'success')
        return redirect(url_for('main.projects_dashboard'))

    return render_template('project_form.html', form=form, is_edit=False, active_page='projects')


@main_bp.route('/projects/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def project_edit(project_id: int):
    org = _current_org()
    try:
        project = ProjectService().get_project(project_id, org.id)
    except ProjectNotFound:
        abort(404)
    form = ProjectForm(obj=project)
    if form.validate_on_submit():
        ProjectService().update_project(
            project.id,
            org.id,
            name=form.name.data,
            description=form.description.data,
            program=form.program.data,
            budget=form.budget.data,
            spent=form.spent.data,
            currency=form.currency.data,
            status=form.status.data,
        )
        flash('Project updated successfully.', 'success')
        return redirect(url_for('main.projects_dashboard'))

    return render_template('project_form.html', form=form, is_edit=True, project=project, active_page='projects')


@main_bp.route('/funds')
@login_required
def funds_list():
    org = _current_org()
    query = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()

    funds = []
    if org:
        funds = FundService().list_all_funds(org.id, search=query or None, status=status or None)
    ai_context = {
        'active_page': 'funds',
        'organization': org.name if org else None,
        'fund_count': len(funds),
    }
    return render_template(
        'funds.html',
        funds=funds,
        active_page='funds',
        filter_q=query,
        filter_status=status,
        ai_context=ai_context,
    )


@main_bp.route('/funds/export/<string:file_type>')
@login_required
def funds_export(file_type: str):
    org = _current_org()
    if not org:
        flash('No organization available for export.', 'error')
        return redirect(url_for('main.funds_list'))

    query = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()

    funds = FundService().list_all_funds(org.id, search=query or None, status=status or None)
    headers = ['ID', 'Name', 'Description', 'Status', 'Updated At']
    rows = [
        [f.id, f.name, f.description or '', 'active' if f.is_active else 'inactive', f.updated_at.strftime('%Y-%m-%d %H:%M:%S') if f.updated_at else '']
        for f in funds
    ]

    if file_type == 'csv':
        data = _build_csv_bytes(headers, rows)
        return send_file(BytesIO(data), mimetype='text/csv', as_attachment=True, download_name='funds.csv')
    if file_type == 'xlsx':
        data = _build_xlsx_bytes('Funds', headers, rows)
        return send_file(
            BytesIO(data),
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name='funds.xlsx',
        )
    flash('Unsupported export type.', 'error')
    return redirect(url_for('main.funds_list'))


@main_bp.route('/funds/new', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def fund_create():
    org = _current_org()
    if not org:
        flash('No organization is available. Please seed data first.', 'error')
        return redirect(url_for('main.dashboard'))

    form = FundForm()
    if form.validate_on_submit():
        try:
            FundService().create_fund(
                org.id,
                form.name.data,
                description=form.description.data,
                is_active=form.is_active.data == 'true',
                actor_id=getattr(current_user, 'id', None),
            )
        except ValueError as exc:
            flash(str(exc), 'error')
            return render_template('fund_form.html', form=form, is_edit=False, active_page='funds')
        except FundConcurrencyError:
            flash('This fund was changed by another user. Please reload and try again.', 'error')
            return render_template('fund_form.html', form=form, is_edit=False, active_page='funds')
        flash('Fund created successfully.', 'success')
        return redirect(url_for('main.funds_list'))

    return render_template('fund_form.html', form=form, is_edit=False, active_page='funds')


@main_bp.route('/funds/<int:fund_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def fund_edit(fund_id: int):
    org = _current_org()
    try:
        fund = FundService().get_fund(fund_id, org.id)
    except FundNotFound:
        abort(404)
    form = FundForm(obj=fund)
    if request.method == 'GET':
        form.is_active.data = 'true' if fund.is_active else 'false'

    if form.validate_on_submit():
        try:
            FundService().update_fund(
                fund.id,
                org.id,
                actor_id=getattr(current_user, 'id', None),
                name=form.name.data,
                description=form.description.data,
                is_active=form.is_active.data == 'true',
            )
        except ValueError as exc:
            flash(str(exc), 'error')
            return render_template('fund_form.html', form=form, is_edit=True, fund=fund, active_page='funds')
        except FundConcurrencyError:
            flash('This fund was updated by another user. Please reload and submit again.', 'error')
            return render_template('fund_form.html', form=form, is_edit=True, fund=fund, active_page='funds')
        flash('Fund updated successfully.', 'success')
        return redirect(url_for('main.funds_list'))

    return render_template('fund_form.html', form=form, is_edit=True, fund=fund, active_page='funds')


@main_bp.route('/reports')
@login_required
def reports_page():
    org = _current_org()
    overview = {
        'total_donations': 0.0,
        'total_expenses': 0.0,
        'net_total': 0.0,
        'chart_data': {
            'labels': [],
            'donations': [],
            'expenses': [],
            'net': [],
            'totals': {'donations': 0.0, 'expenses': 0.0, 'net': 0.0},
        },
    }
    if org:
        overview = ReportingService().financial_overview(org.id)

    return render_template(
        'reports.html',
        total_donations=overview['total_donations'],
        total_expenses=overview['total_expenses'],
        net_total=overview['net_total'],
        chart_data=overview['chart_data'],
        active_page='reports',
        ai_context={
            'active_page': 'reports',
            'organization': org.name if org else None,
            'total_donations': overview['total_donations'],
            'total_expenses': overview['total_expenses'],
            'net_balance': overview['net_total'],
        },
    )


@main_bp.route('/campaigns/email-workbench')
@login_required
def campaign_email_workbench_page():
    org = _current_org()
    overview = {
        'total_donations': 0.0,
        'total_expenses': 0.0,
        'net_total': 0.0,
        'chart_data': {
            'labels': [],
            'donations': [],
            'expenses': [],
            'net': [],
            'totals': {'donations': 0.0, 'expenses': 0.0, 'net': 0.0},
        },
    }
    if org:
        overview = ReportingService().financial_overview(org.id)

    return render_template(
        'reports.html',
        total_donations=overview['total_donations'],
        total_expenses=overview['total_expenses'],
        net_total=overview['net_total'],
        chart_data=overview['chart_data'],
        email_workbench_only=True,
        active_page='campaign_email_workbench',
        ai_context={
            'active_page': 'campaign_email_workbench',
            'organization': org.name if org else None,
            'total_donations': overview['total_donations'],
            'total_expenses': overview['total_expenses'],
            'net_balance': overview['net_total'],
        },
    )


@main_bp.route('/reports/compliance/evidence')
@login_required
@roles_required('admin', 'staff')
def reports_compliance_evidence():
    org = _current_org()
    scope = request.args.get('scope', 'org').strip().lower()
    if scope == 'global' and org is not None:
        return {'error': 'Global compliance scope is restricted to system administrators only.'}, 403
    organization_id = None if scope == 'global' else (org.id if org else None)

    payload = build_compliance_evidence(
        app=current_app,
        organization_id=organization_id,
    )

    as_download = request.args.get('download', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
    if as_download:
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8')
        filename = f"compliance-evidence-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.json"
        return send_file(
            BytesIO(body),
            mimetype='application/json',
            as_attachment=True,
            download_name=filename,
        )
    return payload


@main_bp.route('/about')
def about():
    """About page."""
    return render_template('about.html', active_page='about')


@main_bp.route('/help')
def help():
    """Help/documentation page."""
    return render_template('help.html', active_page='help')


# ---------------------------------------------------------------------------
# Beneficiary Management UI
# ---------------------------------------------------------------------------

@main_bp.route('/beneficiaries')
@login_required
def beneficiaries_list():
    org = _current_org()
    query = request.args.get('q', '').strip()
    status_filter = request.args.get('status', '').strip()

    beneficiaries = []
    if org:
        all_beneficiaries = list_beneficiaries(org.id)
        if query:
            ql = query.lower()
            all_beneficiaries = [
                b for b in all_beneficiaries
                if ql in (b.first_name or '').lower()
                or ql in (b.last_name or '').lower()
                or ql in (b.email or '').lower()
                or ql in (b.program or '').lower()
            ]
        if status_filter:
            all_beneficiaries = [b for b in all_beneficiaries if b.status == status_filter]
        beneficiaries = all_beneficiaries

    ai_context = {
        'active_page': 'beneficiaries',
        'organization': org.name if org else None,
        'beneficiary_count': len(beneficiaries),
    }
    ctx = dict(
        beneficiaries=beneficiaries,
        active_page='beneficiaries',
        filter_q=query,
        filter_status=status_filter,
        ai_context=ai_context,
    )
    if request.headers.get('HX-Request'):
        return render_template('_beneficiaries_rows.html', **ctx)
    return render_template('beneficiaries.html', **ctx)


@main_bp.route('/beneficiaries/new', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def beneficiary_new():
    org = _current_org()
    if not org:
        flash('No organization is available.', 'error')
        return redirect(url_for('main.beneficiaries_list'))

    form = BeneficiaryIntakeForm()
    if form.validate_on_submit():
        try:
            create_beneficiary(
                org.id,
                form.first_name.data,
                form.last_name.data or '',
                email=form.email.data or None,
                phone=form.phone.data or None,
                date_of_birth=form.date_of_birth.data,
                gender=form.gender.data or None,
                program=form.program.data or None,
                status=form.status.data,
                city=form.city.data or None,
                country=form.country.data or None,
                notes=form.notes.data or None,
            )
            flash('Beneficiary added successfully.', 'success')
            return redirect(url_for('main.beneficiaries_list'))
        except ValueError as exc:
            flash(str(exc), 'error')

    return render_template('beneficiary_form.html', form=form, is_edit=False, active_page='beneficiaries')


@main_bp.route('/beneficiaries/<int:beneficiary_id>')
@login_required
def beneficiary_detail(beneficiary_id: int):
    from ngo_homesuite.services.activity_timeline_service import ActivityTimelineService

    org = _current_org()
    if not org:
        abort(404)

    beneficiary = get_beneficiary(beneficiary_id, org.id)
    if not beneficiary:
        abort(404)

    cases = list_cases(org.id, beneficiary_id=beneficiary_id)

    activity_query = (request.args.get('activity_q') or '').strip()

    # Fetch unified activity timeline
    timeline_items = []
    try:
        timeline_items = ActivityTimelineService.get_beneficiary_timeline(
            org.id,
            beneficiary_id,
            limit=25,
            search_query=activity_query or None,
        )
    except Exception:
        # If timeline fetch fails, continue without it
        timeline_items = []

    beneficiary_ai_insights = None
    try:
        beneficiary_ai_insights = CopilotToolRegistry().execute(
            'summarize_activity_timeline',
            {
                'entity_type': 'beneficiary',
                'entity_id': beneficiary.id,
                'limit': 25,
                'query': activity_query or None,
            },
            {
                'organization_id': org.id,
                'actor': getattr(current_user, 'username', 'web'),
            },
        )
        if isinstance(beneficiary_ai_insights, dict) and beneficiary_ai_insights.get('error'):
            beneficiary_ai_insights = None
    except Exception:
        beneficiary_ai_insights = None

    ai_context = {
        'active_page': 'beneficiaries',
        'organization': org.name,
        'beneficiary_id': beneficiary.id,
        'beneficiary_name': f"{beneficiary.first_name} {beneficiary.last_name}".strip(),
        'case_count': len(cases),
    }
    return render_template(
        'beneficiary_detail.html',
        beneficiary=beneficiary,
        cases=cases,
        timeline_items=timeline_items,
        activity_query=activity_query,
        beneficiary_ai_insights=beneficiary_ai_insights,
        active_page='beneficiaries',
        ai_context=ai_context,
    )
