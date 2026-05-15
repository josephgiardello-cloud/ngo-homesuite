# ADR 0001: Feature Claims and Maturity Labels

- Status: Accepted
- Date: 2026-05-15

## Context

Project documentation included broad feature claims that could be interpreted as fully production-mature, while some modules are still foundational or partial.

## Decision

Adopt an explicit maturity model and maintain a feature status matrix.

- README should present capabilities but link to an authoritative status file.
- `docs/feature_status.md` is the source of truth for maturity labels.
- Claims in README and roadmap must not conflict with maturity labels.

## Consequences

Positive:
- Reduces mismatch between product expectations and implementation reality.
- Improves planning and prioritization for hardening work.

Trade-offs:
- Requires ongoing documentation discipline.
- Adds maintenance overhead during rapid iteration.

## Follow-up

- Review maturity labels during each release cut.
- Update labels when test coverage and runbooks materially improve.
