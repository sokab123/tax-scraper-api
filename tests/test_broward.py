"""Synthetic RealTaxDeed contract fixtures, not verified live Broward records."""
import unittest
from unittest.mock import Mock, patch
from datetime import datetime
import app


class BrowardTests(unittest.TestCase):
    def test_complete_case_and_location(self):
        text = '10:00 AM\nCase #: 2026-12345-TD\nProperty Address:\n123 EXAMPLE ST\nFORT LAUDERDALE, FL 33301-1234\nAssessed Value: $1'
        result = app.parse_auction_entry(text, '09/16/2026', 'broward')
        self.assertEqual(result, dict(auction_date='09/16/2026', case_number='2026-12345-TD', address='123 EXAMPLE ST', city='FORT LAUDERDALE', state='FL', zip='33301', county='broward'))
        self.assertEqual(app.normalize_import_case_number(' 2026-12345-td ', 'broward'), result['case_number'])

    def test_incomplete_address_does_not_invent_data(self):
        with self.assertRaises(ValueError):
            app.parse_auction_entry('Case #: 12345\nProperty Address: UNKNOWN', '09/16/2026', 'broward')

    def test_existing_county_case_formats_unchanged(self):
        for county, raw, expected in [('palm_beach', '2025-2212TD', '2212TD'), ('duval', '2024-1429TD', '1429TD'), ('miami_dade', '2025A00443', 'A00443'), ('hillsborough', '2026-188', '2026-188')]:
            self.assertEqual(app.normalize_case_number(raw, county), expected)

    def test_access_denied_is_error_not_empty_success(self):
        with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
            app.validate_broward_page(Mock(), Mock(status=403), 'broward')

    def test_unrecognized_page_is_error(self):
        page = Mock()
        page.query_selector.return_value = None
        page.inner_text.return_value = 'Please sign in'
        with self.assertRaises(RuntimeError):
            app.validate_broward_page(page, Mock(status=200), 'broward')

    def test_calendar_is_accepted(self):
        page = Mock()
        page.query_selector.return_value = None
        page.inner_text.return_value = 'Auction Calendar'
        app.validate_broward_page(page, Mock(status=200), 'broward')

    def test_live_broward_no_sale_preview_is_accepted(self):
        # Text observed on the September 10, 2026 preview page; Current and
        # Next Auction link to October 26 rather than providing Area_W today.
        page = Mock()
        page.query_selector.return_value = None
        page.inner_text.return_value = 'Preview Items For Sale\nThursday September 10, 2026\nNext Auction > >\nCurrent'
        app.validate_broward_page(page, Mock(status=200), 'broward')

    def test_broward_no_inherited_weekday_filter(self):
        for day in range(14, 21):
            self.assertTrue(app.is_supported_auction_date('broward', datetime(2026, 9, day)))

    def test_api_accepts_broward(self):
        with patch.object(app, 'scrape_auction', return_value=([], None)) as scrape:
            response = app.app.test_client().post('/scrape', json={'county': 'broward', 'url': app.COUNTY_BASE_URLS['broward'] + '09/16/2026'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['county_display'], 'Broward')
            scrape.assert_called_once()

    def test_relative_navigation(self):
        page = Mock()
        page.query_selector.return_value.get_attribute.return_value = '/index.cfm?AUCTIONDATE=09/16/2026'
        self.assertEqual(app.get_next_auction_url(page, app.COUNTY_BASE_URLS['broward']), 'https://broward.realtaxdeed.com/index.cfm?AUCTIONDATE=09/16/2026')


if __name__ == '__main__':
    unittest.main()
