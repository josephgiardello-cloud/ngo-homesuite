"""TONY Grant Scoring and Nonprofit Analysis - Full Integration.

This module integrates the complete TONY program (ingest, score, report, dashboard, 
calibration, compliance, evaluation) into NGO HomeSuite as a comprehensive sidebar feature.
"""

import json
import logging
import tempfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
from flask import Blueprint, current_app, render_template, request, jsonify, send_file, flash, redirect, url_for
from flask_login import login_required, current_user

from ngo_homesuite.models.core import Organization, Grant, db
from ngo_homesuite.auth.decorators import roles_required
from ngo_homesuite.tony import ingest, score, report, calibration, compliance, evaluation
from ngo_homesuite.tony.config import load_config
from ngo_homesuite.tony.dashboard import create_app as create_dashboard_app

logger = logging.getLogger(__name__)

tony_bp = Blueprint('tony', __name__, url_prefix='/tony', template_folder='templates')


def _org_id():
    """Helper: Get current user's organization ID."""
    if current_user.is_authenticated and current_user.organization_id:
        return current_user.organization_id
    return None


def _current_org():
    """Helper: Get current user's organization."""
    org_id = _org_id()
    if org_id:
        return db.session.get(Organization, org_id)
    return None


@tony_bp.route('/')
@login_required
@roles_required('admin', 'staff')
def tony_home():
    """TONY main dashboard with all features."""
    org = _current_org()
    if not org:
        flash('No organization found for your account.', 'error')
        return redirect(url_for('main.dashboard'))
    
    return render_template('tony/home.html', org=org, active_page='tony')


@tony_bp.route('/ingest', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def ingest_grants():
    """Ingest grants from CSV or Excel file."""
    org = _current_org()
    if not org:
        flash('No organization found.', 'error')
        return redirect(url_for('main.dashboard'))
    
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file provided.', 'error')
            return redirect(request.url)
        
        file = request.files['file']
        if file.filename == '':
            flash('No file selected.', 'error')
            return redirect(request.url)
        
        try:
            # Load TONY config
            config = load_config()
            
            # Read file into DataFrame
            if file.filename.endswith('.csv'):
                df = pd.read_csv(file)
            elif file.filename.endswith(('.xlsx', '.xls')):
                df = pd.read_excel(file)
            else:
                flash('Only CSV and Excel files are supported.', 'error')
                return redirect(request.url)
            
            # Run TONY ingest
            result = ingest.run(
                source=file,
                config=config,
                ein=org.ein or None,
                organization_name=org.name
            )
            
            flash(f'✓ Successfully ingested {len(result)} grants.', 'success')
            return jsonify({
                'status': 'success',
                'count': len(result),
                'data': result
            })
            
        except Exception as e:
            logger.exception("Ingest error")
            flash(f'Error ingesting grants: {str(e)}', 'error')
            return redirect(request.url)
    
    return render_template('tony/ingest.html', org=org, active_page='tony')


@tony_bp.route('/score', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def score_grants():
    """Score grants using TONY algorithm."""
    org = _current_org()
    if not org:
        flash('No organization found.', 'error')
        return redirect(url_for('main.dashboard'))
    
    if request.method == 'POST':
        try:
            payload = request.get_json(silent=True) or {}
            grants_data = payload.get('grants', [])
            risk_preset = payload.get('preset', 'balanced')
            
            if not grants_data:
                return jsonify({'error': 'No grants provided'}), 400
            
            # Load TONY config
            config = load_config()
            
            # Run TONY scoring
            scored_results = score.run(
                source_data=grants_data,
                config=config,
                risk_preset=risk_preset
            )
            
            return jsonify({
                'status': 'success',
                'scored_count': len(scored_results),
                'results': scored_results
            })
            
        except Exception as e:
            logger.exception("Scoring error")
            return jsonify({'error': str(e)}), 500
    
    # GET: Show scoring interface
    grants = Grant.query.filter_by(organization_id=org.id).all()
    return render_template('tony/score.html', org=org, grants=grants, active_page='tony')


@tony_bp.route('/report', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def generate_report():
    """Generate TONY risk reports (HTML, PDF, JSON)."""
    org = _current_org()
    if not org:
        flash('No organization found.', 'error')
        return redirect(url_for('main.dashboard'))
    
    if request.method == 'POST':
        try:
            payload = request.get_json(silent=True) or {}
            scored_data = payload.get('scored_data', [])
            format_type = payload.get('format', 'html')  # html, pdf, json
            
            if not scored_data:
                return jsonify({'error': 'No scored data provided'}), 400
            
            # Run TONY report generation
            report_content = report.run(
                input_data=scored_data,
                format_type=format_type
            )
            
            if format_type == 'pdf':
                return send_file(
                    BytesIO(report_content),
                    mimetype='application/pdf',
                    as_attachment=True,
                    download_name=f'tony_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.pdf'
                )
            elif format_type == 'json':
                return jsonify(report_content)
            else:  # HTML
                return report_content
            
        except Exception as e:
            logger.exception("Report generation error")
            return jsonify({'error': str(e)}), 500
    
    return render_template('tony/report.html', org=org, active_page='tony')


@tony_bp.route('/compliance', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def check_compliance():
    """Check grant compliance against TONY control catalog."""
    org = _current_org()
    if not org:
        flash('No organization found.', 'error')
        return redirect(url_for('main.dashboard'))
    
    if request.method == 'POST':
        try:
            payload = request.get_json(silent=True) or {}
            grant_data = payload.get('grant_data', {})
            
            # Run TONY compliance evaluation
            compliance_result = compliance.evaluate(grant_data)
            
            return jsonify({
                'status': 'success',
                'compliance_score': compliance_result.get('score'),
                'violations': compliance_result.get('violations', []),
                'recommendations': compliance_result.get('recommendations', [])
            })
            
        except Exception as e:
            logger.exception("Compliance check error")
            return jsonify({'error': str(e)}), 500
    
    return render_template('tony/compliance.html', org=org, active_page='tony')


@tony_bp.route('/calibrate', methods=['GET', 'POST'])
@login_required
@roles_required('admin')
def calibrate_model():
    """Calibrate TONY risk model with historical data."""
    org = _current_org()
    if not org:
        flash('No organization found.', 'error')
        return redirect(url_for('main.dashboard'))
    
    if request.method == 'POST':
        try:
            payload = request.get_json(silent=True) or {}
            historical_data = payload.get('historical_data', [])
            
            if not historical_data:
                return jsonify({'error': 'No historical data provided'}), 400
            
            # Run TONY calibration
            calibration_result = calibration.run(
                input_data=historical_data
            )
            
            return jsonify({
                'status': 'success',
                'calibration_metrics': calibration_result.get('metrics'),
                'adjustments': calibration_result.get('adjustments')
            })
            
        except Exception as e:
            logger.exception("Calibration error")
            return jsonify({'error': str(e)}), 500
    
    return render_template('tony/calibrate.html', org=org, active_page='tony')


@tony_bp.route('/evaluate', methods=['GET', 'POST'])
@login_required
@roles_required('admin', 'staff')
def evaluate_results():
    """Evaluate TONY model performance and results accuracy."""
    org = _current_org()
    if not org:
        flash('No organization found.', 'error')
        return redirect(url_for('main.dashboard'))
    
    if request.method == 'POST':
        try:
            payload = request.get_json(silent=True) or {}
            predictions = payload.get('predictions', [])
            actuals = payload.get('actuals', [])
            
            if not predictions or not actuals:
                return jsonify({'error': 'Predictions and actuals required'}), 400
            
            # Run TONY evaluation
            eval_result = evaluation.run(
                predictions=predictions,
                actuals=actuals
            )
            
            return jsonify({
                'status': 'success',
                'metrics': eval_result.get('metrics'),
                'accuracy': eval_result.get('accuracy'),
                'precision': eval_result.get('precision'),
                'recall': eval_result.get('recall')
            })
            
        except Exception as e:
            logger.exception("Evaluation error")
            return jsonify({'error': str(e)}), 500
    
    return render_template('tony/evaluate.html', org=org, active_page='tony')


@tony_bp.route('/dashboard', methods=['GET'])
@login_required
@roles_required('admin', 'staff')
def tony_dashboard():
    """Streamlit-style interactive TONY dashboard."""
    org = _current_org()
    if not org:
        flash('No organization found.', 'error')
        return redirect(url_for('main.dashboard'))
    
    try:
        # Get all grants for org
        grants = Grant.query.filter_by(organization_id=org.id).all()
        
        if not grants:
            flash('No grants found to display.', 'warning')
            return redirect(url_for('tony.tony_home'))
        
        # Prepare grant data
        grant_data = [
            {
                'id': g.id,
                'title': g.title,
                'funder': g.funder_name,
                'amount': float(g.amount_awarded or 0),
                'status': g.status,
                'start_date': g.start_date.isoformat() if g.start_date else None,
                'end_date': g.end_date.isoformat() if g.end_date else None,
            }
            for g in grants
        ]
        
        return render_template(
            'tony/dashboard.html',
            org=org,
            grants=grant_data,
            grant_count=len(grants),
            active_page='tony'
        )
        
    except Exception as e:
        logger.exception("Dashboard error")
        flash(f'Error loading dashboard: {str(e)}', 'error')
        return redirect(url_for('tony.tony_home'))


@tony_bp.route('/api/config', methods=['GET'])
@login_required
@roles_required('admin')
def get_config():
    """Get TONY configuration (for advanced users)."""
    try:
        config = load_config()
        return jsonify(config)
    except Exception as e:
        logger.exception("Config retrieval error")
        return jsonify({'error': str(e)}), 500


@tony_bp.route('/api/baselines', methods=['GET'])
@login_required
@roles_required('admin', 'staff')
def get_baselines():
    """Get TONY baseline data for comparison."""
    try:
        from ngo_homesuite.tony.ingest import EXTERNAL_BASELINE_FILE
        
        if EXTERNAL_BASELINE_FILE.exists():
            baselines = pd.read_csv(EXTERNAL_BASELINE_FILE)
            return jsonify({
                'status': 'success',
                'baselines': baselines.to_dict('records')
            })
        else:
            return jsonify({'status': 'no_baselines'})
            
    except Exception as e:
        logger.exception("Baseline retrieval error")
        return jsonify({'error': str(e)}), 500
