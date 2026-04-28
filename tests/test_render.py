"""Tests for renderer fallbacks and view-mode/filter behavior."""

import io

from rich.console import Console

from dashboard import config_v2, snapshot
from dashboard.render import (
    _filter_analyses,
    _sort_analyses,
    render_dashboard,
)


def _capture(group) -> str:
    buf = io.StringIO()
    Console(file=buf, force_terminal=False, width=200).print(group)
    return buf.getvalue()


def _build(patched_dashboard, awards):
    return snapshot.build_snapshot(
        validate=True,
        fetch_orders=False,
        fetch_alpaca=False,
        toggles={
            **config_v2.load_v2_toggles(),
            "enable_history": False,
        },
        awards_override=awards,
    )


def test_render_dashboard_runs_all_views_without_raising(patched_dashboard):
    from tests.conftest import SAMPLE_AWARDS

    snap = _build(patched_dashboard, SAMPLE_AWARDS)
    for view in (
        config_v2.VIEW_OVERVIEW,
        config_v2.VIEW_CONTRACTS,
        config_v2.VIEW_TICKERS,
        config_v2.VIEW_TRADING,
        config_v2.VIEW_ALL,
    ):
        out = _capture(render_dashboard(snap, view=view, limit=10))
        assert "Dashboard v2" in out


def test_render_handles_empty_awards(patched_dashboard):
    snap = _build(patched_dashboard, [])
    out = _capture(render_dashboard(snap, view=config_v2.VIEW_ALL, limit=5))
    # Empty-state messages must appear, not crash.
    assert "Dashboard v2" in out


def test_filter_analyses_by_recipient_and_min_amount():
    analyses = [
        {"recipient": "Foo Corp", "agency": "DOD", "amount": 100, "match": {"tier": "high"}, "material": True},
        {"recipient": "Bar Inc", "agency": "DOE", "amount": 1000, "match": {"tier": "low"}, "material": False},
        {"recipient": "Foo LLC", "agency": "DOE", "amount": 50, "match": {"tier": "medium"}, "material": True},
    ]
    rows = _filter_analyses(analyses, recipient="foo", min_amount=75)
    assert len(rows) == 1
    assert rows[0]["recipient"] == "Foo Corp"


def test_filter_analyses_min_tier_and_material_only():
    analyses = [
        {"recipient": "A", "agency": "X", "amount": 10, "match": {"tier": "high"}, "material": False},
        {"recipient": "B", "agency": "X", "amount": 10, "match": {"tier": "low"}, "material": True},
        {"recipient": "C", "agency": "X", "amount": 10, "match": {"tier": "medium"}, "material": True},
    ]
    rows = _filter_analyses(analyses, min_tier="medium", material_only=True)
    assert [r["recipient"] for r in rows] == ["C"]


def test_sort_analyses_by_amount_and_confidence():
    analyses = [
        {"recipient": "A", "agency": "X", "amount": 50, "match": {"tier": "high"}},
        {"recipient": "B", "agency": "X", "amount": 200, "match": {"tier": "low"}},
        {"recipient": "C", "agency": "X", "amount": 100, "match": {"tier": "medium"}},
    ]
    by_amount = _sort_analyses(analyses, "amount")
    assert [r["recipient"] for r in by_amount] == ["B", "C", "A"]
    by_conf = _sort_analyses(analyses, "confidence")
    # high first, then medium, then low (with amount as tiebreaker)
    assert by_conf[0]["match"]["tier"] == "high"
    assert by_conf[-1]["match"]["tier"] == "low"
