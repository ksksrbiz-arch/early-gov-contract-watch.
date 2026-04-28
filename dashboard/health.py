"""
Source health and freshness diagnostics for dashboard v2.

Each section of the dashboard exposes its own freshness/health status so users
can trust panels independently even when one upstream source is degraded.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# Status constants — keep stable; they are part of the versioned schema.
STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_UNAVAILABLE = "unavailable"
STATUS_UNKNOWN = "unknown"
STATUS_NOT_CONFIGURED = "not_configured"


@dataclass
class SectionHealth:
    """Freshness/health for a single dashboard section or upstream source."""

    name: str
    status: str = STATUS_UNKNOWN
    message: str = ""
    checked_at: Optional[str] = None
    last_success_at: Optional[str] = None
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def _now(self) -> str:
        return datetime.now().isoformat(timespec="seconds")

    def mark_ok(self, message: str = "", **details: Any) -> "SectionHealth":
        now = self._now()
        self.status = STATUS_OK
        self.message = message
        self.checked_at = now
        self.last_success_at = now
        self.error = None
        if details:
            self.details.update(details)
        return self

    def mark_degraded(
        self, message: str, error: Optional[str] = None, **details: Any
    ) -> "SectionHealth":
        self.status = STATUS_DEGRADED
        self.message = message
        self.checked_at = self._now()
        self.error = error
        if details:
            self.details.update(details)
        return self

    def mark_unavailable(
        self, message: str, error: Optional[str] = None, **details: Any
    ) -> "SectionHealth":
        self.status = STATUS_UNAVAILABLE
        self.message = message
        self.checked_at = self._now()
        self.error = error
        if details:
            self.details.update(details)
        return self

    def mark_not_configured(
        self, message: str = "not configured"
    ) -> "SectionHealth":
        self.status = STATUS_NOT_CONFIGURED
        self.message = message
        self.checked_at = self._now()
        return self

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def state_file_health(state_file: str, poll_interval_minutes: int) -> SectionHealth:
    """Inspect the bot's state.json for freshness and integrity."""
    h = SectionHealth(name="bot_state")
    if not os.path.exists(state_file):
        h.mark_unavailable(
            "state file not found — bot has not run yet",
            path=state_file,
        )
        return h
    try:
        with open(state_file) as f:
            state = json.load(f)
    except Exception as exc:  # noqa: BLE001
        h.mark_degraded("state file is unreadable", error=str(exc), path=state_file)
        return h

    mtime_dt = datetime.fromtimestamp(os.path.getmtime(state_file))
    age_min = (datetime.now() - mtime_dt).total_seconds() / 60.0
    seen_count = len(state.get("seen_award_ids", []) or [])
    last_check = state.get("last_check")

    details = {
        "path": state_file,
        "age_minutes": round(age_min, 1),
        "last_modified": mtime_dt.isoformat(timespec="seconds"),
        "seen_award_ids": seen_count,
        "last_check": last_check,
    }

    if age_min <= max(poll_interval_minutes * 1.5, 1):
        h.mark_ok("fresh", **details)
    elif age_min <= max(poll_interval_minutes * 4, 1):
        h.mark_degraded("stale (older than 1.5x poll interval)", **details)
    else:
        h.mark_unavailable("cold (older than 4x poll interval)", **details)
    return h


def usaspending_health(
    awards: Optional[List[dict]], fetch_error: Optional[str]
) -> SectionHealth:
    """Health for the USASpending fetch result."""
    h = SectionHealth(name="usaspending")
    if fetch_error:
        h.mark_unavailable("USASpending fetch failed", error=fetch_error)
        return h
    if awards is None:
        h.mark_degraded("no awards payload available")
        return h
    h.mark_ok(f"fetched {len(awards)} award(s)", award_count=len(awards))
    return h


def ticker_source_health(
    cache_file: str = "sec_tickers_cache.json",
) -> SectionHealth:
    """Health for the SEC ticker cache that backs ticker resolution."""
    h = SectionHealth(name="ticker_source")
    if not os.path.exists(cache_file):
        h.mark_degraded(
            "SEC ticker cache missing — first lookup will download",
            path=cache_file,
        )
        return h
    try:
        mtime_dt = datetime.fromtimestamp(os.path.getmtime(cache_file))
        age_hours = (datetime.now() - mtime_dt).total_seconds() / 3600.0
        with open(cache_file) as f:
            data = json.load(f)
        h.mark_ok(
            "SEC ticker cache loaded",
            path=cache_file,
            entries=len(data) if isinstance(data, dict) else None,
            age_hours=round(age_hours, 1),
            last_modified=mtime_dt.isoformat(timespec="seconds"),
        )
    except Exception as exc:  # noqa: BLE001
        h.mark_degraded("SEC ticker cache unreadable", error=str(exc), path=cache_file)
    return h


def alpaca_health(
    api_key_present: bool, error: Optional[str] = None
) -> SectionHealth:
    """Health for the Alpaca trading client connectivity."""
    h = SectionHealth(name="alpaca")
    if not api_key_present:
        h.mark_not_configured("ALPACA_API_KEY not set")
        return h
    if error:
        h.mark_unavailable("Alpaca client unavailable", error=error)
        return h
    h.mark_ok("Alpaca client connected")
    return h


def config_health(issues: List[str], warnings: List[str]) -> SectionHealth:
    """Aggregate config validation issues into a section health entry."""
    h = SectionHealth(name="config")
    if issues:
        h.mark_unavailable(
            f"{len(issues)} configuration issue(s)",
            issues=issues,
            warnings=warnings,
        )
    elif warnings:
        h.mark_degraded(
            f"{len(warnings)} configuration warning(s)",
            issues=[],
            warnings=warnings,
        )
    else:
        h.mark_ok("configuration looks healthy")
    return h
