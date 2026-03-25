"""
Unit tests for browse.py Telegram functions.

Run with: python -m pytest tests/test_browse.py -v
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os
from datetime import datetime, timezone, timedelta

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


class TestTimeFunctions(unittest.TestCase):
    """Tests for _get_time_data() unified helper.

    These tests verify the helper returns correct time_status, time_urgency,
    and abs_time for various event scenarios. Callers extract the fields they
    need from the returned dict.
    """

    def _make_event(self, start_time):
        """Helper to create a minimal event with a startTime."""
        return {"startTime": start_time}

    def _frozen_dt(self, year, month, day, hour, minute, second=0):
        return datetime(year, month, day, hour, minute, second,
                        tzinfo=timezone.utc)

    def _mock_datetime(self, frozen):
        """Return a mock datetime class that freezes now() to the given datetime."""
        class MockDatetime:
            @staticmethod
            def now(tz=None):
                if tz is None:
                    return frozen
                return frozen.astimezone(tz)
            fromisoformat = staticmethod(datetime.fromisoformat)
            def __call__(self, *a, **k):
                return datetime(*a, **k)
        return MockDatetime

    # === _get_time_data core tests ===

    def test_get_time_data_tbd(self):
        """No startTime -> TBD/0urgency/abs TBD."""
        from browse import _get_time_data
        td = _get_time_data({})
        self.assertEqual(td["time_status"], "TBD")
        self.assertEqual(td["time_urgency"], 0)
        self.assertEqual(td["abs_time"], "TBD")

    def test_get_time_data_in_30m(self):
        """Starts in 30 minutes -> 'In 30m', urgency 3."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import _get_time_data
            td = _get_time_data(self._make_event("2026-03-25T12:30:00Z"))
            self.assertEqual(td["time_status"], "In 30m")
            self.assertEqual(td["time_urgency"], 3)
            self.assertIn("WIB", td["abs_time"])

    def test_get_time_data_in_6h(self):
        """Starts in 6 hours -> 'In 6h', urgency 2."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import _get_time_data
            td = _get_time_data(self._make_event("2026-03-25T18:00:00Z"))
            self.assertEqual(td["time_status"], "In 6h")
            self.assertEqual(td["time_urgency"], 2)
            self.assertIn("WIB", td["abs_time"])

    def test_get_time_data_in_2d(self):
        """Starts in 2 days -> 'In 2d', urgency 1."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import _get_time_data
            td = _get_time_data(self._make_event("2026-03-27T12:00:00Z"))
            self.assertEqual(td["time_status"], "In 2d")
            self.assertEqual(td["time_urgency"], 1)

    def test_get_time_data_live(self):
        """Started 30 minutes ago -> 'LIVE', urgency 3."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 30, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import _get_time_data
            td = _get_time_data(self._make_event("2026-03-25T12:00:00Z"))
            self.assertEqual(td["time_status"], "LIVE")
            self.assertEqual(td["time_urgency"], 3)
            self.assertIn("WIB", td["abs_time"])

    def test_get_time_data_started_2h_ago(self):
        """Started 2 hours ago -> 'LIVE 2h', urgency 3."""
        frozen = self._frozen_dt(2026, 3, 25, 14, 0, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import _get_time_data
            td = _get_time_data(self._make_event("2026-03-25T12:00:00Z"))
            self.assertEqual(td["time_status"], "LIVE 2h")
            self.assertEqual(td["time_urgency"], 3)

    def test_get_time_data_started_12h_ago(self):
        """Started 12 hours ago -> '12h ago', urgency 1."""
        frozen = self._frozen_dt(2026, 3, 26, 0, 0, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import _get_time_data
            td = _get_time_data(self._make_event("2026-03-25T12:00:00Z"))
            self.assertEqual(td["time_status"], "12h ago")
            self.assertEqual(td["time_urgency"], 1)

    def test_get_time_data_started_2d_ago(self):
        """Started 2 days ago -> '2d ago', urgency 0."""
        frozen = self._frozen_dt(2026, 3, 27, 12, 0, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import _get_time_data
            td = _get_time_data(self._make_event("2026-03-25T12:00:00Z"))
            self.assertEqual(td["time_status"], "2d ago")
            self.assertEqual(td["time_urgency"], 0)

    def test_get_time_data_abs_time_format(self):
        """abs_time is formatted correctly in WIB."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import _get_time_data
            # 19:00 UTC = 02:00 WIB next day
            td = _get_time_data(self._make_event("2026-03-26T02:00:00Z"))
            self.assertIn("WIB", td["abs_time"])
            # UTC 12:00 -> WIB 19:00 same day
            td2 = _get_time_data(self._make_event("2026-03-25T12:00:00Z"))
            self.assertEqual(td2["abs_time"], "Mar 25, 19:00 WIB")


if __name__ == "__main__":
    unittest.main()
