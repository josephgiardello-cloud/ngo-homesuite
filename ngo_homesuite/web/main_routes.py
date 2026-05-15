"""Main web routes, dashboards, and Phase 1 CRUD interfaces."""

import csv
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional as TypingOptional

from flask import Blueprint, render_template, redirect, url_for, request, flash, send_file, current_app, Response
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, FloatField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Optional as WTOptional, NumberRange, Email
from io import BytesIO
from openpyxl import Workbook

from ngo_homesuite.models.core import (
    Organization, Beneficiary, Project, Donation, Donor, Fund, Expense, DonationReceipt, RecurringDonationPlan, db
)
from sqlalchemy import func
from ngo_homesuite.web.rbac import roles_required
from ngo_homesuite.utils.receipt_pdf import generate_receipt_pdf_bytes
from ngo_homesuite.compliance.evidence_pack import build_compliance_evidence

main_bp = Blueprint('main', __name__)


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


def _issue_receipt_for_donation(donation: Donation, recipient_email: str | None = None):
    existing = DonationReceipt.query.filter_by(donation_id=donation.id).first()
    if existing:
        return existing

    receipt_number = f"R-{donation.id:06d}-{datetime.utcnow().strftime('%Y%m%d')}"
    receipt = DonationReceipt(
        donation_id=donation.id,
        receipt_number=receipt_number,
        status='generated',
        sent_to_email=recipient_email,
    )
    db.session.add(receipt)
    donation.status = 'receipted'

    # Generate bytes to ensure receipt content is always renderable.
    donor = donation.donor
    donation_payload = {
        'amount_cents': int(round(float(donation.amount or 0) * 100)),
        'currency': donation.currency,
        'received_at': donation.donation_date.strftime('%Y-%m-%d') if donation.donation_date else datetime.utcnow().strftime('%Y-%m-%d'),
    }
    donor_payload = {
        'name': donor.name if donor else donation.donor_name,
        'address': '',
    }
    generate_receipt_pdf_bytes(donation_payload, donor_payload)

    if recipient_email:
        try:
            from ngo_homesuite.utils.email_service import send_receipt

            send_receipt(
                recipient_email,
                donor_payload['name'],
                donation_payload['amount_cents'],
                donation.currency,
            )
            receipt.status = 'sent'
            receipt.sent_at = datetime.utcnow()
        except Exception as exc:
            receipt.status = 'failed'
            receipt.error_message = str(exc)

    return receipt


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
    html = (
        '<!doctype html>'
        '<html><head><meta charset="utf-8"><title>NGO HomeSuite API Docs</title>'
        '<style>body{font-family:Segoe UI,Arial,sans-serif;margin:2rem;line-height:1.45;}code{background:#f3f3f3;padding:0.15rem 0.35rem;border-radius:4px;}a{color:#0b5cab;}</style>'
        '</head><body>'
        '<h1>NGO HomeSuite API Docs</h1>'
        '<p>Starter API contract for beta integrations.</p>'
        f'<p>OpenAPI spec: <a href="{spec_url}">{spec_url}</a></p>'
        '<p>Use this spec with Swagger Editor or Redoc for interactive review.</p>'
        '</body></html>'
    )
    return Response(html, mimetype='text/html')


@main_bp.route('/')
def index():
    """Home/landing page."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('index.html')


@main_bp.route('/give', methods=['GET', 'POST'])
def public_give():
    """Public donation page for self-service online giving."""
    org = Organization.query.filter_by(is_active=True).order_by(Organization.id.asc()).first()
    if not org:
        flash('Donation portal is not available yet. Please contact the organization.', 'error')
        return redirect(url_for('main.index'))

    form = PublicDonationForm()
    if form.validate_on_submit():
        donor_email = (form.donor_email.data or '').strip() or None
        donor = None
        if donor_email:
            donor = Donor.query.filter_by(organization_id=org.id, email=donor_email).first()

        if donor is None:
            donor = Donor(
                organization_id=org.id,
                name=form.donor_name.data.strip(),
                email=donor_email,
                phone=(form.donor_phone.data or '').strip() or None,
                donor_type='individual',
                notes='Created from public donation portal.',
            )
            db.session.add(donor)
            db.session.flush()

        donation = Donation(
            organization_id=org.id,
            donor_id=donor.id,
            donor_name=donor.name,
            donor_email=donor.email,
            donor_phone=donor.phone,
            amount=form.amount.data,
            currency=form.currency.data,
            payment_method=form.payment_method.data,
            purpose=form.purpose.data or 'General Fund',
            status='received',
            notes='Public portal donation',
        )
        db.session.add(donation)
        db.session.flush()

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

        _issue_receipt_for_donation(donation, recipient_email=donor.email)
        db.session.commit()

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
        donor_count = Donor.query.filter_by(organization_id=org.id).count()
        total_donations = db.session.query(func.sum(Donation.amount)).filter_by(organization_id=org.id).scalar() or 0

        recent_donations = Donation.query.filter_by(organization_id=org.id).order_by(Donation.donation_date.desc()).limit(5).all()

        total_budget = db.session.query(func.sum(Project.budget)).filter_by(organization_id=org.id).scalar() or 0
        total_expenses = db.session.query(func.sum(Expense.amount)).filter_by(organization_id=org.id).scalar() or 0
        total_funds = Fund.query.filter_by(organization_id=org.id, is_active=True).count()
        
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

    return render_template(
        'donor_detail.html',
        donor=donor,
        donation_count=int(donation_count or 0),
        donation_total=float(total_amount or 0.0),
        recent_donations=recent_donations,
        recurring_plans=recurring_plans,
        active_page='donors',
        ai_context=ai_context,
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
        donor.updated_at = datetime.utcnow()
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
    donation_count = Donation.query.filter_by(donor_id=donor.id).count()
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
            plan.updated_at = datetime.utcnow()
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
        plan.updated_at = datetime.utcnow()
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
        project.updated_at = datetime.utcnow()
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
        fund.updated_at = datetime.utcnow()
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
    organization_id = None if scope == 'global' else (org.id if org else None)

    payload = build_compliance_evidence(
        app=current_app,
        organization_id=organization_id,
    )

    as_download = request.args.get('download', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
    if as_download:
        body = json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8')
        filename = f"compliance-evidence-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.json"
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
