# NGO HomeSuite Sprint 1 Backlog

## Scope
- Recurring donations with basic failed-charge handling
- Automated receipt generation and status tracking
- Duplicate donor detection and merge workflow
- Public donation form/portal endpoint

## Delivered
- [x] Data model: recurring donation plan
- [x] Data model: donation receipt tracking
- [x] Public portal: /give donation form (guest-access)
- [x] Recurring plans UI: create and list plans
- [x] Recurring processor: run due plans and mark failures
- [x] Receipt generation: auto-generate on donation creation
- [x] Donor dedupe view and merge action
- [x] Navigation/CTA updates for new features

## Follow-up Enhancements
- [ ] Integrate real payment gateway retries/webhooks for recurring failures
- [ ] Attach receipt PDF to outbound email
- [ ] Add fuzzy matching thresholds to dedupe (Levenshtein/Jaro-Winkler)
- [ ] Add background scheduler/cron for recurring processing
- [ ] Add merge audit trail entity and rollback support
