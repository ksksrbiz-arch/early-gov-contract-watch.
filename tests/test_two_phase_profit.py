"""Tests for the two-phase profit model (evaluate_entry + evaluate_phase + evaluate_exit)."""

from datetime import datetime, timedelta, timezone

import pytest

from two_phase_profit import (
    EXIT_MAX_HOLD,
    EXIT_STOP_LOSS,
    EXIT_TAKE_PROFIT,
    PHASE_1,
    PHASE_2,
    PHASE_NONE,
    EntryDecision,
    ExitDecision,
    PhaseDecision,
    evaluate_entry,
    evaluate_exit,
    evaluate_phase,
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
    d = evaluate_entry("LMT", 100_000_000, 10_000_000_000, materiality_threshold=0.005)
    assert d.should_buy
    assert d.ticker == "LMT"
    assert "confirmed" in d.reason


def test_entry_checks_materiality_before_held():
    d = evaluate_entry(
        "LMT", 1_000, 1_000_000_000, materiality_threshold=0.005, already_held=False
    )
    assert not d.should_buy
    assert "material" in d.reason


# ── evaluate_phase ────────────────────────────────────────────────────────────


def _phase_kwargs(**overrides):
    """Defaults that produce a material Phase-1-or-2 eligible scenario."""
    defaults = dict(
        ticker="LMT",
        amount=100_000_000,
        market_cap=10_000_000_000,
        materiality_threshold=0.005,
        phase2_materiality_threshold=0.0075,
        quick_buy_notional=300.0,
        large_buy_notional=2000.0,
        volume_spike_multiplier=2.0,
        max_spread_pct=0.005,
    )
    defaults.update(overrides)
    return defaults


def test_phase_no_ticker_returns_none():
    d = evaluate_phase(None, 100_000_000, 10_000_000_000)
    assert isinstance(d, PhaseDecision)
    assert d.phase == PHASE_NONE


def test_phase_zero_market_cap_returns_none():
    d = evaluate_phase(**_phase_kwargs(market_cap=0))
    assert d.phase == PHASE_NONE


def test_phase_below_basic_materiality_returns_none():
    # $1k on $10B = 0.00001% — below 0.5% threshold
    d = evaluate_phase(**_phase_kwargs(amount=1_000))
    assert d.phase == PHASE_NONE
    assert "material" in d.reason


def test_phase_already_held_returns_none():
    d = evaluate_phase(**_phase_kwargs(already_held=True))
    assert d.phase == PHASE_NONE


def test_phase_daily_budget_exhausted_returns_none():
    d = evaluate_phase(**_phase_kwargs(daily_budget_exhausted=True))
    assert d.phase == PHASE_NONE


def test_phase1_fires_on_volume_spike_and_tight_spread():
    d = evaluate_phase(
        **_phase_kwargs(
            daily_volume=2_000_000,
            prev_daily_volume=800_000,      # 2.5× → spike
            bid_price=99.75,
            ask_price=100.25,               # spread 0.5% → tight
        )
    )
    assert d.phase == PHASE_1
    assert d.notional == 300.0
    assert d.volume_spike is True
    assert d.tight_spread is True


def test_phase1_does_not_fire_without_tight_spread():
    d = evaluate_phase(
        **_phase_kwargs(
            daily_volume=2_000_000,
            prev_daily_volume=800_000,
            bid_price=95.0,
            ask_price=105.0,                # spread 10% → wide
        )
    )
    # Materialiy ratio 1% ≥ 0.75% → Phase 2 instead
    assert d.phase == PHASE_2


def test_phase1_does_not_fire_without_volume_spike():
    d = evaluate_phase(
        **_phase_kwargs(
            daily_volume=900_000,
            prev_daily_volume=800_000,      # only 1.125× → no spike
            bid_price=99.75,
            ask_price=100.25,
        )
    )
    # Ratio 1% ≥ 0.75% → Phase 2
    assert d.phase == PHASE_2


def test_phase2_fires_on_high_materiality_no_market_data():
    # No volume/spread data available; ratio is above 0.75% threshold
    d = evaluate_phase(**_phase_kwargs())
    # 100M / 10B = 1% ≥ 0.75%
    assert d.phase == PHASE_2
    assert d.notional == 2000.0


def test_phase_none_when_below_phase2_threshold_and_no_spike():
    # ratio 0.6% — above basic materiality (0.5%) but below phase2 (0.75%)
    d = evaluate_phase(**_phase_kwargs(amount=60_000_000))
    assert d.phase == PHASE_NONE
    assert "no phase trigger" in d.reason


def test_phase_checks_materiality_before_held():
    d = evaluate_phase(**_phase_kwargs())
    assert d.materiality_ratio == pytest.approx(0.01, rel=1e-3)


def test_phase1_notional_and_phase_label():
    d = evaluate_phase(
        **_phase_kwargs(
            daily_volume=3_000_000,
            prev_daily_volume=1_000_000,
            bid_price=100.0,
            ask_price=100.4,
        )
    )
    assert d.phase == PHASE_1
    assert d.notional == 300.0


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
    entry_time = datetime(2020, 1, 1)  # naive UTC, well in the past
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


def test_exit_sell_after_hours_triggers():
    entry_time = datetime.now(timezone.utc) - timedelta(hours=50)
    d = evaluate_exit(
        "LMT", 100.0, 103.0, entry_time,
        take_profit_pct=12.0, stop_loss_pct=5.0, sell_after_hours=48.0,
    )
    assert d.should_sell
    assert d.reason == EXIT_MAX_HOLD


def test_exit_sell_after_hours_does_not_trigger_before_time():
    entry_time = datetime.now(timezone.utc) - timedelta(hours=30)
    d = evaluate_exit(
        "LMT", 100.0, 103.0, entry_time,
        take_profit_pct=12.0, stop_loss_pct=5.0, sell_after_hours=48.0,
    )
    assert not d.should_sell


def test_exit_sell_after_hours_takes_precedence_over_days():
    """If sell_after_hours is set, sell_after_days should be ignored."""
    entry_time = datetime.now(timezone.utc) - timedelta(hours=50)
    # 50h > 48h → fires via hours; 50h is only ~2 days < 5 days
    d = evaluate_exit(
        "LMT", 100.0, 103.0, entry_time,
        take_profit_pct=12.0, stop_loss_pct=5.0,
        sell_after_hours=48.0,
        sell_after_days=5,
    )
    assert d.should_sell
    assert d.reason == EXIT_MAX_HOLD


def test_exit_phase_tag_propagated():
    d = evaluate_exit("LMT", 100.0, 105.0, phase=PHASE_2)
    assert d.phase == PHASE_2


def test_exit_phase_defaults_to_none():
    d = evaluate_exit("LMT", 100.0, 105.0)
    assert d.phase is None

