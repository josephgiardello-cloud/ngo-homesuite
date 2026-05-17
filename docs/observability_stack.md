# Observability Stack (Prometheus + Loki)

This project ships baseline observability deployment artifacts under `deploy/monitoring/`.

## Included Artifacts

- `deploy/monitoring/prometheus.yml`: metrics scrape config
- `deploy/monitoring/alerts.yml`: baseline alert rules
- `deploy/monitoring/promtail-config.yml`: log shipping config to Loki
- `deploy/monitoring/docker-compose.monitoring.yml`: local monitoring stack
- `deploy/monitoring/grafana-dashboard-ngo-homesuite.json`: Grafana dashboard for app and fundraising metrics

## Quick Start

1. Ensure the app container is running as `app:8000` in your network.
2. Create a file containing the Prometheus scrape password and export `PROMETHEUS_SCRAPE_PASSWORD_FILE` to that absolute path.
3. Optionally set `LOKI_PUSH_URL` to forward logs to an external Loki-compatible endpoint.
4. Start monitoring stack:

```bash
export PROMETHEUS_SCRAPE_PASSWORD_FILE=/absolute/path/to/prometheus_scrape_password.txt
export LOKI_PUSH_URL=https://<external-loki-host>/loki/api/v1/push
docker compose -f deploy/monitoring/docker-compose.monitoring.yml up -d
```

4. Validate endpoints:

- Prometheus: `http://localhost:9090`
- Loki health: `http://localhost:3100/ready`

## Alert Expectations

The baseline rules alert on:

- scrape target down
- sustained high HTTP 5xx ratio
- no traffic observed for extended period
- scheduler heartbeat missing
- sustained slow endpoints (latency)
- donation failures present in pipeline

## Grafana Dashboard Import

1. Open Grafana and navigate to Dashboards -> Import.
2. Upload `deploy/monitoring/grafana-dashboard-ngo-homesuite.json`.
3. Select your Prometheus data source.
4. Save as `NGO HomeSuite Operations`.

## Production Notes

- Keep scrape passwords in mounted secret files; avoid inline secrets in Prometheus config.
- Set `LOKI_PUSH_URL` through deployment automation when forwarding logs to managed backends.
- Keep alert thresholds tuned for your real traffic profile.
- Route alerts to your incident channel/on-call destination.
