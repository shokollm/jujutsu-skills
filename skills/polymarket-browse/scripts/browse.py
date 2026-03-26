#!/usr/bin/env python3
"""
Polymarket Event Browser
Browse tradeable Polymarket events by game category.
"""

import html
import json
import time
import argparse
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, TypedDict
from urllib.parse import urlencode
from urllib.request import urlopen, Request


class TimeData(TypedDict):
    time_status: str
    time_urgency: int
    abs_time: str


class MatchEvent(TypedDict):
    title: str
    title_clean: str
    tournament: str
    url: str
    time_status: str
    time_urgency: int
    abs_time: str
    team_a: str
    team_b: str
    odds_a: str
    odds_b: str
    vol: float


class NonMatchEvent(TypedDict):
    title: str
    url: str
    time_status: str
    time_urgency: int
    abs_time: str
    market_count: int
    total_vol: int


class Market(TypedDict):
    type: str
    question: str
    outcomes: list[str]
    prices: list[str]
    best_bid: float
    best_ask: float
    volume: float
    url: str


class DetailEvent(TypedDict):
    title: str
    time_status: str
    abs_time: str
    url: str
    livestream: str | None
    outcomes: list[str]
    prices: list[str]
    best_bid: float
    best_ask: float
    volume: float
    markets: list[Market]


class BrowseResult(TypedDict):
    query: str
    total_raw: int
    total_fetched: int
    total_match: int
    total_non_match: int
    match_events: list[Any]
    non_match_events: list[Any]
    partial: bool


class FetchResult(TypedDict):
    events: list[Any]
    total_raw: int
    partial: bool


# ============================================================
# CONFIG
# ============================================================

PAGE_SIZE = 50
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 2  # exponential backoff starts at 2s
WIB = timezone(timedelta(hours=7))  # UTC+7 for Indonesian users
_DISPLAY_TZ = WIB  # Module-level timezone for display (configurable via --timezone)


def parse_timezone(tz_str: str) -> timezone:
    """
    Parse timezone string to datetime.timezone.
    Supports: UTC offset format (UTC+7, UTC-5).
    Falls back to WIB (UTC+7) on parse failure.
    """
    tz_str = tz_str.strip()
    if tz_str.startswith("UTC"):
        offset_str = tz_str[3:].strip()
        if not offset_str:
            return timezone.utc
        sign = -1 if offset_str[0] == "-" else 1
        if offset_str[0] in "+-":
            offset_str = offset_str[1:]
        try:
            if ":" in offset_str:
                hours, minutes = offset_str.split(":")
                hours = int(hours)
                minutes = int(minutes)
            else:
                hours = int(offset_str)
                minutes = 0
            total_minutes = hours * 60 + minutes
            if sign == -1:
                total_minutes = -total_minutes
            return timezone(timedelta(minutes=total_minutes))
        except ValueError:
            return WIB
    return WIB
    try:
        from datetime import ZoneInfo

        return ZoneInfo(tz_str).utcoffset(None)
    except Exception:
        return WIB


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

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "polymarket-browse")
CACHE_TTL = 300  # 5 minutes default
MAX_PARALLEL_FETCHES = 5

# ============================================================
# CACHE
# ============================================================


def _get_cache_key(q: str) -> str:
    return hashlib.md5(q.encode()).hexdigest()


def _get_cache_path(q: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{_get_cache_key(q)}.json")


def _read_cache(q: str) -> dict[str, Any] | None:
    cache_path = _get_cache_path(q)
    if not os.path.exists(cache_path):
        return None
    try:
        mtime = os.path.getmtime(cache_path)
        age = time.time() - mtime
        if age > CACHE_TTL:
            return None
        with open(cache_path) as f:
            return json.load(f)
    except Exception:
        return None


def _write_cache(q: str, data: dict[str, Any]) -> None:
    try:
        cache_path = _get_cache_path(q)
        with open(cache_path, "w") as f:
            json.dump(data, f)
    except Exception:
        pass


# ============================================================
# FETCH
# ============================================================


def fetch_page(
    q: str,
    page: int = 1,
    max_retries: int = MAX_RETRIES,
    initial_delay: float = INITIAL_RETRY_DELAY,
) -> dict[str, Any] | None:
    base = "https://gamma-api.polymarket.com/public-search"
    url = (
        f"{base}?q={q.replace(' ', '%20')}&limit={PAGE_SIZE}&page={page}"
        f"&search_profiles=false&search_tags=false"
        f"&keep_closed_markets=0&events_status=active&cache=false"
    )

    delay = initial_delay
    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(delay)
        try:
            req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except Exception:
            if attempt < max_retries - 1:
                delay *= 2
                continue
            return None
    return None


def _fetch_page_with_index(q: str, page: int) -> tuple[int, dict[str, Any] | None]:
    return page, fetch_page(q, page)


def fetch_all_pages(
    q: str,
    matches_max: int | None = None,
    non_matches_max: int | None = None,
    max_total: int | None = None,
    use_cache: bool = True,
) -> FetchResult:
    """
    Fetch pages until pagination ends, or until quotas are satisfied.

    Args:
        q: search query
        matches_max: stop early once we have this many match events (None = no limit)
        non_matches_max: stop early once we have this many non-match events (None = no limit)
        max_total: stop early once we have this many total events (None = no limit)
        use_cache: whether to use cache (default True)

    Returns:
        FetchResult with events, total_raw, and partial flag
    """
    cached = _read_cache(q) if use_cache else None
    if cached is not None:
        events = cached.get("events", [])
        total_raw = cached.get("total_raw", 0)
        if events:
            return {"events": events, "total_raw": total_raw, "partial": False}

    total_raw = 0
    page_count = 0
    page1_data = None

    while True:
        page_count += 1
        data = fetch_page(q, page_count)
        if data is None:
            break
        total_raw = data.get("pagination", {}).get("totalResults", 0)
        if page_count == 1:
            page1_data = data
        if total_raw > 0:
            break
        if not data.get("events"):
            break

    if total_raw == 0 or page1_data is None:
        return {"events": [], "total_raw": 0, "partial": False}

    page1_events = page1_data.get("events", [])
    actual_page_size = len(page1_events)

    # Use actual events per page from API for ceiling division
    # ceil(total_raw / actual_page_size) = (total_raw + actual_page_size - 1) // actual_page_size
    total_pages = (total_raw + actual_page_size - 1) // actual_page_size
    concurrency = min(MAX_PARALLEL_FETCHES, total_pages)

    all_page_data: dict[int, list[Any]] = {1: page1_events}

    if total_pages > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {
                executor.submit(_fetch_page_with_index, q, page): page
                for page in range(2, total_pages + 1)
            }
            for future in as_completed(futures):
                try:
                    page_num, data = future.result()
                    if data is not None:
                        all_page_data[page_num] = data.get("events", [])
                except Exception:
                    pass

    all_events = []
    for page_num in sorted(all_page_data.keys()):
        all_events.extend(all_page_data[page_num])

    _write_cache(q, {"events": all_events, "total_raw": total_raw})

    match_count = 0
    non_match_count = 0
    filtered_events = []
    total_seen = 0

    for e in all_events:
        is_match = is_match_market(e)
        if is_match:
            match_count += 1
        else:
            non_match_count += 1

        filtered_events.append(e)

        if matches_max is not None and non_matches_max is not None:
            if match_count >= matches_max and non_match_count >= non_matches_max:
                break

        if max_total is not None:
            total_seen += 1
            if total_seen >= max_total:
                break

    partial = len(all_events) < total_raw
    return {"events": filtered_events, "total_raw": total_raw, "partial": partial}


# ============================================================
# FILTERS
# ============================================================


def is_match_market(e: dict[str, Any]) -> bool:
    return (e.get("seriesSlug") and e.get("gameId")) or " vs " in e.get("title", "")


def get_event_url(e: dict[str, Any]) -> str:
    """Return the correct Polymarket URL for an event.
    Match markets use /market/, non-match events use /event/.
    """
    slug = e.get("slug", "")
    if is_match_market(e):
        return f"https://polymarket.com/market/{slug}"
    else:
        return f"https://polymarket.com/event/{slug}"


def get_ml_market(e: dict[str, Any]) -> dict[str, Any] | None:
    for m in e.get("markets", []):
        if m.get("sportsMarketType") == "moneyline":
            return m
    return None


def get_ml_volume(e: dict[str, Any]) -> float:
    ml = get_ml_market(e)
    return float(ml.get("volume", 0)) if ml else 0.0


def is_bo2_tie(e: dict[str, Any]) -> bool:
    """
    Detect if this is a BO2 that ended in a tie (1-1).
    Returns True if all child_moneyline markets are closed (match is over but tied).
    """
    title = e.get("title", "")
    if "BO2" not in title:
        return False

    child_markets = [
        m
        for m in e.get("markets", [])
        if m.get("sportsMarketType") == "child_moneyline"
    ]
    if len(child_markets) != 2:
        return False

    # If both child markets are closed, it's likely a finished BO2
    all_closed = all(m.get("closed", False) for m in child_markets)
    return all_closed


def is_tradeable_event(e: dict[str, Any]) -> bool:
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
            end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if end_dt < now:
                return False
        except:
            pass

    # Filter: match has already started (startTime is in the past)
    start_str = e.get("startTime") or e.get("startDate", "")
    if start_str:
        try:
            start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            if start_dt < now:
                # Check if it's recently started (within 4h) — consider those "live" still
                hours_ago = (now - start_dt).total_seconds() / 3600
                if hours_ago > 4:
                    return False
        except:
            pass

    return True


def is_tradeable_market(m: dict[str, Any]) -> bool:
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


def prob_to_cents(p: float) -> int:
    return int(round(p * 100))


def format_odds(p: float) -> str:
    return f"{prob_to_cents(p)}c"


def format_spread(bid: float, ask: float) -> str:
    spread = ask - bid
    return f"{prob_to_cents(spread)}c"


def _get_time_data(e: dict[str, Any], tz: timezone | None = None) -> TimeData:
    """
    Unified time data extraction for event timestamps.

    Uses startTime (preferred) or startDate as the event start time.
    Datetime parsing and all relative calculations are UTC-based.
    The tz parameter only affects the abs_time formatting.

    Args:
        e: Event dict with 'startTime' or 'startDate' key.
        tz: datetime.timezone for abs_time formatting.
            Defaults to _DISPLAY_TZ (set via --timezone, or WIB).

    Returns:
        TimeData with time_status, time_urgency, and abs_time
    """
    tz = tz or _DISPLAY_TZ
    start_str = e.get("startTime") or e.get("startDate", "")

    if not start_str:
        return {"time_status": "TBD", "time_urgency": 0, "abs_time": "TBD"}

    try:
        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        now_utc = datetime.now(timezone.utc)
        delta = start_dt - now_utc
        total_sec = delta.total_seconds()

        if total_sec <= 0:
            # Event is in the past or happening now
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
        return {
            "time_status": time_status,
            "time_urgency": time_urgency,
            "abs_time": abs_time,
        }
    except Exception:
        return {"time_status": "", "time_urgency": 0, "abs_time": "TBD"}


def filter_events(
    events: list[dict[str, Any]], tradeable_only: bool = True
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Classify events into match_markets and non_match_markets.
    If tradeable_only=True, also filter out non-tradeable events.
    """
    match_events: list[dict[str, Any]] = []
    non_match_events: list[dict[str, Any]] = []

    for e in events:
        if is_match_market(e):
            if not tradeable_only or is_tradeable_event(e):
                match_events.append(e)
        else:
            non_match_events.append(e)

    return match_events, non_match_events


def sort_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(events, key=get_ml_volume, reverse=True)


# ============================================================
# BROWSE
# ============================================================


def browse_events(
    q: str,
    matches_max: int = 10,
    non_matches_max: int = 10,
    tradeable_only: bool = True,
    sort_by: str | None = None,
    max_total: int | None = None,
    use_cache: bool = True,
) -> BrowseResult:
    """
    Browse Polymarket events.

    Args:
        q: search query
        matches_max: max number of match markets to return
        non_matches_max: max number of non-match markets to return
        tradeable_only: filter to tradeable events only
        sort_by: None (fast, API order) or "volume" (full fetch, sort by volume desc)
        max_total: max total events to fetch before early exit (None = no limit)
        use_cache: whether to use cache (default True)
    """
    use_early_exit = sort_by is None
    fetch_matches_max = matches_max if use_early_exit else None
    fetch_non_matches_max = non_matches_max if use_early_exit else None

    result = fetch_all_pages(
        q,
        matches_max=fetch_matches_max,
        non_matches_max=fetch_non_matches_max,
        max_total=max_total,
        use_cache=use_cache,
    )
    events = result["events"]
    match_events, non_match_events = filter_events(events, tradeable_only)

    # Sort if requested; otherwise preserve API order
    if sort_by == "volume":
        match_events = sort_events(match_events)
        non_match_events = sort_events(non_match_events)

    return {
        "query": q,
        "total_raw": result["total_raw"],
        "total_fetched": len(events),
        "total_match": len(match_events),
        "total_non_match": len(non_match_events),
        "match_events": match_events[:matches_max],
        "non_match_events": non_match_events[:non_matches_max],
        "partial": result.get("partial", False),
    }


# ============================================================
# FORMAT — EVENT
# ============================================================


def format_match_event(e: dict[str, Any]) -> MatchEvent:
    """
    Format a match event into a canonical dict for rendering.
    All computing done here; renderers just template.

    Returns:
        MatchEvent with all required fields
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


def format_non_match_event(e: dict[str, Any]) -> NonMatchEvent:
    """
    Format a non-match event into a canonical dict for rendering.

    Returns:
        NonMatchEvent with all required fields
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


def render_match_lines(event_dict: MatchEvent, i: int, mode: str) -> list[str]:
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
        lines.append(f'<b>{i}.</b> <a href="{url}">{escape_html(title_clean)}</a>')
    else:
        lines.append(f"{i}. [{title_clean}]({url})")

    lines.append(f"   {abs_time} | {time_status}")
    lines.append(f"  Vol: ${vol:,.0f}")

    if tournament:
        lines.append(f"  Tournament: {tournament}")

    lines.append(f"  Odds: {team_a} {odds_a} | {odds_b} {team_b}")

    return lines


def render_non_match_lines(event_dict: NonMatchEvent, i: int, mode: str) -> list[str]:
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
        lines.append(f'<b>{i}.</b> <a href="{url}">{escape_html(title)}</a>')
    else:
        lines.append(f"{i}. [{title}]({url})")

    lines.append(f"   {abs_time} | {time_status}")
    lines.append(f"   Markets: {market_count} | Total Vol: ${total_vol:,.0f}")

    return lines


# ============================================================
# FORMAT — LEGACY
# ============================================================


def format_event(e: dict[str, Any]) -> dict[str, Any]:
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


def format_detail_event(e: dict[str, Any]) -> DetailEvent:
    ml = get_ml_market(e)

    active_markets = [
        m
        for m in e.get("markets", [])
        if float(m.get("volume", 0)) > 0 and is_tradeable_market(m)
    ]
    active_markets = sorted(
        active_markets, key=lambda m: float(m.get("volume", 0)), reverse=True
    )

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


def get_header_date() -> str:
    """Return current date string like 'Mar 25, 2026' in display timezone."""
    now_utc = datetime.now(timezone.utc)
    now_display = now_utc.astimezone(_DISPLAY_TZ)
    return now_display.strftime("%b %d, %Y")


def get_tournament(title: str) -> str:
    """Extract tournament name from event title. Title format: 'Category: Team A vs Team B (BO/X) - Tournament Name'"""
    if " - " in title:
        parts = title.split(" - ")
        if len(parts) > 1:
            return " - ".join(parts[1:]).strip()
    return ""


def print_browse(
    match_events,
    non_match_events,
    category,
    total_raw,
    total_fetched,
    total_match,
    total_non_match,
    raw_mode=False,
    partial=False,
    non_matches_max=5,
    matches_only=False,
    non_matches_only=False,
):
    from datetime import datetime, timezone, timedelta

    now_utc = datetime.now(timezone.utc)
    utc7 = timezone(timedelta(hours=7))
    now_utc7 = now_utc.astimezone(utc7)
    header_date = get_header_date()

    print(f"\n=== {category.upper()}{' [RAW]' if raw_mode else ''} ===")
    print(f"Current time (WIB): {now_utc7.strftime('%H:%M WIB')} | {header_date}")

    if raw_mode:
        print(
            f"Fetched: {total_fetched} / Total API: {total_raw} | Match: {total_match} | Non-match: {total_non_match}"
        )
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


def print_detail(e: dict[str, Any], detail: DetailEvent) -> None:
    print(f"\n{detail['title']}")
    print(f"URL: {detail['url']}")
    print(f"Livestream: {detail['livestream']}")

    spread_str = (
        format_spread(detail["best_bid"], detail["best_ask"])
        if detail["best_bid"] and detail["best_ask"]
        else "N/A"
    )
    print(f"\n{detail['time_status']}")
    print(
        f"ML: {detail['outcomes'][0]} "
        f"{format_odds(float(detail['prices'][0]))} vs "
        f"{detail['outcomes'][1]} {format_odds(float(detail['prices'][1]))}"
    )
    print(f"ML Vol: ${detail['volume']:,.0f} | {spread_str}")

    print(f"\nMarkets ({len(detail['markets'])}):")
    for m in detail["markets"]:
        spread_str = (
            format_spread(m["best_bid"], m["best_ask"])
            if m["best_bid"] and m["best_ask"]
            else "N/A"
        )
        print(f"  [{m['type']}]")
        print(
            f"    {m['outcomes'][0]} "
            f"{format_odds(float(m['prices'][0]))} vs "
            f"{m['outcomes'][1]} {format_odds(float(m['prices'][1]))}"
        )
        print(f"    Vol: ${m['volume']:,.0f} | {spread_str}")
        print(f"    URL: {m['url']}")


# ============================================================
# TELEGRAM
# ============================================================


def escape_html(text: str) -> str:
    """Escape HTML-sensitive characters for Telegram parse_mode=HTML."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def send_telegram_message(
    bot_token: str, chat_id: str, text: str, timeout: int = 10
) -> int:
    """Send a message via Telegram bot API. Returns the message ID on success.

    Raises:
        RuntimeError: If the Telegram API returns an error (e.g. invalid token, rate limit).
        URLError/HTTPError: On network or HTTP-level failures.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    req = Request(url, data=data, method="POST")
    with urlopen(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
        if not result.get("ok"):
            raise RuntimeError(f"Telegram API error: {result.get('description')}")
        return result["result"]["message_id"]


def send_to_telegram(
    match_events: list[dict[str, Any]],
    non_match_events: list[dict[str, Any]],
    category: str,
    matches_only: bool = False,
    non_matches_only: bool = False,
) -> None:
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


def send_chunked(
    all_lines: list[str],
    send_fn: Callable[[str], None],
    category: str,
    header_date: str,
    show_matches: bool,
    show_non_matches: bool,
) -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Browse Polymarket tradeable events by game category."
    )
    parser.add_argument(
        "--category",
        default="Counter Strike",
        choices=list(GAME_CATEGORIES.keys()),
        help="Game category to browse",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Max events per section (match + non-match). Default: 5",
    )
    parser.add_argument(
        "--matches",
        type=int,
        default=None,
        help="Max match markets to show. Default: --limit",
    )
    parser.add_argument(
        "--non-matches",
        type=int,
        default=None,
        help="Max non-match markets to show. Default: --limit",
    )
    parser.add_argument(
        "--search",
        type=str,
        default=None,
        help="Free-text team/term search within the selected category. Overrides default query.",
    )
    parser.add_argument(
        "--matches-only",
        action="store_true",
        help="Show only match markets (suppress non-match section).",
    )
    parser.add_argument(
        "--non-matches-only",
        action="store_true",
        help="Show only non-match markets (suppress match section).",
    )
    parser.add_argument(
        "--list-categories",
        action="store_true",
        help="List available game categories and exit",
    )
    parser.add_argument(
        "--detail",
        type=int,
        default=1,
        help="Index of match event (1-indexed) to show detailed markets. Default: 1. Set to 0 to disable.",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Show all events without tradeable filter (for debugging).",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable cache and fetch fresh data from API.",
    )
    parser.add_argument(
        "--max-total",
        type=int,
        default=None,
        help="Max total events to fetch before early exit. Default: no limit.",
    )
    parser.add_argument(
        "--timezone",
        type=str,
        default="UTC+7",
        help="Timezone for displaying times (e.g., UTC+7, UTC-5). Default: UTC+7",
    )
    parser.add_argument(
        "--telegram",
        action="store_true",
        help="Send results to Telegram (TELEGRAM_BOT_TOKEN and CHAT_ID must be set in environment).",
    )
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

    global _DISPLAY_TZ
    _DISPLAY_TZ = parse_timezone(args.timezone)

    if args.search:
        print(f"\nFetching {args.category} events matching '{args.search}'...")
    else:
        print(f"\nFetching {args.category} events...")

    result = browse_events(
        search_term,
        matches_max=matches_max,
        non_matches_max=non_matches_max,
        tradeable_only=tradeable_only,
        max_total=args.max_total,
        use_cache=not args.no_cache,
    )

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
        non_matches_only=args.non_matches_only,
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
            non_matches_only=args.non_matches_only,
        )


if __name__ == "__main__":
    main()
