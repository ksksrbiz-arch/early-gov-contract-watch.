"""
Contract analytics for dashboard v2: trends, concentration, anomalies, and
profile signals (repeat awards, large-award frequency).

All functions are pure: they take a list of award dicts (USASpending shape)
and return analytics that fit cleanly into the versioned snapshot schema.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional


def _amount(award: dict) -> float:
    try:
        return float(award.get("Award Amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def _action_date(award: dict) -> Optional[date]:
    raw = award.get("Action Date")
    if not raw:
        return None
    raw_str = str(raw)[:10]
    try:
        return datetime.strptime(raw_str, "%Y-%m-%d").date()
    except ValueError:
        return None


def daily_trend(awards: List[dict], *, days: int = 14) -> List[Dict[str, Any]]:
    """Per-day totals/counts for the last `days` days (most recent first)."""
    if days <= 0:
        return []
    today = date.today()
    window_start = today - timedelta(days=days - 1)
    buckets: Dict[date, Dict[str, float]] = {
        window_start + timedelta(days=i): {"count": 0, "total": 0.0}
        for i in range(days)
    }
    for a in awards:
        d = _action_date(a)
        if d is None or d < window_start or d > today:
            continue
        b = buckets[d]
        b["count"] += 1
        b["total"] += _amount(a)
    return [
        {
            "date": d.isoformat(),
            "count": int(buckets[d]["count"]),
            "total": float(buckets[d]["total"]),
        }
        for d in sorted(buckets.keys(), reverse=True)
    ]


def weekly_trend(awards: List[dict], *, weeks: int = 4) -> List[Dict[str, Any]]:
    """Per-ISO-week totals/counts for the last `weeks` weeks (most recent first)."""
    if weeks <= 0:
        return []
    today = date.today()
    buckets: Dict[str, Dict[str, float]] = defaultdict(
        lambda: {"count": 0, "total": 0.0}
    )
    cutoff = today - timedelta(weeks=weeks)
    for a in awards:
        d = _action_date(a)
        if d is None or d <= cutoff:
            continue
        iso_year, iso_week, _ = d.isocalendar()
        key = f"{iso_year}-W{iso_week:02d}"
        b = buckets[key]
        b["count"] += 1
        b["total"] += _amount(a)
    rows = [
        {"week": k, "count": int(v["count"]), "total": float(v["total"])}
        for k, v in buckets.items()
    ]
    rows.sort(key=lambda r: r["week"], reverse=True)
    return rows[:weeks]


def concentration(awards: List[dict], key: str, *, top_n: int = 5) -> Dict[str, Any]:
    """
    Compute concentration metrics keyed by `key` (e.g. 'Recipient Name').

    Returns total $, count, top-N share, and a Herfindahl–Hirschman Index
    (HHI, 0-10000) over share-of-total-dollars. Higher HHI = more concentrated.
    """
    totals: Dict[str, float] = defaultdict(float)
    counts: Dict[str, int] = defaultdict(int)
    for a in awards:
        name = a.get(key) or "(unknown)"
        totals[name] += _amount(a)
        counts[name] += 1

    grand_total = sum(totals.values())
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    top = ranked[:top_n]
    top_share = (sum(v for _, v in top) / grand_total) if grand_total else 0.0

    if grand_total > 0:
        hhi = sum(((v / grand_total) * 100) ** 2 for v in totals.values())
    else:
        hhi = 0.0

    return {
        "key": key,
        "total": grand_total,
        "unique": len(totals),
        "top_n": top_n,
        "top_share": top_share,
        "hhi": hhi,
        "top": [
            {"name": n, "count": counts[n], "total": totals[n]} for n, _ in top
        ],
    }


def repeat_recipients(
    awards: List[dict], *, min_awards: int = 2, top_n: int = 10
) -> List[Dict[str, Any]]:
    """Recipients that appear ≥ min_awards times in the window."""
    by_recipient: Dict[str, List[float]] = defaultdict(list)
    for a in awards:
        name = a.get("Recipient Name") or "(unknown)"
        by_recipient[name].append(_amount(a))
    rows = []
    for name, amounts in by_recipient.items():
        if len(amounts) < min_awards:
            continue
        rows.append(
            {
                "recipient": name,
                "awards": len(amounts),
                "total": float(sum(amounts)),
                "avg": float(sum(amounts) / len(amounts)),
                "max": float(max(amounts)),
            }
        )
    rows.sort(key=lambda r: (r["awards"], r["total"]), reverse=True)
    return rows[:top_n]


def anomaly_flags(
    awards: List[dict], *, z_threshold: float = 2.5
) -> List[Dict[str, Any]]:
    """
    Per-award anomaly flags: outlier amounts (z-score) and missing fields.

    The detector is intentionally simple — it flags signals worth a human
    glance, not strict statistical truth.
    """
    flags: List[Dict[str, Any]] = []
    amounts = [_amount(a) for a in awards if _amount(a) > 0]
    mean = statistics.fmean(amounts) if amounts else 0.0
    stdev = statistics.pstdev(amounts) if len(amounts) > 1 else 0.0

    for a in awards:
        reasons: List[str] = []
        amt = _amount(a)
        if stdev > 0 and amt > 0:
            z = (amt - mean) / stdev
            if z >= z_threshold:
                reasons.append(f"amount {z:.1f}σ above mean")
        if not (a.get("Recipient Name") or "").strip():
            reasons.append("missing recipient name")
        if not (a.get("Awarding Agency") or "").strip():
            reasons.append("missing awarding agency")
        desc = (a.get("Description") or "").strip()
        if len(desc) < 10:
            reasons.append("very short description")
        if reasons:
            flags.append(
                {
                    "award_id": a.get("Award ID"),
                    "recipient": a.get("Recipient Name"),
                    "amount": amt,
                    "reasons": reasons,
                }
            )
    return flags


def basic_stats(awards: List[dict]) -> Dict[str, Any]:
    """Headline numbers for the summary panel."""
    amounts = [_amount(a) for a in awards]
    total = float(sum(amounts))
    n = len(awards)
    return {
        "count": n,
        "total": total,
        "avg": float(total / n) if n else 0.0,
        "median": float(statistics.median(amounts)) if amounts else 0.0,
        "max": float(max(amounts)) if amounts else 0.0,
        "min": float(min(amounts)) if amounts else 0.0,
        "unique_recipients": len({a.get("Recipient Name") for a in awards}),
        "unique_agencies": len({a.get("Awarding Agency") for a in awards}),
    }


def agency_recipient_counts(awards: List[dict]) -> Dict[str, Any]:
    """Quick frequency tables (used both in render and trend deltas)."""
    return {
        "by_recipient": dict(
            Counter((a.get("Recipient Name") or "(unknown)") for a in awards)
        ),
        "by_agency": dict(
            Counter((a.get("Awarding Agency") or "(unknown)") for a in awards)
        ),
    }


def trend_deltas(
    current_stats: Dict[str, Any], previous_stats: Optional[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Compare current basic stats to the previous snapshot's stats.

    Returns absolute and percentage deltas for count and total. Returns an
    empty dict if no previous baseline exists.
    """
    if not previous_stats:
        return {}

    def _delta(curr: float, prev: float) -> Dict[str, float]:
        diff = curr - prev
        pct = (diff / prev * 100) if prev else (math.inf if diff else 0.0)
        return {"current": curr, "previous": prev, "diff": diff, "pct": pct}

    return {
        "count": _delta(
            float(current_stats.get("count", 0)),
            float(previous_stats.get("count", 0)),
        ),
        "total": _delta(
            float(current_stats.get("total", 0.0)),
            float(previous_stats.get("total", 0.0)),
        ),
        "avg": _delta(
            float(current_stats.get("avg", 0.0)),
            float(previous_stats.get("avg", 0.0)),
        ),
    }
