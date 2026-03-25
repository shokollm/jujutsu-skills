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


class TestFormatMatchEvent(unittest.TestCase):
    """Tests for format_match_event() canonical dict."""

    def _frozen_dt(self, year, month, day, hour, minute):
        return datetime(year, month, day, hour, minute,
                        tzinfo=timezone.utc)

    def _mock_datetime(self, frozen):
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

    def _make_event(self, title, ml_market=None, start_time="2026-03-25T18:00:00Z"):
        import json as _json
        e = {
            "title": title,
            "slug": "test-slug",
            "startTime": start_time,
            "markets": [],
        }
        if ml_market:
            e["markets"].append(ml_market)
        return e

    def _make_ml_market(self, outcomes, prices, vol=50000):
        import json
        return {
            "sportsMarketType": "moneyline",
            "outcomes": json.dumps(outcomes),
            "outcomePrices": json.dumps(prices),
            "bestBid": str(float(prices[0]) - 0.01) if prices else "0.49",
            "bestAsk": str(float(prices[0]) + 0.01) if prices else "0.51",
            "volume": str(vol),
            "acceptingOrders": True,
            "closed": False,
        }

    def test_fields_present(self):
        """All canonical fields are present and non-null."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import format_match_event
            e = self._make_event(
                "Counter Strike: Team A vs Team B - ESL Pro League",
                self._make_ml_market(['"Team A"', '"Team B"'], [0.55, 0.45]),
            )
            fd = format_match_event(e)
            self.assertIn("title", fd)
            self.assertIn("title_clean", fd)
            self.assertIn("tournament", fd)
            self.assertIn("url", fd)
            self.assertIn("time_status", fd)
            self.assertIn("time_urgency", fd)
            self.assertIn("abs_time", fd)
            self.assertIn("team_a", fd)
            self.assertIn("team_b", fd)
            self.assertIn("odds_a", fd)
            self.assertIn("odds_b", fd)
            self.assertIn("vol", fd)

    def test_title_clean_no_tournament(self):
        """title_clean strips tournament suffix after ' - '."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import format_match_event
            e = self._make_event(
                "Counter Strike: Team A vs Team B - ESL Pro League",
                self._make_ml_market(['"Team A"', '"Team B"'], [0.55, 0.45]),
            )
            fd = format_match_event(e)
            self.assertEqual(fd["title_clean"], "Counter Strike: Team A vs Team B")
            self.assertEqual(fd["tournament"], "ESL Pro League")

    def test_title_clean_no_dash(self):
        """title_clean is unchanged when no ' - ' separator."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import format_match_event
            e = self._make_event(
                "Counter Strike: Team A vs Team B",
                self._make_ml_market(['"Team A"', '"Team B"'], [0.55, 0.45]),
            )
            fd = format_match_event(e)
            self.assertEqual(fd["title_clean"], "Counter Strike: Team A vs Team B")
            self.assertEqual(fd["tournament"], "")

    def test_missing_ml(self):
        """Returns valid dict with '?' fallbacks when no ML market."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import format_match_event
            e = self._make_event("Team A vs Team B")
            fd = format_match_event(e)
            self.assertEqual(fd["team_a"], "?")
            self.assertEqual(fd["team_b"], "?")
            self.assertEqual(fd["odds_a"], "?")
            self.assertEqual(fd["odds_b"], "?")
            self.assertEqual(fd["vol"], 0)

    def test_missing_outcomes(self):
        """Handles empty outcomes list gracefully."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import format_match_event
            e = self._make_event(
                "Team A vs Team B",
                self._make_ml_market([], []),
            )
            fd = format_match_event(e)
            self.assertEqual(fd["team_a"], "?")
            self.assertEqual(fd["team_b"], "?")

    def test_time_data_passed_through(self):
        """Time fields come from _get_time_data."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import format_match_event
            e = self._make_event(
                "Team A vs Team B",
                self._make_ml_market(['"Team A"', '"Team B"'], [0.55, 0.45]),
                start_time="2026-03-25T18:00:00Z",  # 6h in future
            )
            fd = format_match_event(e)
            self.assertEqual(fd["time_status"], "In 6h")
            self.assertEqual(fd["time_urgency"], 2)
            self.assertIn("WIB", fd["abs_time"])


class TestFormatNonMatchEvent(unittest.TestCase):
    """Tests for format_non_match_event() canonical dict."""

    def _frozen_dt(self, year, month, day, hour, minute):
        return datetime(year, month, day, hour, minute,
                        tzinfo=timezone.utc)

    def _mock_datetime(self, frozen):
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

    def test_fields_present(self):
        """All canonical fields are present."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import format_non_match_event
            e = {
                "title": "Will it rain in Jakarta?",
                "slug": "rain-jakarta",
                "startTime": "2026-03-25T18:00:00Z",
                "markets": [
                    {"volume": "10000"},
                    {"volume": "5000"},
                ],
            }
            fd = format_non_match_event(e)
            self.assertIn("title", fd)
            self.assertIn("url", fd)
            self.assertIn("time_status", fd)
            self.assertIn("time_urgency", fd)
            self.assertIn("abs_time", fd)
            self.assertIn("market_count", fd)
            self.assertIn("total_vol", fd)

    def test_market_stats(self):
        """market_count and total_vol computed correctly."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import format_non_match_event
            e = {
                "title": "Test",
                "slug": "test",
                "startTime": "2026-03-25T18:00:00Z",
                "markets": [
                    {"volume": "10000"},
                    {"volume": "5000"},
                ],
            }
            fd = format_non_match_event(e)
            self.assertEqual(fd["market_count"], 2)
            self.assertEqual(fd["total_vol"], 15000)

    def test_time_passed_through(self):
        """Time fields come from _get_time_data."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import format_non_match_event
            e = {
                "title": "Test",
                "slug": "test",
                "startTime": "2026-03-25T18:00:00Z",
                "markets": [],
            }
            fd = format_non_match_event(e)
            self.assertEqual(fd["time_status"], "In 6h")


class TestRenderMatchLines(unittest.TestCase):
    """Tests for render_match_lines() text and HTML output."""

    def test_text_mode_exact_lines(self):
        """text mode produces expected plain text lines."""
        from browse import render_match_lines
        fd = {
            "title_clean": "Team A vs Team B",
            "url": "https://polymarket.com/market/test",
            "abs_time": "Mar 25, 19:00 WIB",
            "time_status": "In 6h",
            "vol": 50000,
            "tournament": "ESL Pro League",
            "team_a": "Team A",
            "team_b": "Team B",
            "odds_a": "55c",
            "odds_b": "45c",
        }
        lines = render_match_lines(fd, 1, mode="text")
        self.assertEqual(lines[0], "1. [Team A vs Team B](https://polymarket.com/market/test)")
        self.assertEqual(lines[1], "   Mar 25, 19:00 WIB | In 6h")
        self.assertEqual(lines[2], "  Vol: $50,000")
        self.assertEqual(lines[3], "  Tournament: ESL Pro League")
        self.assertEqual(lines[4], "  Odds: Team A 55c | 45c Team B")

    def test_text_mode_no_tournament(self):
        """text mode omits Tournament line when tournament is empty."""
        from browse import render_match_lines
        fd = {
            "title_clean": "Team A vs Team B",
            "url": "https://polymarket.com/market/test",
            "abs_time": "Mar 25, 19:00 WIB",
            "time_status": "In 6h",
            "vol": 50000,
            "tournament": "",
            "team_a": "Team A",
            "team_b": "Team B",
            "odds_a": "55c",
            "odds_b": "45c",
        }
        lines = render_match_lines(fd, 2, mode="text")
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[0], "2. [Team A vs Team B](https://polymarket.com/market/test)")
        self.assertNotIn("Tournament", lines[3])

    def test_html_mode_exact(self):
        """html mode produces expected HTML lines with escape_html."""
        from browse import render_match_lines
        fd = {
            "title_clean": "Team A & Team B vs Team C",
            "url": "https://polymarket.com/market/test",
            "abs_time": "Mar 25, 19:00 WIB",
            "time_status": "LIVE",
            "vol": 50000,
            "tournament": "ESL Pro League",
            "team_a": "Team A & Team B",
            "team_b": "Team C",
            "odds_a": "55c",
            "odds_b": "45c",
        }
        lines = render_match_lines(fd, 1, mode="html")
        self.assertEqual(lines[0], "<b>1.</b> <a href=\"https://polymarket.com/market/test\">Team A &amp; Team B vs Team C</a>")
        self.assertEqual(lines[1], "   Mar 25, 19:00 WIB | LIVE")
        self.assertEqual(lines[2], "  Vol: $50,000")
        self.assertEqual(lines[3], "  Tournament: ESL Pro League")
        self.assertEqual(lines[4], "  Odds: Team A & Team B 55c | 45c Team C")

    def test_html_mode_xss_prevention(self):
        """html mode escapes < and > to prevent XSS."""
        from browse import render_match_lines
        fd = {
            "title_clean": "<script>alert('xss')</script>",
            "url": "https://polymarket.com/market/test",
            "abs_time": "Mar 25, 19:00 WIB",
            "time_status": "LIVE",
            "vol": 1000,
            "tournament": "",
            "team_a": "Team A",
            "team_b": "Team B",
            "odds_a": "50c",
            "odds_b": "50c",
        }
        lines = render_match_lines(fd, 1, mode="html")
        self.assertIn("&lt;script&gt;", lines[0])
        self.assertNotIn("<script>", lines[0])


class TestRenderNonMatchLines(unittest.TestCase):
    """Tests for render_non_match_lines() text and HTML output."""

    def test_text_mode_exact_lines(self):
        """text mode produces expected plain text lines."""
        from browse import render_non_match_lines
        fd = {
            "title": "Will it rain in Jakarta?",
            "url": "https://polymarket.com/event/rain-jakarta",
            "abs_time": "Mar 25, 19:00 WIB",
            "time_status": "In 6h",
            "market_count": 3,
            "total_vol": 25000,
        }
        lines = render_non_match_lines(fd, 1, mode="text")
        self.assertEqual(lines[0], "1. [Will it rain in Jakarta?](https://polymarket.com/event/rain-jakarta)")
        self.assertEqual(lines[1], "   Mar 25, 19:00 WIB | In 6h")
        self.assertEqual(lines[2], "   Markets: 3 | Total Vol: $25,000")

    def test_html_mode_exact(self):
        """html mode produces expected HTML lines with escape_html."""
        from browse import render_non_match_lines
        fd = {
            "title": "Rain <or> Sun?",
            "url": "https://polymarket.com/event/rain-sun",
            "abs_time": "Mar 25, 19:00 WIB",
            "time_status": "In 6h",
            "market_count": 2,
            "total_vol": 10000,
        }
        lines = render_non_match_lines(fd, 1, mode="html")
        self.assertEqual(lines[0], "<b>1.</b> <a href=\"https://polymarket.com/event/rain-sun\">Rain &lt;or&gt; Sun?</a>")
        self.assertEqual(lines[1], "   Mar 25, 19:00 WIB | In 6h")
        self.assertEqual(lines[2], "   Markets: 2 | Total Vol: $10,000")


class TestPrintBrowseIntegration(unittest.TestCase):
    """Integration tests for print_browse using the new pipeline."""

    def _frozen_dt(self, year, month, day, hour, minute):
        return datetime(year, month, day, hour, minute,
                        tzinfo=timezone.utc)

    def _mock_datetime(self, frozen):
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

    @patch('builtins.print')
    def test_print_browse_uses_new_pipeline(self, mock_print):
        """print_browse calls format_match_event and render_match_lines."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import print_browse
            match_events = [{
                "title": "Counter Strike: Team A vs Team B - ESL Pro League",
                "slug": "csa",
                "startTime": "2026-03-25T18:00:00Z",
                "markets": [{
                    "sportsMarketType": "moneyline",
                    "outcomes": '["Team A", "Team B"]',
                    "outcomePrices": "[0.55, 0.45]",
                    "bestBid": "0.54",
                    "bestAsk": "0.56",
                    "volume": "50000",
                    "acceptingOrders": True,
                    "closed": False,
                }],
            }]
            with patch('browse.format_match_event') as mock_fmt, \
                 patch('browse.render_match_lines') as mock_render:
                mock_fmt.return_value = {
                    "title_clean": "Team A vs Team B",
                    "url": "https://polymarket.com/market/csa",
                    "abs_time": "Mar 25, 19:00 WIB",
                    "time_status": "In 6h",
                    "vol": 50000,
                    "tournament": "ESL Pro League",
                    "team_a": "Team A",
                    "team_b": "Team B",
                    "odds_a": "55c",
                    "odds_b": "45c",
                }
                mock_render.return_value = [
                    "1. [Team A vs Team B](https://polymarket.com/market/csa)",
                    "   Mar 25, 19:00 WIB | In 6h",
                    "  Vol: $50,000",
                    "  Tournament: ESL Pro League",
                    "  Odds: Team A 55c | 45c Team B",
                ]
                print_browse(match_events, [], "Counter Strike", 1, 1, 1, 0,
                             non_matches_max=5)

                mock_fmt.assert_called_once_with(match_events[0])
                mock_render.assert_called_once_with(mock_fmt.return_value, 1, mode="text")

    @patch('builtins.print')
    def test_print_browse_matches_only(self, mock_print):
        """matches_only suppresses non-match section."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch('browse.datetime', self._mock_datetime(frozen)):
            from browse import print_browse
            with patch('browse.format_non_match_event') as mock_non_fmt:
                print_browse([], [], "Counter Strike", 0, 0, 0, 0,
                             non_matches_max=5, matches_only=True)
                mock_non_fmt.assert_not_called()


class TestSendChunked(unittest.TestCase):
    """Tests for send_chunked() helper."""

    def test_small_message_sent_directly(self):
        """Messages under 4096 chars go through without chunking."""
        sent_texts = []
        def fake_send(text):
            sent_texts.append(text)

        lines = ["<b>COUNTER STRIKE</b> | Mar 25, 2026", "", "MATCH MARKETS", "", "1. test"]
        # This fits in one message
        from browse import send_chunked
        send_chunked(lines, fake_send, "Counter Strike", "Mar 25, 2026",
                      show_matches=True, show_non_matches=False)
        self.assertEqual(len(sent_texts), 1)

    def test_chunked_message_gets_cont_header(self):
        """Messages over 4096 chars get continuation header."""
        sent_texts = []
        def fake_send(text):
            sent_texts.append(text)

        # Build enough content to exceed 4096 chars
        # Each event line: ~260 chars. Need ~16 events + headers (~4200 chars)
        lines = ["<b>COUNTER STRIKE</b> | Mar 25, 2026", ""]
        for i in range(16):
            lines += [f"<b>{i+1}.</b> <a href=\"https://polymarket.com/market/{i}\">Team {'X' * 250}</a>", "   Mar 25, 19:00 WIB | In 6h", "  Vol: $50,000", "  Odds: TeamA 55c | 45c TeamB", ""]
        lines.append("")

        from browse import send_chunked
        send_chunked(lines, fake_send, "Counter Strike", "Mar 25, 2026",
                      show_matches=True, show_non_matches=False)

        # Should have sent more than one message (chunked)
        self.assertGreater(len(sent_texts), 1)
        # At least one continuation message
        cont_found = any("(cont.)" in t for t in sent_texts)
        self.assertTrue(cont_found, f"Expected at least one '(cont.)' message. Got {len(sent_texts)} messages.")


if __name__ == "__main__":
    unittest.main()
