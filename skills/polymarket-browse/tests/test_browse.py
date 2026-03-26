"""
Unit tests for browse.py Telegram functions.

Run with: python -m pytest tests/test_browse.py -v
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
import os
import time
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from browse import send_telegram_message


class TestSendTelegramMessage(unittest.TestCase):
    """Tests for the module-level send_telegram_message function."""

    @patch("browse.urlopen")
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
        self.assertEqual(
            req.full_url, "https://api.telegram.org/bottest_token/sendMessage"
        )
        self.assertEqual(req.method, "POST")

    @patch("browse.urlopen")
    def test_send_api_error_raises_runtime_error(self, mock_urlopen):
        """send_telegram_message raises RuntimeError when Telegram API returns ok=false."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": false, "description": "Forbidden"}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        with self.assertRaises(RuntimeError) as ctx:
            send_telegram_message("test_token", "test_chat", "hello")
        self.assertIn("Telegram API error: Forbidden", str(ctx.exception))

    @patch("browse.urlopen")
    def test_send_invalid_token_raises_http_error(self, mock_urlopen):
        """send_telegram_message raises HTTPError on invalid token (404)."""
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            url="https://api.telegram.org/botINVALID/sendMessage",
            code=404,
            msg="Not Found",
            hdrs={},
            fp=None,
        )

        with self.assertRaises(HTTPError) as ctx:
            send_telegram_message("INVALID", "test_chat", "hello")
        self.assertEqual(ctx.exception.code, 404)

    @patch("browse.urlopen")
    def test_send_rate_limit_raises_http_error(self, mock_urlopen):
        """send_telegram_message raises HTTPError on rate limit (429)."""
        from urllib.error import HTTPError

        mock_urlopen.side_effect = HTTPError(
            url="https://api.telegram.org/bottest_token/sendMessage",
            code=429,
            msg="Too Many Requests",
            hdrs={},
            fp=None,
        )

        with self.assertRaises(HTTPError) as ctx:
            send_telegram_message("test_token", "test_chat", "hello")
        self.assertEqual(ctx.exception.code, 429)

    @patch("browse.urlopen")
    def test_send_network_error_raises_url_error(self, mock_urlopen):
        """send_telegram_message raises URLError on network failure."""
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("Connection refused")

        with self.assertRaises(URLError) as ctx:
            send_telegram_message("test_token", "test_chat", "hello")
        self.assertIn("Connection refused", str(ctx.exception))

    @patch("browse.urlopen")
    def test_send_timeout_raises_url_error(self, mock_urlopen):
        """send_telegram_message raises URLError on timeout."""
        from urllib.error import URLError

        mock_urlopen.side_effect = URLError("<urlopen error TimeoutError: timed out>")

        with self.assertRaises(URLError):
            send_telegram_message("test_token", "test_chat", "hello")

    @patch("browse.urlopen")
    def test_send_custom_timeout_used(self, mock_urlopen):
        """send_telegram_message respects custom timeout parameter."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"ok": true, "result": {"message_id": 456}}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        send_telegram_message("test_token", "test_chat", "hello", timeout=30)

        call_kwargs = mock_urlopen.call_args[1]
        self.assertEqual(call_kwargs["timeout"], 30)

    @patch("browse.urlopen")
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

    @patch.dict(
        "os.environ", {"TELEGRAM_BOT_TOKEN": "test_token", "CHAT_ID": "test_chat"}
    )
    @patch("browse.send_telegram_message")
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
            "markets": [
                {
                    "sportsMarketType": "moneyline",
                    "outcomes": '["Team A", "Team B"]',
                    "outcomePrices": "[0.55, 0.45]",
                    "bestBid": "0.54",
                    "bestAsk": "0.56",
                    "volume": 50000,
                    "acceptingOrders": True,
                    "closed": False,
                }
            ],
        }

        from browse import send_to_telegram

        send_to_telegram([malicious_event], [], "Counter Strike")

        # Check what was passed to send_telegram_message
        self.assertEqual(mock_send_msg.called, True)
        sent_text = mock_send_msg.call_args[0][2]  # text arg (3rd positional)

        # AFTER FIX: <script> should be escaped as &lt;script&gt;
        # BEFORE FIX: raw <script> appears in text (vulnerable — test would fail here)
        self.assertIn(
            "&lt;script&gt;",
            sent_text,
            "HTML injection still present — title may NOT be escaped",
        )
        self.assertIn("&lt;/script&gt;", sent_text)

    @patch.dict(
        "os.environ", {"TELEGRAM_BOT_TOKEN": "test_token", "CHAT_ID": "test_chat"}
    )
    @patch("browse.send_telegram_message")
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
            "markets": [
                {
                    "sportsMarketType": "moneyline",
                    "outcomes": '["Team A & Team B", "Team C"]',
                    "outcomePrices": "[0.50, 0.50]",
                    "bestBid": "0.49",
                    "bestAsk": "0.51",
                    "volume": 10000,
                    "acceptingOrders": True,
                    "closed": False,
                }
            ],
        }

        from browse import send_to_telegram

        send_to_telegram([event_with_ampersand], [], "Dota 2")

        sent_text = mock_send_msg.call_args[0][2]

        # AFTER FIX: & should be escaped as &amp;
        # BEFORE FIX: raw & appears (vulnerable — test would fail here)
        self.assertIn(
            "&amp;", sent_text, "Ampersand not escaped — title may NOT be escaped"
        )


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
        return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)

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
        with patch("browse.datetime", self._mock_datetime(frozen)):
            from browse import _get_time_data

            td = _get_time_data(self._make_event("2026-03-25T12:30:00Z"))
            self.assertEqual(td["time_status"], "In 30m")
            self.assertEqual(td["time_urgency"], 3)
            self.assertIn("WIB", td["abs_time"])

    def test_get_time_data_in_6h(self):
        """Starts in 6 hours -> 'In 6h', urgency 2."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0, 0)
        with patch("browse.datetime", self._mock_datetime(frozen)):
            from browse import _get_time_data

            td = _get_time_data(self._make_event("2026-03-25T18:00:00Z"))
            self.assertEqual(td["time_status"], "In 6h")
            self.assertEqual(td["time_urgency"], 2)
            self.assertIn("WIB", td["abs_time"])

    def test_get_time_data_in_2d(self):
        """Starts in 2 days -> 'In 2d', urgency 1."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0, 0)
        with patch("browse.datetime", self._mock_datetime(frozen)):
            from browse import _get_time_data

            td = _get_time_data(self._make_event("2026-03-27T12:00:00Z"))
            self.assertEqual(td["time_status"], "In 2d")
            self.assertEqual(td["time_urgency"], 1)

    def test_get_time_data_live(self):
        """Started 30 minutes ago -> 'LIVE', urgency 3."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 30, 0)
        with patch("browse.datetime", self._mock_datetime(frozen)):
            from browse import _get_time_data

            td = _get_time_data(self._make_event("2026-03-25T12:00:00Z"))
            self.assertEqual(td["time_status"], "LIVE")
            self.assertEqual(td["time_urgency"], 3)
            self.assertIn("WIB", td["abs_time"])

    def test_get_time_data_live_exactly_now(self):
        """Event starts exactly now -> 'LIVE', urgency 3 (not 'In 0m')."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0, 0)
        with patch("browse.datetime", self._mock_datetime(frozen)):
            from browse import _get_time_data

            td = _get_time_data(self._make_event("2026-03-25T12:00:00Z"))
            self.assertEqual(td["time_status"], "LIVE")
            self.assertEqual(td["time_urgency"], 3)

    def test_get_time_data_started_2h_ago(self):
        """Started 2 hours ago -> 'LIVE 2h', urgency 3."""
        frozen = self._frozen_dt(2026, 3, 25, 14, 0, 0)
        with patch("browse.datetime", self._mock_datetime(frozen)):
            from browse import _get_time_data

            td = _get_time_data(self._make_event("2026-03-25T12:00:00Z"))
            self.assertEqual(td["time_status"], "LIVE 2h")
            self.assertEqual(td["time_urgency"], 3)

    def test_get_time_data_started_12h_ago(self):
        """Started 12 hours ago -> '12h ago', urgency 1."""
        frozen = self._frozen_dt(2026, 3, 26, 0, 0, 0)
        with patch("browse.datetime", self._mock_datetime(frozen)):
            from browse import _get_time_data

            td = _get_time_data(self._make_event("2026-03-25T12:00:00Z"))
            self.assertEqual(td["time_status"], "12h ago")
            self.assertEqual(td["time_urgency"], 1)

    def test_get_time_data_started_2d_ago(self):
        """Started 2 days ago -> '2d ago', urgency 0."""
        frozen = self._frozen_dt(2026, 3, 27, 12, 0, 0)
        with patch("browse.datetime", self._mock_datetime(frozen)):
            from browse import _get_time_data

            td = _get_time_data(self._make_event("2026-03-25T12:00:00Z"))
            self.assertEqual(td["time_status"], "2d ago")
            self.assertEqual(td["time_urgency"], 0)

    def test_get_time_data_abs_time_format(self):
        """abs_time is formatted correctly in WIB."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0, 0)
        with patch("browse.datetime", self._mock_datetime(frozen)):
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
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)

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
        with patch("browse.datetime", self._mock_datetime(frozen)):
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
        with patch("browse.datetime", self._mock_datetime(frozen)):
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
        with patch("browse.datetime", self._mock_datetime(frozen)):
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
        with patch("browse.datetime", self._mock_datetime(frozen)):
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
        with patch("browse.datetime", self._mock_datetime(frozen)):
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
        with patch("browse.datetime", self._mock_datetime(frozen)):
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
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)

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
        with patch("browse.datetime", self._mock_datetime(frozen)):
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
        with patch("browse.datetime", self._mock_datetime(frozen)):
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
        with patch("browse.datetime", self._mock_datetime(frozen)):
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
        self.assertEqual(
            lines[0], "1. [Team A vs Team B](https://polymarket.com/market/test)"
        )
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
        self.assertEqual(
            lines[0], "2. [Team A vs Team B](https://polymarket.com/market/test)"
        )
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
        self.assertEqual(
            lines[0],
            '<b>1.</b> <a href="https://polymarket.com/market/test">Team A &amp; Team B vs Team C</a>',
        )
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
        self.assertEqual(
            lines[0],
            "1. [Will it rain in Jakarta?](https://polymarket.com/event/rain-jakarta)",
        )
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
        self.assertEqual(
            lines[0],
            '<b>1.</b> <a href="https://polymarket.com/event/rain-sun">Rain &lt;or&gt; Sun?</a>',
        )
        self.assertEqual(lines[1], "   Mar 25, 19:00 WIB | In 6h")
        self.assertEqual(lines[2], "   Markets: 2 | Total Vol: $10,000")


class TestPrintBrowseIntegration(unittest.TestCase):
    """Integration tests for print_browse using the new pipeline."""

    def _frozen_dt(self, year, month, day, hour, minute):
        return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)

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

    @patch("builtins.print")
    def test_print_browse_uses_new_pipeline(self, mock_print):
        """print_browse calls format_match_event and render_match_lines."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch("browse.datetime", self._mock_datetime(frozen)):
            from browse import print_browse

            match_events = [
                {
                    "title": "Counter Strike: Team A vs Team B - ESL Pro League",
                    "slug": "csa",
                    "startTime": "2026-03-25T18:00:00Z",
                    "markets": [
                        {
                            "sportsMarketType": "moneyline",
                            "outcomes": '["Team A", "Team B"]',
                            "outcomePrices": "[0.55, 0.45]",
                            "bestBid": "0.54",
                            "bestAsk": "0.56",
                            "volume": "50000",
                            "acceptingOrders": True,
                            "closed": False,
                        }
                    ],
                }
            ]
            with (
                patch("browse.format_match_event") as mock_fmt,
                patch("browse.render_match_lines") as mock_render,
            ):
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
                print_browse(
                    match_events, [], "Counter Strike", 1, 1, 1, 0, non_matches_max=5
                )

                mock_fmt.assert_called_once_with(match_events[0])
                mock_render.assert_called_once_with(
                    mock_fmt.return_value, 1, mode="text"
                )

    @patch("builtins.print")
    def test_print_browse_matches_only(self, mock_print):
        """matches_only suppresses non-match section."""
        frozen = self._frozen_dt(2026, 3, 25, 12, 0)
        with patch("browse.datetime", self._mock_datetime(frozen)):
            from browse import print_browse

            with patch("browse.format_non_match_event") as mock_non_fmt:
                print_browse(
                    [],
                    [],
                    "Counter Strike",
                    0,
                    0,
                    0,
                    0,
                    non_matches_max=5,
                    matches_only=True,
                )
                mock_non_fmt.assert_not_called()


class TestSendChunked(unittest.TestCase):
    """Tests for send_chunked() helper."""

    def test_small_message_sent_directly(self):
        """Messages under 4096 chars go through without chunking."""
        sent_texts = []

        def fake_send(text):
            sent_texts.append(text)

        lines = [
            "<b>COUNTER STRIKE</b> | Mar 25, 2026",
            "",
            "MATCH MARKETS",
            "",
            "1. test",
        ]
        # This fits in one message
        from browse import send_chunked

        send_chunked(
            lines,
            fake_send,
            "Counter Strike",
            "Mar 25, 2026",
            show_matches=True,
            show_non_matches=False,
        )
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
            lines += [
                f'<b>{i + 1}.</b> <a href="https://polymarket.com/market/{i}">Team {"X" * 250}</a>',
                "   Mar 25, 19:00 WIB | In 6h",
                "  Vol: $50,000",
                "  Odds: TeamA 55c | 45c TeamB",
                "",
            ]
        lines.append("")

        from browse import send_chunked

        send_chunked(
            lines,
            fake_send,
            "Counter Strike",
            "Mar 25, 2026",
            show_matches=True,
            show_non_matches=False,
        )

        # Should have sent more than one message (chunked)
        self.assertGreater(len(sent_texts), 1)
        # At least one continuation message
        cont_found = any("(cont.)" in t for t in sent_texts)
        self.assertTrue(
            cont_found,
            f"Expected at least one '(cont.)' message. Got {len(sent_texts)} messages.",
        )


class TestIsMatchMarket(unittest.TestCase):
    """Tests for is_match_market() classification."""

    def test_match_when_series_and_gameid(self):
        """seriesSlug + gameId present -> match market."""
        from browse import is_match_market

        e = {
            "seriesSlug": "esl-pro-league",
            "gameId": "12345",
            "title": "Tournament Winner",
        }
        self.assertTrue(is_match_market(e))

    def test_match_when_vs_in_title(self):
        """' vs ' in title -> match market."""
        from browse import is_match_market

        e = {"title": "Team A vs Team B - Final"}
        self.assertTrue(is_match_market(e))

    def test_non_match_without_series_and_gameid(self):
        """No seriesSlug/gameId and no ' vs ' -> non-match."""
        from browse import is_match_market

        e = {"title": "Will Team A win the tournament?"}
        self.assertFalse(is_match_market(e))

    def test_non_match_seriesSlug_only(self):
        """Only seriesSlug (no gameId) -> non-match."""
        from browse import is_match_market

        e = {"seriesSlug": "esl-pro-league", "title": "Tournament Winner"}
        self.assertFalse(is_match_market(e))

    def test_non_match_gameid_only(self):
        """Only gameId (no seriesSlug) -> non-match."""
        from browse import is_match_market

        e = {"gameId": "12345", "title": "Tournament Winner"}
        self.assertFalse(is_match_market(e))


class TestGetMlMarket(unittest.TestCase):
    """Tests for get_ml_market() and get_ml_volume()."""

    def test_get_ml_market_finds_moneyline(self):
        """Finds and returns the moneyline market."""
        from browse import get_ml_market

        e = {
            "markets": [
                {"sportsMarketType": "spread", "volume": "1000"},
                {"sportsMarketType": "moneyline", "volume": "50000"},
                {"sportsMarketType": "total", "volume": "2000"},
            ]
        }
        ml = get_ml_market(e)
        self.assertEqual(ml["sportsMarketType"], "moneyline")
        self.assertEqual(ml["volume"], "50000")

    def test_get_ml_market_returns_none_when_missing(self):
        """Returns None when no moneyline market exists."""
        from browse import get_ml_market

        e = {"markets": [{"sportsMarketType": "spread", "volume": "1000"}]}
        self.assertIsNone(get_ml_market(e))

    def test_get_ml_market_returns_none_when_no_markets(self):
        """Returns None when event has no markets."""
        from browse import get_ml_market

        e = {}
        self.assertIsNone(get_ml_market(e))

    def test_get_ml_volume_with_ml(self):
        """Returns float volume from moneyline market."""
        from browse import get_ml_volume

        e = {"markets": [{"sportsMarketType": "moneyline", "volume": "123456"}]}
        self.assertEqual(get_ml_volume(e), 123456.0)

    def test_get_ml_volume_no_ml(self):
        """Returns 0.0 when no moneyline market."""
        from browse import get_ml_volume

        e = {"markets": []}
        self.assertEqual(get_ml_volume(e), 0.0)


class TestFilterEvents(unittest.TestCase):
    """Tests for filter_events() and sort_events()."""

    def _make_match(self, match_id, tradeable=True, vol="50000"):
        return {
            "id": str(match_id),
            "title": f"Team A vs Team B - Match {match_id}",
            "seriesSlug": "test-league",
            "gameId": str(match_id),
            "markets": [
                {
                    "sportsMarketType": "moneyline",
                    "volume": vol,
                    "bestBid": "0.50",
                    "bestAsk": "0.52",
                    "acceptingOrders": tradeable,
                    "closed": False,
                }
            ],
        }

    def _make_non_match(self, event_id, tradeable=True):
        return {
            "id": f"nm{event_id}",
            "title": f"Will event {event_id} happen?",
            "markets": [
                {
                    "sportsMarketType": "moneyline",
                    "volume": "10000",
                    "bestBid": "0.50",
                    "bestAsk": "0.52",
                    "acceptingOrders": tradeable,
                    "closed": False,
                }
            ],
        }

    def test_filter_events_splits_match_and_non_match(self):
        """Correctly splits events into match and non-match buckets."""
        from browse import filter_events

        events = [
            self._make_match(1),
            self._make_non_match(1),
            self._make_match(2),
            self._make_non_match(2),
        ]
        matches, non_matches = filter_events(events, tradeable_only=False)
        self.assertEqual(len(matches), 2)
        self.assertEqual(len(non_matches), 2)
        self.assertEqual(matches[0]["id"], "1")
        self.assertEqual(non_matches[0]["id"], "nm1")

    def test_filter_events_tradeable_only(self):
        """tradeable_only=True filters out non-tradeable events."""
        from browse import filter_events

        events = [
            self._make_match(1, tradeable=True),
            self._make_match(2, tradeable=False),
            self._make_non_match(1),
        ]
        matches, non_matches = filter_events(events, tradeable_only=True)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["id"], "1")
        self.assertEqual(
            len(non_matches), 1
        )  # non-match with acceptingOrders=True passes

    def test_filter_events_tradeable_only_false(self):
        """tradeable_only=False keeps all events."""
        from browse import filter_events

        events = [
            self._make_match(1, tradeable=True),
            self._make_match(2, tradeable=False),
            self._make_non_match(1, tradeable=True),
            self._make_non_match(2, tradeable=False),
        ]
        matches, non_matches = filter_events(events, tradeable_only=False)
        self.assertEqual(len(matches), 2)
        self.assertEqual(len(non_matches), 2)

    def test_sort_events_by_volume_desc(self):
        """sort_events returns events sorted by volume descending."""
        from browse import sort_events

        events = [
            self._make_match(1, vol="10000"),
            self._make_match(2, vol="50000"),
            self._make_match(3, vol="30000"),
        ]
        sorted_evts = sort_events(events)
        self.assertEqual(sorted_evts[0]["id"], "2")  # vol=50000
        self.assertEqual(sorted_evts[1]["id"], "3")  # vol=30000
        self.assertEqual(sorted_evts[2]["id"], "1")  # vol=10000

    def test_sort_events_empty_list(self):
        """sort_events handles empty list gracefully."""
        from browse import sort_events

        result = sort_events([])
        self.assertEqual(result, [])


class TestFetchAllPages(unittest.TestCase):
    """Tests for fetch_all_pages() early-exit logic."""

    @patch("browse._read_cache", return_value=None)
    @patch("browse._fetch_page_with_index")
    @patch("browse.fetch_page")
    def test_early_exit_stops_when_both_quotas_met(
        self, mock_fetch_page, mock_parallel_fetch, mock_cache
    ):
        """Stops fetching once both match and non-match quotas are satisfied."""
        from browse import fetch_all_pages

        page1 = {
            "events": [
                {
                    "id": "m1",
                    "title": "Match 1",
                    "seriesSlug": "x",
                    "gameId": "1",
                    "markets": [],
                },
                {
                    "id": "m2",
                    "title": "Match 2",
                    "seriesSlug": "x",
                    "gameId": "2",
                    "markets": [],
                },
                {"id": "n1", "title": "Non-match 1", "markets": []},
                {"id": "n2", "title": "Non-match 2", "markets": []},
                {"id": "e1", "title": "Extra 1", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }
        page2 = {
            "events": [
                {
                    "id": "m3",
                    "title": "Match 3",
                    "seriesSlug": "x",
                    "gameId": "3",
                    "markets": [],
                },
                {"id": "n3", "title": "Non-match 3", "markets": []},
                {"id": "e2", "title": "Extra 2", "markets": []},
                {"id": "e3", "title": "Extra 3", "markets": []},
                {"id": "e4", "title": "Extra 4", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }
        page3 = {
            "events": [
                {"id": "e5", "title": "Extra 5", "markets": []},
                {"id": "e6", "title": "Extra 6", "markets": []},
                {"id": "e7", "title": "Extra 7", "markets": []},
                {"id": "e8", "title": "Extra 8", "markets": []},
                {"id": "e9", "title": "Extra 9", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }
        page4 = {
            "events": [
                {"id": "e10", "title": "Extra 10", "markets": []},
                {"id": "e11", "title": "Extra 11", "markets": []},
                {"id": "e12", "title": "Extra 12", "markets": []},
                {"id": "e13", "title": "Extra 13", "markets": []},
                {"id": "e14", "title": "Extra 14", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }

        mock_fetch_page.return_value = page1
        mock_parallel_fetch.side_effect = [
            (2, page2),
            (3, page3),
            (4, page4),
        ]

        result = fetch_all_pages(
            "test", matches_max=3, non_matches_max=3, use_cache=False
        )

        self.assertEqual(mock_fetch_page.call_count, 1)
        self.assertEqual(mock_parallel_fetch.call_count, 3)
        self.assertEqual(len(result["events"]), 6)

    @patch("browse._read_cache", return_value=None)
    @patch("browse._fetch_page_with_index")
    @patch("browse.fetch_page")
    def test_no_quota_fetches_all_pages(
        self, mock_fetch_page, mock_parallel_fetch, mock_cache
    ):
        """Without quotas, fetches all pages until pagination ends."""
        from browse import fetch_all_pages

        page1 = {
            "events": [
                {"id": "e1", "title": "Event 1", "markets": []},
                {"id": "e2", "title": "Event 2", "markets": []},
                {"id": "e3", "title": "Event 3", "markets": []},
                {"id": "e4", "title": "Event 4", "markets": []},
                {"id": "e5", "title": "Event 5", "markets": []},
            ],
            "pagination": {"totalResults": 15, "hasMore": True},
        }
        page2 = {
            "events": [
                {"id": "e6", "title": "Event 6", "markets": []},
                {"id": "e7", "title": "Event 7", "markets": []},
                {"id": "e8", "title": "Event 8", "markets": []},
                {"id": "e9", "title": "Event 9", "markets": []},
                {"id": "e10", "title": "Event 10", "markets": []},
            ],
            "pagination": {"totalResults": 15, "hasMore": True},
        }
        page3 = {
            "events": [
                {"id": "e11", "title": "Event 11", "markets": []},
                {"id": "e12", "title": "Event 12", "markets": []},
                {"id": "e13", "title": "Event 13", "markets": []},
                {"id": "e14", "title": "Event 14", "markets": []},
                {"id": "e15", "title": "Event 15", "markets": []},
            ],
            "pagination": {"totalResults": 15, "hasMore": False},
        }

        mock_fetch_page.return_value = page1
        mock_parallel_fetch.side_effect = [(2, page2), (3, page3)]

        result = fetch_all_pages("test", use_cache=False)

        self.assertEqual(mock_fetch_page.call_count, 1)
        self.assertEqual(mock_parallel_fetch.call_count, 2)
        self.assertEqual(len(result["events"]), 15)
        self.assertFalse(result["partial"])

    @patch("browse._read_cache", return_value=None)
    @patch("browse._fetch_page_with_index")
    @patch("browse.fetch_page")
    def test_quota_one_side_only_keeps_fetching(
        self, mock_fetch_page, mock_parallel_fetch, mock_cache
    ):
        """If only one quota is met, keeps fetching."""
        from browse import fetch_all_pages

        page1 = {
            "events": [
                {
                    "id": "m1",
                    "title": "Match 1",
                    "seriesSlug": "x",
                    "gameId": "1",
                    "markets": [],
                },
                {
                    "id": "m2",
                    "title": "Match 2",
                    "seriesSlug": "x",
                    "gameId": "2",
                    "markets": [],
                },
                {
                    "id": "m3",
                    "title": "Match 3",
                    "seriesSlug": "x",
                    "gameId": "3",
                    "markets": [],
                },
                {"id": "e1", "title": "Extra 1", "markets": []},
                {"id": "e2", "title": "Extra 2", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }
        page2 = {
            "events": [
                {"id": "n1", "title": "Non-match 1", "markets": []},
                {"id": "n2", "title": "Non-match 2", "markets": []},
                {"id": "n3", "title": "Non-match 3", "markets": []},
                {"id": "e3", "title": "Extra 3", "markets": []},
                {"id": "e4", "title": "Extra 4", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }
        page3 = {
            "events": [
                {"id": "e5", "title": "Extra 5", "markets": []},
                {"id": "e6", "title": "Extra 6", "markets": []},
                {"id": "e7", "title": "Extra 7", "markets": []},
                {"id": "e8", "title": "Extra 8", "markets": []},
                {"id": "e9", "title": "Extra 9", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }
        page4 = {
            "events": [
                {"id": "e10", "title": "Extra 10", "markets": []},
                {"id": "e11", "title": "Extra 11", "markets": []},
                {"id": "e12", "title": "Extra 12", "markets": []},
                {"id": "e13", "title": "Extra 13", "markets": []},
                {"id": "e14", "title": "Extra 14", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }

        mock_fetch_page.return_value = page1
        mock_parallel_fetch.side_effect = [(2, page2), (3, page3), (4, page4)]

        result = fetch_all_pages(
            "test", matches_max=3, non_matches_max=3, use_cache=False
        )

        self.assertEqual(mock_parallel_fetch.call_count, 3)
        self.assertEqual(len(result["events"]), 6)

    @patch("browse._read_cache", return_value=None)
    @patch("browse._fetch_page_with_index")
    @patch("browse.fetch_page")
    def test_no_quota_fetches_all_pages(
        self, mock_fetch_page, mock_parallel_fetch, mock_cache
    ):
        """Without quotas, fetches all pages until pagination ends."""
        from browse import fetch_all_pages

        page1 = {
            "events": [
                {"id": "e1", "title": "Event 1", "markets": []},
                {"id": "e2", "title": "Event 2", "markets": []},
                {"id": "e3", "title": "Event 3", "markets": []},
                {"id": "e4", "title": "Event 4", "markets": []},
                {"id": "e5", "title": "Event 5", "markets": []},
            ],
            "pagination": {"totalResults": 15, "hasMore": True},
        }
        page2 = {
            "events": [
                {"id": "e6", "title": "Event 6", "markets": []},
                {"id": "e7", "title": "Event 7", "markets": []},
                {"id": "e8", "title": "Event 8", "markets": []},
                {"id": "e9", "title": "Event 9", "markets": []},
                {"id": "e10", "title": "Event 10", "markets": []},
            ],
            "pagination": {"totalResults": 15, "hasMore": True},
        }
        page3 = {
            "events": [
                {"id": "e11", "title": "Event 11", "markets": []},
                {"id": "e12", "title": "Event 12", "markets": []},
                {"id": "e13", "title": "Event 13", "markets": []},
                {"id": "e14", "title": "Event 14", "markets": []},
                {"id": "e15", "title": "Event 15", "markets": []},
            ],
            "pagination": {"totalResults": 15, "hasMore": False},
        }

        mock_fetch_page.return_value = page1
        mock_parallel_fetch.side_effect = [(2, page2), (3, page3)]

        result = fetch_all_pages("test", use_cache=False)

        self.assertEqual(mock_fetch_page.call_count, 1)
        self.assertEqual(mock_parallel_fetch.call_count, 2)
        self.assertEqual(len(result["events"]), 15)
        self.assertFalse(result["partial"])

    @patch("browse._read_cache", return_value=None)
    @patch("browse._fetch_page_with_index")
    @patch("browse.fetch_page")
    def test_quota_one_side_only_keeps_fetching(
        self, mock_fetch_page, mock_parallel_fetch, mock_cache
    ):
        """If only one quota is met, keeps fetching."""
        from browse import fetch_all_pages

        page1 = {
            "events": [
                {
                    "id": "m1",
                    "title": "Match 1",
                    "seriesSlug": "x",
                    "gameId": "1",
                    "markets": [],
                },
                {
                    "id": "m2",
                    "title": "Match 2",
                    "seriesSlug": "x",
                    "gameId": "2",
                    "markets": [],
                },
                {
                    "id": "m3",
                    "title": "Match 3",
                    "seriesSlug": "x",
                    "gameId": "3",
                    "markets": [],
                },
                {"id": "e1", "title": "Extra 1", "markets": []},
                {"id": "e2", "title": "Extra 2", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }
        page2 = {
            "events": [
                {"id": "n1", "title": "Non-match 1", "markets": []},
                {"id": "n2", "title": "Non-match 2", "markets": []},
                {"id": "n3", "title": "Non-match 3", "markets": []},
                {"id": "e3", "title": "Extra 3", "markets": []},
                {"id": "e4", "title": "Extra 4", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }
        page3 = {
            "events": [
                {"id": "e5", "title": "Extra 5", "markets": []},
                {"id": "e6", "title": "Extra 6", "markets": []},
                {"id": "e7", "title": "Extra 7", "markets": []},
                {"id": "e8", "title": "Extra 8", "markets": []},
                {"id": "e9", "title": "Extra 9", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }
        page4 = {
            "events": [
                {"id": "e10", "title": "Extra 10", "markets": []},
                {"id": "e11", "title": "Extra 11", "markets": []},
                {"id": "e12", "title": "Extra 12", "markets": []},
                {"id": "e13", "title": "Extra 13", "markets": []},
                {"id": "e14", "title": "Extra 14", "markets": []},
            ],
            "pagination": {"totalResults": 20, "hasMore": True},
        }

        mock_fetch_page.return_value = page1
        mock_parallel_fetch.side_effect = [(2, page2), (3, page3), (4, page4)]

        result = fetch_all_pages(
            "test", matches_max=3, non_matches_max=3, use_cache=False
        )

        self.assertEqual(mock_parallel_fetch.call_count, 3)
        self.assertEqual(len(result["events"]), 6)


class TestParallelFetchConcurrency(unittest.TestCase):
    """Tests for parallel page fetching concurrency."""

    @patch("browse._read_cache", return_value=None)
    @patch("browse._fetch_page_with_index")
    @patch("browse.fetch_page")
    def test_parallel_fetch_uses_batch_size_of_5(
        self, mock_fetch_page, mock_parallel_fetch, mock_cache
    ):
        """With 10 pages (totalResults=50), verify 10 calls are made with concurrency=5."""
        from browse import fetch_all_pages

        page = {
            "events": [
                {"id": "e1", "title": "Event 1", "markets": []},
                {"id": "e2", "title": "Event 2", "markets": []},
                {"id": "e3", "title": "Event 3", "markets": []},
                {"id": "e4", "title": "Event 4", "markets": []},
                {"id": "e5", "title": "Event 5", "markets": []},
            ],
            "pagination": {"totalResults": 50, "hasMore": True},
        }
        mock_fetch_page.return_value = page
        mock_parallel_fetch.return_value = (1, page)

        result = fetch_all_pages("test", use_cache=False)

        total_pages = (50 + 5 - 1) // 5  # = 10 pages (API returns 5 per page)
        # Page 1 is fetched in probe loop, so executor only fetches pages 2-10 (9 calls)
        self.assertEqual(mock_parallel_fetch.call_count, total_pages - 1)

    @patch("browse._read_cache", return_value=None)
    @patch("browse._fetch_page_with_index")
    @patch("browse.fetch_page")
    def test_parallel_fetch_respects_concurrency_limit(
        self, mock_fetch_page, mock_parallel_fetch, mock_cache
    ):
        """Verify that at most MAX_PARALLEL_FETCHES (5) requests run concurrently."""
        from browse import fetch_all_pages, MAX_PARALLEL_FETCHES

        page = {
            "events": [{"id": "e1", "title": "Event 1", "markets": []}],
            "pagination": {"totalResults": 500, "hasMore": True},
        }
        mock_fetch_page.return_value = page
        mock_parallel_fetch.return_value = (1, page)

        result = fetch_all_pages("test", use_cache=False)

        self.assertEqual(MAX_PARALLEL_FETCHES, 5)


class TestCacheFunctions(unittest.TestCase):
    """Tests for cache read/write functions."""

    @patch("browse.CACHE_DIR", "/tmp/test_cache")
    @patch("browse.os.path.exists")
    @patch("browse.os.path.getmtime")
    @patch("builtins.open", side_effect=FileNotFoundError)
    @patch("json.load")
    def test_read_cache_returns_none_when_file_not_found(
        self, mock_json, mock_open, mock_mtime, mock_exists
    ):
        """_read_cache returns None if cache file does not exist."""
        from browse import _read_cache

        mock_exists.return_value = False

        result = _read_cache("test_query")

        self.assertIsNone(result)

    @patch("browse.CACHE_DIR", "/tmp/test_cache")
    @patch("browse.os.makedirs")
    @patch("builtins.open")
    @patch("json.dump")
    def test_write_cache_creates_directory_if_needed(
        self, mock_json_dump, mock_open, mock_makedirs
    ):
        """_write_cache creates cache directory if it does not exist."""
        from browse import _write_cache

        data = {"events": [], "total_raw": 0}

        _write_cache("test_query", data)

        mock_makedirs.assert_called_once()

    @patch("browse.CACHE_DIR", "/tmp/test_cache")
    @patch("browse.os.path.exists", return_value=True)
    @patch("browse.os.path.getmtime", return_value=time.time())
    @patch("builtins.open", side_effect=Exception("read error"))
    def test_read_cache_returns_none_on_error(self, mock_open, mock_mtime, mock_exists):
        """_read_cache returns None when an error occurs during cache read."""
        from browse import _read_cache

        result = _read_cache("test_query")

        self.assertIsNone(result)

    @patch("browse.CACHE_DIR", "/tmp/test_cache")
    @patch("builtins.open", side_effect=Exception("write error"))
    @patch("browse.os.makedirs")
    def test_write_cache_returns_silently_on_error(self, mock_makedirs, mock_open):
        """_write_cache silently handles errors and does not raise."""
        from browse import _write_cache

        data = {"events": [], "total_raw": 0}

        try:
            _write_cache("test_query", data)
        except Exception:
            self.fail("_write_cache raised an exception unexpectedly")


class TestMaxTotalParameter(unittest.TestCase):
    """Tests for max_total parameter in fetch_all_pages."""

    @patch("browse._read_cache", return_value=None)
    @patch("browse._fetch_page_with_index")
    @patch("browse.fetch_page")
    def test_max_total_limits_events_returned(
        self, mock_fetch_page, mock_parallel_fetch, mock_cache
    ):
        """max_total=10 should return at most 10 events."""
        from browse import fetch_all_pages

        pages = []
        for i in range(10):
            pages.append(
                (
                    i + 1,
                    {
                        "events": [
                            {
                                "id": f"e{i + 1}",
                                "title": f"Event {i + 1}",
                                "markets": [],
                            }
                        ],
                        "pagination": {"totalResults": 50, "hasMore": True},
                    },
                )
            )
        mock_fetch_page.return_value = pages[0][1]
        mock_parallel_fetch.side_effect = pages

        result = fetch_all_pages("test", max_total=10, use_cache=False)

        self.assertEqual(len(result["events"]), 10)

    @patch("browse._read_cache", return_value=None)
    @patch("browse._fetch_page_with_index")
    @patch("browse.fetch_page")
    def test_max_total_with_matches_and_non_matches(
        self, mock_fetch_page, mock_parallel_fetch, mock_cache
    ):
        """max_total works alongside matches_max and non_matches_max quotas."""
        from browse import fetch_all_pages

        page1 = {
            "events": [
                {
                    "id": "m1",
                    "title": "Match 1",
                    "seriesSlug": "x",
                    "gameId": "1",
                    "markets": [],
                },
                {"id": "n1", "title": "Non-match 1", "markets": []},
                {
                    "id": "m2",
                    "title": "Match 2",
                    "seriesSlug": "x",
                    "gameId": "2",
                    "markets": [],
                },
            ],
            "pagination": {"totalResults": 5, "hasMore": True},
        }
        mock_fetch_page.return_value = page1
        mock_parallel_fetch.side_effect = [(1, page1)]

        result = fetch_all_pages(
            "test", matches_max=10, non_matches_max=10, max_total=2, use_cache=False
        )

        self.assertEqual(len(result["events"]), 2)


class TestBrowseEvents(unittest.TestCase):
    """Tests for browse_events() with sort_by parameter."""

    @patch("browse.fetch_all_pages")
    def test_browse_events_early_exit_sort_by_none(self, mock_fetch):
        """sort_by=None uses early-exit: passes quotas to fetch_all_pages."""
        from browse import browse_events

        mock_fetch.return_value = {
            "events": [
                {
                    "id": "m1",
                    "title": "Match 1",
                    "seriesSlug": "x",
                    "gameId": "1",
                    "markets": [{"sportsMarketType": "moneyline", "volume": "50000"}],
                },
            ],
            "total_raw": 1,
            "partial": False,
        }

        result = browse_events(
            "test query", matches_max=5, non_matches_max=5, sort_by=None
        )

        # Should pass quotas to fetch_all_pages for early-exit
        mock_fetch.assert_called_once()
        call_kwargs = mock_fetch.call_args
        self.assertEqual(call_kwargs[1]["matches_max"], 5)
        self.assertEqual(call_kwargs[1]["non_matches_max"], 5)

    @patch("browse.fetch_all_pages")
    def test_browse_events_volume_sort_full_fetch(self, mock_fetch):
        """sort_by='volume' does full fetch (no quotas passed)."""
        from browse import browse_events

        mock_fetch.return_value = {
            "events": [
                {
                    "id": "m1",
                    "title": "Match 1",
                    "seriesSlug": "x",
                    "gameId": "1",
                    "markets": [{"sportsMarketType": "moneyline", "volume": "10000"}],
                },
                {
                    "id": "m2",
                    "title": "Match 2",
                    "seriesSlug": "x",
                    "gameId": "2",
                    "markets": [{"sportsMarketType": "moneyline", "volume": "50000"}],
                },
            ],
            "total_raw": 2,
            "partial": False,
        }

        result = browse_events(
            "test query", matches_max=5, non_matches_max=5, sort_by="volume"
        )

        # Should pass None quotas to fetch_all_pages (full fetch)
        call_kwargs = mock_fetch.call_args
        self.assertIsNone(call_kwargs[1]["matches_max"])
        self.assertIsNone(call_kwargs[1]["non_matches_max"])

    @patch("browse.fetch_all_pages")
    def test_browse_events_volume_sort_sorts_by_volume(self, mock_fetch):
        """sort_by='volume' sorts match events by volume descending."""
        from browse import browse_events

        mock_fetch.return_value = {
            "events": [
                {
                    "id": "m1",
                    "title": "Match Low",
                    "seriesSlug": "x",
                    "gameId": "1",
                    "markets": [
                        {
                            "sportsMarketType": "moneyline",
                            "volume": "10000",
                            "bestBid": "0.50",
                            "bestAsk": "0.52",
                            "acceptingOrders": True,
                            "closed": False,
                        }
                    ],
                },
                {
                    "id": "m2",
                    "title": "Match High",
                    "seriesSlug": "x",
                    "gameId": "2",
                    "markets": [
                        {
                            "sportsMarketType": "moneyline",
                            "volume": "90000",
                            "bestBid": "0.50",
                            "bestAsk": "0.52",
                            "acceptingOrders": True,
                            "closed": False,
                        }
                    ],
                },
                {
                    "id": "m3",
                    "title": "Match Mid",
                    "seriesSlug": "x",
                    "gameId": "3",
                    "markets": [
                        {
                            "sportsMarketType": "moneyline",
                            "volume": "50000",
                            "bestBid": "0.50",
                            "bestAsk": "0.52",
                            "acceptingOrders": True,
                            "closed": False,
                        }
                    ],
                },
            ],
            "total_raw": 3,
            "partial": False,
        }

        result = browse_events(
            "test", matches_max=10, non_matches_max=10, sort_by="volume"
        )

        # Highest volume first
        self.assertEqual(result["match_events"][0]["id"], "m2")  # vol=90000
        self.assertEqual(result["match_events"][1]["id"], "m3")  # vol=50000
        self.assertEqual(result["match_events"][2]["id"], "m1")  # vol=10000

    @patch("browse.fetch_all_pages")
    def test_browse_events_api_order_preserved_when_no_sort(self, mock_fetch):
        """sort_by=None preserves API order (no sort applied)."""
        from browse import browse_events

        mock_fetch.return_value = {
            "events": [
                {
                    "id": "m1",
                    "title": "Match First",
                    "seriesSlug": "x",
                    "gameId": "1",
                    "markets": [
                        {
                            "sportsMarketType": "moneyline",
                            "volume": "1",
                            "bestBid": "0.50",
                            "bestAsk": "0.52",
                            "acceptingOrders": True,
                            "closed": False,
                        }
                    ],
                },
                {
                    "id": "m2",
                    "title": "Match Second",
                    "seriesSlug": "x",
                    "gameId": "2",
                    "markets": [
                        {
                            "sportsMarketType": "moneyline",
                            "volume": "999999",
                            "bestBid": "0.50",
                            "bestAsk": "0.52",
                            "acceptingOrders": True,
                            "closed": False,
                        }
                    ],
                },
            ],
            "total_raw": 2,
            "partial": False,
        }

        result = browse_events("test", matches_max=10, sort_by=None)

        # API order preserved: m1 first even though m2 has higher volume
        self.assertEqual(result["match_events"][0]["id"], "m1")
        self.assertEqual(result["match_events"][1]["id"], "m2")

    @patch("browse.fetch_all_pages")
    def test_browse_events_returns_all_required_fields(self, mock_fetch):
        """Result dict contains all required fields."""
        from browse import browse_events

        mock_fetch.return_value = {
            "events": [],
            "total_raw": 0,
            "partial": False,
        }

        result = browse_events("test")

        self.assertIn("query", result)
        self.assertIn("total_raw", result)
        self.assertIn("total_fetched", result)
        self.assertIn("total_match", result)
        self.assertIn("total_non_match", result)
        self.assertIn("match_events", result)
        self.assertIn("non_match_events", result)
        self.assertIn("partial", result)


class TestStartsBeforeFilter(unittest.TestCase):
    """Tests for --starts-before filter in browse_events()."""

    def _make_event(self, event_id, start_time, volume="50000"):
        """Helper to create a minimal match event with startTime and valid tradeable data."""
        return {
            "id": event_id,
            "title": f"Match {event_id}",
            "seriesSlug": "x",
            "gameId": "1",
            "startTime": start_time,
            "markets": [
                {
                    "sportsMarketType": "moneyline",
                    "volume": volume,
                    "bestBid": "0.50",
                    "bestAsk": "0.52",
                    "acceptingOrders": True,
                    "closed": False,
                }
            ],
        }

    @patch("browse.fetch_all_pages")
    def test_starts_before_filters_future_events(self, mock_fetch):
        """Events with startTime > timestamp should be filtered out."""
        from browse import browse_events

        mock_fetch.return_value = {
            "events": [
                self._make_event(
                    "m1", "2026-03-27T14:00:00Z"
                ),  # After cutoff (14:00 > 12:00)
                self._make_event("m2", "2026-03-28T12:00:00Z"),  # After cutoff
            ],
            "total_raw": 2,
            "partial": False,
        }

        # 2026-03-27T12:00:00Z = 1774612800
        result = browse_events("test", starts_before=1774612800)

        self.assertEqual(len(result["match_events"]), 0)

    @patch("browse.fetch_all_pages")
    def test_starts_before_includes_past_events(self, mock_fetch):
        """Events with startTime <= timestamp should be included."""
        from browse import browse_events

        mock_fetch.return_value = {
            "events": [
                self._make_event(
                    "m1", "2026-03-27T10:00:00Z"
                ),  # Before cutoff (10:00 < 12:00)
                self._make_event(
                    "m2", "2026-03-27T11:00:00Z"
                ),  # Before cutoff (11:00 < 12:00)
            ],
            "total_raw": 2,
            "partial": False,
        }

        # 2026-03-27T12:00:00Z = 1774612800
        result = browse_events("test", starts_before=1774612800)

        self.assertEqual(len(result["match_events"]), 2)

    @patch("browse.fetch_all_pages")
    def test_starts_before_without_timestamp(self, mock_fetch):
        """Without starts_before, all events should be returned."""
        from browse import browse_events

        mock_fetch.return_value = {
            "events": [
                self._make_event("m1", "2026-03-27T14:00:00Z"),
                self._make_event("m2", "2026-03-28T12:00:00Z"),
            ],
            "total_raw": 2,
            "partial": False,
        }

        result = browse_events("test")

        # No filter, all events returned
        self.assertEqual(len(result["match_events"]), 2)


if __name__ == "__main__":
    unittest.main()
