"""Tests for trade-eligibility evaluation."""

from dashboard.eligibility import BLOCKED, ELIGIBLE, SKIPPED, evaluate_eligibility


def _kw(**overrides):
    base = dict(
        ticker="LMT",
        confidence_tier="high",
        has_market_data=True,
        is_material=True,
        held_symbols=[],
        daily_buys_used=0,
        max_daily_trades=2,
        min_confidence_tier="medium",
    )
    base.update(overrides)
    return base


def test_eligible_when_all_gates_pass():
    e = evaluate_eligibility(**_kw())
    assert e.status == ELIGIBLE
    assert e.reasons == []


def test_skipped_when_no_ticker():
    e = evaluate_eligibility(**_kw(ticker=None))
    assert e.status == SKIPPED
    assert "no ticker match" in e.reasons


def test_skipped_when_low_confidence_below_threshold():
    e = evaluate_eligibility(**_kw(confidence_tier="low"))
    assert e.status == SKIPPED
    assert any("below required" in r for r in e.reasons)


def test_blocked_when_already_holding():
    e = evaluate_eligibility(**_kw(held_symbols=["LMT"]))
    assert e.status == BLOCKED
    assert any("already holding" in r for r in e.reasons)


def test_blocked_when_daily_limit_reached():
    e = evaluate_eligibility(**_kw(daily_buys_used=2, max_daily_trades=2))
    assert e.status == BLOCKED
    assert any("daily trade limit" in r for r in e.reasons)


def test_skipped_when_not_material_or_no_market_data():
    e = evaluate_eligibility(**_kw(is_material=False))
    assert e.status == SKIPPED
    e2 = evaluate_eligibility(**_kw(has_market_data=False))
    assert e2.status == SKIPPED
