"""
Configuration validation and v2 toggles.

Surfaces config quality issues directly in the dashboard so misconfiguration
is visible before live trading. v2 toggles let operators keep the lightweight
fast-path while opting into heavier modules.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

# Values for DASHBOARD_TICKER_MIN_CONFIDENCE — used by eligibility gating.
_VALID_CONFIDENCE_TIERS = {"none", "low", "medium", "high"}

# v2 export profile names.
PROFILE_COMPACT = "compact"
PROFILE_FULL = "full"
_VALID_PROFILES = {PROFILE_COMPACT, PROFILE_FULL}

# v2 view modes for the dashboard CLI.
VIEW_OVERVIEW = "overview"
VIEW_CONTRACTS = "contracts"
VIEW_TICKERS = "tickers"
VIEW_TRADING = "trading"
VIEW_ALL = "all"
_VALID_VIEWS = {VIEW_OVERVIEW, VIEW_CONTRACTS, VIEW_TICKERS, VIEW_TRADING, VIEW_ALL}


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_v2_toggles() -> Dict[str, Any]:
    """Read v2-only toggles from environment (all optional)."""
    profile = os.getenv("DASHBOARD_EXPORT_PROFILE", PROFILE_FULL).strip().lower()
    if profile not in _VALID_PROFILES:
        profile = PROFILE_FULL

    min_tier = (
        os.getenv("DASHBOARD_TICKER_MIN_CONFIDENCE", "medium").strip().lower()
    )
    if min_tier not in _VALID_CONFIDENCE_TIERS:
        min_tier = "medium"

    return {
        "enable_analytics": _bool_env("DASHBOARD_ENABLE_ANALYTICS", True),
        "enable_anomalies": _bool_env("DASHBOARD_ENABLE_ANOMALIES", True),
        "enable_history": _bool_env("DASHBOARD_ENABLE_HISTORY", True),
        "history_file": os.getenv(
            "DASHBOARD_HISTORY_FILE", "dashboard_history.json"
        ),
        "history_limit": int(os.getenv("DASHBOARD_HISTORY_LIMIT", "30")),
        "export_profile": profile,
        "ticker_min_confidence": min_tier,
        "trend_days": int(os.getenv("DASHBOARD_TREND_DAYS", "14")),
        "trend_weeks": int(os.getenv("DASHBOARD_TREND_WEEKS", "4")),
    }


def validate_config(config_view: Dict[str, Any]) -> Tuple[List[str], List[str]]:
    """
    Return (issues, warnings) for the active configuration view.

    `config_view` is the dict produced by `snapshot._config_view()`.
    Issues are blockers that should stop or alert; warnings are advisory.
    """
    issues: List[str] = []
    warnings: List[str] = []

    if not config_view.get("alpaca_api_key_set"):
        warnings.append(
            "ALPACA_API_KEY is not set — Alpaca panels and trading disabled"
        )

    if not config_view.get("alpaca_paper", True):
        warnings.append(
            "Alpaca is in LIVE mode — verify intentional before deploying"
        )

    buy_notional = float(config_view.get("buy_notional") or 0)
    if buy_notional <= 0:
        issues.append("BUY_NOTIONAL must be > 0")
    elif buy_notional > 10_000:
        warnings.append(
            f"BUY_NOTIONAL is unusually large (${buy_notional:,.0f}) — "
            "double-check before live trading"
        )

    min_amt = float(config_view.get("min_contract_amount") or 0)
    if min_amt <= 0:
        issues.append("MIN_CONTRACT_AMOUNT must be > 0")

    poll = int(config_view.get("poll_interval_minutes") or 0)
    if poll < 1:
        issues.append("POLL_INTERVAL_MINUTES must be >= 1")

    materiality = float(config_view.get("materiality_threshold") or 0)
    if materiality <= 0 or materiality > 1:
        issues.append(
            "MATERIALITY_THRESHOLD must be between 0 and 1 (e.g. 0.005 for 0.5%)"
        )

    days_lookback = int(config_view.get("days_lookback") or 0)
    if days_lookback <= 0:
        issues.append("DAYS_LOOKBACK must be >= 1")

    return issues, warnings
