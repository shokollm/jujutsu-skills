"""
Unit tests for browse.py Telegram functions.

Run with: python -m pytest tests/test_browse.py -v
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from browse import send_telegram_message


class TestSendTelegramMessage(unittest.TestCase):
    """Tests for the module-level send_telegram_message function."""

    @patch('browse.urlopen')
    def test_send_success(self, mock_urlopen):
        """send_telegram_message returns message_id on success."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 123}}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        result = send_telegram_message("test_token", "test_chat", "hello world")

        self.assertEqual(result, 123)
        mock_urlopen.assert_called_once()
        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        self.assertEqual(req.full_url, "https://api.telegram.org/bottest_token/sendMessage")
        self.assertEqual(req.method, "POST")

    @patch('browse.urlopen')
    def test_send_api_error_raises_runtime_error(self, mock_urlopen):
        """send_telegram_message raises RuntimeError when Telegram API returns ok=false."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": false, "description": "Forbidden"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            send_telegram_message("test_token", "test_chat", "hello")
        self.assertIn("Telegram API error: Forbidden", str(ctx.exception))

    @patch('browse.urlopen')
    def test_send_invalid_token_raises_http_error(self, mock_urlopen):
        """send_telegram_message raises HTTPError on invalid token (404)."""
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            url="https://api.telegram.org/botINVALID/sendMessage",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None
        )

        with self.assertRaises(HTTPError) as ctx:
            send_telegram_message("INVALID", "test_chat", "hello")
        self.assertEqual(ctx.exception.code, 404)

    @patch('browse.urlopen')
    def test_send_rate_limit_raises_http_error(self, mock_urlopen):
        """send_telegram_message raises HTTPError on rate limit (429)."""
        from urllib.error import HTTPError
        mock_urlopen.side_effect = HTTPError(
            url="https://api.telegram.org/bottest_token/sendMessage",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=None
        )

        with self.assertRaises(HTTPError) as ctx:
            send_telegram_message("test_token", "test_chat", "hello")
        self.assertEqual(ctx.exception.code, 429)

    @patch('browse.urlopen')
    def test_send_network_error_raises_url_error(self, mock_urlopen):
        """send_telegram_message raises URLError on network failure."""
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("Connection refused")

        with self.assertRaises(URLError) as ctx:
            send_telegram_message("test_token", "test_chat", "hello")
        self.assertIn("Connection refused", str(ctx.exception))

    @patch('browse.urlopen')
    def test_send_timeout_raises_url_error(self, mock_urlopen):
        """send_telegram_message raises URLError on timeout."""
        from urllib.error import URLError
        mock_urlopen.side_effect = URLError("<urlopen error TimeoutError: timed out>")

        with self.assertRaises(URLError):
            send_telegram_message("test_token", "test_chat", "hello")

    @patch('browse.urlopen')
    def test_send_custom_timeout_used(self, mock_urlopen):
        """send_telegram_message respects custom timeout parameter."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 456}}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        send_telegram_message("test_token", "test_chat", "hello", timeout=30)

        call_kwargs = mock_urlopen.call_args[1]
        self.assertEqual(call_kwargs['timeout'], 30)

    @patch('browse.urlopen')
    def test_send_html_parsing_mode(self, mock_urlopen):
        """send_telegram_message sends with parse_mode=HTML."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 789}}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        send_telegram_message("test_token", "test_chat", "<b>bold</b>")

        call_args = mock_urlopen.call_args
        req = call_args[0][0]
        # Verify parse_mode=HTML is in the data
        self.assertIn(b"parse_mode=HTML", req.data)


class TestHtmlInjection(unittest.TestCase):
    """Tests for HTML injection prevention in Telegram messages."""

    @patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': 'test_token', 'CHAT_ID': 'test_chat'})
    @patch('browse.send_telegram_message')
    def test_send_to_telegram_html_injection_in_match_title(self, mock_send_msg):
        """
        titles in match events are NOT escaped before inserting into HTML.
        This test FAILS if HTML chars are unescaped (vulnerable),
        and PASSES once escape_html() is implemented.
        """
        mock_send_msg.return_value = 123

        # Simulate a Polymarket event with HTML injection in the title
        malicious_event = {
            "title": "<script>alert('XSS')</script> - Team A vs Team B",
            "slug": "test-event",
            "startTime": "2027-03-26T12:00:00Z",
            "markets": [{
                "sportsMarketType": "moneyline",
                "outcomes": '["Team A", "Team B"]',
                "outcomePrices": "[0.55, 0.45]",
                "bestBid": "0.54",
                "bestAsk": "0.56",
                "volume": 50000,
                "acceptingOrders": True,
                "closed": False,
            }],
        }

        from browse import send_to_telegram
        send_to_telegram([malicious_event], [], "Counter Strike")

        # Check what was passed to send_telegram_message
        self.assertEqual(mock_send_msg.called, True)
        sent_text = mock_send_msg.call_args[0][2]  # text arg (3rd positional)

        # AFTER FIX: <script> should be escaped as &lt;script&gt;
        # BEFORE FIX: raw <script> appears in text (vulnerable — test would fail here)
        self.assertIn("&lt;script&gt;", sent_text,
            "HTML injection still present — title may NOT be escaped")
        self.assertIn("&lt;/script&gt;", sent_text)

    @patch.dict('os.environ', {'TELEGRAM_BOT_TOKEN': 'test_token', 'CHAT_ID': 'test_chat'})
    @patch('browse.send_telegram_message')
    def test_send_to_telegram_ampersand_in_title(self, mock_send_msg):
        """
        Ampersands in titles should be escaped as &amp; when using HTML parse_mode.
        BEFORE fix: "&" appears raw in the HTML (vulnerable).
        AFTER fix: "&" appears as "&amp;".
        """
        mock_send_msg.return_value = 123

        event_with_ampersand = {
            "title": "Team A & Team B vs Team C",
            "slug": "amp-test",
            "startTime": "2027-03-26T12:00:00Z",
            "markets": [{
                "sportsMarketType": "moneyline",
                "outcomes": '["Team A & Team B", "Team C"]',
                "outcomePrices": "[0.50, 0.50]",
                "bestBid": "0.49",
                "bestAsk": "0.51",
                "volume": 10000,
                "acceptingOrders": True,
                "closed": False,
            }],
        }

        from browse import send_to_telegram
        send_to_telegram([event_with_ampersand], [], "Dota 2")

        sent_text = mock_send_msg.call_args[0][2]

        # AFTER FIX: & should be escaped as &amp;
        # BEFORE FIX: raw & appears (vulnerable — test would fail here)
        self.assertIn("&amp;", sent_text,
            "Ampersand not escaped — title may NOT be escaped")


if __name__ == "__main__":
    unittest.main()
