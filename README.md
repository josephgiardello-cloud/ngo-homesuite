# NGO HomeSuite

A comprehensive nonprofit management system for managing donors, donations, funds, staff, volunteers, and compliance tracking. Built with Flask, SQLAlchemy, and security-first principles.

![Status](https://img.shields.io/badge/status-active-success)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

## Project Metadata

- Description: Local-first nonprofit operations suite with integrated AI Copilot (RAG + internal tools)
- Primary topics: nonprofit, flask, donations, donor-management, reporting, ai-copilot, rag
- Screenshots folder: `docs/screenshots/`
- Demo guide: `docs/demo/README.md`

### Screenshot Gallery (Add Your Captures)

- `docs/screenshots/dashboard-overview.png`
- `docs/screenshots/donors-list.png`
- `docs/screenshots/donor-profile.png`
- `docs/screenshots/donations-list.png`
- `docs/screenshots/reports-compliance.png`
- `docs/screenshots/copilot-approval-queue.png`

## Features

### 💰 Financial Management
- Donation tracking and management
- Fund management with allocation tracking
- Bank account reconciliation
- Expense tracking
- Accounting exports for external systems
- Currency handling and conversion

### 👥 Relationship Management
- Donor database with comprehensive profiles
- Donor interaction history
- Pledge tracking
- Peer fundraising management
- Event management
- Campaign management

### 👔 Staff & Volunteers
- Staff payroll management
- Volunteer tracking and hours
- Role-based access control
- Session management

### 📊 Reporting & Analytics
- Donor reports
- Donation analysis
- Fund performance reports
- Integrity drift detection for data consistency
- OpenTimestamps integration for audit trail immutability

### 🤖 HomeSuite Copilot
- Local-first AI assistant endpoint at `/ai/copilot/chat`
- RAG indexing for source/docs/models via `homesuite reindex`
- Role-aware tool execution for internal actions (report generation, donor search, donation lookup)
- Optional web tooling is disabled by default and can be enabled explicitly

### 🧭 API Docs
- OpenAPI starter spec available at `/api/openapi.yaml`
- In-app docs landing page at `/api/docs`
- Interactive Swagger UI at `/api/swagger`
- Source spec file is maintained in `docs/openapi.yaml`

### 🔒 Security & Compliance
- SQLCipher encryption support (optional)
- Append-only audit logging (all changes tracked immutably)
- HIBP (Have I Been Pwned) password breach checking
- Environment-based key rotation capability
- S3 seal anchoring for audit trail verification
- Secure database permissions enforcement
- Session-based authentication

### 🌍 Internationalization
- Spanish (es) translations
- French (fr) translations
- Easy expansion to additional languages

## Architecture

```
ngo-homesuite/
├── auth/              # Authentication, session, password policy
├── db/                # Database layer, migrations, schema
├── dal/               # Data access layer (donations, donors, funds, etc.)
├── models/            # Data models and entities
├── services/          # Business logic (reporting, reconciliation, etc.)
├── utils/             # Utilities (backup, export, email, integrity)
├── ui/                # User interface components
├── web/               # Flask web application
├── migrations/        # SQL migrations
├── config/            # Configuration management
└── translations/      # i18n translations
```

## Quick Start

### Prerequisites
- Python 3.8+
- SQLite3
- pip or conda

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/josephgiardello-cloud/ngo-homesuite.git
   cd ngo-homesuite
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv .venv
   # On Windows
   .venv\Scripts\activate
   # On macOS/Linux
   source .venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure the application**
   ```bash
   cp ngo-homesuite.yaml.example ngo-homesuite.yaml
   # Edit ngo-homesuite.yaml with your settings
   ```

### Database Setup

#### Unencrypted (Default)
```bash
python -m ngo_homesuite.db.migrate
```

#### With SQLCipher Encryption
1. Install optional dependency:
   ```bash
   pip install pysqlcipher3
   ```

2. Set encryption key:
   ```bash
   # On Windows (PowerShell)
   $env:NGO_HOMESUITE_DB_KEY = "your-strong-key-here"
   
   # On macOS/Linux
   export NGO_HOMESUITE_DB_KEY="your-strong-key-here"
   ```

3. Run migrations:
   ```bash
   python -m ngo_homesuite.db.migrate
   ```

### Running the Application

```bash
python ngo_homesuite/main.py
```

The Flask application will be available at `http://localhost:5000` (default).

## Testing

### Run All Tests
```bash
python -m pytest
```

### Run Specific Test Suite
```bash
# Database tests
pytest ngo_homesuite/db --maxfail=10 -v

# Authentication tests
pytest ngo_homesuite/auth --maxfail=10 -v

# Integrity drift tests
pytest ngo_homesuite/utils --maxfail=10 -v
```

### Test Configuration

**Important**: Tests are sensitive to the `NGO_HOMESUITE_DB_KEY` environment variable. 

- **For unencrypted DB tests** (recommended for CI):
  ```bash
  $env:NGO_HOMESUITE_DB_KEY = ""  # Clear the variable
  pytest ngo_homesuite/
  ```

- **For encrypted DB tests**:
  ```bash
  $env:NGO_HOMESUITE_DB_KEY = "test-key-12345"
  pip install pysqlcipher3
  pytest ngo_homesuite/
  ```

### Current Test Status
- First-party test suite runs from `ngo_homesuite/` by default via `pytest.ini`
- Includes AI hardening, copilot routes, auth models, DB hardening, integrity drift, and web sprint tests
- Bundled third-party test trees outside `ngo_homesuite/` are excluded from default project test runs

## Configuration

### Environment Variables

| Variable | Purpose | Example |
|----------|---------|---------|
| `NGO_HOMESUITE_DB_KEY` | SQLCipher encryption key (optional) | `your-secure-key` |
| `NGO_HOMESUITE_DB_PATH` | Database file path | `/data/ngo.db` |
| `FLASK_ENV` | Flask environment | `development`, `production` |
| `SECRET_KEY` | Flask session key | (auto-generated if not set) |

### Database Encryption (SQLCipher)

When `NGO_HOMESUITE_DB_KEY` is set:
- Database file is encrypted at rest
- All sensitive data is protected
- Key rotation is supported via `cron_safe_rotate_db_key`
- Unencrypted access is blocked (security by default)

**Warning**: If the key is lost, the database cannot be recovered.

## Key Modules

### Authentication (`ngo_homesuite/auth/`)
- Password policy enforcement
- HIBP breach checking
- Session management
- User model with role-based access

### Database (`ngo_homesuite/db/`)
- SQLAlchemy connection pooling
- Schema management
- Append-only audit logging
- Encryption/decryption layer
- Health checks and diagnostics

### Data Access Layer (`ngo_homesuite/dal/`)
- Donations DAL
- Donors DAL
- Funds DAL
- Projects DAL
- Bank accounts DAL
- Expenses DAL

### Services (`ngo_homesuite/services/`)
- Donation service (processing, tracking)
- Donor service (profiles, interactions)
- Fund service (allocation, management)
- Bank reconciliation service
- Reporting service

### Utilities (`ngo_homesuite/utils/`)
- Backup and restore
- Integrity drift detection
- CSV/Excel export
- Email service integration
- Payment webhook handling
- S3 audit anchor integration
- OpenTimestamps verification

## Development

### Code Style
- Follow PEP 8
- Use type hints where possible
- Document public functions and classes

### Database Migrations
Place new migrations in `ngo_homesuite/migrations/` as `.sql` files:
```sql
-- ngo_homesuite/migrations/0004_new_feature.sql
ALTER TABLE donors ADD COLUMN custom_field TEXT;
```

Run migrations:
```bash
python -m ngo_homesuite.db.migrate
```

### Contributing
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

## Backup & Recovery

### Automated Backups
```bash
python -m ngo_homesuite.utils.backup
```

### Manual Backup
```python
from ngo_homesuite.utils.backup_core import create_backup
create_backup(output_path="/backups/manual_backup.zip")
```

### Restore
```python
from ngo_homesuite.utils.backup_core import restore_backup
restore_backup(backup_path="/backups/manual_backup.zip")
```

## Audit & Compliance

### Append-Only Audit Log
All entity changes are recorded in an immutable audit log:
```sql
SELECT * FROM audit_log WHERE entity_id = 'donor_123';
```

Fields:
- `entity_type`: Type of entity (user, donation, donor, etc.)
- `entity_id`: ID of the entity
- `action`: insert, update, delete
- `actor`: User who made the change
- `timestamp`: When the change occurred
- `details`: JSON details of the change
- `hash_prev`: Hash of previous state
- `hash_event`: Hash of this event (integrity verification)

### Integrity Drift Detection
Periodically verify data consistency:
```bash
python -m ngo_homesuite.utils.integrity_drift
```

This checks:
- Schema integrity
- Referential constraints
- Audit log consistency
- S3 seal anchors (if configured)

## Troubleshooting

### Database Locked
If you see "database is locked":
1. Ensure only one instance is running
2. Check for orphaned connections
3. Restart the application

### Password Policy Errors
The system enforces strong password policies:
- Minimum 12 characters
- Mix of uppercase, lowercase, digits, symbols
- No breach detection matches from HIBP

Check `ngo_homesuite/auth/models.py` for policy details.

### SQLCipher Issues
- **"SQLCipher driver isn't installed"**: Run `pip install pysqlcipher3`
- **"Key mismatch"**: Ensure `NGO_HOMESUITE_DB_KEY` is consistent
- **"Key rotation failed"**: Check file permissions on the database

## Performance

### Optimization Tips
1. **Indexing**: Check `ngo_homesuite/db/schema.py` for indexes
2. **Query optimization**: Use DAL layer, avoid N+1 queries
3. **Connection pooling**: SQLAlchemy is configured with reasonable pool sizes
4. **Caching**: Consider adding Redis for session caching

### Monitoring
- Enable Flask debug logging for development
- Use `healthcheck()` from `ngo_homesuite/db/healthcheck.py` to monitor DB health
- Check audit logs for suspicious patterns

## License

This project is licensed under the MIT License.

See `LICENSE` for the full text.

## Support

- 📧 Email: [Your support email]
- 💬 Discussions: [Link to discussions]
- 🐛 Issue Tracker: https://github.com/josephgiardello-cloud/ngo-homesuite/issues

## Roadmap

- [x] Web UI for donor management (list, detail, create/edit/delete, dedupe/merge)
- [ ] Mobile app for volunteer check-in
- [ ] Advanced reporting dashboard
- [ ] Integration with popular accounting software
- [ ] Multi-organization support
- [ ] Workflow automation
- [ ] API documentation (OpenAPI/Swagger)
- [ ] Full frontend modernization (component system + richer interactivity)
- [ ] Continuous compliance evidence publishing pipeline

---

**Last Updated**: May 15, 2026  
**Version**: 0.1.0 (Beta)
