#!/usr/bin/env python3
"""
Polymarket Event Browser
Browse tradeable Polymarket events by game category.
"""

import subprocess
import json
import time
import argparse
from datetime import datetime, timezone, timedelta

# ============================================================
# CONFIG
# ============================================================

PAGE_SIZE = 50
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 2  # exponential backoff starts at 2s

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

def get_match_time_status(e):
    """
    Return a human-readable match time status.
    Returns (status_str, urgency) where urgency is 0-3 (higher = more urgent/live).
    Uses startTime for actual match start time.
    Displays times in WIB (UTC+7 for Indonesian users).
    """
    # Use startTime for actual match start, not startDate (which is market creation time)
    start_str = e.get("startTime") or e.get("startDate", "")
    
    if not start_str:
        return "TBD", 0
    
    try:
        start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
        now_utc = datetime.now(timezone.utc)
        utc7 = timezone(timedelta(hours=7))
        now = now_utc.astimezone(utc7)
        start_utc7 = start_dt.astimezone(utc7)
        
        delta = start_dt - now_utc
        
        if delta.total_seconds() < 0:
            # Started already
            hours_ago = abs(delta.total_seconds()) / 3600
            if hours_ago < 1:
                return "LIVE", 3
            elif hours_ago < 4:
                return f"LIVE {int(hours_ago)}h", 3
            elif hours_ago < 24:
                return f"Started {int(hours_ago)}h ago", 1
            else:
                days = int(hours_ago / 24)
                return f"{days}d ago", 0
        else:
            # Starts in future
            hours_until = delta.total_seconds() / 3600
            if hours_until < 1:
                mins = int(delta.total_seconds() / 60)
                return f"In {mins}m", 3
            elif hours_until < 24:
                return f"In {int(hours_until)}h", 2
            else:
                days = int(hours_until / 24)
                return f"In {days}d", 1
    except:
        return "", 0

def get_match_time_str(e):
    """
    Return just the time status string (e.g. 'LIVE', 'In 6h', 'In 1d').
    Uses startTime for actual match start time.
    """
    start_str = e.get("startTime") or e.get("startDate", "")
    if not start_str:
        return "TBD"
    try:
        start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
        now_utc = datetime.now(timezone.utc)
        delta = start_dt - now_utc
        
        if delta.total_seconds() < 0:
            hours_ago = abs(delta.total_seconds()) / 3600
            if hours_ago < 1:
                return "LIVE"
            elif hours_ago < 4:
                return f"LIVE {int(hours_ago)}h"
            elif hours_ago < 24:
                return f"{int(hours_ago)}h ago"
            else:
                days = int(hours_ago / 24)
                return f"{days}d ago"
        else:
            hours_until = delta.total_seconds() / 3600
            if hours_until < 1:
                mins = int(delta.total_seconds() / 60)
                return f"In {mins}m"
            elif hours_until < 24:
                return f"In {int(hours_until)}h"
            else:
                days = int(hours_until / 24)
                return f"In {days}d"
    except:
        return ""

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
# FORMAT
# ============================================================

def format_event(e):
    ml = get_ml_market(e)
    outcomes = json.loads(ml.get("outcomes", "[]")) if ml else []
    prices = json.loads(ml.get("outcomePrices", "[]")) if ml else []
    best_bid = float(ml.get("bestBid", 0)) if ml else 0
    best_ask = float(ml.get("bestAsk", 0)) if ml else 0
    vol = get_ml_volume(e)
    time_status, urgency = get_match_time_status(e)
    
    return {
        "title": e.get("title", ""),
        "time_status": time_status,
        "time_urgency": urgency,
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
    
    time_status, urgency = get_match_time_status(e)
    
    return {
        "title": e.get("title", ""),
        "time_status": time_status,
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

def get_start_time_wib(e):
    """Return (date_time_str, relative_str) for display."""
    start_str = e.get("startTime") or e.get("startDate", "")
    if not start_str:
        return "TBD", ""
    try:
        start_dt = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
        now_utc = datetime.now(timezone.utc)
        utc7 = timezone(timedelta(hours=7))
        start_utc7 = start_dt.astimezone(utc7)
        
        # Absolute: "Mar 25, 19:00 WIB"
        abs_str = start_utc7.strftime("%b %d, %H:%M WIB")
        
        # Relative: "In 5h", "In 10h", "LIVE", etc.
        delta = start_dt - now_utc
        if delta.total_seconds() < 0:
            hours_ago = abs(delta.total_seconds()) / 3600
            if hours_ago < 1:
                rel_str = "LIVE"
            elif hours_ago < 24:
                rel_str = f"{int(hours_ago)}h ago"
            else:
                days = int(hours_ago / 24)
                rel_str = f"{days}d ago"
        else:
            hours_until = delta.total_seconds() / 3600
            if hours_until < 1:
                mins_until = int(delta.total_seconds() / 60)
                rel_str = f"In {mins_until}m"
            elif hours_until < 24:
                rel_str = f"In {int(hours_until)}h"
            else:
                days = int(hours_until / 24)
                rel_str = f"In {days}d"
        
        return abs_str, rel_str
    except:
        return "TBD", ""

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
    
    # --- MATCH MARKETS ---
    if not matches_only and not non_matches_only:
        # Default: show both
        show_matches = True
        show_non_matches = True
    elif matches_only:
        show_matches = True
        show_non_matches = False
    else:
        show_matches = False
        show_non_matches = True
    
    if show_matches:
        print(f"\nMATCH MARKETS")
        if not match_events:
            print("  No match markets found.")
        else:
            for i, e in enumerate(match_events, 1):
                f = format_event(e)
                ml = get_ml_market(e)
                outcomes = json.loads(ml.get("outcomes", "[]")) if ml else []
                prices = json.loads(ml.get("outcomePrices", "[]")) if ml else []
                vol = f["volume"]
                title = f["title"]
                url = f["url"]
                start_time_wib, rel_time = get_start_time_wib(e)
                
                team_a = outcomes[0] if len(outcomes) > 0 else "?"
                team_b = outcomes[1] if len(outcomes) > 1 else "?"
                odds_a = format_odds(float(prices[0])) if len(prices) > 0 else "?"
                odds_b = format_odds(float(prices[1])) if len(prices) > 1 else "?"
                
                if " - " in title:
                    title_clean = title.split(" - ")[0].strip()
                else:
                    title_clean = title
                
                tournament = get_tournament(title)
                
                print(f"\n  {i}. [{title_clean}]({url})")
                print(f"     {start_time_wib} | {rel_time}")
                print(f"  Vol: ${vol:,.0f}")
                if tournament:
                    print(f"  Tournament: {tournament}")
                print(f"  Odds: {team_a} {odds_a} | {odds_b} {team_b}")
    
    # --- NON-MATCH MARKETS ---
    if show_non_matches and non_match_events:
        print(f"\nNON-MATCH MARKETS")
        
        for i, e in enumerate(non_match_events[:non_matches_max], 1):
            title = e.get("title", "?")
            url = get_event_url(e)
            start_time_wib, rel_time = get_start_time_wib(e)
            
            total_vol = sum(float(m.get("volume", 0)) for m in e.get("markets", []))
            market_count = len(e.get("markets", []))
            
            print(f"\n  {i}. [{title}]({url})")
            print(f"     {start_time_wib} | {rel_time}")
            print(f"     Markets: {market_count} | Total Vol: ${total_vol:,.0f}")

def print_detail(e, detail):
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    utc7 = timezone(timedelta(hours=7))
    now_utc7 = now_utc.astimezone(utc7)
    
    print(f"\n{detail['title']}")
    print(f"URL: {detail['url']}")
    print(f"Livestream: {detail['livestream']}")
    
    spread_str = format_spread(detail["best_bid"], detail["best_ask"]) if detail["best_bid"] and detail["best_ask"] else "N/A"
    time_str = get_match_time_str(e)
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

def send_to_telegram(match_events, non_match_events, category, matches_only=False, non_matches_only=False):
    """Send browse results to Telegram. Reads BOT_TOKEN and CHAT_ID from environment."""
    import os
    bot_token = os.environ.get("BOT_TOKEN")
    chat_id = os.environ.get("CHAT_ID")
    if not bot_token or not chat_id:
        print("WARNING: BOT_TOKEN or CHAT_ID not set in environment. Skipping Telegram send.")
        return
    
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    utc7 = timezone(timedelta(hours=7))
    now_utc7 = now_utc.astimezone(utc7)
    header_date = now_utc7.strftime("%b %d, %Y")
    
    # Determine sections to show
    show_matches = (not matches_only and not non_matches_only) or matches_only
    show_non_matches = (not matches_only and not non_matches_only) or non_matches_only
    
    def send(text):
        result = subprocess.run(
            ["curl", "-s", f"https://api.telegram.org/bot{bot_token}/sendMessage",
             "-d", f"chat_id={chat_id}",
             "-d", f"text={text}",
             "-d", "parse_mode=HTML",
             "-d", "disable_web_page_preview=true"],
            capture_output=True
        )
        resp = json.loads(result.stdout.decode())
        if resp.get("ok"):
            print(f"  Sent msg {resp['result']['message_id']}")
        else:
            print(f"  Error: {resp.get('description')}")
    
    # Build sections
    lines = [f"<b>{category.upper()}</b> | {header_date}"]
    lines.append("")
    
    if show_matches:
        lines.append("MATCH MARKETS")
        lines.append("")
        if not match_events:
            lines.append("  No match markets found.")
        else:
            for i, e in enumerate(match_events, 1):
                ml = get_ml_market(e)
                outcomes = json.loads(ml.get("outcomes", "[]")) if ml else []
                prices = json.loads(ml.get("outcomePrices", "[]")) if ml else []
                vol = get_ml_volume(e)
                title = e.get("title", "?")
                url = get_event_url(e)
                start_time_wib, rel_time = get_start_time_wib(e)
                team_a = outcomes[0] if len(outcomes) > 0 else "?"
                team_b = outcomes[1] if len(outcomes) > 1 else "?"
                odds_a = format_odds(float(prices[0])) if len(prices) > 0 else "?"
                odds_b = format_odds(float(prices[1])) if len(prices) > 1 else "?"
                tournament = get_tournament(title)
                title_clean = title.split(" - ")[0].strip() if " - " in title else title
                lines.append(f"<b>{i}.</b> <a href=\"{url}\">{title_clean}</a>")
                lines.append(f"   {start_time_wib} | {rel_time}")
                lines.append(f"   Vol: ${vol:,.0f}")
                if tournament:
                    lines.append(f"   Tournament: {tournament}")
                lines.append(f"   Odds: {team_a} {odds_a} | {odds_b} {team_b}")
                lines.append("")
        lines.append("")
    
    if show_non_matches:
        lines.append("NON-MATCH MARKETS")
        lines.append("")
        if not non_match_events:
            lines.append("  No non-match markets found.")
        else:
            for i, e in enumerate(non_match_events, 1):
                title = e.get("title", "?")
                url = get_event_url(e)
                start_time_wib, rel_time = get_start_time_wib(e)
                total_vol = sum(float(m.get("volume", 0)) for m in e.get("markets", []))
                market_count = len(e.get("markets", []))
                lines.append(f"<b>{i}.</b> <a href=\"{url}\">{title}</a>")
                lines.append(f"   {start_time_wib} | {rel_time}")
                lines.append(f"   Markets: {market_count} | Total Vol: ${total_vol:,.0f}")
                lines.append("")
    
    # Chunk by 10 items (events), respecting 4096 char Telegram limit
    text = "\n".join(lines)
    if len(text) <= 4096:
        send(text)
        return
    
    # Split into chunks of 10 events
    all_items = []
    in_match = True
    for line in lines:
        if line == "MATCH MARKETS":
            in_match = True
        elif line == "NON-MATCH MARKETS":
            in_match = False
        elif line.startswith("<b>") and ". " in line and "</a>" in line:
            all_items.append((in_match, line))
    
    chunk = []
    chunk_len = 0
    chunk_num = 1
    
    # Header is always first
    header = f"<b>{category.upper()}</b> | {header_date}\n"
    if show_matches:
        header += "\nMATCH MARKETS\n\n"
    if show_non_matches:
        header += "\nNON-MATCH MARKETS\n\n"
    
    for is_match, item_line in all_items:
        test_chunk = chunk + [item_line, ""]
        test_text = header + "\n".join(chunk) + "\n".join(test_chunk)
        if len(test_text) > 4096 or len(chunk) >= 10:
            # Send current chunk
            msg = header + "\n".join(chunk)
            send(msg)
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
        send(msg)


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
                       help="Send results to Telegram (BOT_TOKEN and CHAT_ID must be set in environment).")
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
