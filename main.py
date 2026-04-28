#!/usr/bin/env python3
import logging
import requests
import time

from config import (
    LOG_LEVEL,
    MIN_CONTRACT_AMOUNT,
    POLL_INTERVAL_MINUTES,
    BUY_NOTIONAL,
    SLACK_WEBHOOK,
)
from usaspending_fetcher import fetch_recent_large_contracts, print_award_summary
from ticker_lookup import get_ticker_for_company, validate_ticker, is_material_award
from trader import AlpacaTrader
from alpaca.trading.enums import OrderSide
from two_phase_profit import evaluate_exit

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)


def send_slack(msg):
    if SLACK_WEBHOOK:
        try:
            requests.post(SLACK_WEBHOOK, json={"text": msg}, timeout=10)
        except Exception as e:
            logger.warning(f"Slack notification failed: {e}")


def process_award(award, trader):
    recipient = award.get("Recipient Name", "")
    amount = float(award.get("Award Amount") or 0)
    if amount < MIN_CONTRACT_AMOUNT:
        return False

    print_award_summary(award)
    ticker = get_ticker_for_company(recipient)
    if not ticker:
        return False

    validation = validate_ticker(ticker)
    if not validation or not is_material_award(amount, validation["market_cap"]):
        return False

    if trader and trader.can_trade(ticker):
        success = trader.buy_stock(ticker, BUY_NOTIONAL)
        if success:
            send_slack(f"🟢 Bought {ticker} for ${BUY_NOTIONAL}")
            logger.info(f"Bought {ticker}")
        return success
    return False


def process_exits(trader):
    """Phase 2: scan all open Alpaca positions and close those that hit an exit condition."""
    try:
        positions = trader.client.get_all_positions()
    except Exception as e:
        logger.error(f"Failed to fetch positions for exit scan: {e}")
        return

    # Build a symbol → earliest fill time map from order history.
    entry_times = {}
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        filled_orders = trader.client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED)
        )
        for o in filled_orders:
            if o.side == o.side.BUY and o.filled_at and o.symbol not in entry_times:
                entry_times[o.symbol] = o.filled_at
    except Exception as e:
        logger.warning(f"Could not load order history for exit scan: {e}")

    for pos in positions:
        symbol = pos.symbol
        entry_price = float(pos.avg_entry_price or 0)
        current_price = float(pos.current_price or 0)
        decision = evaluate_exit(symbol, entry_price, current_price, entry_times.get(symbol))
        if decision.should_sell:
            pct_str = f"{decision.unrealized_pct:+.1f}%" if decision.unrealized_pct is not None else ""
            logger.info(f"Exit signal for {symbol}: {decision.reason} {pct_str}")
            success = trader.sell_stock(symbol)
            if success:
                send_slack(f"🔴 Sold {symbol} ({decision.reason}) {pct_str}")


def run_bot():
    logger.info("=== Early Gov Contract Bot Started ===")
    trader = None
    try:
        trader = AlpacaTrader()
    except Exception as e:
        logger.error(f"Alpaca connection error: {e}")

    while True:
        try:
            awards = fetch_recent_large_contracts()
            for award in awards:
                process_award(award, trader)
            if trader:
                process_exits(trader)
            logger.info(f"Sleeping {POLL_INTERVAL_MINUTES} minutes...")
            time.sleep(POLL_INTERVAL_MINUTES * 60)
        except KeyboardInterrupt:
            logger.info("Bot stopped by user.")
            break
        except Exception as e:
            logger.error(f"Error: {e}")
            time.sleep(60)


if __name__ == "__main__":
    run_bot()
