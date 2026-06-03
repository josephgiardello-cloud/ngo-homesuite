# NGO HomeSuite

A practical nonprofit operations platform for donor management, fundraising, grants, volunteers, and reporting.

![Status](https://img.shields.io/badge/status-active-success)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-MIT-lightgrey)

Last updated: 2026-06-02

## Overview

NGO HomeSuite is a local-first Flask application designed for nonprofit teams that need a single system for core operations:

- Donors, donations, recurring giving, and receipts
- Campaign operations (including queue visibility and retry controls)
- Grants lifecycle, budget/accounting controls, and compliance package export
- Membership and volunteer workflows
- Role-based access, tenant-aware data boundaries, and auditability
- API-first integrations and operational reporting

## Core Capabilities

- Financial management: donations, funds, expenses, reconciliation-ready exports
- Relationship management: donor profiles, interactions, pledges, peer fundraising
- Campaign operations: segmentation, preview/send, queue processing, failed-batch retry
- Grants operations: lifecycle transitions, disbursements, budget lines, compliance packages
- Membership and volunteer tools: enrollment/tracking, training, searchable list views
- Reporting and analytics: exports, dashboards, guardrails, activity insights
- API and integrations: versioned API routes plus docs and OpenAPI spec
- AI assistant support: local-first Copilot endpoint with role-aware tooling

## Current State (June 2026)

Shipped and working in the main product path:

- Campaign queue controls: queue visibility, due-batch processing, and failed-batch retry
- Grants compliance packaging: compliance-ready grant package endpoint and service flow
- Membership and volunteer UX improvements: searchable/filterable/paginated list paths
- Expanded tenant and RBAC validation for newly added API surfaces

## What Is Left To Do

Key work still tracked as open:

- External formal security review evidence (pentest/sign-off artifacts)
- Broader tenant-isolation and AI context-boundary evidence across all mutation paths
- Deeper end-to-end journey coverage (failure/recovery and multi-role scenarios)
- Additional UX modernization in high-traffic workflows
- Ongoing dependency hardening and release-evidence automation

## Tech Stack

- Backend: Python, Flask, SQLAlchemy
- Data: PostgreSQL (recommended), SQLite (local/demo)
- Optional encryption: SQLCipher
- Testing: pytest
- Deployment: Docker, Gunicorn

## Quick Start

1. Clone

```bash
git clone https://github.com/josephgiardello-cloud/ngo-homesuite.git
cd ngo-homesuite
```

2. Create and activate virtual environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate
```

3. Install dependencies

```bash
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

4. Configure environment

```bash
cp .env.example .env
```

5. Run migrations

```bash
python -m ngo_homesuite.db.migrate
```

6. Start the app

```bash
python -m ngo_homesuite.main
```

Default URL: http://localhost:5000

## Testing

Run the full suite:

```bash
python -m pytest --maxfail=10 -v
```

## Docker (Optional)

```bash
docker compose --profile postgres up --build
```

## Documentation

- Feature maturity: [docs/feature_status.md](docs/feature_status.md)
- Production checklist: [docs/production_checklist.md](docs/production_checklist.md)
- API specification: [docs/openapi.yaml](docs/openapi.yaml)
- Architecture notes: [docs/architecture_v2.md](docs/architecture_v2.md)
- Observability runbook: [docs/observability_stack.md](docs/observability_stack.md)
- Backup/restore drill: [docs/backup_restore_drill.md](docs/backup_restore_drill.md)
- Release process: [docs/release_process.md](docs/release_process.md)

## Project Status

This repository is actively maintained. For exact maturity by module and current release blockers, use:

- [docs/feature_status.md](docs/feature_status.md)
- [docs/production_checklist.md](docs/production_checklist.md)

## Contributing

Please review [CONTRIBUTING.md](CONTRIBUTING.md) before opening pull requests.

## License

Licensed under MIT. See [LICENSE](LICENSE).
