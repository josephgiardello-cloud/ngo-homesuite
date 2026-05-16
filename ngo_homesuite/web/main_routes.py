"""Main web routes, dashboards, and Phase 1 CRUD interfaces."""

import csv
import json
from ngo_homesuite.ai.copilot_tools import CopilotToolRegistry
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional as TypingOptional

from flask import Blueprint, render_template, redirect, url_for, request, flash, send_file, current_app, Response, session
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, FloatField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Optional as WTOptional, NumberRange, Email
from io import BytesIO
from openpyxl import Workbook

from ngo_homesuite.models.core import (
    Organization, Beneficiary, Project, Donation, Donor, Fund, Expense, DonationReceipt, P2PPage, RecurringDonationPlan, Volunteer, db
)
from ngo_homesuite.services.donation_service import DonationService
from ngo_homesuite.services.donor_service import DonorService
from ngo_homesuite.services.fund_service import FundService
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


def _current_org() -> TypingOptional[Organization]:
    """Pick assigned org first, then fallback to first active org for seeded demo users."""
    if current_user.organization:
        return current_user.organization
    return Organization.query.filter_by(is_active=True).first()


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

    donors = Donor.query.filter_by(organization_id=org.id).all()
    projects = Project.query.filter_by(organization_id=org.id).all()
    beneficiaries = Beneficiary.query.filter_by(organization_id=org.id).all()

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

    purpose_sums = (
        db.session.query(Donation.purpose, func.coalesce(func.sum(Donation.amount), 0.0))
        .filter(Donation.organization_id == org.id)
        .group_by(Donation.purpose)
        .all()
    )
    for idx, (purpose, total) in enumerate(purpose_sums, start=1):
        if not purpose:
            continue
        campaign = CampaignEntity(
            entity_id=f"campaign:{idx}",
            name=str(purpose),
            lifecycle_state=LifecycleState.active,
            fundraising_goal=round(float(total) * 1.25, 2),
            raised_amount=float(total),
        )
        campaign.transition(LifecycleState.active, actor='system', reason='derived_from_donation_purpose')
        registry.upsert(campaign)

    foundation_totals = (
        db.session.query(Donor.name, func.coalesce(func.sum(Donation.amount), 0.0))
        .join(Donation, Donation.donor_id == Donor.id)
        .filter(Donor.organization_id == org.id, Donor.donor_type == 'foundation')
        .group_by(Donor.name)
        .all()
    )
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

    for project in projects:
        related_donations = Donation.query.filter_by(organization_id=org.id, project_id=project.id).count()
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
    org = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
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
                db.session.rollback()
                return render_template('public_donation_form.html', form=form, active_page='give')

            plan = RecurringDonationPlan(
                organization_id=org.id,
                donor_id=donor.id,
                amount=form.amount.data,
                currency=form.currency.data,
                payment_method=form.payment_method.data,
                purpose=form.purpose.data or 'General Fund',
                frequency=form.recurring_frequency.data or 'monthly',
                next_charge_date=_next_charge_date(date.today(), form.recurring_frequency.data or 'monthly'),
                status='active',
            )
            db.session.add(plan)
            db.session.commit()

        # Advance to 'processed' so generate_receipt() is satisfied, then issue.
        _donation_svc.update_status(donation.id, org.id, 'processed', actor_id=None)
        _issue_receipt_for_donation(donation, recipient_email=donor.email)

        flash('Thank you for your donation. Your receipt has been generated.', 'success')
        return redirect(url_for('main.public_give'))

    return render_template('public_donation_form.html', form=form, active_page='give')


@main_bp.route('/dashboard')
@login_required
def dashboard():
    """User dashboard with summary cards."""

    org = _current_org()
    if org:
        beneficiary_count = Beneficiary.query.filter_by(organization_id=org.id, status='active').count()
        project_count = Project.query.filter_by(organization_id=org.id, status='active').count()

        _donor_svc = DonorService()
        donor_count = _donor_svc.list_donors(org.id, per_page=1)['total']

        _donation_svc = DonationService()
        donation_page = _donation_svc.list_donations(org.id, per_page=5)
        recent_donations = donation_page['items']
        total_donations = (
            db.session.query(func.sum(Donation.amount)).filter_by(organization_id=org.id).scalar() or 0
        )

        total_budget = db.session.query(func.sum(Project.budget)).filter_by(organization_id=org.id).scalar() or 0
        total_expenses = db.session.query(func.sum(Expense.amount)).filter_by(organization_id=org.id).scalar() or 0

        _fund_svc = FundService()
        total_funds = _fund_svc.list_funds(org.id, active_only=True, per_page=1)['total']

        stats = {
            'organization': org,
            'beneficiary_count': beneficiary_count,
            'project_count': project_count,
            'donor_count': donor_count,
            'total_donations': total_donations,
            'total_budget': total_budget,
            'total_expenses': total_expenses,
            'total_funds': total_funds,
            'recent_donations': recent_donations,
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
                beneficiary = Beneficiary(
                    organization_id=org.id,
                    first_name=first_name,
                    last_name=last_name,
                    phone=(request.form.get('phone') or '').strip() or None,
                    city=(request.form.get('city') or '').strip() or None,
                    program=(request.form.get('program') or '').strip() or None,
                    status=(request.form.get('status') or 'active').strip() or 'active',
                    notes=(request.form.get('notes') or '').strip() or None,
                )
                db.session.add(beneficiary)
                db.session.commit()
                flash('Beneficiary intake captured.', 'success')
                return redirect(url_for('main.mobile_intake'))
        elif action == 'volunteer':
            name = (request.form.get('volunteer_name') or '').strip()
            if not name:
                flash('Volunteer name is required.', 'error')
            else:
                volunteer = Volunteer(
                    organization_id=org.id,
                    name=name,
                    email=(request.form.get('volunteer_email') or '').strip() or None,
                    phone=(request.form.get('volunteer_phone') or '').strip() or None,
                    status=(request.form.get('volunteer_status') or 'active').strip() or 'active',
                    hours_logged=0.0,
                )
                db.session.add(volunteer)
                db.session.commit()
                flash('Volunteer quick registration captured.', 'success')
                return redirect(url_for('main.mobile_intake'))
        else:
            flash('Unsupported intake action.', 'error')

    recent_beneficiaries = (
        Beneficiary.query.filter_by(organization_id=org.id)
        .order_by(Beneficiary.created_at.desc())
        .limit(8)
        .all()
    )
    recent_volunteers = (
        Volunteer.query.filter_by(organization_id=org.id)
        .order_by(Volunteer.created_at.desc())
        .limit(8)
        .all()
    )

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
    donors = (
        Donor.query.filter_by(organization_id=org.id)
        .order_by(Donor.name.asc())
        .all()
    )
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
            except Exception:
                flash('Unable to update fundraiser status.', 'error')
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

    donors_query = Donor.query.filter_by(organization_id=org.id) if org else Donor.query.filter_by(id=-1)
    if query:
        like_term = f"%{query}%"
        donors_query = donors_query.filter(
            (Donor.name.ilike(like_term)) |
            (Donor.email.ilike(like_term)) |
            (Donor.phone.ilike(like_term))
        )
    if donor_type:
        donors_query = donors_query.filter_by(donor_type=donor_type)

    donors = donors_query.order_by(Donor.name.asc()).all()
    delete_form = ConfirmDeleteForm()
    ai_context = {
        'active_page': 'donors',
        'organization': org.name if org else None,
        'donor_count': len(donors),
    }
    return render_template(
        'donors.html',
        donors=donors,
        delete_form=delete_form,
        active_page='donors',
        filter_q=query,
        filter_donor_type=donor_type,
        ai_context=ai_context,
    )


@main_bp.route('/donors/<int:donor_id>')
@login_required
def donor_detail(donor_id: int):
    org = _current_org()
    donor = Donor.query.filter_by(id=donor_id, organization_id=org.id).first_or_404()

    donation_count, total_amount = (
        db.session.query(func.count(Donation.id), func.coalesce(func.sum(Donation.amount), 0.0))
        .filter_by(organization_id=org.id, donor_id=donor.id)
        .first()
    )

    recent_donations = (
        Donation.query.filter_by(organization_id=org.id, donor_id=donor.id)
        .order_by(Donation.donation_date.desc())
        .limit(10)
        .all()
    )
    recurring_plans = (
        RecurringDonationPlan.query.filter_by(organization_id=org.id, donor_id=donor.id)
        .order_by(RecurringDonationPlan.created_at.desc())
        .all()
    )

    ai_context = {
        'active_page': 'donors',
        'organization': org.name if org else None,
        'donor_id': donor.id,
        'donor_name': donor.name,
        'donation_count': int(donation_count or 0),
        'donation_total': float(total_amount or 0.0),
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

    return render_template(
        'donor_detail.html',
        donor=donor,
        donation_count=int(donation_count or 0),
        donation_total=float(total_amount or 0.0),
        recent_donations=recent_donations,
        recurring_plans=recurring_plans,
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
    donors_query = Donor.query.filter_by(organization_id=org.id)
    if query:
        like_term = f"%{query}%"
        donors_query = donors_query.filter(
            (Donor.name.ilike(like_term)) |
            (Donor.email.ilike(like_term)) |
            (Donor.phone.ilike(like_term))
        )
    if donor_type:
        donors_query = donors_query.filter_by(donor_type=donor_type)

    donors = donors_query.order_by(Donor.name.asc()).all()
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
        donor = Donor(
            organization_id=org.id,
            name=form.name.data,
            email=form.email.data,
            phone=form.phone.data,
            donor_type=form.donor_type.data,
            notes=form.notes.data,
        )
        db.session.add(donor)
        db.session.commit()
        flash('Donor created successfully.', 'success')
        return redirect(url_for('main.donors_list'))

    return render_template('donor_form.html', form=form, is_edit=False, active_page='donors')


@main_bp.route('/donors/<int:donor_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def donor_edit(donor_id: int):
    org = _current_org()
    donor = Donor.query.filter_by(id=donor_id, organization_id=org.id).first_or_404()
    form = DonorForm(obj=donor)

    if form.validate_on_submit():
        donor.name = form.name.data
        donor.email = form.email.data
        donor.phone = form.phone.data
        donor.donor_type = form.donor_type.data
        donor.notes = form.notes.data
        donor.updated_at = datetime.now(timezone.utc)
        db.session.commit()
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
    donor = Donor.query.filter_by(id=donor_id, organization_id=org.id).first_or_404()
    donation_count = Donation.query.filter_by(donor_id=donor.id, organization_id=org.id).count()
    if donation_count > 0:
        flash('Cannot delete donor with existing donations. Edit donor instead.', 'error')
        return redirect(url_for('main.donors_list'))

    db.session.delete(donor)
    db.session.commit()
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

    donors = Donor.query.filter_by(organization_id=org.id).order_by(Donor.created_at.asc(), Donor.id.asc()).all()
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

    primary = Donor.query.filter_by(id=primary_id, organization_id=org.id).first_or_404()
    duplicate = Donor.query.filter_by(id=duplicate_id, organization_id=org.id).first_or_404()

    Donation.query.filter_by(organization_id=org.id, donor_id=duplicate.id).update(
        {
            Donation.donor_id: primary.id,
            Donation.donor_name: primary.name,
            Donation.donor_email: primary.email,
            Donation.donor_phone: primary.phone,
        },
        synchronize_session=False,
    )

    if duplicate.notes and duplicate.notes not in (primary.notes or ''):
        primary.notes = ((primary.notes or '').strip() + '\n' + f"[Merged from donor #{duplicate.id}] {duplicate.notes}").strip()

    db.session.delete(duplicate)
    db.session.commit()
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

    donor_options = [(0, 'Select a donor')] + [(d.id, d.name) for d in Donor.query.filter_by(organization_id=org.id).order_by(Donor.name.asc()).all()]
    project_options = [(0, 'General / None')] + [(p.id, p.name) for p in Project.query.filter_by(organization_id=org.id).order_by(Project.name.asc()).all()]
    fund_options = [(0, 'General / None')] + [(f.id, f.name) for f in Fund.query.filter_by(organization_id=org.id, is_active=True).order_by(Fund.name.asc()).all()]

    form = DonationForm()
    form.donor_id.choices = donor_options
    form.project_id.choices = project_options
    form.fund_id.choices = fund_options

    if form.validate_on_submit():
        donor = Donor.query.filter_by(id=form.donor_id.data, organization_id=org.id).first()
        if donor is None:
            flash('Please select a valid donor.', 'error')
            return render_template('donation_form.html', form=form, active_page='donations')

        donation = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            donor_phone=donor.phone,
            amount=form.amount.data,
            currency=form.currency.data,
            payment_method=form.payment_method.data,
            purpose=form.purpose.data,
            reference_number=form.reference_number.data or None,
            notes=form.notes.data,
            project_id=form.project_id.data or None,
            fund_id=form.fund_id.data or None,
        )
        db.session.add(donation)
        db.session.commit()
        _issue_receipt_for_donation(donation, recipient_email=donor.email)
        db.session.commit()
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

    donor_options = [(0, 'Select a donor')] + [
        (d.id, f"{d.name} ({d.email or 'no email'})")
        for d in Donor.query.filter_by(organization_id=org.id).order_by(Donor.name.asc()).all()
    ]

    form = RecurringDonationForm()
    form.donor_id.choices = donor_options

    if form.validate_on_submit():
        donor = Donor.query.filter_by(id=form.donor_id.data, organization_id=org.id).first()
        if donor is None:
            flash('Please select a valid donor.', 'error')
            return render_template('recurring_donations.html', form=form, plans=[], active_page='donations')

        plan = RecurringDonationPlan(
            organization_id=org.id,
            donor_id=donor.id,
            amount=form.amount.data,
            currency=form.currency.data,
            payment_method=form.payment_method.data,
            purpose=form.purpose.data,
            frequency=form.frequency.data,
            next_charge_date=_next_charge_date(date.today(), form.frequency.data),
            status='active',
        )
        db.session.add(plan)
        db.session.commit()
        flash('Recurring donation plan created.', 'success')
        return redirect(url_for('main.recurring_donations'))

    plans = RecurringDonationPlan.query.filter_by(organization_id=org.id).order_by(RecurringDonationPlan.created_at.desc()).all()
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

    today = date.today()
    plans = RecurringDonationPlan.query.filter(
        RecurringDonationPlan.organization_id == org.id,
        RecurringDonationPlan.status == 'active',
        RecurringDonationPlan.next_charge_date <= today,
    ).all()

    processed = 0
    failed = 0
    for plan in plans:
        donor = Donor.query.filter_by(id=plan.donor_id, organization_id=org.id).first()
        if donor is None or (plan.payment_method in ('credit_card', 'bank_transfer') and not donor.email):
            plan.status = 'failed'
            plan.fail_count = int(plan.fail_count or 0) + 1
            plan.last_error = 'Missing donor contact info for payment retry workflow.'
            plan.updated_at = datetime.now(timezone.utc)
            failed += 1
            continue

        donation = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            donor_phone=donor.phone,
            amount=plan.amount,
            currency=plan.currency,
            payment_method=plan.payment_method,
            purpose=plan.purpose,
            status='received',
            notes=f'Recurring donation charge from plan #{plan.id}',
        )
        db.session.add(donation)
        db.session.flush()
        _issue_receipt_for_donation(donation, recipient_email=donor.email)

        plan.next_charge_date = _next_charge_date(plan.next_charge_date, plan.frequency)
        plan.fail_count = 0
        plan.last_error = None
        plan.status = 'active'
        plan.updated_at = datetime.now(timezone.utc)
        processed += 1

    db.session.commit()
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

    donations_query = Donation.query.filter_by(organization_id=org.id) if org else Donation.query.filter_by(id=-1)
    if query:
        like_term = f"%{query}%"
        donations_query = donations_query.filter(
            (Donation.donor_name.ilike(like_term)) |
            (Donation.donor_email.ilike(like_term)) |
            (Donation.reference_number.ilike(like_term)) |
            (Donation.purpose.ilike(like_term))
        )
    if payment_method:
        donations_query = donations_query.filter_by(payment_method=payment_method)
    if status:
        donations_query = donations_query.filter_by(status=status)
    if min_amount is not None:
        donations_query = donations_query.filter(Donation.amount >= min_amount)
    if max_amount is not None:
        donations_query = donations_query.filter(Donation.amount <= max_amount)

    donations = donations_query.order_by(Donation.donation_date.desc()).all()
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

    donations_query = Donation.query.filter_by(organization_id=org.id)
    if query:
        like_term = f"%{query}%"
        donations_query = donations_query.filter(
            (Donation.donor_name.ilike(like_term)) |
            (Donation.donor_email.ilike(like_term)) |
            (Donation.reference_number.ilike(like_term)) |
            (Donation.purpose.ilike(like_term))
        )
    if payment_method:
        donations_query = donations_query.filter_by(payment_method=payment_method)
    if status:
        donations_query = donations_query.filter_by(status=status)
    if min_amount is not None:
        donations_query = donations_query.filter(Donation.amount >= min_amount)
    if max_amount is not None:
        donations_query = donations_query.filter(Donation.amount <= max_amount)

    donations = donations_query.order_by(Donation.donation_date.desc()).all()
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
    donation = Donation.query.filter_by(id=donation_id, organization_id=org.id).first_or_404()
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

    expenses_query = Expense.query.filter_by(organization_id=org.id) if org else Expense.query.filter_by(id=-1)
    if query:
        like_term = f"%{query}%"
        expenses_query = expenses_query.filter(
            (Expense.payee.ilike(like_term)) |
            (Expense.description.ilike(like_term))
        )
    if min_amount is not None:
        expenses_query = expenses_query.filter(Expense.amount >= min_amount)
    if max_amount is not None:
        expenses_query = expenses_query.filter(Expense.amount <= max_amount)

    expenses = expenses_query.order_by(Expense.paid_at.desc()).all()
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

    project_options = [(0, 'General / None')] + [(p.id, p.name) for p in Project.query.filter_by(organization_id=org.id).order_by(Project.name.asc()).all()]
    fund_options = [(0, 'General / None')] + [(f.id, f.name) for f in Fund.query.filter_by(organization_id=org.id, is_active=True).order_by(Fund.name.asc()).all()]

    form = ExpenseForm()
    form.project_id.choices = project_options
    form.fund_id.choices = fund_options

    if form.validate_on_submit():
        expense = Expense(
            organization_id=org.id,
            project_id=form.project_id.data or None,
            fund_id=form.fund_id.data or None,
            amount=form.amount.data,
            currency=form.currency.data,
            payee=form.payee.data,
            description=form.description.data,
        )
        db.session.add(expense)
        db.session.commit()
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

    expenses_query = Expense.query.filter_by(organization_id=org.id)
    if query:
        like_term = f"%{query}%"
        expenses_query = expenses_query.filter((Expense.payee.ilike(like_term)) | (Expense.description.ilike(like_term)))
    if min_amount is not None:
        expenses_query = expenses_query.filter(Expense.amount >= min_amount)
    if max_amount is not None:
        expenses_query = expenses_query.filter(Expense.amount <= max_amount)

    expenses = expenses_query.order_by(Expense.paid_at.desc()).all()
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

    projects_query = Project.query.filter_by(organization_id=org.id) if org else Project.query.filter_by(id=-1)
    if query:
        like_term = f"%{query}%"
        projects_query = projects_query.filter(
            (Project.name.ilike(like_term)) |
            (Project.program.ilike(like_term)) |
            (Project.description.ilike(like_term))
        )
    if status:
        projects_query = projects_query.filter_by(status=status)

    projects = projects_query.order_by(Project.name.asc()).all()
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
    projects_query = Project.query.filter_by(organization_id=org.id)
    if query:
        like_term = f"%{query}%"
        projects_query = projects_query.filter(
            (Project.name.ilike(like_term)) |
            (Project.program.ilike(like_term)) |
            (Project.description.ilike(like_term))
        )
    if status:
        projects_query = projects_query.filter_by(status=status)

    projects = projects_query.order_by(Project.name.asc()).all()
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
        project = Project(
            organization_id=org.id,
            name=form.name.data,
            description=form.description.data,
            program=form.program.data,
            budget=form.budget.data,
            spent=form.spent.data,
            currency=form.currency.data,
            status=form.status.data,
        )
        db.session.add(project)
        db.session.commit()
        flash('Project created successfully.', 'success')
        return redirect(url_for('main.projects_dashboard'))

    return render_template('project_form.html', form=form, is_edit=False, active_page='projects')


@main_bp.route('/projects/<int:project_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def project_edit(project_id: int):
    org = _current_org()
    project = Project.query.filter_by(id=project_id, organization_id=org.id).first_or_404()
    form = ProjectForm(obj=project)
    if form.validate_on_submit():
        project.name = form.name.data
        project.description = form.description.data
        project.program = form.program.data
        project.budget = form.budget.data
        project.spent = form.spent.data
        project.currency = form.currency.data
        project.status = form.status.data
        project.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        flash('Project updated successfully.', 'success')
        return redirect(url_for('main.projects_dashboard'))

    return render_template('project_form.html', form=form, is_edit=True, project=project, active_page='projects')


@main_bp.route('/funds')
@login_required
def funds_list():
    org = _current_org()
    query = request.args.get('q', '').strip()
    status = request.args.get('status', '').strip()

    funds_query = Fund.query.filter_by(organization_id=org.id) if org else Fund.query.filter_by(id=-1)
    if query:
        like_term = f"%{query}%"
        funds_query = funds_query.filter((Fund.name.ilike(like_term)) | (Fund.description.ilike(like_term)))
    if status == 'active':
        funds_query = funds_query.filter_by(is_active=True)
    elif status == 'inactive':
        funds_query = funds_query.filter_by(is_active=False)

    funds = funds_query.order_by(Fund.name.asc()).all()
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

    funds_query = Fund.query.filter_by(organization_id=org.id)
    if query:
        like_term = f"%{query}%"
        funds_query = funds_query.filter((Fund.name.ilike(like_term)) | (Fund.description.ilike(like_term)))
    if status == 'active':
        funds_query = funds_query.filter_by(is_active=True)
    elif status == 'inactive':
        funds_query = funds_query.filter_by(is_active=False)

    funds = funds_query.order_by(Fund.name.asc()).all()
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
        fund = Fund(
            organization_id=org.id,
            name=form.name.data,
            description=form.description.data,
            is_active=form.is_active.data == 'true',
        )
        db.session.add(fund)
        db.session.commit()
        flash('Fund created successfully.', 'success')
        return redirect(url_for('main.funds_list'))

    return render_template('fund_form.html', form=form, is_edit=False, active_page='funds')


@main_bp.route('/funds/<int:fund_id>/edit', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def fund_edit(fund_id: int):
    org = _current_org()
    fund = Fund.query.filter_by(id=fund_id, organization_id=org.id).first_or_404()
    form = FundForm(obj=fund)
    if request.method == 'GET':
        form.is_active.data = 'true' if fund.is_active else 'false'

    if form.validate_on_submit():
        fund.name = form.name.data
        fund.description = form.description.data
        fund.is_active = form.is_active.data == 'true'
        fund.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        flash('Fund updated successfully.', 'success')
        return redirect(url_for('main.funds_list'))

    return render_template('fund_form.html', form=form, is_edit=True, fund=fund, active_page='funds')


@main_bp.route('/reports')
@login_required
def reports_page():
    org = _current_org()
    total_donations = db.session.query(func.sum(Donation.amount)).filter_by(organization_id=org.id).scalar() or 0 if org else 0
    total_expenses = db.session.query(func.sum(Expense.amount)).filter_by(organization_id=org.id).scalar() or 0 if org else 0
    net_total = total_donations - total_expenses

    monthly_donations = defaultdict(float)
    monthly_expenses = defaultdict(float)
    labels = []

    if org:
        donations = Donation.query.filter_by(organization_id=org.id).all()
        expenses = Expense.query.filter_by(organization_id=org.id).all()

        for donation in donations:
            if donation.donation_date:
                key = donation.donation_date.strftime('%Y-%m')
                monthly_donations[key] += float(donation.amount or 0)
        for expense in expenses:
            if expense.paid_at:
                key = expense.paid_at.strftime('%Y-%m')
                monthly_expenses[key] += float(expense.amount or 0)

        labels = sorted(set(list(monthly_donations.keys()) + list(monthly_expenses.keys())))

    chart_data = {
        'labels': labels,
        'donations': [round(monthly_donations[label], 2) for label in labels],
        'expenses': [round(monthly_expenses[label], 2) for label in labels],
        'net': [round(monthly_donations[label] - monthly_expenses[label], 2) for label in labels],
        'totals': {
            'donations': round(total_donations, 2),
            'expenses': round(total_expenses, 2),
            'net': round(net_total, 2),
        },
    }

    return render_template(
        'reports.html',
        total_donations=total_donations,
        total_expenses=total_expenses,
        net_total=net_total,
        chart_data=chart_data,
        active_page='reports',
        ai_context={
            'active_page': 'reports',
            'organization': org.name if org else None,
            'total_donations': total_donations,
            'total_expenses': total_expenses,
            'net_balance': net_total,
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
