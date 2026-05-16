from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_observability_deploy_artifacts_exist() -> None:
    required = [
        "deploy/monitoring/prometheus.yml",
        "deploy/monitoring/alerts.yml",
        "deploy/monitoring/promtail-config.yml",
        "deploy/monitoring/docker-compose.monitoring.yml",
        "docs/observability_stack.md",
    ]
    missing = [path for path in required if not Path(path).exists()]
    assert not missing, f"Missing observability artifact(s): {missing}"


def test_prometheus_config_scrapes_metrics_endpoint() -> None:
    content = _read("deploy/monitoring/prometheus.yml")
    assert "metrics_path: /api/v1/metrics" in content
    assert "job_name: ngo_homesuite_app" in content


def test_alert_rules_include_target_and_http5xx_alerts() -> None:
    content = _read("deploy/monitoring/alerts.yml")
    assert "alert: NgoHomeSuiteTargetDown" in content
    assert "alert: NgoHomeSuiteHttp5xxRateHigh" in content
    assert "http_requests_total" in content


def test_promtail_config_points_to_application_logs() -> None:
    content = _read("deploy/monitoring/promtail-config.yml")
    assert "__path__: /var/log/ngo_homesuite/*.log" in content
    assert "/loki/api/v1/push" in content
