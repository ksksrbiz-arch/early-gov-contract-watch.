"""Tests for the two-phase profit model (evaluate_entry + evaluate_exit)."""

from datetime import datetime, timedelta, timezone

import pytest

from two_phase_profit import (
    EXIT_MAX_HOLD,
    EXIT_STOP_LOSS,
    EXIT_TAKE_PROFIT,
    EntryDecision,
    ExitDecision,
    evaluate_entry,
    evaluate_exit,
)

# ── evaluate_entry ────────────────────────────────────────────────────────────


def test_entry_no_ticker_returns_no_buy():
    d = evaluate_entry(None, 100_000_000, 1_000_000_000)
    assert isinstance(d, EntryDecision)
    assert not d.should_buy
    assert d.ticker is None
    assert "no ticker match" in d.reason


def test_entry_zero_market_cap_not_material():
    d = evaluate_entry("LMT", 100_000_000, 0)
    assert not d.should_buy
    assert "material" in d.reason


def test_entry_ratio_below_threshold_not_material():
    # $1 000 on a $1B company is 0.0000001 — below the 0.5% default.
    d = evaluate_entry("LMT", 1_000, 1_000_000_000, materiality_threshold=0.005)
    assert not d.should_buy
    assert "material" in d.reason


def test_entry_already_held_blocks_buy():
    d = evaluate_entry("LMT", 100_000_000, 1_000_000_000, already_held=True)
    assert not d.should_buy
    assert "already holding" in d.reason


def test_entry_daily_budget_exhausted_blocks_buy():
    d = evaluate_entry("LMT", 100_000_000, 1_000_000_000, daily_budget_exhausted=True)
    assert not d.should_buy
    assert "daily trade limit" in d.reason


def test_entry_eligible_returns_should_buy():
    # $100M on a $10B company = 1% — above 0.5% threshold.
    d = evaluate_entry("LMT", 100_000_000, 10_000_000_000, materiality_threshold=0.005)
    assert d.should_buy
    assert d.ticker == "LMT"
    assert "confirmed" in d.reason


def test_entry_checks_materiality_before_held():
    # Not material → should NOT buy regardless of held flag.
    d = evaluate_entry(
        "LMT", 1_000, 1_000_000_000, materiality_threshold=0.005, already_held=False
    )
    assert not d.should_buy
    assert "material" in d.reason


# ── evaluate_exit ─────────────────────────────────────────────────────────────


def test_exit_returns_exit_decision_type():
    d = evaluate_exit("LMT", 100.0, 105.0)
    assert isinstance(d, ExitDecision)


def test_exit_take_profit_triggers_above_threshold():
    d = evaluate_exit("LMT", 100.0, 113.0, take_profit_pct=12.0, stop_loss_pct=6.0, sell_after_days=5)
    assert d.should_sell
    assert d.reason == EXIT_TAKE_PROFIT
    assert d.unrealized_pct == pytest.approx(13.0, rel=1e-3)


def test_exit_take_profit_triggers_at_exact_threshold():
    d = evaluate_exit("LMT", 100.0, 112.0, take_profit_pct=12.0, stop_loss_pct=6.0, sell_after_days=5)
    assert d.should_sell
    assert d.reason == EXIT_TAKE_PROFIT


def test_exit_stop_loss_triggers_below_threshold():
    d = evaluate_exit("LMT", 100.0, 93.0, take_profit_pct=12.0, stop_loss_pct=6.0, sell_after_days=5)
    assert d.should_sell
    assert d.reason == EXIT_STOP_LOSS
    assert d.unrealized_pct == pytest.approx(-7.0, rel=1e-3)


def test_exit_stop_loss_triggers_at_exact_threshold():
    d = evaluate_exit("LMT", 100.0, 94.0, take_profit_pct=12.0, stop_loss_pct=6.0, sell_after_days=5)
    assert d.should_sell
    assert d.reason == EXIT_STOP_LOSS


def test_exit_max_hold_triggers_when_hold_exceeds_days():
    entry_time = datetime.now(timezone.utc) - timedelta(days=6)
    d = evaluate_exit(
        "LMT", 100.0, 103.0, entry_time,
        take_profit_pct=12.0, stop_loss_pct=6.0, sell_after_days=5,
    )
    assert d.should_sell
    assert d.reason == EXIT_MAX_HOLD


def test_exit_max_hold_handles_naive_datetime():
    entry_time = datetime.utcnow() - timedelta(days=6)  # naive UTC
    d = evaluate_exit(
        "LMT", 100.0, 103.0, entry_time,
        take_profit_pct=12.0, stop_loss_pct=6.0, sell_after_days=5,
    )
    assert d.should_sell
    assert d.reason == EXIT_MAX_HOLD


def test_exit_no_signal_within_all_thresholds():
    entry_time = datetime.now(timezone.utc) - timedelta(days=2)
    d = evaluate_exit(
        "LMT", 100.0, 105.0, entry_time,
        take_profit_pct=12.0, stop_loss_pct=6.0, sell_after_days=5,
    )
    assert not d.should_sell
    assert d.reason is None
    assert d.unrealized_pct == pytest.approx(5.0, rel=1e-3)


def test_exit_no_entry_time_skips_max_hold_check():
    d = evaluate_exit(
        "LMT", 100.0, 105.0, None,
        take_profit_pct=12.0, stop_loss_pct=6.0, sell_after_days=5,
    )
    assert not d.should_sell


def test_exit_zero_entry_price_returns_no_sell():
    d = evaluate_exit("LMT", 0.0, 105.0)
    assert not d.should_sell
    assert d.unrealized_pct is None


def test_exit_negative_entry_price_returns_no_sell():
    d = evaluate_exit("LMT", -10.0, 105.0)
    assert not d.should_sell
    assert d.unrealized_pct is None


def test_exit_take_profit_checked_before_max_hold():
    """Take-profit should fire even when the position is old enough for max_hold."""
    entry_time = datetime.now(timezone.utc) - timedelta(days=10)
    d = evaluate_exit(
        "LMT", 100.0, 115.0, entry_time,
        take_profit_pct=12.0, stop_loss_pct=6.0, sell_after_days=5,
    )
    assert d.should_sell
    assert d.reason == EXIT_TAKE_PROFIT


def test_exit_stop_loss_checked_before_max_hold():
    """Stop-loss should fire even when the position is old enough for max_hold."""
    entry_time = datetime.now(timezone.utc) - timedelta(days=10)
    d = evaluate_exit(
        "LMT", 100.0, 90.0, entry_time,
        take_profit_pct=12.0, stop_loss_pct=6.0, sell_after_days=5,
    )
    assert d.should_sell
    assert d.reason == EXIT_STOP_LOSS


def test_exit_unrealized_pct_reported_on_no_signal():
    d = evaluate_exit("LMT", 200.0, 210.0, take_profit_pct=20.0, stop_loss_pct=10.0, sell_after_days=30)
    assert not d.should_sell
    assert d.unrealized_pct == pytest.approx(5.0, rel=1e-3)
