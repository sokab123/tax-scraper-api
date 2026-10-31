"""Bounded retries for transient clerk-source failures; no live traffic."""
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import Request
import app


class PalmBeachRetries(unittest.TestCase):
    def response(self, body=b'valid source'):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = body
        return response

    def test_clerk_500_then_success(self):
        opener = Mock()
        opener.open.side_effect = [HTTPError(app.PALM_BEACH_CLERK_HOME_URL, 500, 'temporary failure', {}, None), self.response()]
        with patch.object(app.time, 'sleep') as sleep:
            self.assertEqual(app.http_get(opener, app.PALM_BEACH_CLERK_HOME_URL), 'valid source')
        self.assertEqual(opener.open.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_persistent_503_is_error_not_empty_success(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(app.PALM_BEACH_CLERK_HOME_URL, 503, 'unavailable', {}, None)
        with patch.object(app.time, 'sleep') as sleep, self.assertRaises(HTTPError):
            app.http_get(opener, app.PALM_BEACH_CLERK_HOME_URL)
        self.assertEqual(opener.open.call_count, 3)
        self.assertEqual([x.args[0] for x in sleep.call_args_list], [1, 2])

    def test_403_is_not_retried(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(app.PALM_BEACH_CLERK_HOME_URL, 403, 'forbidden', {}, None)
        with patch.object(app.time, 'sleep') as sleep, self.assertRaises(HTTPError):
            app.http_get(opener, app.PALM_BEACH_CLERK_HOME_URL)
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()

    def test_search_form_can_retry_network_error(self):
        opener = Mock()
        opener.open.side_effect = [URLError('connection reset'), self.response()]
        with patch.object(app.time, 'sleep'):
            self.assertEqual(app.http_post(opener, app.PALM_BEACH_CLERK_HOME_URL, {'SearchSaleDateFrom': 'date'}), 'valid source')
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(opener.open.call_args.args[0].get_method(), 'POST')

    def test_non_clerk_post_never_retried(self):
        opener = Mock()
        opener.open.side_effect = URLError('uncertain outcome')
        with patch.object(app.time, 'sleep') as sleep, self.assertRaises(URLError):
            app.http_post(opener, 'https://example.com/import', {'record': 1})
        self.assertEqual(opener.open.call_count, 1)
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
