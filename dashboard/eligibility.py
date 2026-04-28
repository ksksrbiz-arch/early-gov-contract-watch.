"""
Trade-eligibility context for dashboard v2.

Explains *why* a ticker is or isn't tradable so operators can quickly tell
the difference between "no trade" because of policy (already held / daily
limit reached) versus "no trade" because of data quality (missing market
data, low confidence match, not material).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# Eligibility status constants — part of the snapshot schema.
ELIGIBLE = "eligible"
BLOCKED = "blocked"
SKIPPED = "skipped"


@dataclass
class Eligibility:
    status: str
    reasons: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evaluate_eligibility(
    *,
    ticker: Optional[str],
    confidence_tier: str,
    has_market_data: bool,
    is_material: bool,
    held_symbols: Optional[List[str]] = None,
    daily_buys_used: Optional[int] = None,
    max_daily_trades: Optional[int] = None,
    min_confidence_tier: str = "medium",
) -> Eligibility:
    """
    Decide whether the configured trader would buy this ticker right now.

    The decision mirrors the policy in `trader.AlpacaTrader.can_trade` plus
    the upstream gates in `main.process_award` (materiality + match present).
    Adds an explicit confidence-tier gate so operators can preview the effect
    of stricter ticker matching without changing trading code.
    """
    reasons: List[str] = []
    details: Dict[str, Any] = {
        "confidence_tier": confidence_tier,
        "min_confidence_tier": min_confidence_tier,
        "is_material": is_material,
        "has_market_data": has_market_data,
    }

    if not ticker:
        reasons.append("no ticker match")
        return Eligibility(status=SKIPPED, reasons=reasons, details=details)

    if not has_market_data:
        reasons.append("missing market data (yfinance)")
    if not is_material:
        reasons.append("award not material vs market cap")

    tier_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    if tier_rank.get(confidence_tier, 0) < tier_rank.get(min_confidence_tier, 2):
        reasons.append(
            f"match confidence '{confidence_tier}' below required "
            f"'{min_confidence_tier}'"
        )

    held_symbols = held_symbols or []
    if ticker in held_symbols:
        reasons.append("already holding position")
        details["held"] = True

    if (
        max_daily_trades is not None
        and daily_buys_used is not None
        and daily_buys_used >= max_daily_trades
    ):
        reasons.append(
            f"daily trade limit reached ({daily_buys_used}/{max_daily_trades})"
        )
        details["daily_buys_used"] = daily_buys_used
        details["max_daily_trades"] = max_daily_trades

    if reasons:
        # Distinguish "blocked by policy" from "skipped due to data quality":
        # if the only reasons are policy-based (held / daily limit), surface
        # as BLOCKED; otherwise SKIPPED.
        policy_only = all(
            r.startswith("already holding") or r.startswith("daily trade limit")
            for r in reasons
        )
        return Eligibility(
            status=BLOCKED if policy_only else SKIPPED,
            reasons=reasons,
            details=details,
        )

    return Eligibility(status=ELIGIBLE, reasons=[], details=details)
