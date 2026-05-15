from __future__ import annotations

from pathlib import Path

from ngo_homesuite.app_factory import create_app, seed_demo_data
from ngo_homesuite.flask_config import TestingConfig
from ngo_homesuite.models.core import (
    Donation,
    Donor,
    Expense,
    Fund,
    Organization,
    Project,
    User,
    Volunteer,
)


class _NoAutoSeedTestingConfig(TestingConfig):
    ENABLE_DEMO_SEED = False



def _build_test_app(tmp_path: Path):
    class _Cfg(_NoAutoSeedTestingConfig):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'demo-seed-test.sqlite3'}"

    return create_app(_Cfg)



def test_seed_demo_data_creates_expected_records(tmp_path):
    app = _build_test_app(tmp_path)

    with app.app_context():
        assert User.query.count() == 0

        seed_demo_data(app)

        assert Organization.query.count() == 1
        assert User.query.count() == 4
        assert Donor.query.count() == 2
        assert Donation.query.count() == 1
        assert Project.query.count() == 1
        assert Fund.query.count() == 1
        assert Volunteer.query.count() == 1
        assert Expense.query.count() == 1

        admin = User.query.filter_by(username="admin").first()
        assert admin is not None
        assert admin.role == "admin"
        assert admin.check_password("admin123!")



def test_seed_demo_data_is_idempotent(tmp_path):
    app = _build_test_app(tmp_path)

    with app.app_context():
        seed_demo_data(app)
        first_counts = {
            "org": Organization.query.count(),
            "users": User.query.count(),
            "donors": Donor.query.count(),
            "donations": Donation.query.count(),
            "projects": Project.query.count(),
            "funds": Fund.query.count(),
            "volunteers": Volunteer.query.count(),
            "expenses": Expense.query.count(),
        }

        seed_demo_data(app)
        second_counts = {
            "org": Organization.query.count(),
            "users": User.query.count(),
            "donors": Donor.query.count(),
            "donations": Donation.query.count(),
            "projects": Project.query.count(),
            "funds": Fund.query.count(),
            "volunteers": Volunteer.query.count(),
            "expenses": Expense.query.count(),
        }

        assert first_counts == second_counts
