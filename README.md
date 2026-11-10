# Tax Scraper API

Flask API for scraping tax auction websites using Playwright.

## Endpoints

### GET /health
Health check endpoint

### POST /scrape
Scrape a tax auction URL

**Headers:**
- `X-API-Key`: API key for authentication
- `Content-Type`: application/json

**Body:**
```json
{
  "url": "https://palmbeach.realtaxdeed.com/...",
  "county": "palm_beach"
}
```

**Response:**
```json
{
  "success": true,
  "county": "palm_beach",
  "count": 96,
  "properties": [...]
}
```

## Environment Variables

- `API_KEY`: API key for authentication
- `PORT`: Port to run the server (default: 8000)

## Deployment

Deployed on Railway. Auto-deploys from GitHub.
# Force rebuild Sun Mar  8 19:50:46 EDT 2026
# Rebuild 1773015409

## Broward County

`broward` uses https://broward.realtaxdeed.com with the shared waiting-auctions
pagination and upcoming-date navigation. Both `/scrape` and `/scrape-multi`
accept the county; the existing daily job includes it automatically.
Full case numbers are preserved. HTTP failures and unfamiliar/incomplete
Broward listing layouts raise an error instead of producing a misleading empty
success or partial import. Apply the CRM's `20260910_add_broward.sql` migration
before importing into an existing database.

Run contract tests with `python3 -m unittest discover -s tests -v`.
Fifteen contract/regression tests pass, including the observed Broward no-sale-page
layout. Live verification on September 10, 2026 collected 27 unique properties
across three pages for October 26, 2026, using the existing configured browser
user agent. Sample source fields matched Chromium; both single-date scraping
and local/hosted upcoming-auction navigation were verified. The CRM imported all 27
through its existing duplicate/history checks.

## Palm Beach transient-source recovery (October 31, 2026)

The daily job's first Palm Beach clerk home request returned HTTP 500 while
the other four counties completed. The clerk site later recovered. `http_get`
and the clerk's read-only search-form `http_post` now retry transient HTTP
429/500/502/503/504 and network/timeouts up to three attempts with 1s/2s
backoff and the existing 60s per-request timeout. Non-transient HTTP errors
still fail immediately; non-clerk requests are never automatically retried.
An exhausted retry remains an error, never an empty successful scrape.

The authorized recovery called only `scrape_auction_multi('palm_beach', 120)`
and the existing deduplicating `import_scraped_properties` in the running
service. It did not rescrape the four successful counties. Full-failure
evidence and recovery output are retained under Stewart Files / Tax CRM Audit /
Automation Repair 2026-10-31. Do not repeat an uncertain recovery blindly:
check the completed output and a live read-only database audit first.

## Palm Beach property ZIP integrity (November 10, 2026)

The property appraiser's `AddressLine1`/`AddressLine3` are **owner mailing**
fields, not situs fields. Never assign that ZIP to a property just because it
is a Florida address or the owner's mailing city matches the municipality.
`propertyDetail.ZipCode` is often null. Resolve ZIPs through the official
county Situs Address layer (`OpenData/open_data_v2/FeatureServer/0`), querying
an exact 17-digit `PCN` and reading `ZIP_CODE`. Multiple unit records are safe
only when all returned ZIPs agree; missing, ambiguous, truncated or failed
lookups must not fall back to the owner's ZIP. A missing situs street address
must not fall back to the owner's mailing street either.

The importer can repair a nonblank Palm Beach ZIP only when incoming metadata
identifies this verified source and the stored, scraped, and appraiser situs
addresses match. Other counties retain the missing-only update rule. Imports
preserve notes, stages, export/deletion history and case identifiers. Tests
cover the reported `3207TD` example (1256 W 30TH ST 1: situs 33404, owner 33407),
ambiguous units, lookup failures, wrong parcels and idempotent nonblank updates.

For audits, map a saved case to its official clerk PCN using its sale date;
rescheduled cases may use a unique PCN across clerk sale dates, but require
an unchanged appraiser situs street before correcting the ZIP. Back up all
records, then apply guarded ZIP-only updates by database id and original ZIP,
address and timestamp. Verify all other fields and counties remain unchanged.
Do not restore deleted/exported leads. Evidence for this repair is under
Stewart Files / Tax CRM Audit / ZIP Repair 2026-11-10.
