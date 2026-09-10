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
Fixtures are synthetic: live requests from the development machine returned
HTTP 403 on September 10, 2026. Live source fields and pagination still require
verification from an environment with access.
