# Early Gov Contract Watch & Auto-Trader (Render Edition)

**⚠️ HIGH RISK - EDUCATIONAL ONLY ⚠️**  
This bot watches for big U.S. government contracts and automatically buys/sells stocks. It can lose money. **Always start with paper trading.**

## What It Does

The bot uses a **two-phase profit engine** backed by the Alpaca Market Data v2 API (snapshots, latest bars, quotes):

- **Phase 1 — Quick Profit**: On every new contract award, pulls a live Alpaca snapshot for the matched ticker. If the stock is showing a *volume spike* (today's volume ≥ 2× yesterday's) **and** a *tight bid/ask spread* (≤ 0.5% of mid-price), opens a small position (`QUICK_BUY_NOTIONAL`, default $300). Auto-closes after 48 hours, +12% gain, or −5% loss — whichever fires first.
- **Phase 2 — Large Profit**: If Phase 1 does not trigger (no volume spike/spread signal) but the contract is ≥ 0.75% of the awardee's market cap, opens a larger position (`LARGE_BUY_NOTIONAL`, default $2,000) and holds it with a 20% take-profit target and 8% trailing stop over up to 14 days.
- Checks USASpending.gov every 30 minutes for new contracts > $10M
- Matches company names to stock tickers using SEC fuzzy matching
- Runs 24/7 on Render as a Background Worker
- Paper trading enabled by default (`ALPACA_PAPER=true`)

## Quick Start (Render - Recommended)

1. Fork or clone this repo
2. Go to [render.com](https://render.com) → New → Background Worker
3. Connect this GitHub repo
4. Set Environment = Docker
5. Add the environment variables from `.env.example`
6. Deploy

## Environment Variables

### Required

| Variable                | Example          | Description |
|-------------------------|------------------|-----------|
| ALPACA_API_KEY          | AK...            | Your Alpaca key |
| ALPACA_SECRET_KEY       | ...              | Your secret |
| ALPACA_PAPER            | true             | `true` = paper, `false` = real money |
| MIN_CONTRACT_AMOUNT     | 10000000         | Minimum contract size to watch |
| POLL_INTERVAL_MINUTES   | 30               | How often to check USASpending |
| SLACK_WEBHOOK           | (optional)       | For buy/sell Slack alerts |

### Phase 1 — Quick Profit

| Variable                  | Default | Description |
|---------------------------|---------|-------------|
| QUICK_BUY_NOTIONAL        | 300     | $ per Phase-1 trade |
| QUICK_HOLD_HOURS          | 48      | Max hold time before forced exit |
| QUICK_TAKE_PROFIT_PCT     | 12      | Take-profit threshold (%) |
| QUICK_STOP_LOSS_PCT       | 5       | Stop-loss threshold (%) |
| VOLUME_SPIKE_MULTIPLIER   | 2.0     | Min ratio of today's vs yesterday's volume |
| MAX_SPREAD_PCT            | 0.005   | Max bid/ask spread as fraction of mid-price |

### Phase 2 — Large Profit

| Variable                      | Default | Description |
|-------------------------------|---------|-------------|
| LARGE_BUY_NOTIONAL            | 2000    | $ per Phase-2 trade |
| PHASE2_MATERIALITY_THRESHOLD  | 0.0075  | Min contract/market-cap ratio (0.75%) |
| PHASE2_TAKE_PROFIT_PCT        | 20      | Take-profit threshold (%) |
| TRAILING_STOP_PCT             | 8       | Trailing stop-loss threshold (%) |
| TRAILING_STOP_DAYS            | 14      | Max hold days before forced exit |

### Flip to Live Trading

Set `ALPACA_PAPER=false` **only after thorough paper-trading validation**.  Start with small notional values and confirm the bot behaves as expected across several cycles before increasing position sizes.

## Local Testing
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your keys
python main.py
```

## Files
- `main.py` — Main loop + buy/sell logic
- `ticker_lookup.py` — SEC fuzzy matching
- `trader.py` — Alpaca buy/sell
- `usaspending_fetcher.py` — Government API calls
- `gov_contract_dashboard.py` — Terminal dashboard CLI shim (see below)
- `dashboard/` — Dashboard v2 package (snapshot data layer + renderers + CLI)
- `tests/` — Hermetic pytest suite for the dashboard
- `Dockerfile` — For Render deployment

## Dashboard (v2)

Run a one-shot terminal snapshot of the bot's state:

```bash
python gov_contract_dashboard.py
```

The dashboard is built on a **versioned snapshot schema** (`schema_version: "2.0"`)
that cleanly separates data collection from rendering. The same snapshot
powers the terminal view, JSON exports, and any future UI surface. The data
layer lives in the `dashboard/` package; `gov_contract_dashboard.py` is a
thin CLI shim.

The dashboard displays:
- **Section Health** — explicit per-source freshness (`config`, `bot_state`,
  `usaspending`, `ticker_source`, `alpaca`) so you can trust each panel
  independently.
- **Configuration** — active env-var settings, with inline issues/warnings
  from config validation (e.g. risky LIVE-mode flag, missing API key).
- **Bot state** — `state.json` health (fresh / stale / cold), seen-award
  count, last-modified time, last successful check.
- **Summary stats** — total contract value, ticker matches, validated
  tickers, material-award count, total $ exposure, plus *deltas vs the
  previous snapshot* (count / total / average) when history is enabled, and
  ambiguous / low-confidence match counts.
- **Concentration** — Herfindahl–Hirschman Index (HHI) and top-share for
  recipients and agencies.
- **Daily trend** — per-day award counts and totals over the configured
  window (default 14 days).
- **Repeat recipients** — recipients with ≥2 awards in the window with
  total / average / max amounts.
- **Anomaly flags** — outlier amounts (z-score), missing recipient/agency,
  and very short descriptions.
- **Recent contracts** — top awards from USASpending.gov, with filtering
  and sorting.
- **Ticker matches & materiality** — match **confidence tier** (high /
  medium / low / none), fuzzy score, near-tied alternatives (ambiguity
  signal), market cap, materiality ratio, price, and **trade eligibility**
  context (eligible / blocked / skipped, with reasons).
- **Alpaca account** — portfolio value, equity, today's P/L, buying power,
  daily-trade budget, and **exposure HHI**.
- **Open positions** — with current price and unrealized P/L, plus a
  **Drawdown Leaders** table.
- **Order lifecycle** — submitted / filled / rejected / canceled / aging
  counters over the last 14 days.
- **Recent orders** — last 14 days of activity (skipped if API key absent).

### Options

v1-compatible:

```text
--refresh N         Live auto-refresh every N seconds (Ctrl+C to exit)
--limit N           Max contracts shown in the contracts table (default: 20)
--no-validate       Skip yfinance market-data lookups (much faster)
--no-orders         Skip the Alpaca recent-orders table
--export FILE       Write the snapshot to a JSON file (profile-aware)
```

New in v2:

```text
--view {overview,contracts,tickers,trading,all}
                    Focused view mode (default: all).
--sort {amount,date,recipient,agency,confidence,materiality}
                    Sort order for the contracts table (default: amount).
--ticker-sort {amount,confidence,materiality,recipient}
                    Sort order for the ticker table (default: materiality).
--filter-agency TEXT       Substring filter on awarding agency.
--filter-recipient TEXT    Substring filter on recipient.
--min-amount AMOUNT        Hide rows below this award amount.
--min-tier {none,low,medium,high}
                    Minimum match-confidence tier required to show a row.
--material-only     Show only awards flagged as material vs market cap.
--profile {compact,full}
                    Export profile (default: $DASHBOARD_EXPORT_PROFILE or full).
```

Examples:

```bash
# Live mode, refresh every 60s
python gov_contract_dashboard.py --refresh 60

# Fast snapshot, skip market data
python gov_contract_dashboard.py --no-validate

# Just the trading view (positions, lifecycle, orders)
python gov_contract_dashboard.py --view trading

# High-confidence material awards only, sorted by confidence
python gov_contract_dashboard.py --view tickers --min-tier high \
    --material-only --ticker-sort confidence

# Compact export for downstream tooling (no raw award payloads)
python gov_contract_dashboard.py --export snapshot.json --profile compact
```

### Snapshot schema

Both the live UI and the `--export` payload conform to a versioned schema:

```jsonc
{
  "schema_version": "2.0",
  "generated_at": "...",
  "config": { ... },
  "config_validation": { "issues": [...], "warnings": [...] },
  "health": { "config": {...}, "bot_state": {...}, "usaspending": {...},
              "ticker_source": {...}, "alpaca": {...} },
  "contracts": [ ... ],          // omitted in compact profile
  "summary":   { "stats": {...}, "deltas": {...},
                 "matched": N, "validated": N, "material": N,
                 "ambiguous_matches": N, "low_confidence_matches": N },
  "analytics": { "trends": {...}, "concentration": {...},
                 "repeat_recipients": [...], "anomalies": [...] },
  "analyses":  [ { recipient, amount, ticker, match{tier,score,...},
                   info, material, eligibility{status,reasons,...} } ],
  "alpaca":    { "configured": ..., "account": ..., "positions": ...,
                 "orders": ..., "lifecycle": {...},
                 "exposure_concentration": {...}, "drawdown_leaders": [...] },
  "two_phase": { "phase1_candidates": N, "phase2_candidates": N,
                 "phase2_threshold": 0.0075, "phase2_tickers": [...] },
  "errors":    { ... }
}
```

The schema version will be bumped on any breaking change so downstream
tooling can guard with a single field check.

### Dashboard environment toggles (all optional)

| Variable                          | Default                  | Description |
|-----------------------------------|--------------------------|-------------|
| `DASHBOARD_ENABLE_ANALYTICS`      | `true`                   | Trends / concentration / repeat recipients |
| `DASHBOARD_ENABLE_ANOMALIES`      | `true`                   | Anomaly flag detector |
| `DASHBOARD_ENABLE_HISTORY`        | `true`                   | Persist a small history file for delta metrics |
| `DASHBOARD_HISTORY_FILE`          | `dashboard_history.json` | Path to the history file |
| `DASHBOARD_HISTORY_LIMIT`         | `30`                     | Max history entries retained |
| `DASHBOARD_EXPORT_PROFILE`        | `full`                   | Default for `--profile` (`compact` or `full`) |
| `DASHBOARD_TICKER_MIN_CONFIDENCE` | `medium`                 | Display-only eligibility gate (`none`/`low`/`medium`/`high`) |
| `DASHBOARD_TREND_DAYS`            | `14`                     | Daily-trend window |
| `DASHBOARD_TREND_WEEKS`           | `4`                      | Weekly-trend window |

### Tests

```bash
pip install pytest
pytest -q
```

The test suite is hermetic — it patches USASpending, SEC, yfinance, and
Alpaca so it runs offline.

## Render Deployment Guide

1. Push this repo to GitHub (fork or clone)
2. Log in to [render.com](https://render.com) and create a new **Background Worker**
3. Connect your GitHub repo
4. Set **Environment** to **Docker**
5. Add the following environment variables in the Render dashboard:
   - `ALPACA_API_KEY`
   - `ALPACA_SECRET_KEY`
   - `ALPACA_PAPER` = `true` (use paper trading first!)
   - `BUY_NOTIONAL` = `300`
   - `MIN_CONTRACT_AMOUNT` = `10000000`
   - `POLL_INTERVAL_MINUTES` = `30`
   - `SLACK_WEBHOOK` (optional)
6. Click **Deploy**

The bot will start and poll USASpending.gov every 30 minutes automatically.

## Disclaimer
Not financial advice. You can lose money. Use at your own risk. This is for educational purposes only.