# Contributing to NGO Homesuite

We welcome contributions! Please submit issues, pull requests, and join discussions.

## How to Contribute
- Fork the repo
- Create a feature branch
- Submit a pull request
- Join our community chat

## Code Style
- Follow PEP8 for Python
- Document new features

## Dependency Profiles
- Full runtime (legacy behavior): `pip install -r requirements.txt`
- Core runtime only: `pip install -r requirements-core.txt`
- Optional production DB drivers (PostgreSQL/MySQL): `pip install -r requirements-db.txt`
- Optional AI features: `pip install -r requirements-ai.txt`
- Optional cloud integrations (AWS/Azure): `pip install -r requirements-cloud.txt`
- Dev and test tooling: `pip install -r requirements-dev.txt`

## Community
- [Discord](https://discord.gg/example)
- [Forum](https://community.ngohomesuite.org)

## Production Database Setup
- See [docs/production-database.md](docs/production-database.md) for PostgreSQL/MySQL configuration, pooling, and migration workflow.
