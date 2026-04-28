"""
Ticker-match confidence tiers and reasoning for dashboard v2.

The base `ticker_lookup.get_ticker_for_company` only returns the best match.
For v2 we re-run the same matching logic to expose:
  - the confidence tier (high / medium / low / none)
  - the reason (substring vs fuzzy vs no-match)
  - the score (0-100 fuzzy WRatio)
  - any near-tied alternative candidates (ambiguity signal)

Mirrors the matching behavior in `ticker_lookup.get_ticker_for_company` so
the snapshot's chosen ticker is always the same as what `main.py` would
trade on — confidence metadata is layered on top, never substituted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# Confidence tier constants — part of the snapshot schema.
TIER_HIGH = "high"
TIER_MEDIUM = "medium"
TIER_LOW = "low"
TIER_NONE = "none"

# Match reason constants.
REASON_SUBSTRING = "substring"
REASON_FUZZY = "fuzzy"
REASON_NONE = "no_match"
REASON_EMPTY = "empty_recipient"


@dataclass
class MatchResult:
    ticker: Optional[str]
    tier: str
    reason: str
    score: Optional[float]
    matched_title: Optional[str]
    alternatives: List[Dict[str, Any]] = field(default_factory=list)
    ambiguous: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _score_to_tier(score: Optional[float], reason: str) -> str:
    if reason == REASON_SUBSTRING:
        return TIER_HIGH
    if reason == REASON_NONE or score is None:
        return TIER_NONE
    if score >= 95:
        return TIER_HIGH
    if score >= 90:
        return TIER_MEDIUM
    return TIER_LOW


def _empty(reason: str = REASON_NONE, score: Optional[float] = None) -> MatchResult:
    return MatchResult(
        ticker=None,
        tier=TIER_NONE,
        reason=reason,
        score=score,
        matched_title=None,
        alternatives=[],
        ambiguous=False,
    )


def classify_match(
    name: str,
    companies: List[Tuple[str, str]],
    *,
    min_score: int = 85,
    ambiguity_window: int = 3,
) -> MatchResult:
    """
    Classify ticker match for a recipient name.

    `companies` is a pre-computed list of `(title, ticker)` pairs from the SEC
    ticker source. The function mirrors
    `ticker_lookup.get_ticker_for_company` (substring first, fuzzy
    `WRatio` fallback) so the chosen ticker stays consistent with what the
    bot trades on, and adds tier/reason/alternatives metadata on top.
    """
    if not name:
        return _empty(reason=REASON_EMPTY)

    name_upper = name.upper()
    for title, ticker in companies:
        title_upper = title.upper()
        if name_upper in title_upper or title_upper in name_upper:
            return MatchResult(
                ticker=ticker,
                tier=TIER_HIGH,
                reason=REASON_SUBSTRING,
                score=100.0,
                matched_title=title,
                alternatives=[],
                ambiguous=False,
            )

    # Fuzzy fallback — guard against missing rapidfuzz at runtime.
    try:
        from rapidfuzz import fuzz, process  # type: ignore
    except Exception:  # pragma: no cover — defensive
        return _empty()

    titles = [c[0] for c in companies]
    if not titles:
        return _empty()

    matches = process.extract(
        name, titles, scorer=fuzz.WRatio, limit=ambiguity_window
    )
    if not matches:
        return _empty()

    best_title, best_score, best_idx = matches[0]
    if best_score < min_score:
        return MatchResult(
            ticker=None,
            tier=TIER_NONE,
            reason=REASON_NONE,
            score=float(best_score),
            matched_title=None,
            alternatives=[
                {"title": t, "ticker": companies[i][1], "score": float(s)}
                for t, s, i in matches
            ],
            ambiguous=False,
        )

    alternatives = [
        {"title": t, "ticker": companies[i][1], "score": float(s)}
        for t, s, i in matches[1:]
        if s >= min_score - 5
    ]
    # Ambiguous if a runner-up is within 3 points of the winner.
    ambiguous = bool(
        alternatives and (best_score - alternatives[0]["score"]) <= 3
    )

    return MatchResult(
        ticker=companies[best_idx][1],
        tier=_score_to_tier(float(best_score), REASON_FUZZY),
        reason=REASON_FUZZY,
        score=float(best_score),
        matched_title=best_title,
        alternatives=alternatives,
        ambiguous=ambiguous,
    )
