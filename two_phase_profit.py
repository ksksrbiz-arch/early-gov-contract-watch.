"""
Two-phase profit model for the gov-contract trading bot.

Phase 1 — Entry: decide whether a large government-contract award justifies
opening a long equity position in the awardee's stock.

Phase 2 — Exit: decide whether an open position should be closed based on
one of three conditions (checked in priority order):

  1. Take-profit  — unrealised gain  >=  TAKE_PROFIT_PCT
  2. Stop-loss    — unrealised loss  >=  STOP_LOSS_PCT
  3. Max hold     — position age     >=  SELL_AFTER_DAYS calendar days

Both phases are expressed as pure functions so they can be tested without a
live Alpaca connection.  Side-effecting order submission lives in trader.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from config import MATERIALITY_THRESHOLD, SELL_AFTER_DAYS, STOP_LOSS_PCT, TAKE_PROFIT_PCT

# ── Exit-reason constants (part of ExitDecision.reason vocabulary) ──────────

EXIT_TAKE_PROFIT = "take_profit"
EXIT_STOP_LOSS = "stop_loss"
EXIT_MAX_HOLD = "max_hold"


# ── Data containers ──────────────────────────────────────────────────────────


@dataclass
class EntryDecision:
    """Result of Phase-1 evaluation for a single contract award."""

    should_buy: bool
    ticker: Optional[str]
    reason: str


@dataclass
class ExitDecision:
    """Result of Phase-2 evaluation for a single open position."""

    should_sell: bool
    ticker: str
    reason: Optional[str]          # EXIT_* constant when should_sell is True
    unrealized_pct: Optional[float]  # % gain/loss vs. avg entry price


# ── Phase 1 ──────────────────────────────────────────────────────────────────


def evaluate_entry(
    ticker: Optional[str],
    amount: float,
    market_cap: float,
    *,
    materiality_threshold: float = MATERIALITY_THRESHOLD,
    already_held: bool = False,
    daily_budget_exhausted: bool = False,
) -> EntryDecision:
    """Phase 1: decide whether to buy *ticker* based on a contract award.

    Parameters
    ----------
    ticker:
        Matched equity symbol for the contract recipient (``None`` → no match).
    amount:
        Contract award amount in USD.
    market_cap:
        Recipient's market capitalisation in USD (0 → unknown / unlisted).
    materiality_threshold:
        Minimum contract-to-market-cap ratio for the award to be considered
        material.  Defaults to the project-wide value from config.
    already_held:
        ``True`` when the account already holds a position in *ticker*.
    daily_budget_exhausted:
        ``True`` when today's ``MAX_DAILY_TRADES`` cap has been reached.
    """
    if not ticker:
        return EntryDecision(should_buy=False, ticker=None, reason="no ticker match")

    if market_cap <= 0 or amount / market_cap < materiality_threshold:
        return EntryDecision(
            should_buy=False,
            ticker=ticker,
            reason="award not material vs market cap",
        )

    if already_held:
        return EntryDecision(
            should_buy=False, ticker=ticker, reason="already holding position"
        )

    if daily_budget_exhausted:
        return EntryDecision(
            should_buy=False, ticker=ticker, reason="daily trade limit reached"
        )

    return EntryDecision(
        should_buy=True, ticker=ticker, reason="entry signal confirmed"
    )


# ── Phase 2 ──────────────────────────────────────────────────────────────────


def evaluate_exit(
    ticker: str,
    avg_entry_price: float,
    current_price: float,
    entry_time: Optional[datetime] = None,
    *,
    take_profit_pct: float = TAKE_PROFIT_PCT,
    stop_loss_pct: float = STOP_LOSS_PCT,
    sell_after_days: int = SELL_AFTER_DAYS,
) -> ExitDecision:
    """Phase 2: decide whether to close an open position in *ticker*.

    Exit conditions are evaluated in priority order:

    1. Take-profit  — unrealised gain ≥ ``take_profit_pct``
    2. Stop-loss    — unrealised loss ≥ ``stop_loss_pct``
    3. Max hold     — calendar days held ≥ ``sell_after_days``

    Parameters
    ----------
    ticker:
        Equity symbol of the open position.
    avg_entry_price:
        Volume-weighted average fill price (must be positive).
    current_price:
        Latest market price.
    entry_time:
        UTC-aware (or naive UTC) datetime when the position was opened.
        When ``None`` the max-hold check is skipped.
    take_profit_pct, stop_loss_pct, sell_after_days:
        Override the config-level thresholds for testing or per-trade
        customisation.
    """
    if avg_entry_price <= 0:
        return ExitDecision(
            should_sell=False, ticker=ticker, reason=None, unrealized_pct=None
        )

    unrealized_pct = (current_price - avg_entry_price) / avg_entry_price * 100

    if unrealized_pct >= take_profit_pct:
        return ExitDecision(
            should_sell=True,
            ticker=ticker,
            reason=EXIT_TAKE_PROFIT,
            unrealized_pct=unrealized_pct,
        )

    if unrealized_pct <= -stop_loss_pct:
        return ExitDecision(
            should_sell=True,
            ticker=ticker,
            reason=EXIT_STOP_LOSS,
            unrealized_pct=unrealized_pct,
        )

    if entry_time is not None:
        now = datetime.now(timezone.utc)
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)
        held_days = (now - entry_time).days
        if held_days >= sell_after_days:
            return ExitDecision(
                should_sell=True,
                ticker=ticker,
                reason=EXIT_MAX_HOLD,
                unrealized_pct=unrealized_pct,
            )

    return ExitDecision(
        should_sell=False, ticker=ticker, reason=None, unrealized_pct=unrealized_pct
    )
