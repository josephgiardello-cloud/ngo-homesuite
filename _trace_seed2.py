import os, sys, traceback
os.chdir(r'C:\Users\josep\OneDrive\Desktop\Codes\ngo-homesuite')

from ngo_homesuite.flask_config import TestingConfig

class _TestCfg(TestingConfig):
    COPILOT_ENABLED = True
    ENABLE_DEMO_SEED = False  # Disable auto-seed so we can manually trace

from ngo_homesuite.app_factory import create_app
app = create_app(_TestCfg)

from ngo_homesuite.models.core import db, User, Organization, Donor, Donation, Project, Fund, Volunteer, Expense
from ngo_homesuite.config import get_runtime_settings
from sqlalchemy import inspect as sa_inspect

with app.app_context():
    # Replicate seed_demo_data step by step
    print("=== Step 1: create org ===")
    org = Organization.query.filter_by(slug='community-hope-initiative').first()
    if org is None:
        from ngo_homesuite.models.core import Organization
        org = Organization(
            name='Community Hope Initiative',
            slug='community-hope-initiative',
            description='Demo organization.',
            mission='Serve families.',
            country='US',
            city='Austin',
            is_active=True,
        )
        db.session.add(org)
        db.session.flush()
    print(f"  org.id={org.id}")

    print("=== Step 2: create users ===")
    try:
        user = User.query.filter_by(username='admin').first()
        if user is None:
            user = User(username='admin')
            db.session.add(user)
        user.email = 'admin@ngohomesuite.local'
        user.first_name = 'System'
        user.last_name = 'Admin'
        user.role = 'admin'
        user.organization_id = org.id
        user.is_active = True
        user.set_password('admin123!')
        db.session.commit()
        print("  users committed ok")
    except Exception as e:
        print(f"  user error: {e}")
        traceback.print_exc()
        db.session.rollback()
        sys.exit(1)

    print("=== Step 3: create donors ===")
    try:
        if Donor.query.filter_by(organization_id=org.id).count() == 0:
            donors = [
                Donor(organization_id=org.id, name='Ana Martins', email='ana@example.org', phone='+1-555-0101', donor_type='individual'),
                Donor(organization_id=org.id, name='BFF', email='contact@bf.org', donor_type='foundation'),
            ]
            db.session.add_all(donors)
            db.session.flush()
            print(f"  donors flushed, ids={[d.id for d in donors]}")
        donors = Donor.query.filter_by(organization_id=org.id).order_by(Donor.id).all()
        print(f"  donors in session: {len(donors)}, ids={[d.id for d in donors]}")
    except Exception as e:
        print(f"  donor error: {e}")
        traceback.print_exc()

    print("=== Step 4: create fund ===")
    try:
        fund = Fund.query.filter_by(organization_id=org.id, name='General Fund').first()
        if fund is None:
            fund = Fund(organization_id=org.id, name='General Fund', description='General.', is_active=True)
            db.session.add(fund)
            db.session.flush()
        print(f"  fund.id={fund.id}")
    except Exception as e:
        print(f"  fund error: {e}")
        traceback.print_exc()

    print("=== Step 5: create project ===")
    try:
        project = Project.query.filter_by(organization_id=org.id, name='Youth Learning Program').first()
        if project is None:
            project = Project(
                organization_id=org.id,
                name='Youth Learning Program',
                description='After-school.',
                program='Education',
                budget=25000,
                spent=5400,
                status='active',
            )
            db.session.add(project)
        db.session.flush()
        print(f"  project.id={project.id}")
    except Exception as e:
        print(f"  project error: {e}")
        traceback.print_exc()

    print("=== Step 6: create donation ===")
    try:
        donation_columns = {c['name'] for c in sa_inspect(db.engine).get_columns('donations')}
        print(f"  has campaign_id: {'campaign_id' in donation_columns}")
        if 'campaign_id' in donation_columns:
            existing = Donation.query.filter_by(reference_number='DEMO-001').first()
            if existing is None and donors:
                d = Donation(
                    organization_id=org.id,
                    donor_id=donors[0].id,
                    donor_name=donors[0].name,
                    donor_email=donors[0].email,
                    donor_phone=donors[0].phone,
                    amount=1200,
                    currency='USD',
                    payment_method='bank_transfer',
                    status='received',
                    purpose='Education materials',
                    reference_number='DEMO-001',
                    project_id=project.id,
                    fund_id=fund.id,
                )
                db.session.add(d)
                print(f"  donation added (donor_id={donors[0].id})")
    except Exception as e:
        print(f"  donation error: {e}")
        traceback.print_exc()

    print("=== Step 7: create volunteer ===")
    try:
        volunteer = Volunteer.query.filter_by(organization_id=org.id, email='luis.volunteer@example.org').first()
        if volunteer is None:
            v = Volunteer(
                organization_id=org.id,
                name='Luis Parker',
                email='luis.volunteer@example.org',
                phone='+1-555-0109',
                hours_logged=14.5,
                status='active',
            )
            db.session.add(v)
        print("  volunteer ok")
    except Exception as e:
        print(f"  volunteer error: {e}")
        traceback.print_exc()

    print("=== Step 8: create expense ===")
    try:
        expense = Expense.query.filter_by(organization_id=org.id, payee='Learning Supplies Co', amount=780).first()
        if expense is None:
            e2 = Expense(
                organization_id=org.id,
                project_id=project.id,
                fund_id=fund.id,
                amount=780,
                currency='USD',
                payee='Learning Supplies Co',
                description='Starter packs for 30 students',
            )
            db.session.add(e2)
        print("  expense ok")
    except Exception as e:
        print(f"  expense error: {e}")
        traceback.print_exc()

    print("=== Step 9: final commit ===")
    try:
        db.session.commit()
        print("  commit ok")
    except Exception as e:
        print(f"  commit error: {e}")
        traceback.print_exc()
        db.session.rollback()

    print(f"=== Final state: Donors={Donor.query.count()} ===")
