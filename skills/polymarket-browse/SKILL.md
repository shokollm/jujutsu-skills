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
hermes skills install https://git.fbrns.co/shoko/jujutsu-skills#polymarket-browse
```

**For OpenClaw users:**
```bash
# Clone the repo
git clone https://git.fbrns.co/shoko/jujutsu-skills.git ~/jujutsu-skills

# Copy skill to your OpenClaw skills folder
cp -r ~/jujutsu-skills/skills/polymarket-browse ~/.openclaw/skills/
```

**Manual installation:**
```bash
# Clone the repo
git clone https://git.fbrns.co/shoko/jujutsu-skills.git ~/jujutsu-skills

# Copy skill to your Hermes skills folder
cp -r ~/jujutsu-skills/skills/polymarket-browse ~/.hermes/skills/
```

## Usage

```
polymarket-browse [--category "Counter Strike"] [--limit 5] [--detail N]
```

## Arguments

- `--category` : Game category to browse. Options: All Esports, Counter Strike, League of Legends, Dota 2, Valorant, NBA, NFL, UFC, Tennis (default: Counter Strike)
- `--limit` : Max number of events to show (default: 5)
- `--detail` : Index of event (1-indexed) to show detailed markets for. Defaults to 1 (first event). Set to 0 to disable.
- `--list-categories` : List available game categories and exit

## Output Format

Each event displays in a **6-line format**:

```
=== DOTA 2 ===
Query: 'Dota2' | Total API: 54 | Tradeable: 3
Current time (WIB): 13:58 WIB | Mar 24, 2026

1. [Dota 2: Yakult Brothers vs BetBoom Team (BO2)](https://polymarket.com/market/...)
   Mar 24, 19:00 WIB | In 5h

   Vol: $4,952
   Tournament: ESL One Birmingham Group A
   Odds: Yakult Brothers 29c | 71c BetBoom Team

2. [Dota 2: GamerLegion vs Team Yandex (BO2)](https://polymarket.com/market/...)
   Mar 24, 19:00 WIB | In 5h

   Vol: $3,944
   Tournament: ESL One Birmingham Group A
   Odds: GamerLegion 28c | 72c Team Yandex
```

Format per event:
- **Line 1**: `[number]. [Title](url)` — title without tournament name
- **Line 2**: `Date, Time WIB | Relative` (e.g., "Mar 24, 19:00 WIB | In 5h")
- **Line 3**: (empty separator)
- **Line 4**: `Vol: $XXX`
- **Line 5**: `Tournament: [tournament name]`
- **Line 6**: `Odds: [Team A] [Odds A] | [Odds B] [Team B]`

Time shows in WIB (UTC+7 for Indonesian users). Relative time shows "In Xh", "Xd ago", or "LIVE".

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

Events are filtered to show only **tradeable** matches:
- Must have seriesSlug + gameId (actual match, not tournament future)
- ML volume > 0
- acceptingOrders = true
- ML closed = false
- ML bestBid < 0.99 (market hasn't converged toward favorite)
- ML bestAsk > 0.01 (market hasn't converged toward underdog)
- BO2 matches that ended in a tie (1-1) are filtered out
- Event endDate has not passed (API doesn't always close ML market promptly)

Markets are filtered to only tradeable ones:
- acceptingOrders = true
- bestBid < 0.99
- bestAsk > 0.01
- closed = false

## Rate Limiting

- Exponential backoff: 2s → 4s → 8s → 16s → 32s
- Max 5 retries before aborting

## Known Issues

- **BO2 matches that end in a tie (1-1)**: Filtered out by checking if all child_moneyline markets are closed.

## Odds Format

All odds are shown in **cents** format:
- `30c` = 0.30 probability
- `95c` = 0.95 probability
- `GamerLegion 28c | 72c Team Yandex` = GamerLegion at 28c, Team Yandex at 72c
