"""Tests for the ticker confidence classifier."""

from dashboard.confidence import (
    REASON_FUZZY,
    REASON_NONE,
    REASON_SUBSTRING,
    TIER_HIGH,
    TIER_NONE,
    classify_match,
)
from tests.conftest import SAMPLE_COMPANIES


def test_substring_match_is_high_tier():
    res = classify_match("LOCKHEED MARTIN CORPORATION", SAMPLE_COMPANIES)
    assert res.ticker == "LMT"
    assert res.tier == TIER_HIGH
    assert res.reason == REASON_SUBSTRING
    assert res.score == 100.0
    assert res.ambiguous is False


def test_no_match_when_unknown_recipient():
    res = classify_match("ACME UNKNOWN LLC", SAMPLE_COMPANIES)
    assert res.ticker is None
    assert res.tier == TIER_NONE
    assert res.reason == REASON_NONE


def test_empty_recipient_returns_empty_reason():
    res = classify_match("", SAMPLE_COMPANIES)
    assert res.ticker is None
    assert res.tier == TIER_NONE
    assert res.reason == "empty_recipient"


def test_fuzzy_match_returns_score_and_reason():
    res = classify_match("Lockheed Martin Corp.", SAMPLE_COMPANIES)
    # Substring vs fuzzy depends on punctuation; in either case we should map
    # to LMT and get a meaningful tier.
    assert res.ticker == "LMT"
    assert res.tier in {TIER_HIGH, "medium", "low"}
    assert res.reason in {REASON_SUBSTRING, REASON_FUZZY}
