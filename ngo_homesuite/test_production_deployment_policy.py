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