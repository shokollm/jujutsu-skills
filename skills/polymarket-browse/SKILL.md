---
name: polymarket-browse
category: research
description: Browse tradeable Polymarket events by game category. Shows active matches with ML odds (cents format), volume, tournament, and market URLs. Supports Counter Strike, League of Legends, Dota 2, Valorant, NBA, NFL, UFC, Tennis.
---

# Polymarket Event Browser

Browse tradeable Polymarket prediction market events by game category.

## Installation

**For Hermes Agent users:**
```bash
hermes skills install https://github.com/shokollm/jujutsu-skills#polymarket-browse
```

**For OpenClaw users:**
```bash
# Clone the repo
git clone https://github.com/shokollm/jujutsu-skills.git ~/jujutsu-skills

# Copy skill to your OpenClaw skills folder
cp -r ~/jujutsu-skills/skills/polymarket-browse ~/.openclaw/skills/
```

**Optional: For better experience, install the Polymarket MCP server:**
This lets the agent answer questions about Polymarket markets, rules, and trading mechanics.
```bash
# Ask your agent to install it for you, or add to config.yaml:
hermes mcp add polymarket https://docs.polymarket.com/mcp
```

## Usage

```
polymarket-browse [--category "Counter Strike"] [--limit 5] [--matches N] [--non-matches N] [--search "TeamName"] [--matches-only] [--non-matches-only] [--detail N] [--raw] [--telegram] [--no-cache] [--max-total N] [--starts-before TIMESTAMP]
```

## Arguments

- `--category` : Game category to browse. Options: All Esports, Counter Strike, League of Legends, Dota 2, Valorant, NBA, NFL, UFC, Tennis (default: Counter Strike)
- `--limit` : Max events per section (match + non-match). Default: 5
- `--matches` : Max match markets to show. Default: --limit
- `--non-matches` : Max non-match markets to show. Default: --limit
- `--search` : Free-text team/term search within the selected category. Appends to the category query. Example: `--category "Counter Strike" --search "FlyQuest"`
- `--matches-only` : Show only match markets (suppress non-match section).
- `--non-matches-only` : Show only non-match markets (suppress match section).
- `--detail` : Index of match event (1-indexed) to show detailed markets. Default: 1. Set to 0 to disable.
- `--list-categories` : List available game categories and exit
- `--raw` : Show all events without tradeable filter (for debugging). Includes fetch stats.
- `--no-cache` : Disable caching and fetch fresh data from the API.
- `--max-total` : Maximum total events to fetch before early exit. Default: no limit. Useful for quick snapshots.
- `--starts-before` : Unix timestamp filter. Only show match events starting before this time (LIVE events always shown regardless of timestamp).
- `--telegram` : Send results to Telegram. Requires `BOT_TOKEN` and `CHAT_ID` in environment variables.

## Output Format

Output is split into two sections:

```
=== COUNTER STRIKE ===
Current time (WIB): 17:00 WIB | Mar 24, 2026

MATCH MARKETS

  1. [Counter-Strike: TheMongolz vs Spirit (BO3)](https://polymarket.com/market/...)
     Mar 24, 03:45 WIB | LIVE
     Vol: $2,626,029
     Tournament: BLAST Open Rotterdam Group B
     Odds: TheMongolz 100c | 0c Spirit

NON-MATCH MARKETS

  1. [Blast Open Rotterdam 2026: Winner](https://polymarket.com/event/...)
     Feb 28, 04:43 WIB | 24d ago
     Markets: 17 | Total Vol: $958,116
```

**Match Markets** are actual head-to-head matches with moneyline odds (sorted by volume).
**Non-match Markets** are tournament futures, props, and other markets without direct match odds.

Stats line (Fetched / Total / Match counts) only shown when `--raw` flag is used.

If a fetch is interrupted (API error/timeout), a `WARNING: Partial fetch` line appears showing data may be incomplete.

## Game Categories

| Category | Search Term |
|---|---|
| All Esports | Esports |
| Counter Strike | Counter Strike |
| League of Legends | League of Legends |
| Dota 2 | Dota2 |
| Valorant | Valorant |
| NBA | NBA |
| NFL | Football |
| UFC | UFC |
| Tennis | Tennis |

## Filters Applied

The script classifies every event into one of two categories:

**Match Markets**: Events that are actual head-to-head matches (have `seriesSlug` + `gameId`, OR title contains " vs ").

**Non-match Markets**: Everything else — tournament futures, prop bets, player props, etc.

Tradeable match markets additionally require:
- Must have seriesSlug + gameId (actual match, not tournament future)
- ML volume > 0
- acceptingOrders = true
- ML closed = false
- ML bestBid < 0.99 (market hasn't converged toward favorite)
- ML bestAsk > 0.01 (market hasn't converged toward underdog)
- BO2 matches that ended in a tie (1-1) are filtered out
- Event endDate has not passed (API doesn't always close ML market promptly)
- Match startTime is not more than 4 hours in the past (matches that already ended are filtered out)

Use `--raw` to disable the tradeable filter and see all match markets regardless of volume or open orders.

## Pagination

The script fetches **ALL pages** until the API runs out of results.

### Parallel Fetching

Pages are fetched in **parallel batches of 5** using ThreadPoolExecutor. This significantly reduces fetch time:

| Scenario | Without Parallelization | With Parallelization |
|----------|------------------------|---------------------|
| 10 pages (50 events) | ~20s (2s per page × 10) | ~4s (2s per batch × 2 batches) |
| 20 pages (100 events) | ~40s | ~8s |

The script first fetches page 1 to determine total pages, then fetches remaining pages in parallel batches of 5.

## Rate Limiting

- Exponential backoff: 2s → 4s → 8s → 16s → 32s
- Max 5 retries before aborting

## Caching

Results are cached in `~/.cache/polymarket-browse/` with a **5-minute TTL** to reduce redundant API calls.

- Use `--no-cache` to bypass the cache and fetch fresh data
- Cached data is automatically used when available and not expired
- Useful when running the script repeatedly (e.g., for monitoring)

## Odds Format

All odds are shown in **cents** format:
- `30c` = 0.30 probability
- `95c` = 0.95 probability
- `GamerLegion 28c | 72c Team Yandex` = GamerLegion at 28c, Team Yandex at 72c
