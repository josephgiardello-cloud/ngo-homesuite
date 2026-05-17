# Email Integration Readiness Checklist

Use this checklist before enabling outbound email in production.

## 1) Choose primary provider

- Option A: SendGrid API (`SENDGRID_API_KEY`)
- Option B: SMTP (`MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, `MAIL_USERNAME`, `MAIL_PASSWORD`)
- You can configure both; delivery code is SendGrid-first with SMTP fallback.

## 2) Set required runtime configuration

- `DEFAULT_MAIL_SENDER` should be a domain you control.
- If using SMTP with authentication, set `MAIL_USERNAME` and `MAIL_PASSWORD`.
- If using TLS SMTP relay, set `MAIL_USE_TLS=true`.
- For Gmail SMTP, use `MAIL_SERVER=smtp.gmail.com`, `MAIL_PORT=587`, `MAIL_USE_TLS=true`, and a Gmail app password.

## 3) Verify DNS and sender reputation

- Configure SPF for your sending domain.
- Configure DKIM for your provider/domain.
- Configure DMARC policy and reporting.
- Confirm the sender mailbox/domain is verified in your provider.

## 4) Run smoke validation in the app

- Call `POST /integrations/email/smoke` with `{ "probe": false }` to validate configuration-only readiness.
- Call `POST /integrations/email/smoke` with `{ "probe": true }` to validate provider connectivity (no message is sent).
- Ensure at least one provider is `ready=true`.

## 5) Confirm governance controls for external comms

- Ensure sender is `admin` or has `can_authorize_external_comms=true`.
- Ensure required human-in-the-loop confirmation metadata is enforced.
- Review audit log entries under external communications authorization.

## 6) Run operational checks

- Trigger a low-volume test campaign to internal test recipients.
- Verify bounce/complaint handling path for your provider.
- Monitor app integration events (`email_smoke`, campaign send outcomes).

## 7) Security and secrets

- Store all mail credentials in environment/secret manager only.
- Rotate API keys/passwords on schedule.
- Never commit credentials to repository files.

## 8) Rollout strategy

- Start with a small allowlist audience.
- Increase batch size gradually while monitoring failures.
- Define rollback criteria (for example, sustained provider errors > 5%).
