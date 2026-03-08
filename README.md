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
