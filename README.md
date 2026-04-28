# Early Gov Contract Watch & Auto-Trader (Render Edition)

**⚠️ HIGH RISK - EDUCATIONAL ONLY ⚠️**  
This bot watches for big U.S. government contracts and automatically buys/sells stocks. It can lose money. Start with paper trading.

## What It Does
- Checks USASpending.gov every 30 minutes for new contracts > $10M
- Matches company names to stock tickers using fuzzy matching
- Buys automatically if the contract is material
- Sells after 5 days, +12% profit, or -6% loss
- Runs 24/7 on Render as a Background Worker

## Quick Start (Render - Recommended)

1. Fork or clone this repo
2. Go to [render.com](https://render.com) → New → Background Worker
3. Connect this GitHub repo
4. Set Environment = Docker
5. Add the environment variables from `.env.example`
6. Deploy

## Environment Variables (Required)

| Variable                | Example          | Description |
|-------------------------|------------------|-----------|
| ALPACA_API_KEY          | AK...            | Your Alpaca key |
| ALPACA_SECRET_KEY       | ...              | Your secret |
| ALPACA_PAPER            | true             | true = paper, false = real money |
| BUY_NOTIONAL            | 300              | $ amount per trade (start small) |
| MIN_CONTRACT_AMOUNT     | 10000000         | Minimum contract size |
| POLL_INTERVAL_MINUTES   | 30               | How often to check |
| SLACK_WEBHOOK           | (optional)       | For buy/sell alerts |

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
- `gov_contract_dashboard.py` — Terminal dashboard (see below)
- `Dockerfile` — For Render deployment

## Dashboard

Run a one-shot terminal snapshot of the bot's state:

```bash
python gov_contract_dashboard.py
```

The dashboard displays:
- **Configuration** — active env-var settings
- **Bot state** — `state.json` health (fresh / stale / cold), seen-award count, last-modified time
- **Summary stats** — total contract value, ticker matches, validated tickers, material-award count and total $ exposure
- **Top agencies & top recipients** — aggregated rankings by total contract value
- **Recent contracts** — top awards (sorted by amount) from USASpending.gov
- **Ticker matches** — fuzzy-matched tickers, market-cap, materiality ratio, current price; sorted with material awards first
- **Alpaca account** — portfolio value, equity, today's P/L, buying power, daily-trade budget
- **Open positions** — with current price and unrealized P/L
- **Recent orders** — last 14 days of activity (skipped if API key absent)

### Options

```text
--refresh N      Live auto-refresh every N seconds (Ctrl+C to exit)
--limit N        Max contracts shown in the contracts table (default: 20)
--top N          Rows in top-agencies / top-recipients tables (default: 5)
--no-validate    Skip yfinance market-data lookups (much faster)
--no-orders      Skip the Alpaca recent-orders table
--export FILE    Write the full snapshot (contracts + analyses + config) to a JSON file
```

Examples:

```bash
# Live mode, refresh every 60s
python gov_contract_dashboard.py --refresh 60

# Fast snapshot, skip market data
python gov_contract_dashboard.py --no-validate

# Snapshot to JSON for further analysis
python gov_contract_dashboard.py --export snapshot.json
```

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