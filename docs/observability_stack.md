# Observability Stack (Prometheus + Loki)

This project ships baseline observability deployment artifacts under `deploy/monitoring/`.

## Included Artifacts

- `deploy/monitoring/prometheus.yml`: metrics scrape config
- `deploy/monitoring/alerts.yml`: baseline alert rules
- `deploy/monitoring/promtail-config.yml`: log shipping config to Loki
- `deploy/monitoring/docker-compose.monitoring.yml`: local monitoring stack

## Quick Start

1. Ensure the app container is running as `app:8000` in your network.
2. Update credentials in `deploy/monitoring/prometheus.yml`.
3. Start monitoring stack:

```bash
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

## Production Notes

- Replace placeholder auth values with secret-manager injected values.
- Keep alert thresholds tuned for your real traffic profile.
- Route alerts to your incident channel/on-call destination.
