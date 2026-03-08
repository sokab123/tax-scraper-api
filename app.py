from flask import Flask, request, jsonify
from playwright.sync_api import sync_playwright
import re
import time
import os

app = Flask(__name__)

COUNTY_MAP = {
    'palm_beach': 'Palm Beach',
    'miami_dade': 'Miami-Dade',
    'duval': 'Duval',
    'hillsborough': 'Hillsborough'
}

def scrape_auction(url, county_key):
    """Scrape auction data using Playwright"""
    
    if county_key not in COUNTY_MAP:
        raise ValueError(f"Invalid county: {county_key}. Must be one of: {', '.join(COUNTY_MAP.keys())}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        page = context.new_page()
        
        page.goto(url, wait_until='networkidle')
        time.sleep(8)
        
        # Extract auction date from URL
        date_match = re.search(r'AUCTIONDATE=(\d{2}/\d{2}/\d{4})', url)
        auction_date = date_match.group(1) if date_match else None
        
        try:
            page.wait_for_selector('#Area_W', timeout=5000)
        except:
            pass
        
        listings = []
        page_num = 1
        
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
            
            # Try next page
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
                    
                    page_num += 1
                else:
                    break
                    
            except:
                break
        
        browser.close()
        return listings

def parse_auction_entry(text, auction_date, county_key):
    """Parse a single auction entry - handles multiple case number formats"""
    
    # Try different case number patterns
    # Pattern 1: Palm Beach format (26-ABC123)
    case_match = re.search(r'Case\s*#\s*:\s*(\d{2,4}-[A-Z0-9]+)', text, re.IGNORECASE)
    
    # Pattern 2: Miami-Dade format (2025A00443)
    if not case_match:
        case_match = re.search(r'Case\s*#\s*:\s*(\d{4}[A-Z]\d+)', text, re.IGNORECASE)
    
    # Pattern 3: Generic fallback (any text after "Case #:")
    if not case_match:
        case_match = re.search(r'Case\s*#\s*:\s*([A-Z0-9-]+)', text, re.IGNORECASE)
    
    if not case_match:
        return None
    
    case_number = case_match.group(1)
    
    # Try to find property address
    # Pattern 1: Multi-line with city/state/zip on next line
    addr_match = re.search(r'Property Address:\s*([^\n]+)\n\s*([^,]+),\s*FL-?\s*(\d{5})', text, re.IGNORECASE)
    
    # Pattern 2: All on one line
    if not addr_match:
        addr_match = re.search(r'Property Address:\s*([^\n]+?)\s+([A-Z\s]+?),?\s*FL-?\s*(\d{5})', text, re.IGNORECASE)
    
    # Pattern 3: Simpler format without ZIP match
    if not addr_match:
        addr_match = re.search(r'Property Address:\s*([^\n]+)', text, re.IGNORECASE)
        if addr_match:
            # Try to extract city/zip from the address line
            full_addr = addr_match.group(1).strip()
            # Look for city and ZIP in the full address
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
        # If no address found, skip this entry
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
    # TEMPORARILY REMOVED API KEY CHECK FOR TESTING
    
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
        listings = scrape_auction(url, county)
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

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port)
