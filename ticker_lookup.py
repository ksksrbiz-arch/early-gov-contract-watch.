import requests
import json
import os
import logging

from rapidfuzz import process, fuzz
import yfinance as yf
from datetime import datetime, timedelta

from config import SEC_USER_AGENT, SEC_TICKER_CACHE_DAYS, MATERIALITY_THRESHOLD

logger = logging.getLogger(__name__)

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CACHE_FILE = "sec_tickers_cache.json"


def download_sec_tickers():
    headers = {"User-Agent": SEC_USER_AGENT}
    try:
        resp = requests.get(SEC_TICKERS_URL, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        with open(CACHE_FILE, "w") as f:
            json.dump(data, f)
        return data
    except Exception as e:
        logger.warning(f"Failed to download SEC tickers: {e}")
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE) as f:
                return json.load(f)
        raise


def load_tickers():
    if os.path.exists(CACHE_FILE):
        cache_age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))
        if cache_age < timedelta(days=SEC_TICKER_CACHE_DAYS):
            with open(CACHE_FILE) as f:
                return json.load(f)
    return download_sec_tickers()


def get_ticker_for_company(name, min_score=85):
    if not name:
        return None
    companies = [
        (v["title"], v["ticker"])
        for v in load_tickers().values()
        if v.get("ticker")
    ]
    titles = [c[0] for c in companies]

    # Try direct substring match first (faster)
    name_upper = name.upper()
    for title, ticker in companies:
        if name_upper in title.upper() or title.upper() in name_upper:
            return ticker

    # Fall back to fuzzy matching
    match = process.extractOne(name, titles, scorer=fuzz.WRatio, score_cutoff=min_score)
    if match:
        return companies[match[2]][1]
    return None


def validate_ticker(ticker):
    try:
        t = yf.Ticker(ticker)
        info = t.info
        if info.get("regularMarketPrice") and info.get("marketCap", 0) > 0:
            return {
                "ticker": ticker,
                "name": info.get("shortName"),
                "market_cap": info.get("marketCap"),
            }
        return None
    except Exception as e:
        logger.warning(f"Failed to validate ticker {ticker}: {e}")
        return None


def is_material_award(amount, mkt_cap, pct=None):
    if pct is None:
        pct = MATERIALITY_THRESHOLD
    return mkt_cap > 0 and (amount / mkt_cap) >= pct
