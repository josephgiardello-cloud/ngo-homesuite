# Service Layer Maturity Audit - 2026-05-15

## Scope
Route/service consistency focus in `ngo_homesuite/web/main_routes.py` for donation and recurring donation flows.

## Completed in This Pass
- Moved recurring plan create/list/process logic from route handlers into `DonationService`.
- Moved donation list/export filtering into `DonationService`.
- Moved donation creation in `/donations/new` to `DonationService` and aligned lifecycle to receipt generation (`received -> processed -> receipted`).
- Replaced direct fund option query in donation form with `FundService.list_funds(active_only=True)`.
- Added 404-safe donation receipt lookup via service (`DonationNotFound` -> HTTP 404).
- Added regression test for successful recurring processing creating both donation and receipt.

## Additional Pass (Option 1: Expenses + Funds)
- Added `ExpenseService` and moved expense list/create/export logic out of route query composition.
- Extended `FundService` with filtered non-paginated listing for route and selector usage.
- Migrated funds list/export/new/edit routes to `FundService` methods.
- Migrated expense create form fund selectors to `FundService` active-fund listing.
- Preserved existing route behavior for not-found fund edit (`404`) after service migration.

## Additional Pass (Proceed): Projects + Reports
- Added `ProjectService` and moved project list/create/edit/export filtering and persistence into service methods.
- Added `ReportingService.financial_overview()` and moved reports-page aggregate/chart query logic out of route handlers.
- Migrated projects routes to `ProjectService` methods and preserved not-found edit behavior (`404`).
- Migrated reports page route to consume a single reporting service payload.
- Added route regression coverage for project list/export, project create/edit, and reports page rendering.

## Final Pass (Proceed): Mobile Intake + Domain Registry
- Rewired mobile intake beneficiary create to `beneficiary_service.create_beneficiary`.
- Added `volunteer_service.create_volunteer` and `volunteer_service.list_recent_volunteers`, then rewired mobile intake volunteer create/list to use them.
- Rewired domain registry entity source reads to service methods:
	- donors -> `DonorService.list_all_donors`
	- projects -> `ProjectService.list_all_projects`
	- beneficiaries -> `beneficiary_service.list_beneficiaries`
- Added domain aggregate helpers in `ReportingService` and rewired registry aggregates to them:
	- `donation_purpose_totals`
	- `foundation_donor_totals`
	- `project_donation_counts`

## Current Maturity Snapshot
- Donation + recurring flows: ready (service-backed, route-thin, tested).
- Donor CRUD + merge + dashboard profile summary: beta-to-ready (service-backed, tested).
- Expenses + funds flows: beta-to-ready (service-backed, route-thin, tested).
- Projects + reports flows: beta-to-ready (service-backed, route-thin, tested).
- Mobile intake + domain registry flows: beta-to-ready (service-backed, route-thin, tested).
- Remaining route hotspots: limited to lower-priority route areas outside the original donation/fund/donor/payment cleanup scope.

## Remaining Direct ORM in Main Routes (Updated)
- Org fallback helper and selected route helpers still perform direct reads in low-risk paths.
- Primary financial + donor + project + reports + mobile intake paths are now service-backed.

## Validation
- Targeted tests: `ngo_homesuite/web/test_sprint1_features.py`, `ngo_homesuite/web/test_donor_delete_routes.py`.
- Additional targeted tests: `ngo_homesuite/web/test_mobile_intake_routes.py`, `ngo_homesuite/web/test_workflow_routes.py`.
- Full suite: `255 passed, 1 skipped`.

## Suggested Next Pass (No New Framework Layers)
1. Optional: remove remaining low-impact direct ORM reads in helper/fallback routes.
2. Optional: replace `Query.get()` usages in volunteer/admin areas with `db.session.get()` to eliminate legacy warnings.
