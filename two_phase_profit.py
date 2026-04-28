"""
Two-phase profit model for the gov-contract trading bot.

Phase 1 — Quick Profit
    Triggered by a volume spike combined with a tight bid/ask spread.
    Opens a small position (QUICK_BUY_NOTIONAL) and exits via:
      • Take-profit  ≥ QUICK_TAKE_PROFIT_PCT (default 12%)
      • Stop-loss    ≥ QUICK_STOP_LOSS_PCT    (default 5%)
      • Max hold       QUICK_HOLD_HOURS        (default 48 h)

Phase 2 — Large Profit
    Triggered when the contract award exceeds PHASE2_MATERIALITY_THRESHOLD
    (default 0.75%) of the awardee's market cap.
    Opens a larger position (LARGE_BUY_NOTIONAL) and exits via:
      • Take-profit  ≥ PHASE2_TAKE_PROFIT_PCT (default 20%)
      • Trailing stop ≥ TRAILING_STOP_PCT     (default 8%)
      • Max hold       TRAILING_STOP_DAYS      (default 14 days)

All public functions are pure: they accept data and return named decisions.
Side-effecting order submission lives in trader.py / main.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from config import (
    LARGE_BUY_NOTIONAL,
    MATERIALITY_THRESHOLD,
    MAX_SPREAD_PCT,
    PHASE2_MATERIALITY_THRESHOLD,
    PHASE2_TAKE_PROFIT_PCT,
    QUICK_BUY_NOTIONAL,
    QUICK_HOLD_HOURS,
    QUICK_STOP_LOSS_PCT,
    QUICK_TAKE_PROFIT_PCT,
    SELL_AFTER_DAYS,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRAILING_STOP_DAYS,
    TRAILING_STOP_PCT,
    VOLUME_SPIKE_MULTIPLIER,
)

# ── Phase constants ───────────────────────────────────────────────────────────

PHASE_NONE = "none"
PHASE_1 = "phase1"
PHASE_2 = "phase2"

# ── Exit-reason constants (part of ExitDecision.reason vocabulary) ──────────

EXIT_TAKE_PROFIT = "take_profit"
EXIT_STOP_LOSS = "stop_loss"
EXIT_MAX_HOLD = "max_hold"


# ── Data containers ──────────────────────────────────────────────────────────


@dataclass
class EntryDecision:
    """Result of a basic Phase-1 eligibility check for a single contract award."""

    should_buy: bool
    ticker: Optional[str]
    reason: str


@dataclass
class PhaseDecision:
    """Full two-phase entry decision for a single contract award."""

    phase: str                  # PHASE_1, PHASE_2, or PHASE_NONE
    ticker: Optional[str]
    notional: float             # $ to spend; 0 when phase == PHASE_NONE
    reason: str
    volume_spike: bool = field(default=False)
    tight_spread: bool = field(default=False)
    materiality_ratio: float = field(default=0.0)


@dataclass
class ExitDecision:
    """Result of a phase-aware exit evaluation for a single open position."""

    should_sell: bool
    ticker: str
    reason: Optional[str]           # EXIT_* constant when should_sell is True
    unrealized_pct: Optional[float]  # % gain/loss vs. avg entry price
    phase: Optional[str] = field(default=None)  # PHASE_1 / PHASE_2 / None


# ── Phase 1 (basic entry check, no market-data required) ─────────────────────


def evaluate_entry(
    ticker: Optional[str],
    amount: float,
    market_cap: float,
    *,
    materiality_threshold: float = MATERIALITY_THRESHOLD,
    already_held: bool = False,
    daily_budget_exhausted: bool = False,
) -> EntryDecision:
    """Phase 1 entry check that does not require live market data.

    This mirrors the legacy `main.process_award` gate and is kept for
    backward compatibility.  For full two-phase logic use `evaluate_phase`.

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


# ── Full two-phase entry evaluation ──────────────────────────────────────────


def evaluate_phase(
    ticker: Optional[str],
    amount: float,
    market_cap: float,
    *,
    daily_volume: Optional[float] = None,
    prev_daily_volume: Optional[float] = None,
    bid_price: Optional[float] = None,
    ask_price: Optional[float] = None,
    volume_spike_multiplier: float = VOLUME_SPIKE_MULTIPLIER,
    max_spread_pct: float = MAX_SPREAD_PCT,
    materiality_threshold: float = MATERIALITY_THRESHOLD,
    phase2_materiality_threshold: float = PHASE2_MATERIALITY_THRESHOLD,
    quick_buy_notional: float = QUICK_BUY_NOTIONAL,
    large_buy_notional: float = LARGE_BUY_NOTIONAL,
    already_held: bool = False,
    daily_budget_exhausted: bool = False,
) -> PhaseDecision:
    """Select which trading phase (if any) to enter for a contract award.

    Decision tree
    -------------
    1. Must have a valid ticker and positive market cap.
    2. The award must pass the basic materiality gate
       (``amount / market_cap >= materiality_threshold``).
    3. Already-held or exhausted daily budget → ``PHASE_NONE``.
    4. **Phase 1** fires when live market data shows a *volume spike*
       (today's daily volume ≥ ``volume_spike_multiplier`` × yesterday's)
       **and** a *tight bid/ask spread*
       (spread / mid ≤ ``max_spread_pct``).
    5. **Phase 2** fires when no Phase-1 trigger is present but the award
       exceeds ``phase2_materiality_threshold`` of market cap.
    6. Otherwise ``PHASE_NONE``.

    Parameters
    ----------
    daily_volume, prev_daily_volume:
        Today's and yesterday's total daily volume (from Alpaca snapshot).
        ``None`` means the data is unavailable; the volume-spike check is
        then skipped (Phase 1 cannot fire).
    bid_price, ask_price:
        Latest bid and ask from the Alpaca snapshot quote.  ``None`` means
        the spread check is skipped; tight_spread defaults to ``False``.
    """
    _none = PhaseDecision(
        phase=PHASE_NONE, ticker=ticker, notional=0.0,
        reason="", volume_spike=False, tight_spread=False, materiality_ratio=0.0,
    )

    if not ticker:
        return PhaseDecision(
            phase=PHASE_NONE, ticker=None, notional=0.0, reason="no ticker match"
        )

    if market_cap <= 0:
        return PhaseDecision(
            phase=PHASE_NONE, ticker=ticker, notional=0.0, reason="unknown market cap"
        )

    materiality_ratio = amount / market_cap
    if materiality_ratio < materiality_threshold:
        return PhaseDecision(
            phase=PHASE_NONE, ticker=ticker, notional=0.0,
            reason="award not material vs market cap",
            materiality_ratio=materiality_ratio,
        )

    if already_held:
        return PhaseDecision(
            phase=PHASE_NONE, ticker=ticker, notional=0.0,
            reason="already holding position",
            materiality_ratio=materiality_ratio,
        )

    if daily_budget_exhausted:
        return PhaseDecision(
            phase=PHASE_NONE, ticker=ticker, notional=0.0,
            reason="daily trade limit reached",
            materiality_ratio=materiality_ratio,
        )

    # Volume spike check.
    volume_spike = (
        daily_volume is not None
        and prev_daily_volume is not None
        and prev_daily_volume > 0
        and daily_volume >= volume_spike_multiplier * prev_daily_volume
    )

    # Tight spread check.
    tight_spread = False
    if (
        bid_price is not None
        and ask_price is not None
        and bid_price > 0
        and ask_price > 0
        and ask_price >= bid_price
    ):
        mid = (bid_price + ask_price) / 2
        spread_pct = (ask_price - bid_price) / mid
        tight_spread = spread_pct <= max_spread_pct

    if volume_spike and tight_spread:
        return PhaseDecision(
            phase=PHASE_1,
            ticker=ticker,
            notional=quick_buy_notional,
            reason="volume spike + tight spread",
            volume_spike=True,
            tight_spread=True,
            materiality_ratio=materiality_ratio,
        )

    if materiality_ratio >= phase2_materiality_threshold:
        return PhaseDecision(
            phase=PHASE_2,
            ticker=ticker,
            notional=large_buy_notional,
            reason="high materiality ratio",
            volume_spike=volume_spike,
            tight_spread=tight_spread,
            materiality_ratio=materiality_ratio,
        )

    return PhaseDecision(
        phase=PHASE_NONE,
        ticker=ticker,
        notional=0.0,
        reason="no phase trigger (no volume spike and below phase-2 materiality)",
        volume_spike=volume_spike,
        tight_spread=tight_spread,
        materiality_ratio=materiality_ratio,
    )


# ── Exit evaluation ──────────────────────────────────────────────────────────


def evaluate_exit(
    ticker: str,
    avg_entry_price: float,
    current_price: float,
    entry_time: Optional[datetime] = None,
    *,
    take_profit_pct: float = TAKE_PROFIT_PCT,
    stop_loss_pct: float = STOP_LOSS_PCT,
    sell_after_days: Optional[int] = SELL_AFTER_DAYS,
    sell_after_hours: Optional[float] = None,
    phase: Optional[str] = None,
) -> ExitDecision:
    """Decide whether to close an open position in *ticker*.

    Exit conditions are evaluated in priority order:

    1. Take-profit   — unrealized gain  ≥ ``take_profit_pct``
    2. Stop-loss     — unrealized loss  ≥ ``stop_loss_pct``
    3. Max hold      — time held        ≥ ``sell_after_hours`` (if set) or
                                          ``sell_after_days``

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
        When ``None`` the time-based exit check is skipped.
    take_profit_pct, stop_loss_pct:
        Override the config-level percentage thresholds.
    sell_after_hours:
        If set, the time-based exit fires after this many hours (used for
        Phase 1's 48-hour rule).  Takes precedence over ``sell_after_days``
        when both are provided.
    sell_after_days:
        Fall-back time-based exit in whole calendar days.
    phase:
        Informational tag (PHASE_1 / PHASE_2) propagated into the result.
    """
    if avg_entry_price <= 0:
        return ExitDecision(
            should_sell=False, ticker=ticker, reason=None,
            unrealized_pct=None, phase=phase,
        )

    unrealized_pct = (current_price - avg_entry_price) / avg_entry_price * 100

    if unrealized_pct >= take_profit_pct:
        return ExitDecision(
            should_sell=True, ticker=ticker, reason=EXIT_TAKE_PROFIT,
            unrealized_pct=unrealized_pct, phase=phase,
        )

    if unrealized_pct <= -stop_loss_pct:
        return ExitDecision(
            should_sell=True, ticker=ticker, reason=EXIT_STOP_LOSS,
            unrealized_pct=unrealized_pct, phase=phase,
        )

    if entry_time is not None:
        now = datetime.now(timezone.utc)
        if entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)

        if sell_after_hours is not None:
            held_hours = (now - entry_time).total_seconds() / 3600
            if held_hours >= sell_after_hours:
                return ExitDecision(
                    should_sell=True, ticker=ticker, reason=EXIT_MAX_HOLD,
                    unrealized_pct=unrealized_pct, phase=phase,
                )
        elif sell_after_days is not None:
            held_days = (now - entry_time).days
            if held_days >= sell_after_days:
                return ExitDecision(
                    should_sell=True, ticker=ticker, reason=EXIT_MAX_HOLD,
                    unrealized_pct=unrealized_pct, phase=phase,
                )

    return ExitDecision(
        should_sell=False, ticker=ticker, reason=None,
        unrealized_pct=unrealized_pct, phase=phase,
    )

