"""CLI smoke tests: parse args + export profile dispatch."""

import json
import os

from dashboard import cli, config_v2, snapshot
from tests.conftest import SAMPLE_AWARDS


def test_parse_args_defaults_match_v1():
    args = cli._parse_args([])
    assert args.refresh is None
    assert args.limit == 20
    assert args.validate is True
    assert args.orders is True
    assert args.export is None
    assert args.view == config_v2.VIEW_ALL
    assert args.sort == "amount"
    assert args.material_only is False


def test_parse_args_v2_flags():
    args = cli._parse_args(
        [
            "--view", "tickers",
            "--sort", "confidence",
            "--ticker-sort", "amount",
            "--filter-agency", "DOD",
            "--filter-recipient", "lock",
            "--min-amount", "1000000",
            "--min-tier", "high",
            "--material-only",
            "--profile", "compact",
            "--no-validate",
            "--no-orders",
        ]
    )
    assert args.view == "tickers"
    assert args.sort == "confidence"
    assert args.ticker_sort == "amount"
    assert args.filter_agency == "DOD"
    assert args.filter_recipient == "lock"
    assert args.min_amount == 1_000_000
    assert args.min_tier == "high"
    assert args.material_only is True
    assert args.profile == "compact"
    assert args.validate is False
    assert args.orders is False


def test_export_writes_compact_json(patched_dashboard, tmp_path, monkeypatch):
    snap = snapshot.build_snapshot(
        validate=True,
        fetch_orders=False,
        fetch_alpaca=False,
        toggles={**config_v2.load_v2_toggles(), "enable_history": False},
        awards_override=SAMPLE_AWARDS,
    )
    out = tmp_path / "snap.json"

    class Args:
        export = str(out)
        profile = "compact"

    cli._maybe_export(Args(), snap)
    payload = json.loads(out.read_text())
    assert payload["schema_version"] == snapshot.SNAPSHOT_SCHEMA_VERSION
    assert payload["export_profile"] == "compact"
    assert "contracts" not in payload
    assert isinstance(payload["analyses"], list)


def test_export_full_keeps_contracts(patched_dashboard, tmp_path):
    snap = snapshot.build_snapshot(
        validate=False,
        fetch_orders=False,
        fetch_alpaca=False,
        toggles={**config_v2.load_v2_toggles(), "enable_history": False},
        awards_override=SAMPLE_AWARDS,
    )
    out = tmp_path / "full.json"

    class Args:
        export = str(out)
        profile = "full"

    cli._maybe_export(Args(), snap)
    payload = json.loads(out.read_text())
    assert payload["export_profile"] == "full"
    assert "contracts" in payload
    assert len(payload["contracts"]) == len(SAMPLE_AWARDS)
