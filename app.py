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

MIAMI_DADE_TAX_LIEN_WEEKDAY = 3  # Thursday

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



def get_navigation_auction_url(page, base_url, link_text):
    """Extract a navigation URL like Current or Next Auction from the current page."""
    try:
        link = page.query_selector(f'a:has-text("{link_text}")')
        if link:
            href = link.get_attribute('href')
            if href:
                domain = re.match(r'(https?://[^/]+)', base_url).group(1)
                return domain + href
    except:
        pass
    return None


def get_next_auction_url(page, base_url):
    return get_navigation_auction_url(page, base_url, 'Next Auction')


def get_current_auction_url(page, base_url):
    return get_navigation_auction_url(page, base_url, 'Current')


def is_supported_auction_date(county_key, auction_date):
    """Return True when this auction date should be scraped for the county."""
    if auction_date is None:
        return False

    if county_key == 'miami_dade':
        # Miami-Dade tax lien sales are on Thursdays. Other calendar dates can be
        # mortgage/bank foreclosure auctions that we do not want in the CRM.
        return auction_date.weekday() == MIAMI_DADE_TAX_LIEN_WEEKDAY

    return True


def get_initial_scrape_date(county_key):
    """Return the first date the multi-scraper should hit for a county."""
    today = datetime.today()

    if county_key == 'miami_dade':
        days_until_thursday = (MIAMI_DADE_TAX_LIEN_WEEKDAY - today.weekday()) % 7
        return today + timedelta(days=days_until_thursday)

    return today


def find_first_upcoming_hillsborough_auction_url(browser, base_url, today, cutoff_date):
    """Find the first real upcoming Hillsborough auction date from the calendar page."""
    context = browser.new_context(
        user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )

    try:
        page = context.new_page()
        calendar_url = base_url + today.strftime('%m/%d/%Y')
        page.goto(calendar_url, wait_until='networkidle')
        time.sleep(4)

        html = page.content()
        candidate_dates = []
        for date_str in set(re.findall(r'(?:AuctionDate|AUCTIONDATE)=(\d{2}/\d{2}/\d{4})', html, re.IGNORECASE)):
            try:
                candidate_date = datetime.strptime(date_str, '%m/%d/%Y')
            except ValueError:
                continue

            if today <= candidate_date <= cutoff_date:
                candidate_dates.append(candidate_date)

        if not candidate_dates:
            return None

        first_date = min(candidate_dates)
        return base_url + first_date.strftime('%m/%d/%Y')
    finally:
        context.close()


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

    today = datetime.today()
    cutoff_date = today + timedelta(days=days_ahead)
    base_url = COUNTY_BASE_URLS[county_key]
    start_date = get_initial_scrape_date(county_key)
    current_url = base_url + start_date.strftime('%m/%d/%Y')

    all_listings = []
    seen_dates = set()
    dates_scraped = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        try:
            if county_key == 'hillsborough':
                current_url = find_first_upcoming_hillsborough_auction_url(browser, base_url, today, cutoff_date)

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

                should_scrape_date = is_supported_auction_date(county_key, auction_date)

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

                if should_scrape_date and listings:
                    all_listings.extend(listings)
                    dates_scraped.append(auction_date_str)

                # Decide the next auction date to scrape.
                next_url = None
                if county_key == 'miami_dade':
                    next_candidate_date = auction_date + timedelta(days=7)
                    if next_candidate_date <= cutoff_date:
                        next_url = base_url + next_candidate_date.strftime('%m/%d/%Y')
                else:
                    try:
                        # Palm Beach can land on a calendar page for a non-sale date where
                        # "Next Auction" points backward, but "Current" points to the next
                        # real upcoming auction. Prefer Current when we did not get listings.
                        fallback_candidates = []
                        if not listings:
                            current_candidate_url = get_current_auction_url(page, current_url)
                            if current_candidate_url:
                                fallback_candidates.append(current_candidate_url)

                        next_candidate_url = get_next_auction_url(page, current_url)
                        if next_candidate_url:
                            fallback_candidates.append(next_candidate_url)

                        for candidate_url in fallback_candidates:
                            candidate_date = extract_auction_date(candidate_url)
                            if (
                                candidate_date
                                and candidate_date >= auction_date
                                and candidate_date >= today
                                and is_supported_auction_date(county_key, candidate_date)
                            ):
                                next_url = candidate_url
                                break
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
# ---- Daily automation: scrape all counties and import directly to Neon ----
daily_scrape_jobs = {}

def is_cron_authorized(req):
    expected = os.environ.get('CRON_SECRET') or os.environ.get('SCRAPER_API_KEY')
    if not expected:
        return False
    auth = req.headers.get('Authorization', '')
    api_key = req.headers.get('X-API-Key', '')
    return auth == f'Bearer {expected}' or api_key == expected


def get_db_connection():
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        raise RuntimeError('DATABASE_URL is not configured')

    import psycopg2
    return psycopg2.connect(database_url, sslmode='require')


def normalize_import_case_number(case_number, county_key):
    value = (case_number or '').strip().upper()
    if county_key in ['palm_beach', 'duval']:
        return value.split('-')[-1] if '-' in value else value
    if county_key == 'miami_dade':
        return value[-6:] if len(value) > 6 else value
    return value


def initialize_import_tables(conn):
    with conn.cursor() as cur:
        cur.execute('''
            CREATE TABLE IF NOT EXISTS seen_properties (
              id SERIAL PRIMARY KEY,
              county VARCHAR(255) NOT NULL,
              case_number VARCHAR(255) NOT NULL,
              first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              last_exported_at TIMESTAMP DEFAULT NULL,
              export_count INTEGER DEFAULT 0,
              notes TEXT DEFAULT '',
              CONSTRAINT seen_properties_county_check CHECK (county IN ('palm_beach', 'miami_dade', 'duval', 'hillsborough')),
              CONSTRAINT seen_properties_unique UNIQUE (county, case_number)
            )
        ''')
        cur.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS properties_active_unique
            ON properties (county, case_number)
            WHERE deleted_at IS NULL
        ''')
        cur.execute('''
            CREATE TABLE IF NOT EXISTS scrape_logs (
              id SERIAL PRIMARY KEY,
              county VARCHAR(255) NOT NULL,
              properties_found INTEGER DEFAULT 0,
              status VARCHAR(50) NOT NULL,
              timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    conn.commit()


def mark_import_property_seen(conn, county_key, case_number):
    normalized = normalize_import_case_number(case_number, county_key)
    with conn.cursor() as cur:
        cur.execute('''
            INSERT INTO seen_properties (county, case_number, first_seen_at, last_seen_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT (county, case_number)
            DO UPDATE SET last_seen_at = CURRENT_TIMESTAMP
        ''', (county_key, normalized))


def import_scraped_properties(county_key, properties):
    conn = get_db_connection()
    imported = 0
    already_present = 0
    errors = 0

    try:
        initialize_import_tables(conn)
        with conn.cursor() as cur:
            for property_data in properties:
                try:
                    case_number = normalize_import_case_number(property_data.get('case_number'), county_key)
                    if not case_number:
                        errors += 1
                        continue

                    cur.execute(
                        'SELECT id FROM seen_properties WHERE county = %s AND case_number = %s',
                        (county_key, case_number)
                    )
                    if cur.fetchone():
                        mark_import_property_seen(conn, county_key, case_number)
                        already_present += 1
                        continue

                    cur.execute('''
                        SELECT id FROM properties
                        WHERE county = %s
                          AND deleted_at IS NULL
                          AND (
                            case_number = %s
                            OR REPLACE(case_number, '-', '') = REPLACE(%s, '-', '')
                          )
                    ''', (county_key, case_number, case_number))
                    if cur.fetchone():
                        mark_import_property_seen(conn, county_key, case_number)
                        already_present += 1
                        continue

                    cur.execute('''
                        INSERT INTO properties (case_number, address, city, state, zip, auction_date, county, stage, notes)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'new_leads', %s)
                    ''', (
                        case_number,
                        property_data.get('address') or '',
                        property_data.get('city') or 'Unknown',
                        property_data.get('state') or 'FL',
                        property_data.get('zip') or '00000',
                        property_data.get('auction_date'),
                        county_key,
                        f'Auto-imported from {county_key} daily automation'
                    ))
                    mark_import_property_seen(conn, county_key, case_number)
                    imported += 1
                except Exception as exc:
                    print(f'Error importing {county_key} property {property_data.get("case_number")}: {exc}', flush=True)
                    errors += 1

            cur.execute(
                'INSERT INTO scrape_logs (county, properties_found, status) VALUES (%s, %s, %s)',
                (county_key, imported, 'success' if errors == 0 else 'failure')
            )

        conn.commit()
        return {
            'success': True,
            'county': county_key,
            'imported': imported,
            'already_present': already_present,
            'errors': errors,
            'total': len(properties)
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_daily_scrape_job(job_id, days_ahead):
    daily_scrape_jobs[job_id]['status'] = 'running'
    total_imported = 0
    results = []

    for county_key in COUNTY_MAP.keys():
        county_result = {
            'county': county_key,
            'status': 'running',
            'imported': 0,
            'already_present': 0,
            'errors': 0,
            'total': 0,
            'dates_scraped': [],
            'error': None
        }
        daily_scrape_jobs[job_id]['results'][county_key] = county_result

        try:
            listings, dates_scraped = scrape_auction_multi(county_key, days_ahead)
            import_result = import_scraped_properties(county_key, listings)
            county_result.update(import_result)
            county_result['status'] = 'done'
            county_result['dates_scraped'] = dates_scraped
            total_imported += import_result['imported']
        except Exception as exc:
            import traceback
            county_result['status'] = 'error'
            county_result['error'] = str(exc)
            county_result['traceback'] = traceback.format_exc()

        results.append(county_result.copy())

    has_errors = any(result['status'] == 'error' or result.get('errors', 0) > 0 for result in results)
    daily_scrape_jobs[job_id].update({
        'status': 'error' if has_errors else 'done',
        'total_imported': total_imported,
        'results_list': results,
        'finished_at': datetime.utcnow().isoformat() + 'Z'
    })


@app.route('/cron/daily-scrape', methods=['POST', 'GET'])
def start_daily_scrape():
    if not is_cron_authorized(request):
        return jsonify({'error': 'Unauthorized or missing CRON_SECRET/SCRAPER_API_KEY'}), 401

    data = request.get_json(silent=True) or {}
    days_ahead = int(data.get('days_ahead', 120))
    job_id = f"daily_{int(time.time())}"

    daily_scrape_jobs[job_id] = {
        'job_id': job_id,
        'status': 'started',
        'days_ahead': days_ahead,
        'started_at': datetime.utcnow().isoformat() + 'Z',
        'finished_at': None,
        'total_imported': 0,
        'results': {},
        'results_list': []
    }

    thread = threading.Thread(target=run_daily_scrape_job, args=(job_id, days_ahead), daemon=True)
    thread.start()

    return jsonify({
        'success': True,
        'job_id': job_id,
        'status': 'started',
        'message': 'Daily four-county scrape started in Railway background worker.'
    })


@app.route('/cron/daily-scrape/status/<job_id>', methods=['GET'])
def daily_scrape_status(job_id):
    if not is_cron_authorized(request):
        return jsonify({'error': 'Unauthorized or missing CRON_SECRET/SCRAPER_API_KEY'}), 401
    if job_id not in daily_scrape_jobs:
        return jsonify({'error': 'Job not found'}), 404
    return jsonify(daily_scrape_jobs[job_id])

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
