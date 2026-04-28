"""Tests for snapshot assembly + export profiles + history."""

import json
import os

from dashboard import config_v2, snapshot


def _toggles(**overrides):
    t = config_v2.load_v2_toggles()
    t.update(overrides)
    return t


def test_snapshot_has_versioned_schema_and_required_sections(patched_dashboard, monkeypatch):
    from tests.conftest import SAMPLE_AWARDS

    snap = snapshot.build_snapshot(
        validate=True,
        fetch_orders=False,
        fetch_alpaca=False,
        toggles=_toggles(enable_history=False),
        awards_override=SAMPLE_AWARDS,
    )
    assert snap["schema_version"] == snapshot.SNAPSHOT_SCHEMA_VERSION
    for key in (
        "generated_at",
        "config",
        "config_validation",
        "health",
        "contracts",
        "summary",
        "analytics",
        "analyses",
        "alpaca",
        "errors",
    ):
        assert key in snap
    # Each award produced an analysis.
    assert len(snap["analyses"]) == len(SAMPLE_AWARDS)
    # Health entries have a status string.
    for h in snap["health"].values():
        assert h["status"] in {"ok", "degraded", "unavailable", "not_configured", "unknown"}


def test_snapshot_match_classification_and_eligibility(patched_dashboard):
    from tests.conftest import SAMPLE_AWARDS

    snap = snapshot.build_snapshot(
        validate=True,
        fetch_orders=False,
        fetch_alpaca=False,
        toggles=_toggles(enable_history=False),
        awards_override=SAMPLE_AWARDS,
    )
    by_id = {a["award_id"]: a for a in snap["analyses"]}
    # Lockheed-style names should classify high tier with LMT.
    assert by_id["AW-1"]["ticker"] == "LMT"
    assert by_id["AW-1"]["match"]["tier"] == "high"
    # Unknown recipient gets no ticker and skipped eligibility.
    assert by_id["AW-3"]["ticker"] is None
    assert by_id["AW-3"]["eligibility"]["status"] == "skipped"
    # Empty recipient is also skipped.
    assert by_id["AW-4"]["eligibility"]["status"] == "skipped"


def test_snapshot_handles_fetch_failure_gracefully(patched_dashboard, monkeypatch):
    # Force fetch to raise — snapshot must still build with empty contracts.
    import usaspending_fetcher

    def boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(usaspending_fetcher, "fetch_recent_large_contracts", boom)

    snap = snapshot.build_snapshot(
        validate=False,
        fetch_orders=False,
        fetch_alpaca=False,
        toggles=_toggles(enable_history=False),
    )
    assert snap["contracts"] == []
    assert snap["health"]["usaspending"]["status"] == "unavailable"
    assert "usaspending" in snap["errors"]


def test_export_profiles_strip_or_keep_payload(patched_dashboard):
    from tests.conftest import SAMPLE_AWARDS

    snap = snapshot.build_snapshot(
        validate=True,
        fetch_orders=False,
        fetch_alpaca=False,
        toggles=_toggles(enable_history=False),
        awards_override=SAMPLE_AWARDS,
    )

    full = snapshot.snapshot_to_export(snap, profile=config_v2.PROFILE_FULL)
    assert full["schema_version"] == snapshot.SNAPSHOT_SCHEMA_VERSION
    assert full["export_profile"] == config_v2.PROFILE_FULL
    assert "contracts" in full  # full keeps raw awards

    compact = snapshot.snapshot_to_export(snap, profile=config_v2.PROFILE_COMPACT)
    assert compact["export_profile"] == config_v2.PROFILE_COMPACT
    assert "contracts" not in compact
    # Compact analyses are flattened.
    assert all("tier" in row for row in compact["analyses"])


def test_history_persists_and_drives_deltas(patched_dashboard, tmp_path):
    from tests.conftest import SAMPLE_AWARDS

    history_file = tmp_path / "hist.json"
    toggles = _toggles(
        enable_history=True,
        history_file=str(history_file),
        history_limit=5,
    )

    snap1 = snapshot.build_snapshot(
        validate=False,
        fetch_orders=False,
        fetch_alpaca=False,
        toggles=toggles,
        awards_override=SAMPLE_AWARDS[:2],
    )
    assert snap1["summary"]["deltas"] == {}  # no prior baseline yet
    assert history_file.exists()

    # Second run with more awards — deltas should be populated.
    snap2 = snapshot.build_snapshot(
        validate=False,
        fetch_orders=False,
        fetch_alpaca=False,
        toggles=toggles,
        awards_override=SAMPLE_AWARDS,
    )
    deltas = snap2["summary"]["deltas"]
    assert deltas, "deltas should be present after a baseline exists"
    assert deltas["count"]["diff"] == len(SAMPLE_AWARDS) - 2

    # History file is bounded by history_limit.
    data = json.loads(history_file.read_text())
    assert len(data["entries"]) <= 5
