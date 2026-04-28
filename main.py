#!/usr/bin/env python3
import logging
import requests
import time

from config import (
    LOG_LEVEL,
    MIN_CONTRACT_AMOUNT,
    POLL_INTERVAL_MINUTES,
    SLACK_WEBHOOK,
    QUICK_BUY_NOTIONAL,
    QUICK_HOLD_HOURS,
    QUICK_TAKE_PROFIT_PCT,
    QUICK_STOP_LOSS_PCT,
    LARGE_BUY_NOTIONAL,
    PHASE2_TAKE_PROFIT_PCT,
    TRAILING_STOP_PCT,
    TRAILING_STOP_DAYS,
)
from usaspending_fetcher import fetch_recent_large_contracts, print_award_summary, load_state, save_state
from ticker_lookup import get_ticker_for_company, validate_ticker, is_material_award
from trader import AlpacaTrader
from alpaca.trading.enums import OrderSide
from two_phase_profit import (
    evaluate_phase,
    evaluate_exit,
    PHASE_1,
    PHASE_2,
    PHASE_NONE,
)

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


# ── Phase tracking helpers ───────────────────────────────────────────────────

def _save_position_phase(symbol: str, phase: str) -> None:
    """Tag an open position with the trading phase that opened it."""
    state = load_state()
    phases = state.setdefault("position_phases", {})
    phases[symbol] = phase
    save_state(state)


def _get_position_phase(symbol: str) -> str:
    """Return the phase tag for *symbol*, defaulting to PHASE_1 if unknown."""
    return load_state().get("position_phases", {}).get(symbol, PHASE_1)


def _clear_position_phase(symbol: str) -> None:
    """Remove the phase tag for *symbol* after it has been sold."""
    state = load_state()
    state.setdefault("position_phases", {}).pop(symbol, None)
    save_state(state)


# ── Per-award processing ─────────────────────────────────────────────────────

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

    if not trader:
        return False

    # Pull live market data from Alpaca Market Data v2 (best-effort).
    daily_volume = prev_daily_volume = bid_price = ask_price = None
    snapshot = trader.get_snapshot(ticker)
    if snapshot:
        try:
            daily_volume = float(snapshot.daily_bar.volume) if snapshot.daily_bar else None
            prev_daily_volume = (
                float(snapshot.prev_daily_bar.volume) if snapshot.prev_daily_bar else None
            )
        except Exception:
            pass
        try:
            bid_price = float(snapshot.latest_quote.bid_price) if snapshot.latest_quote else None
            ask_price = float(snapshot.latest_quote.ask_price) if snapshot.latest_quote else None
        except Exception:
            pass

    decision = evaluate_phase(
        ticker,
        amount,
        float(validation["market_cap"]),
        daily_volume=daily_volume,
        prev_daily_volume=prev_daily_volume,
        bid_price=bid_price,
        ask_price=ask_price,
        already_held=not trader.can_trade(ticker),
    )

    if decision.phase == PHASE_NONE:
        logger.info(f"No phase trigger for {ticker}: {decision.reason}")
        return False

    notional = decision.notional
    success = trader.buy_stock(ticker, notional)
    if success:
        _save_position_phase(ticker, decision.phase)
        phase_label = "🎯 PHASE 1 (quick)" if decision.phase == PHASE_1 else "🚀 PHASE 2 (large)"
        msg = (
            f"{phase_label} BUY: {ticker} ${notional:,.0f}  "
            f"ratio={decision.materiality_ratio:.3%}  {decision.reason}"
        )
        logger.info(msg)
        send_slack(f"🟢 {msg}")
    return success


# ── Exit scanning ─────────────────────────────────────────────────────────────

def process_exits(trader):
    """Scan all open Alpaca positions and close those that hit their exit condition."""
    try:
        positions = trader.client.get_all_positions()
    except Exception as e:
        logger.error(f"Failed to fetch positions for exit scan: {e}")
        return

    # Build symbol → earliest fill time from order history.
    entry_times = {}
    try:
        from alpaca.trading.requests import GetOrdersRequest
        from alpaca.trading.enums import QueryOrderStatus

        filled_orders = trader.client.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.CLOSED)
        )
        for o in filled_orders:
            if o.side == OrderSide.BUY and o.filled_at and o.symbol not in entry_times:
                entry_times[o.symbol] = o.filled_at
    except Exception as e:
        logger.warning(f"Could not load order history for exit scan: {e}")

    for pos in positions:
        symbol = pos.symbol
        entry_price = float(pos.avg_entry_price or 0)
        current_price = float(pos.current_price or 0)
        phase = _get_position_phase(symbol)
        entry_time = entry_times.get(symbol)

        if phase == PHASE_2:
            decision = evaluate_exit(
                symbol, entry_price, current_price, entry_time,
                take_profit_pct=PHASE2_TAKE_PROFIT_PCT,
                stop_loss_pct=TRAILING_STOP_PCT,
                sell_after_days=TRAILING_STOP_DAYS,
                phase=PHASE_2,
            )
        else:
            decision = evaluate_exit(
                symbol, entry_price, current_price, entry_time,
                take_profit_pct=QUICK_TAKE_PROFIT_PCT,
                stop_loss_pct=QUICK_STOP_LOSS_PCT,
                sell_after_hours=float(QUICK_HOLD_HOURS),
                phase=PHASE_1,
            )

        if decision.should_sell:
            pct_str = f"{decision.unrealized_pct:+.1f}%" if decision.unrealized_pct is not None else ""
            phase_label = "P2" if phase == PHASE_2 else "P1"
            logger.info(f"Exit [{phase_label}] {symbol}: {decision.reason} {pct_str}")
            success = trader.sell_stock(symbol)
            if success:
                _clear_position_phase(symbol)
                send_slack(f"🔴 [{phase_label}] Sold {symbol} ({decision.reason}) {pct_str}")


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_bot():
    logger.info("=== Early Gov Contract Bot Started (Two-Phase Engine) ===")
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
