"""Main web routes, dashboards, and Phase 1 CRUD interfaces."""

import csv
from collections import defaultdict
from datetime import datetime
from typing import Optional as TypingOptional

from flask import Blueprint, render_template, redirect, url_for, request, flash, send_file
from flask_login import login_required, current_user
from flask_wtf import FlaskForm
from wtforms import StringField, SelectField, TextAreaField, FloatField, SubmitField
from wtforms.validators import DataRequired, Optional as WTOptional, NumberRange, Email
from io import BytesIO
from openpyxl import Workbook

from ngo_homesuite.models.core import (
    Organization, Beneficiary, Project, Donation, Donor, Fund, Expense, db
)
from sqlalchemy import func
from ngo_homesuite.web.rbac import roles_required
from ngo_homesuite.utils.receipt_pdf import generate_receipt_pdf_bytes

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


def _current_org() -> TypingOptional[Organization]:
    """Pick assigned org first, then fallback to first active org for seeded demo users."""
    if current_user.organization:
        return current_user.organization
    return Organization.query.filter_by(is_active=True).first()


def _build_csv_bytes(headers, rows):
    buffer = BytesIO()
    text_buffer = buffer if False else None
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


@main_bp.route('/')
def index():
    """Home/landing page."""
    if current_user.is_authenticated:
        return redirect(url_for('main.dashboard'))
    return render_template('index.html')


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
    
    return render_template('dashboard.html', stats=stats, active_page='dashboard')


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
    return render_template(
        'donors.html',
        donors=donors,
        delete_form=delete_form,
        active_page='donors',
        filter_q=query,
        filter_donor_type=donor_type,
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
        flash('Donation recorded successfully.', 'success')
        return redirect(url_for('main.dashboard'))

    return render_template('donation_form.html', form=form, active_page='donations')


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
    return render_template(
        'projects.html',
        projects=projects,
        active_page='projects',
        filter_q=query,
        filter_status=status,
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
    return render_template(
        'funds.html',
        funds=funds,
        active_page='funds',
        filter_q=query,
        filter_status=status,
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
    }

    return render_template(
        'reports.html',
        total_donations=total_donations,
        total_expenses=total_expenses,
        net_total=net_total,
        chart_data=chart_data,
        active_page='reports',
    )


@main_bp.route('/about')
def about():
    """About page."""
    return render_template('about.html', active_page='about')


@main_bp.route('/help')
def help():
    """Help/documentation page."""
    return render_template('help.html', active_page='help')
