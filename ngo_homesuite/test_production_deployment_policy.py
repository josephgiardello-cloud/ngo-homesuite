from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parent.parent


def _read_yaml(path: str) -> dict:
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


def test_runtime_monitoring_and_compose_artifacts_enforce_production_policy() -> None:
    compose = _read_yaml("docker-compose.yml")
    monitoring = _read_yaml("deploy/monitoring/docker-compose.monitoring.yml")
    promtail = _read_yaml("deploy/monitoring/promtail-config.yml")
    prometheus = _read_yaml("deploy/monitoring/prometheus.yml")

    app = compose["services"]["app"]
    migrator = compose["services"]["migrator"]
    postgres = compose["services"]["postgres"]
    redis = compose["services"]["redis"]

    app_env = app["environment"]
    assert app_env["DATABASE_BACKEND"] == "postgresql"
    assert app_env["SECRET_KEY_FILE"]
    assert app_env["DATABASE_URL_FILE"]
    assert app["user"] != "0:0"
    assert app["read_only"] is True
    assert "no-new-privileges:true" in app["security_opt"]
    assert "ALL" in app["cap_drop"]
    assert app["pids_limit"] > 0
    assert app["mem_limit"]
    assert app["cpus"]
    assert app["restart"] == "unless-stopped"
    assert "healthcheck" in app
    app_depends = app["depends_on"]
    assert "migrator" in app_depends
    assert app_depends["migrator"]["condition"] == "service_completed_successfully"

    migrator_env = migrator["environment"]
    assert migrator_env["APP_PROCESS_ROLE"] == "migrator"
    assert migrator_env["DATABASE_BACKEND"] == "postgresql"
    assert migrator["command"] == "python -m ngo_homesuite.db.deploy_migrate upgrade"
    assert migrator["restart"] == "no"

    assert (ROOT / "migrations" / "env.py").exists()
    assert (ROOT / "migrations" / "versions" / "20260604_0001_baseline.py").exists()

    postgres_env = postgres["environment"]
    assert ":?" in postgres_env["POSTGRES_PASSWORD"]
    assert postgres["pids_limit"] > 0
    assert postgres["mem_limit"]
    assert postgres["cpus"]
    assert postgres["restart"] == "unless-stopped"
    assert "healthcheck" in postgres

    assert redis["mem_limit"]
    assert redis["cpus"]
    assert redis["pids_limit"] > 0

    promtail_service = monitoring["services"]["promtail"]
    promtail_volumes = "\n".join(promtail_service["volumes"])
    assert "./logs:/var/log/ngo_homesuite:ro" in promtail_volumes

    prometheus_service = monitoring["services"]["prometheus"]
    prometheus_volumes = "\n".join(prometheus_service["volumes"])
    assert "/run/secrets/prometheus_scrape_password:ro" in prometheus_volumes

    assert promtail["clients"][0]["url"] == "${LOKI_PUSH_URL:-http://loki:3100/loki/api/v1/push}"
    scrape = promtail["scrape_configs"][0]["static_configs"][0]["labels"]
    assert scrape["__path__"] == "/var/log/ngo_homesuite/*.log"

    app_scrape = prometheus["scrape_configs"][0]
    assert app_scrape["metrics_path"] == "/api/v1/metrics"
    assert app_scrape["basic_auth"]["username"] == "observability"
    assert app_scrape["basic_auth"]["password_file"] == "/run/secrets/prometheus_scrape_password"
    assert "password" not in app_scrape["basic_auth"]


def test_dockerfile_runs_non_root_with_healthcheck() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "USER appuser" in dockerfile
    assert "HEALTHCHECK" in dockerfile


def test_security_release_policy_enforces_dependency_ai_and_evidence_gates() -> None:
    tests_workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    release_workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    dependabot = _read_yaml(".github/dependabot.yml")
    config_text = (ROOT / "ngo_homesuite" / "config.py").read_text(encoding="utf-8")
    ai_routes = (ROOT / "ngo_homesuite" / "web" / "ai_routes.py").read_text(encoding="utf-8")
    release_process = (ROOT / "docs" / "release_process.md").read_text(encoding="utf-8")
    pentest_playbook = (ROOT / "docs" / "security_pentest_playbook.md").read_text(encoding="utf-8")
    dependency_policy = (ROOT / "docs" / "dependency_policy.md").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "production_checklist.md").read_text(encoding="utf-8")

    assert (ROOT / "tools" / "verify_release_evidence_bundle.py").exists()
    assert (ROOT / "artifacts" / "release-evidence-bundle.json").exists()

    assert "name: Security Checks" in tests_workflow
    assert "pip install bandit pip-audit" in tests_workflow
    assert "pip-audit -r requirements.txt" in tests_workflow
    assert "gitleaks/gitleaks-action" in tests_workflow
    assert "ngo_homesuite/ai/test_ai_hardening.py" in tests_workflow
    assert "ngo_homesuite/web/test_cross_tenant_boundaries.py" in tests_workflow
    assert "python tools/verify_release_evidence_bundle.py" in tests_workflow
    assert "python tools/verify_release_evidence_bundle.py --strict" in release_workflow

    pip_updates = dependabot["updates"][0]
    assert pip_updates["package-ecosystem"] == "pip"
    assert pip_updates["schedule"]["interval"] == "weekly"
    assert "security" in pip_updates["labels"]
    assert "dependabot" in pip_updates["pull-request-branch-name"]["prefix"]

    assert 'minion_allow_web_tools: bool = Field(default=False)' in config_text
    assert 'minion_require_approval_token: bool = Field(default=True)' in config_text
    assert 'current_app.config.get("MINION_REQUIRE_APPROVAL_TOKEN", True)' in ai_routes
    assert '_verify_approval_token(' in ai_routes
    assert '"allow_web_tools": bool(current_app.config.get("MINION_ALLOW_WEB_TOOLS", False))' in ai_routes

    assert "pip-audit" in dependency_policy
    assert "Dependabot opens weekly pip dependency PRs." in dependency_policy
    assert "docs/security_pentest_playbook.md" in release_process
    assert "artifacts/dast-smoke-report.json" in release_process
    assert "Evidence Artifacts" in pentest_playbook
    assert "Security test lane output" in pentest_playbook
    assert "Manual pentest notes and repro steps" in pentest_playbook
    assert "Remediation links/commit references for fixed findings" in pentest_playbook
    assert "artifacts/release-evidence-bundle.json" in checklist
    assert "security release lane" in checklist.lower()


def test_tenant_isolation_release_policy_enforces_cross_org_and_ai_scoping_gates() -> None:
    tests_workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "production_checklist.md").read_text(encoding="utf-8")
    release_process = (ROOT / "docs" / "release_process.md").read_text(encoding="utf-8")
    pentest_playbook = (ROOT / "docs" / "security_pentest_playbook.md").read_text(encoding="utf-8")
    app_factory = (ROOT / "ngo_homesuite" / "app_factory.py").read_text(encoding="utf-8")
    api_v1 = (ROOT / "ngo_homesuite" / "api" / "v1.py").read_text(encoding="utf-8")
    ai_routes = (ROOT / "ngo_homesuite" / "ai" / "test_minion_routes.py").read_text(encoding="utf-8")
    ai_tools = (ROOT / "ngo_homesuite" / "ai" / "minion_tools.py").read_text(encoding="utf-8")
    semantic_memory = (ROOT / "ngo_homesuite" / "ai" / "semantic_memory.py").read_text(encoding="utf-8")

    assert "ngo_homesuite/web/test_cross_tenant_boundaries.py" in tests_workflow
    assert "ngo_homesuite/web/test_rbac_tenant_route_audit.py" in tests_workflow
    assert "tenant isolation release lane" in checklist.lower()
    assert "tenant isolation release lane" in release_process.lower()
    assert "cross-tenant mutation denial" in pentest_playbook
    assert "Run cross-tenant negative tests for mutating endpoints." in checklist

    assert "register_rls_listeners(app)" in app_factory
    assert "User is not allowed to access another tenant org_id" in api_v1
    assert "tenant_id" in ai_routes and "cross_tenant_payload" in ai_routes
    assert 'organization_id is required in runtime context' in ai_tools
    assert 'node.get("organization_id") != organization_id' in semantic_memory


def test_observability_release_policy_enforces_request_tracing_metrics_and_alerts() -> None:
    tests_workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    checklist = (ROOT / "docs" / "production_checklist.md").read_text(encoding="utf-8")
    release_process = (ROOT / "docs" / "release_process.md").read_text(encoding="utf-8")
    observability_stack = (ROOT / "docs" / "observability_stack.md").read_text(encoding="utf-8")
    alerts = (ROOT / "deploy" / "monitoring" / "alerts.yml").read_text(encoding="utf-8")
    app_factory = (ROOT / "ngo_homesuite" / "app_factory.py").read_text(encoding="utf-8")
    obs_api_tests = (ROOT / "ngo_homesuite" / "api" / "test_observability_api.py").read_text(encoding="utf-8")
    obs_log_tests = (ROOT / "ngo_homesuite" / "api" / "test_observability_request_logs.py").read_text(encoding="utf-8")

    assert "observability release lane" in checklist.lower()
    assert "observability release lane" in release_process.lower()
    assert "api/test_observability_api.py" in release_process
    assert "api/test_observability_request_logs.py" in release_process
    assert "Forward application logs to centralized storage" in checklist
    assert "Alert Expectations" in observability_stack
    assert "NgoHomeSuiteHttp5xxRateHigh" in alerts
    assert "NgoHomeSuiteSchedulerHeartbeatMissing" in alerts
    assert "NgoHomeSuiteSlowEndpoints" in alerts

    assert "request.headers.get('X-Request-ID'" in app_factory
    assert "response.headers.setdefault('X-Request-ID'" in app_factory
    assert "metrics.inc('http_requests_total'" in app_factory
    assert "metrics.observe('http_request_latency_ms'" in app_factory
    assert "test_request_id_header_and_metrics_endpoint" in obs_api_tests
    assert "test_request_completed_log_contains_structured_request_fields" in obs_log_tests


def test_production_boot_policy_enforces_schema_preflight_and_scheduler_role_guard() -> None:
    app_factory = (ROOT / "ngo_homesuite" / "app_factory.py").read_text(encoding="utf-8")

    assert "def _assert_production_schema_ready() -> None:" in app_factory
    assert "'schema_version'" in app_factory
    assert "Production startup blocked: database schema is not ready." in app_factory
    assert "if is_production:" in app_factory
    assert "APP_PROCESS_ROLE" in app_factory
    assert "Event scheduler startup skipped for APP_PROCESS_ROLE" in app_factory
    assert "Grant scheduler startup skipped for APP_PROCESS_ROLE" in app_factory
