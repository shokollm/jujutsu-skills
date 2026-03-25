#!/usr/bin/env python3
"""
Polymarket Event Browser
Browse tradeable Polymarket events by game category.
"""

import html
import json
import time
import argparse
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode
from urllib.request import urlopen, Request

# ============================================================
# CONFIG
# ============================================================

PAGE_SIZE = 50
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 2  # exponential backoff starts at 2s
WIB = timezone(timedelta(hours=7))  # UTC+7 for Indonesian users

GAME_CATEGORIES = {
    "All Esports": "Esports",
    "Counter Strike": "Counter Strike",
    "League of Legends": "League of Legends",
    "Dota 2": "Dota2",
    "Valorant": "Valorant",
    "NBA": "NBA",
    "NFL": "Football",
    "UFC": "UFC",
    "Tennis": "Tennis",
}

# ============================================================
# FETCH
# ============================================================

def fetch_page(q, page=1, max_retries=MAX_RETRIES, initial_delay=INITIAL_RETRY_DELAY):
    base = "https://gamma-api.polymarket.com/public-search"
    url = (f"{base}?q={q.replace(' ', '%20')}&limit={PAGE_SIZE}&page={page}"
           f"&search_profiles=false&search_tags=false"
           f"&keep_closed_markets=0&events_status=active&cache=false")
    
    delay = initial_delay
    for attempt in range(max_retries):
        time.sleep(delay)
        r = subprocess.run(
            ["curl", "-s", url, "--max-time", "10", "-H", "User-Agent: curl/7.88.1"],
            capture_output=True
        )
        
        if r.returncode == 0 and len(r.stdout) > 0:
            try:
                return json.loads(r.stdout.decode('utf-8'))
            except json.JSONDecodeError:
                if attempt < max_retries - 1:
                    delay *= 2  # Exponential backoff
                    continue
                return None
        else:
            # Rate limit or other error - exponential backoff
            if attempt < max_retries - 1:
                delay *= 2
                continue
            return None
    return None

def fetch_all_pages(q, max_pages=100):
    """
    Fetch ALL pages until pagination ends.
    max_pages is a safety cap to prevent infinite loops.
    """
    all_events = []
    total_raw = 0
    for page in range(1, max_pages + 1):
        time.sleep(0.2)  # small delay between pages (API rate limit is generous)
        data = fetch_page(q, page)
        if data is None:
            break
        events = data.get("events", [])
        total_raw = data.get("pagination", {}).get("totalResults", 0)
        all_events.extend(events)
        # Stop when we get 0 events (no more pages),
        # OR when we've fetched >= total results
        if len(events) == 0:
            break
        if len(all_events) >= total_raw:
            break
    partial = (total_raw > 0 and len(all_events) < total_raw)
    return {"events": all_events, "total_raw": total_raw, "partial": partial}

# ============================================================
# FILTERS
# ============================================================

def is_match_market(e):
    return (e.get("seriesSlug") and e.get("gameId")) or " vs " in e.get("title", "")

def get_event_url(e):
    """Return the correct Polymarket URL for an event.
    Match markets use /market/, non-match events use /event/.
    """
    slug = e.get("slug", "")
    if is_match_market(e):
        return f"https://polymarket.com/market/{slug}"
    else:
        return f"https://polymarket.com/event/{slug}"

def get_ml_market(e):
    for m in e.get("markets", []):
        if m.get("sportsMarketType") == "moneyline":
            return m
    return None

def get_ml_volume(e):
    ml = get_ml_market(e)
    return float(ml.get("volume", 0)) if ml else 0.0

def is_bo2_tie(e):
    """
    Detect if this is a BO2 that ended in a tie (1-1).
    Returns True if all child_moneyline markets are closed (match is over but tied).
    """
    title = e.get("title", "")
    if "BO2" not in title:
        return False
    
    child_markets = [m for m in e.get("markets", []) if m.get("sportsMarketType") == "child_moneyline"]
    if len(child_markets) != 2:
        return False
    
    # If both child markets are closed, it's likely a finished BO2
    all_closed = all(m.get("closed", False) for m in child_markets)
    return all_closed

def is_tradeable_event(e):
    ml = get_ml_market(e)
    if not ml:
        return False
    best_bid = float(ml.get("bestBid", 0))
    best_ask = float(ml.get("bestAsk", 0))
    ml_vol = float(ml.get("volume", 0))
    accepting = ml.get("acceptingOrders", False)
    ml_closed = ml.get("closed", True)
    
    # Filter: market has converged (bestBid >= 0.99 means near-resolved)
    if best_bid >= 0.99:
        return False
    # Filter: market has converged the other way (bestAsk <= 0.01)
    if best_ask <= 0.01:
        return False
    # Filter: no volume or not accepting orders or closed
    if ml_vol <= 0:
        return False
    if not accepting:
        return False
    if ml_closed:
        return False
    # Filter: BO2 that ended in a tie (1-1)
    if is_bo2_tie(e):
        return False
    # Filter: event has ended (check endDate - Polymarket API doesn't always close ML market promptly)
    end_str = e.get("endDate", "")
    if end_str:
        try:
            end_dt = datetime.fromisoformat(end_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            if end_dt < now:
                return False
        except:
            pass
    
    # Filter: match has already started (startTime is in the past)
    start_str = e.get("startTime") or e.get("startDate", "")
    if start_str:
        try:
            start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            if start_dt < now:
                # Check if it's recently started (within 4h) — consider those "live" still
                hours_ago = (now - start_dt).total_seconds() / 3600
                if hours_ago > 4:
                    return False
        except:
            pass
    
    return True

def is_tradeable_market(m):
    accepting = m.get("acceptingOrders", False)
    closed = m.get("closed", True)
    best_ask = float(m.get("bestAsk", 0))
    best_bid = float(m.get("bestBid", 0))
    
    # Filter: market has converged (bestBid >= 0.99)
    if best_bid >= 0.99:
        return False
    # Filter: market has converged the other way (bestAsk <= 0.01)
    if best_ask <= 0.01:
        return False
    # Filter: not accepting orders or closed
    if not accepting:
        return False
    if closed:
        return False
    
    return True

# ============================================================
# FORMATTING
# ============================================================

def prob_to_cents(p):
    return int(round(p * 100))

def format_odds(p):
    return f"{prob_to_cents(p)}c"

def format_spread(bid, ask):
    spread = ask - bid
    return f"{prob_to_cents(spread)}c"


def _get_time_data(e, tz=None):
    """
    Unified time data extraction for event timestamps.

    Uses startTime (preferred) or startDate as the event start time.
    Datetime parsing and all relative calculations are UTC-based.
    The tz parameter only affects the abs_time formatting.

    Args:
        e: Event dict with 'startTime' or 'startDate' key.
        tz: datetime.timezone for abs_time formatting.
            Defaults to WIB (UTC+7).

    Returns:
        {
            "time_status": str,    # e.g. "LIVE", "In 6h", "12h ago"
            "time_urgency": int,  # 0-3 (higher = more urgent/live)
            "abs_time": str,       # e.g. "Mar 25, 19:00 WIB" or "TBD"
        }
    """
    tz = tz or WIB
    start_str = e.get("startTime") or e.get("startDate", "")

    if not start_str:
        return {"time_status": "TBD", "time_urgency": 0, "abs_time": "TBD"}

    try:
        start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
        now_utc = datetime.now(timezone.utc)
        delta = start_dt - now_utc
        total_sec = delta.total_seconds()

        if total_sec < 0:
            # Event is in the past
            hours_ago = abs(total_sec) / 3600
            if hours_ago < 1:
                time_status = "LIVE"
                time_urgency = 3
            elif hours_ago < 4:
                time_status = f"LIVE {int(hours_ago)}h"
                time_urgency = 3
            elif hours_ago < 24:
                time_status = f"{int(hours_ago)}h ago"
                time_urgency = 1
            else:
                days = int(hours_ago / 24)
                time_status = f"{days}d ago"
                time_urgency = 0
        else:
            # Event is in the future
            if total_sec < 3600:
                mins = int(total_sec / 60)
                time_status = f"In {mins}m"
                time_urgency = 3
            elif total_sec < 86400:
                hours_until = int(total_sec / 3600)
                time_status = f"In {hours_until}h"
                time_urgency = 2
            else:
                days = int(total_sec / 86400)
                time_status = f"In {days}d"
                time_urgency = 1

        abs_time = start_dt.astimezone(tz).strftime("%b %d, %H:%M ")
        if tz == WIB:
            abs_time += "WIB"
        else:
            abs_time += start_dt.astimezone(tz).strftime("%Z")
        return {"time_status": time_status, "time_urgency": time_urgency, "abs_time": abs_time}
    except Exception:
        return {"time_status": "", "time_urgency": 0, "abs_time": "TBD"}


def filter_events(events, tradeable_only=True):
    """
    Classify events into match_markets and non_match_markets.
    If tradeable_only=True, also filter out non-tradeable events.
    """
    match_events = []
    non_match_events = []

    for e in events:
        if is_match_market(e):
            if not tradeable_only or is_tradeable_event(e):
                match_events.append(e)
        else:
            non_match_events.append(e)

    return match_events, non_match_events


def sort_events(events):
    return sorted(events, key=get_ml_volume, reverse=True)

# ============================================================
# BROWSE
# ============================================================

def browse_events(q, matches_max=10, non_matches_max=10, tradeable_only=True):
    result = fetch_all_pages(q)
    events = result["events"]
    match_events, non_match_events = filter_events(events, tradeable_only)
    sorted_match = sort_events(match_events)
    return {
        "query": q,
        "total_raw": result["total_raw"],
        "total_fetched": len(events),
        "total_match": len(match_events),
        "total_non_match": len(non_match_events),
        "match_events": sorted_match[:matches_max],
        "non_match_events": non_match_events[:non_matches_max],
        "partial": result.get("partial", False),
    }

# ============================================================
# FORMAT — EVENT
# ============================================================

def format_match_event(e):
    """
    Format a match event into a canonical dict for rendering.
    All computing done here; renderers just template.

    Returns:
        {
            "title": str,           # raw title
            "title_clean": str,      # "Team A vs Team B"
            "tournament": str,       # "Tournament Name" or ""
            "url": str,
            "time_status": str,      # "LIVE", "In 6h", "12h ago"
            "time_urgency": int,     # 0-3
            "abs_time": str,         # "Mar 25, 19:00 WIB"
            "team_a": str,
            "team_b": str,
            "odds_a": str,           # "55c"
            "odds_b": str,
            "vol": int,
        }
    """
    ml = get_ml_market(e)
    outcomes = json.loads(ml.get("outcomes", "[]")) if ml else []
    prices = json.loads(ml.get("outcomePrices", "[]")) if ml else []
    td = _get_time_data(e)
    title = e.get("title", "")

    team_a = outcomes[0] if len(outcomes) > 0 else "?"
    team_b = outcomes[1] if len(outcomes) > 1 else "?"
    odds_a = format_odds(float(prices[0])) if len(prices) > 0 else "?"
    odds_b = format_odds(float(prices[1])) if len(prices) > 1 else "?"

    if " - " in title:
        title_clean = title.split(" - ")[0].strip()
    else:
        title_clean = title

    tournament = get_tournament(title)

    return {
        "title": title,
        "title_clean": title_clean,
        "tournament": tournament,
        "url": get_event_url(e),
        "time_status": td["time_status"],
        "time_urgency": td["time_urgency"],
        "abs_time": td["abs_time"],
        "team_a": team_a,
        "team_b": team_b,
        "odds_a": odds_a,
        "odds_b": odds_b,
        "vol": get_ml_volume(e),
    }


def format_non_match_event(e):
    """
    Format a non-match event into a canonical dict for rendering.

    Returns:
        {
            "title": str,
            "url": str,
            "time_status": str,
            "time_urgency": int,
            "abs_time": str,
            "market_count": int,
            "total_vol": int,
        }
    """
    td = _get_time_data(e)
    total_vol = sum(float(m.get("volume", 0)) for m in e.get("markets", []))
    market_count = len(e.get("markets", []))

    return {
        "title": e.get("title", "?"),
        "url": get_event_url(e),
        "time_status": td["time_status"],
        "time_urgency": td["time_urgency"],
        "abs_time": td["abs_time"],
        "market_count": market_count,
        "total_vol": int(total_vol),
    }


# ============================================================
# FORMAT — RENDER
# ============================================================

def render_match_lines(event_dict, i, mode):
    """
    Render a formatted match event dict into lines of text.

    Args:
        event_dict: canonical dict from format_match_event()
        i: 1-based index for the event number
        mode: "text" for plain text/Markdown, "html" for Telegram HTML

    Returns:
        List[str], one line per element (no trailing blank line).
        Caller adds the blank line separator between events.
    """
    title_clean = event_dict["title_clean"]
    url = event_dict["url"]
    abs_time = event_dict["abs_time"]
    time_status = event_dict["time_status"]
    vol = event_dict["vol"]
    tournament = event_dict["tournament"]
    team_a = event_dict["team_a"]
    team_b = event_dict["team_b"]
    odds_a = event_dict["odds_a"]
    odds_b = event_dict["odds_b"]

    lines = []

    if mode == "html":
        lines.append(
            f"<b>{i}.</b> <a href=\"{url}\">{escape_html(title_clean)}</a>"
        )
    else:
        lines.append(f"{i}. [{title_clean}]({url})")

    lines.append(f"   {abs_time} | {time_status}")
    lines.append(f"  Vol: ${vol:,.0f}")

    if tournament:
        lines.append(f"  Tournament: {tournament}")

    lines.append(f"  Odds: {team_a} {odds_a} | {odds_b} {team_b}")

    return lines


def render_non_match_lines(event_dict, i, mode):
    """
    Render a formatted non-match event dict into lines of text.

    Args:
        event_dict: canonical dict from format_non_match_event()
        i: 1-based index for the event number
        mode: "text" for plain text/Markdown, "html" for Telegram HTML

    Returns:
        List[str], one line per element (no trailing blank line).
    """
    title = event_dict["title"]
    url = event_dict["url"]
    abs_time = event_dict["abs_time"]
    time_status = event_dict["time_status"]
    market_count = event_dict["market_count"]
    total_vol = event_dict["total_vol"]

    lines = []

    if mode == "html":
        lines.append(f"<b>{i}.</b> <a href=\"{url}\">{escape_html(title)}</a>")
    else:
        lines.append(f"{i}. [{title}]({url})")

    lines.append(f"   {abs_time} | {time_status}")
    lines.append(f"   Markets: {market_count} | Total Vol: ${total_vol:,.0f}")

    return lines


# ============================================================
# FORMAT — LEGACY
# ============================================================

def format_event(e):
    ml = get_ml_market(e)
    outcomes = json.loads(ml.get("outcomes", "[]")) if ml else []
    prices = json.loads(ml.get("outcomePrices", "[]")) if ml else []
    best_bid = float(ml.get("bestBid", 0)) if ml else 0
    best_ask = float(ml.get("bestAsk", 0)) if ml else 0
    vol = get_ml_volume(e)
    td = _get_time_data(e)

    return {
        "title": e.get("title", ""),
        "time_status": td["time_status"],
        "time_urgency": td["time_urgency"],
        "url": get_event_url(e),
        "livestream": e.get("resolutionSource"),
        "outcomes": outcomes,
        "prices": prices,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "volume": vol,
    }

def format_detail_event(e):
    ml = get_ml_market(e)
    
    active_markets = [
        m for m in e.get("markets", [])
        if float(m.get("volume", 0)) > 0 and is_tradeable_market(m)
    ]
    active_markets = sorted(active_markets, key=lambda m: float(m.get("volume", 0)), reverse=True)

    td = _get_time_data(e)

    return {
        "title": e.get("title", ""),
        "time_status": td["time_status"],
        "abs_time": td["abs_time"],
        "url": get_event_url(e),
        "livestream": e.get("resolutionSource"),
        "outcomes": json.loads(ml.get("outcomes", "[]")) if ml else [],
        "prices": json.loads(ml.get("outcomePrices", "[]")) if ml else [],
        "best_bid": float(ml.get("bestBid", 0)) if ml else 0,
        "best_ask": float(ml.get("bestAsk", 0)) if ml else 0,
        "volume": get_ml_volume(e),
        "markets": [
            {
                "type": m.get("sportsMarketType"),
                "question": m.get("question", ""),
                "outcomes": json.loads(m.get("outcomes", "[]")),
                "prices": json.loads(m.get("outcomePrices", "[]")),
                "best_bid": float(m.get("bestBid", 0)),
                "best_ask": float(m.get("bestAsk", 0)),
                "volume": float(m.get("volume", 0)),
                "url": f"https://polymarket.com/market/{m.get('slug')}",
            }
            for m in active_markets
        ],
    }

# ============================================================
# DISPLAY
# ============================================================

def get_header_date():
    """Return current date string like 'Mar 25, 2026'"""
    now_utc = datetime.now(timezone.utc)
    utc7 = timezone(timedelta(hours=7))
    now_utc7 = now_utc.astimezone(utc7)
    return now_utc7.strftime("%b %d, %Y")

def get_tournament(title):
    """Extract tournament name from event title. Title format: 'Category: Team A vs Team B (BO/X) - Tournament Name'"""
    if " - " in title:
        parts = title.split(" - ")
        if len(parts) > 1:
            return " - ".join(parts[1:]).strip()
    return ""

def print_browse(match_events, non_match_events, category, total_raw, total_fetched, total_match, total_non_match, raw_mode=False, partial=False, non_matches_max=5, matches_only=False, non_matches_only=False):
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    utc7 = timezone(timedelta(hours=7))
    now_utc7 = now_utc.astimezone(utc7)
    header_date = get_header_date()

    print(f"\n=== {category.upper()}{' [RAW]' if raw_mode else ''} ===")
    print(f"Current time (WIB): {now_utc7.strftime('%H:%M WIB')} | {header_date}")

    if raw_mode:
        print(f"Fetched: {total_fetched} / Total API: {total_raw} | Match: {total_match} | Non-match: {total_non_match}")
    if partial:
        print(f"WARNING: Partial fetch (API error or timeout) — data may be incomplete")

    # Determine sections to show
    if not matches_only and not non_matches_only:
        show_matches = True
        show_non_matches = True
    elif matches_only:
        show_matches = True
        show_non_matches = False
    else:
        show_matches = False
        show_non_matches = True

    # Match events
    if show_matches:
        print("\nMATCH MARKETS")
        if not match_events:
            print("  No match markets found.")
        else:
            for i, e in enumerate(match_events, 1):
                fd = format_match_event(e)
                for line in render_match_lines(fd, i, mode="text"):
                    print(line)

    # Non-match events
    if show_non_matches and non_match_events:
        print("\nNON-MATCH MARKETS")
        for i, e in enumerate(non_match_events[:non_matches_max], 1):
            fd = format_non_match_event(e)
            for line in render_non_match_lines(fd, i, mode="text"):
                print(line)

def print_detail(e, detail):
    print(f"\n{detail['title']}")
    print(f"URL: {detail['url']}")
    print(f"Livestream: {detail['livestream']}")

    spread_str = format_spread(detail["best_bid"], detail["best_ask"]) if detail["best_bid"] and detail["best_ask"] else "N/A"
    print(f"\n{detail['time_status']}")
    print(f"ML: {detail['outcomes'][0]} {format_odds(float(detail['prices'][0]))} vs {detail['outcomes'][1]} {format_odds(float(detail['prices'][1]))}")
    print(f"ML Vol: ${detail['volume']:,.0f} | {spread_str}")
    
    print(f"\nMarkets ({len(detail['markets'])}):")
    for m in detail["markets"]:
        spread_str = format_spread(m["best_bid"], m["best_ask"]) if m["best_bid"] and m["best_ask"] else "N/A"
        print(f"  [{m['type']}]")
        print(f"    {m['outcomes'][0]} {format_odds(float(m['prices'][0]))} vs {m['outcomes'][1]} {format_odds(float(m['prices'][1]))}")
        print(f"    Vol: ${m['volume']:,.0f} | {spread_str}")
        print(f"    URL: {m['url']}")

# ============================================================
# TELEGRAM
# ============================================================

def escape_html(text):
    """Escape HTML-sensitive characters for Telegram parse_mode=HTML."""
    return (text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;"))


def send_telegram_message(bot_token, chat_id, text, timeout=10):
    """Send a message via Telegram bot API. Returns the message ID on success.

    Raises:
        RuntimeError: If the Telegram API returns an error (e.g. invalid token, rate limit).
        URLError/HTTPError: On network or HTTP-level failures.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = Request(url, data=data, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API error: {result.get('description')}")
        return result["result"]["message_id"]


def send_to_telegram(match_events, non_match_events, category, matches_only=False, non_matches_only=False):
    """Send browse results to Telegram. Reads TELEGRAM_BOT_TOKEN and CHAT_ID from environment."""
    import os
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if not bot_token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN or CHAT_ID not set in environment")

    from datetime import datetime, timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    utc7 = timezone(timedelta(hours=7))
    now_utc7 = now_utc.astimezone(utc7)
    header_date = now_utc7.strftime("%b %d, %Y")

    # Determine sections to show
    show_matches = (not matches_only and not non_matches_only) or matches_only
    show_non_matches = (not matches_only and not non_matches_only) or non_matches_only

    def send(text):
        msg_id = send_telegram_message(bot_token, chat_id, text)
        print(f"  Sent msg {msg_id}")

    # Build lines
    lines = [f"<b>{category.upper()}</b> | {header_date}", ""]

    if show_matches:
        lines += ["MATCH MARKETS", ""]
        if not match_events:
            lines.append("  No match markets found.")
        else:
            for i, e in enumerate(match_events, 1):
                fd = format_match_event(e)
                lines += render_match_lines(fd, i, mode="html")
                lines.append("")
        lines.append("")

    if show_non_matches:
        lines += ["NON-MATCH MARKETS", ""]
        if not non_match_events:
            lines.append("  No non-match markets found.")
        else:
            for i, e in enumerate(non_match_events, 1):
                fd = format_non_match_event(e)
                lines += render_non_match_lines(fd, i, mode="html")
                lines.append("")
        lines.append("")

    # Chunk and send
    send_chunked(lines, send, category, header_date, show_matches, show_non_matches)


def send_chunked(all_lines, send_fn, category, header_date, show_matches, show_non_matches):
    """
    Split already-built lines into Telegram-safe chunks and send them.

    Telegram messages are capped at 4096 chars. Chunks are grouped by
    section header so no event is split across messages.

    Args:
        all_lines: Full message lines list (built by caller).
        send_fn: Closure that sends a single string and prints confirmation.
        category: Category name for header.
        header_date: Date string for header.
        show_matches: Whether MATCH MARKETS section is present.
        show_non_matches: Whether NON-MATCH MARKETS section is present.
    """
    text = "\n".join(all_lines)
    if len(text) <= 4096:
        send_fn(text)
        return

    # Split into chunks of 10 events, respecting section headers
    all_items = []
    in_match = True
    for line in all_lines:
        if line == "MATCH MARKETS":
            in_match = True
        elif line == "NON-MATCH MARKETS":
            in_match = False
        elif line.startswith("<b>") and "</a>" in line:
            # Event title line: <b>1.</b> <a href="...">Title</a>
            all_items.append((in_match, line))

    chunk = []
    header = f"<b>{category.upper()}</b> | {header_date}\n"
    if show_matches:
        header += "\nMATCH MARKETS\n\n"
    if show_non_matches:
        header += "\nNON-MATCH MARKETS\n\n"

    for is_match, item_line in all_items:
        test_chunk = chunk + [item_line, ""]
        test_text = header + "\n".join(chunk) + "\n".join(test_chunk)
        if len(test_text) > 4096 or len(chunk) >= 10:
            msg = header + "\n".join(chunk)
            send_fn(msg)
            chunk = [item_line, ""]
            header = f"<b>{category.upper()}</b> (cont.) | {header_date}\n"
            if show_matches and is_match:
                header += "\nMATCH MARKETS\n\n"
            elif show_non_matches and not is_match:
                header += "\nNON-MATCH MARKETS\n\n"
        else:
            chunk.extend([item_line, ""])

    if chunk:
        msg = header + "\n".join(chunk)
        send_fn(msg)


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Browse Polymarket tradeable events by game category.")
    parser.add_argument("--category", default="Counter Strike", 
                       choices=list(GAME_CATEGORIES.keys()),
                       help="Game category to browse")
    parser.add_argument("--limit", type=int, default=5,
                       help="Max events per section (match + non-match). Default: 5")
    parser.add_argument("--matches", type=int, default=None,
                       help="Max match markets to show. Default: --limit")
    parser.add_argument("--non-matches", type=int, default=None,
                       help="Max non-match markets to show. Default: --limit")
    parser.add_argument("--search", type=str, default=None,
                       help="Free-text team/term search within the selected category. Overrides default query.")
    parser.add_argument("--matches-only", action="store_true",
                       help="Show only match markets (suppress non-match section).")
    parser.add_argument("--non-matches-only", action="store_true",
                       help="Show only non-match markets (suppress match section).")
    parser.add_argument("--list-categories", action="store_true",
                       help="List available game categories and exit")
    parser.add_argument("--detail", type=int, default=1,
                       help="Index of match event (1-indexed) to show detailed markets. Default: 1. Set to 0 to disable.")
    parser.add_argument("--raw", action="store_true",
                       help="Show all events without tradeable filter (for debugging).")
    parser.add_argument("--telegram", action="store_true",
                       help="Send results to Telegram (TELEGRAM_BOT_TOKEN and CHAT_ID must be set in environment).")
    args = parser.parse_args()
    
    if args.list_categories:
        print("Available categories:")
        for name in GAME_CATEGORIES:
            print(f"  - {name}")
        return
    
    category_term = GAME_CATEGORIES[args.category]
    search_term = f"{category_term} {args.search}" if args.search else category_term
    tradeable_only = not args.raw
    matches_max = args.matches if args.matches is not None else args.limit
    non_matches_max = args.non_matches if args.non_matches is not None else args.limit
    
    if args.search:
        print(f"\nFetching {args.category} events matching '{args.search}'...")
    else:
        print(f"\nFetching {args.category} events...")
    
    result = browse_events(search_term, matches_max=matches_max, non_matches_max=non_matches_max, tradeable_only=tradeable_only)
    
    print_browse(
        result["match_events"],
        result["non_match_events"],
        args.category,
        result["total_raw"],
        result["total_fetched"],
        result["total_match"],
        result["total_non_match"],
        raw_mode=args.raw,
        partial=result.get("partial", False),
        non_matches_max=non_matches_max,
        matches_only=args.matches_only,
        non_matches_only=args.non_matches_only
    )
    
    # Print detail for selected event if any
    if result["match_events"] and args.detail > 0:
        print("\n")
        idx = args.detail - 1
        if idx < 0 or idx >= len(result["match_events"]):
            idx = 0
        detail_event = result["match_events"][idx]
        detail = format_detail_event(detail_event)
        print_detail(detail_event, detail)
    
    # Send to Telegram if requested
    if args.telegram:
        send_to_telegram(
            result["match_events"],
            result["non_match_events"],
            args.category,
            matches_only=args.matches_only,
            non_matches_only=args.non_matches_only
        )

if __name__ == "__main__":
    main()
