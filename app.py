from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
import re
import time
import os
import threading
from datetime import datetime, timedelta

app = Flask(__name__)

# In-memory job store: job_id -> {status, properties, dates_scraped, total, error}
scrape_jobs = {}

COUNTY_MAP = {
    'palm_beach': 'Palm Beach',
    'miami_dade': 'Miami-Dade',
    'duval': 'Duval',
    'hillsborough': 'Hillsborough'
}

COUNTY_BASE_URLS = {
    'palm_beach': 'https://palmbeach.realtaxdeed.com/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=',
    'miami_dade': 'https://miami-dade.realtaxdeed.com/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=',
    'duval': 'https://duval.realtaxdeed.com/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=',
    'hillsborough': 'https://hillsborough.realtaxdeed.com/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=',
}

def normalize_case_number(case_number, county_key):
    """
    Normalize case number to correct format per county.

    - Palm Beach: 2025-2212TD → 2212TD
    - Miami-Dade: 2025A00443 → A00443
    - Duval: 2024-1429TD → 1429TD
    - Hillsborough: 2026-188 → 2026-188 (keep full format)
    """
    if county_key in ['palm_beach', 'duval']:
        if '-' in case_number:
            return case_number.split('-')[-1]
        return case_number

    elif county_key == 'hillsborough':
        # Keep full format e.g. 2026-188
        return case_number

    elif county_key == 'miami_dade':
        if len(case_number) > 6:
            return case_number[-6:]
        return case_number

    else:
        return case_number


def extract_auction_date(url):
    """Return auction date from a URL, if present."""
    if not url:
        return None

    match = re.search(r'(?:AUCTIONDATE|AuctionDate)=(\d{2}/\d{2}/\d{4})', url, re.IGNORECASE)
    if not match:
        return None

    try:
        return datetime.strptime(match.group(1), '%m/%d/%Y')
    except ValueError:
        return None



def get_next_auction_url(page, base_url):
    """Extract the Next Auction URL from the current page."""
    try:
        # Look for "Next Auction >>" link
        next_link = page.query_selector('a:has-text("Next Auction")')
        if next_link:
            href = next_link.get_attribute('href')
            if href:
                # href is relative like /index.cfm?zaction=AUCTION&zmethod=PREVIEW&AuctionDate=04/16/2026
                domain = re.match(r'(https?://[^/]+)', base_url).group(1)
                full_url = domain + href
                return full_url
    except:
        pass
    return None


def scrape_auction(url, county_key, page=None, browser=None):
    """Scrape a single auction date. Reuses existing page/browser if provided."""

    own_browser = False
    if browser is None:
        own_browser = True

    try:
        if own_browser:
            from playwright.sync_api import sync_playwright
            p_instance = sync_playwright().__enter__()
            browser = p_instance.chromium.launch(headless=True)

        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()
        page.goto(url, wait_until='networkidle')
        time.sleep(6)

        date_match = re.search(r'AUCTIONDATE=(\d{2}/\d{2}/\d{4})', url, re.IGNORECASE)
        if not date_match:
            date_match = re.search(r'AuctionDate=(\d{2}/\d{2}/\d{4})', url, re.IGNORECASE)
        auction_date = date_match.group(1) if date_match else None

        try:
            page.wait_for_selector('#Area_W', timeout=5000)
        except:
            pass

        listings = []

        while True:
            time.sleep(2)
            area_w = page.query_selector('#Area_W')
            if area_w:
                text = area_w.inner_text()
                entries = text.split('Auction Starts')
                for entry in entries[1:]:
                    listing = parse_auction_entry(entry, auction_date, county_key)
                    if listing:
                        listings.append(listing)

            try:
                next_button = page.query_selector('.Head_W .PageFrame .PageRight')
                if next_button:
                    old_text = area_w.inner_text() if area_w else ""
                    next_button.click()
                    time.sleep(3)
                    new_area_w = page.query_selector('#Area_W')
                    new_text = new_area_w.inner_text() if new_area_w else ""
                    if new_text == old_text or not new_text:
                        break
                else:
                    break
            except:
                break

        # Get next auction URL before closing page
        next_url = get_next_auction_url(page, url)
        context.close()

        return listings, next_url

    finally:
        if own_browser and browser:
            browser.close()


def scrape_auction_multi(county_key, days_ahead=120):
    """
    Scrape all auction dates for a county up to days_ahead days from today.
    Follows 'Next Auction >>' links automatically.
    """
    if county_key not in COUNTY_BASE_URLS:
        raise ValueError(f"Invalid county: {county_key}")

    cutoff_date = datetime.today() + timedelta(days=days_ahead)
    base_url = COUNTY_BASE_URLS[county_key]
    today_str = datetime.today().strftime('%m/%d/%Y')
    current_url = base_url + today_str

    all_listings = []
    seen_dates = set()
    dates_scraped = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        try:
            max_iterations = 30  # Safety cap
            iteration = 0

            while current_url and iteration < max_iterations:
                iteration += 1

                # Extract date from URL
                date_match = re.search(r'(?:AUCTIONDATE|AuctionDate)=(\d{2}/\d{2}/\d{4})', current_url, re.IGNORECASE)
                if not date_match:
                    break

                auction_date_str = date_match.group(1)

                # Parse and check cutoff
                try:
                    auction_date = datetime.strptime(auction_date_str, '%m/%d/%Y')
                except ValueError:
                    break

                if auction_date > cutoff_date:
                    break

                # Skip already-seen dates
                if auction_date_str in seen_dates:
                    break
                seen_dates.add(auction_date_str)

                # Scrape this date
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                )
                page = context.new_page()
                page.goto(current_url, wait_until='networkidle')
                time.sleep(6)

                try:
                    page.wait_for_selector('#Area_W', timeout=5000)
                except:
                    pass

                listings = []
                while True:
                    time.sleep(2)
                    area_w = page.query_selector('#Area_W')
                    if area_w:
                        text = area_w.inner_text()
                        entries = text.split('Auction Starts')
                        for entry in entries[1:]:
                            listing = parse_auction_entry(entry, auction_date_str, county_key)
                            if listing:
                                listings.append(listing)

                    try:
                        next_page_btn = page.query_selector('.Head_W .PageFrame .PageRight')
                        if next_page_btn:
                            old_text = area_w.inner_text() if area_w else ""
                            next_page_btn.click()
                            time.sleep(3)
                            new_area_w = page.query_selector('#Area_W')
                            new_text = new_area_w.inner_text() if new_area_w else ""
                            if new_text == old_text or not new_text:
                                break
                        else:
                            break
                    except:
                        break

                if listings:
                    all_listings.extend(listings)
                    dates_scraped.append(auction_date_str)

                # Get next auction URL, but never walk backwards into historical auctions.
                next_url = None
                try:
                    next_link = page.query_selector('a:has-text("Next Auction")')
                    if next_link:
                        href = next_link.get_attribute('href')
                        if href:
                            domain = re.match(r'(https?://[^/]+)', current_url).group(1)
                            candidate_url = domain + href
                            candidate_date = extract_auction_date(candidate_url)

                            # Hillsborough can return a past "Next Auction" link when the
                            # requested date has no upcoming sale, which makes the job crawl
                            # through old weeks until max_iterations is hit.
                            if candidate_date and candidate_date >= auction_date and candidate_date >= datetime.today():
                                next_url = candidate_url
                except:
                    pass

                context.close()
                current_url = next_url

        finally:
            browser.close()

    return all_listings, dates_scraped


def parse_auction_entry(text, auction_date, county_key):
    """Parse a single auction entry - handles multiple case number formats"""

    case_match = re.search(r'Case\s*#\s*:\s*(\d{2,4}-[A-Z0-9]+)', text, re.IGNORECASE)

    if not case_match:
        case_match = re.search(r'Case\s*#\s*:\s*(\d{4}[A-Z]\d+)', text, re.IGNORECASE)

    if not case_match:
        case_match = re.search(r'Case\s*#\s*:\s*([A-Z0-9-]+)', text, re.IGNORECASE)

    if not case_match:
        return None

    raw_case_number = case_match.group(1)
    case_number = normalize_case_number(raw_case_number, county_key)

    addr_match = re.search(r'Property Address:\s*([^\n]+)\n\s*([^,]+),\s*FL-?\s*(\d{5})', text, re.IGNORECASE)

    if not addr_match:
        addr_match = re.search(r'Property Address:\s*([^\n]+?)\s+([A-Z\s]+?),?\s*FL-?\s*(\d{5})', text, re.IGNORECASE)

    if not addr_match:
        addr_match = re.search(r'Property Address:\s*([^\n]+)', text, re.IGNORECASE)
        if addr_match:
            full_addr = addr_match.group(1).strip()
            city_zip_match = re.search(r'([A-Za-z\s]+),?\s*FL[- ]?(\d{5})', full_addr, re.IGNORECASE)
            if city_zip_match:
                street = re.sub(r'[,]?\s*[A-Za-z\s]+,?\s*FL[- ]?\d{5}.*$', '', full_addr, flags=re.IGNORECASE).strip()
                city = city_zip_match.group(1).strip()
                zip_code = city_zip_match.group(2)
                return {
                    'auction_date': auction_date,
                    'case_number': case_number,
                    'address': street,
                    'city': city,
                    'state': 'FL',
                    'zip': zip_code,
                    'county': county_key
                }

    if not addr_match:
        return None

    street = addr_match.group(1).strip()
    city = addr_match.group(2).strip() if len(addr_match.groups()) >= 2 else "Unknown"
    zip_code = addr_match.group(3).strip() if len(addr_match.groups()) >= 3 else "00000"

    return {
        'auction_date': auction_date,
        'case_number': case_number,
        'address': street,
        'city': city,
        'state': 'FL',
        'zip': zip_code,
        'county': county_key
    }


@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status': 'healthy'}), 200


@app.route('/scrape', methods=['POST'])
def scrape():
    data = request.json
    url = data.get('url')
    county = data.get('county', '').lower()

    if not url:
        return jsonify({'error': 'URL is required'}), 400

    if not county or county not in COUNTY_MAP:
        return jsonify({
            'error': 'Invalid county',
            'valid_counties': list(COUNTY_MAP.keys())
        }), 400

    try:
        listings, _ = scrape_auction(url, county)
        return jsonify({
            'success': True,
            'county': county,
            'county_display': COUNTY_MAP.get(county, county),
            'count': len(listings),
            'properties': listings
        })
    except Exception as e:
        import traceback
        return jsonify({
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }), 500


def run_scrape_job(job_id, county, days_ahead):
    """Background thread: scrapes and stores results in scrape_jobs."""
    try:
        scrape_jobs[job_id]['status'] = 'running'
        listings, dates_scraped = scrape_auction_multi(county, days_ahead)
        scrape_jobs[job_id].update({
            'status': 'done',
            'properties': listings,
            'dates_scraped': dates_scraped,
            'total': len(listings),
            'error': None
        })
    except Exception as e:
        import traceback
        scrape_jobs[job_id].update({
            'status': 'error',
            'error': str(e),
            'traceback': traceback.format_exc()
        })


@app.route('/scrape-multi', methods=['POST'])
def scrape_multi():
    """
    Start async scrape job for all upcoming auction dates up to days_ahead days out.
    Returns job_id immediately; use /scrape-multi/status/<job_id> to poll,
    and /scrape-multi/results/<job_id> to fetch properties when done.
    """
    data = request.json
    county = data.get('county', '').lower()
    days_ahead = int(data.get('days_ahead', 120))

    if not county or county not in COUNTY_MAP:
        return jsonify({
            'error': 'Invalid county',
            'valid_counties': list(COUNTY_MAP.keys())
        }), 400

    job_id = f"{county}_{int(time.time())}"
    scrape_jobs[job_id] = {
        'status': 'started',
        'county': county,
        'properties': [],
        'dates_scraped': [],
        'total': 0,
        'error': None
    }

    t = threading.Thread(target=run_scrape_job, args=(job_id, county, days_ahead), daemon=True)
    t.start()

    return jsonify({
        'success': True,
        'job_id': job_id,
        'status': 'started',
        'message': f'Scraping all {COUNTY_MAP[county]} auctions for next {days_ahead} days. Poll /scrape-multi/status/{job_id} for progress.'
    })


@app.route('/scrape-multi/status/<job_id>', methods=['GET'])
def scrape_multi_status(job_id):
    """Poll job status. When status=done, call /results/<job_id> to get properties."""
    if job_id not in scrape_jobs:
        return jsonify({'error': 'Job not found'}), 404

    job = scrape_jobs[job_id]
    return jsonify({
        'job_id': job_id,
        'status': job['status'],
        'total': job['total'],
        'dates_scraped': job['dates_scraped'],
        'imported': 0,  # import happens on Vercel side
        'error': job.get('error')
    })


@app.route('/scrape-multi/results/<job_id>', methods=['GET'])
def scrape_multi_results(job_id):
    """Fetch actual scraped properties once job is done."""
    if job_id not in scrape_jobs:
        return jsonify({'error': 'Job not found'}), 404

    job = scrape_jobs[job_id]
    if job['status'] not in ('done', 'error'):
        return jsonify({'error': 'Job not done yet', 'status': job['status']}), 202

    return jsonify({
        'job_id': job_id,
        'status': job['status'],
        'success': job['status'] == 'done',
        'county': job['county'],
        'count': job['total'],
        'dates_scraped': job['dates_scraped'],
        'properties': job['properties'],
        'error': job.get('error')
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
# cache bust Thu Apr  2 09:54:07 EDT 2026
