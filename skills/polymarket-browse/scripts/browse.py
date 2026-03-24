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

FETCH_PAGES = 4
PAGE_SIZE = 5
DISPLAY_MAX = 10
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

def fetch_all_pages(q, num_pages=FETCH_PAGES):
    all_events = []
    total_raw = 0
    for page in range(1, num_pages + 1):
        time.sleep(1)
        data = fetch_page(q, page)
        if data is None:
            break
        events = data.get("events", [])
        total_raw = data.get("pagination", {}).get("totalResults", "?")
        all_events.extend(events)
        if len(events) < PAGE_SIZE:
            break
    return {"events": all_events, "total_raw": total_raw}

# ============================================================
# FILTERS
# ============================================================

def is_match_market(e):
    return (e.get("seriesSlug") and e.get("gameId")) or " vs " in e.get("title", "")

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

def filter_events(events):
    return [e for e in events if is_match_market(e) and is_tradeable_event(e)]

def sort_events(events):
    return sorted(events, key=get_ml_volume, reverse=True)

# ============================================================
# BROWSE
# ============================================================

def browse_events(q, display_max=DISPLAY_MAX):
    result = fetch_all_pages(q, FETCH_PAGES)
    events = result["events"]
    filtered = filter_events(events)
    sorted_events = sort_events(filtered)
    return {
        "query": q,
        "total_raw": result["total_raw"],
        "total_filtered": len(filtered),
        "events": sorted_events[:display_max],
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
        "url": f"https://polymarket.com/market/{e.get('slug')}",
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
        "url": f"https://polymarket.com/market/{e.get('slug')}",
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
                mins = int(delta.total_seconds() / 60)
                rel_str = f"In {mins}m"
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

def print_browse(events, category, total_raw, total_filtered):
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    utc7 = timezone(timedelta(hours=7))
    now_utc7 = now_utc.astimezone(utc7)
    header_date = get_header_date()
    
    print(f"\n{'='*60}")
    print(f"=== {category.upper()} ===")
    print(f"{'='*60}")
    print(f"Query: '{GAME_CATEGORIES[category]}' | Total API: {total_raw} | Tradeable: {total_filtered}")
    print(f"Current time (WIB): {now_utc7.strftime('%H:%M WIB')} | {header_date}")
    
    if not events:
        print("  No tradeable events found.")
        return
    
    for i, e in enumerate(events, 1):
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
        
        # Extract title without tournament for line 1
        # Title format: "Category: TeamA vs TeamB (BO/X) - Tournament"
        # We want: "Category: TeamA vs TeamB (BO/X)"
        if " - " in title:
            title_clean = title.split(" - ")[0].strip()
        else:
            title_clean = title
        
        tournament = get_tournament(title)
        
        # New format:
        # 1. [Title](url)  <- title without tournament
        # 2. Date, Time WIB | Relative
        # (empty)
        # Vol: $XXX
        # Tournament: XXXXX
        # Odds: TeamA Xc | Xc TeamB
        print(f"\n  {i}. [{title_clean}]({url})")
        print(f"     {start_time_wib} | {rel_time}")
        print(f"")
        print(f"  Vol: ${vol:,.0f}")
        if tournament:
            print(f"  Tournament: {tournament}")
        print(f"  Odds: {team_a} {odds_a} | {odds_b} {team_b}")

def print_detail(e, detail):
    from datetime import datetime, timezone, timedelta
    now_utc = datetime.now(timezone.utc)
    utc7 = timezone(timedelta(hours=7))
    now_utc7 = now_utc.astimezone(utc7)
    
    print(f"\n{'='*60}")
    print(f"=== DETAIL: {detail['title'][:60]} ===")
    print(f"{'='*60}")
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
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Browse Polymarket tradeable events by game category.")
    parser.add_argument("--category", default="Counter Strike", 
                       choices=list(GAME_CATEGORIES.keys()),
                       help="Game category to browse")
    parser.add_argument("--limit", type=int, default=5,
                       help="Max events to show")
    parser.add_argument("--list-categories", action="store_true",
                       help="List available game categories and exit")
    parser.add_argument("--detail", type=int, default=1,
                       help="Index of event (1-indexed) to show detailed markets for. Default: 1. Set to 0 to disable.")
    args = parser.parse_args()
    
    if args.list_categories:
        print("Available categories:")
        for name in GAME_CATEGORIES:
            print(f"  - {name}")
        return
    
    search_term = GAME_CATEGORIES[args.category]
    
    print(f"\nFetching {args.category} events...")
    
    result = browse_events(search_term, display_max=args.limit)
    
    print_browse(
        result["events"], 
        args.category,
        result["total_raw"],
        result["total_filtered"]
    )
    
    # Print detail for selected event if any
    if result["events"] and args.detail > 0:
        print("\n")
        idx = args.detail - 1
        if idx < 0 or idx >= len(result["events"]):
            idx = 0
        detail_event = result["events"][idx]
        detail = format_detail_event(detail_event)
        print_detail(detail_event, detail)

if __name__ == "__main__":
    main()
