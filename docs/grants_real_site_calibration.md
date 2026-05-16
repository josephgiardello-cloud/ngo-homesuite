# Real Grant-Site Calibration and Sync (Grants.gov)

Date: 2026-05-16

## Purpose

Move pre-award opportunity intake from mock-only records to live grant-site payloads while enforcing baseline standards checks before opportunities enter the pipeline.

## What Was Added

- API standards calibration endpoint:
  - `POST /api/v2/grants/opportunities/calibrate`
- API import endpoint:
  - `POST /api/v2/grants/opportunities/import/grants-gov`
- Service methods in grants pre-award domain:
  - `calibrate_external_opportunity(...)`
  - `import_external_opportunity(...)`
  - `import_grants_gov_opportunities(...)`

## Calibration Standard (Current)

For `source=grants_gov`, a payload is considered ready when all required fields are present:

- `external_id`
- `title`
- `funder_name`
- `deadline`

Returned calibration object includes:

- `score` (0-100)
- `missing_fields`
- `is_ready`
- `normalized_preview`

## Live Sync Configuration

Environment variables:

- `GRANTS_GOV_SEARCH_URL`
  - Optional override. Default is `https://api.grants.gov/v1/api/search2`.
- `GRANTS_GOV_API_KEY`
  - Optional. If present, sent as `X-Api-Key` header.

## API Usage

1. Calibrate one raw external payload first:

```http
POST /api/v2/grants/opportunities/calibrate
Content-Type: application/json

{
  "source": "grants_gov",
  "payload": {
    "opportunityId": "GG-1001",
    "opportunityNumber": "HHS-2026-123",
    "opportunityTitle": "Community Health Expansion",
    "agencyName": "Department of Health and Human Services",
    "closeDate": "2026-12-15",
    "awardFloor": 25000,
    "awardCeiling": 100000,
    "opportunityUrl": "https://www.grants.gov/example-opportunity"
  }
}
```

2. Import explicit records (deterministic mode):

```http
POST /api/v2/grants/opportunities/import/grants-gov
Content-Type: application/json

{
  "records": [
    {
      "opportunityId": "GG-2002",
      "opportunityNumber": "HUD-2026-777",
      "opportunityTitle": "Affordable Housing Support",
      "agencyName": "Department of Housing and Urban Development",
      "closeDate": "2026-11-30",
      "awardFloor": 10000,
      "awardCeiling": 50000,
      "opportunityUrl": "https://www.grants.gov/hud-2026-777"
    }
  ],
  "probability": 0.55,
  "status": "identified"
}
```

3. Import live from Grants.gov endpoint:

```http
POST /api/v2/grants/opportunities/import/grants-gov
Content-Type: application/json

{
  "keyword": "housing",
  "rows": 25,
  "status": "identified",
  "probability": 0.4
}
```

## Idempotency Behavior

The importer attempts to match existing opportunities for the organization using source markers in `notes` (`source` + `external_id`).

- If matched: existing opportunity is updated.
- If not matched: a new opportunity is created.

## Compliance and Evidence Notes

- Imported records retain source provenance in opportunity notes (`source`, `external_id`, source URL, sync timestamp).
- Calibration failures are returned in API responses and are not imported.
- This is the baseline ingestion standard; extend required-field checks per funder profile and 2 CFR evidence requirements over time.
