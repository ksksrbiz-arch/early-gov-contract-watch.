"""
Shared fixtures and helpers for dashboard v2 tests.

Tests are intentionally hermetic: no real USASpending, SEC, yfinance, or
Alpaca calls. We patch the small set of helpers that touch the network so
the snapshot assembler and renderers can be tested deterministically.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def _today_iso(offset_days: int = 0) -> str:
    return (date.today() - timedelta(days=offset_days)).isoformat()


SAMPLE_AWARDS = [
    {
        "Award ID": "AW-1",
        "Recipient Name": "LOCKHEED MARTIN CORPORATION",
        "Award Amount": 100_000_000,
        "Awarding Agency": "Department of Defense",
        "Description": "Aircraft maintenance services for FY26",
        "Action Date": _today_iso(0),
        "Modification Number": "0",
    },
    {
        "Award ID": "AW-2",
        "Recipient Name": "Lockheed Martin Corp",
        "Award Amount": 25_000_000,
        "Awarding Agency": "Department of Defense",
        "Description": "Software upgrade contract",
        "Action Date": _today_iso(1),
        "Modification Number": "0",
    },
    {
        "Award ID": "AW-3",
        "Recipient Name": "ACME UNKNOWN LLC",
        "Award Amount": 12_000_000,
        "Awarding Agency": "Department of Energy",
        "Description": "Research grant for advanced materials",
        "Action Date": _today_iso(2),
        "Modification Number": "0",
    },
    {
        "Award ID": "AW-4",
        "Recipient Name": "",
        "Award Amount": 9_500_000_000,  # huge outlier with missing recipient
        "Awarding Agency": "",
        "Description": "x",
        "Action Date": _today_iso(3),
        "Modification Number": "0",
    },
]


SAMPLE_COMPANIES = [
    ("LOCKHEED MARTIN CORP", "LMT"),
    ("BOEING CO", "BA"),
    ("RAYTHEON TECHNOLOGIES CORP", "RTX"),
    ("NORTHROP GRUMMAN CORP", "NOC"),
]


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Run every test in an isolated working directory with no env leakage."""
    monkeypatch.chdir(tmp_path)
    # Make sure the snapshot history file is local + small.
    monkeypatch.setenv("DASHBOARD_HISTORY_FILE", str(tmp_path / "history.json"))
    monkeypatch.setenv("DASHBOARD_HISTORY_LIMIT", "5")
    # Default-disable yfinance during tests; individual tests can re-enable.
    monkeypatch.setenv("ALPACA_PAPER", "true")
    yield


@pytest.fixture
def patched_dashboard(monkeypatch):
    """
    Patch the dashboard package to use deterministic upstream stand-ins:
      - _config_view returns a fixed config dict (no .env dependency)
      - SEC ticker source returns SAMPLE_COMPANIES
      - yfinance lookup returns a deterministic shape per known ticker
      - is_material_award uses the project's threshold (0.005) by default
    """
    from dashboard import snapshot as snap_mod

    # Deterministic config — no .env / config.py dependency needed.
    FAKE_CONFIG = {
        "alpaca_api_key_set": False,
        "alpaca_paper": True,
        "buy_notional": 300.0,
        "min_contract_amount": 10_000_000.0,
        "days_lookback": 7,
        "poll_interval_minutes": 30,
        "max_daily_trades": 2,
        "take_profit_pct": 12.0,
        "stop_loss_pct": 6.0,
        "sell_after_days": 5,
        "materiality_threshold": 0.005,
        "state_file": "state.json",
    }
    monkeypatch.setattr(snap_mod, "_config_view", lambda: dict(FAKE_CONFIG))

    monkeypatch.setattr(
        snap_mod, "_load_companies", lambda: list(SAMPLE_COMPANIES)
    )

    def fake_ticker_info(ticker):
        if not ticker:
            return None
        # Treat LMT as a $100B company so $100M = 0.1% (below 0.5% threshold).
        # AW-1 (100M) -> ratio 0.001 -> not material at default threshold.
        # If we want material we'd raise the contract amount.
        market_caps = {
            "LMT": 100_000_000_000,
            "BA": 120_000_000_000,
            "RTX": 110_000_000_000,
            "NOC": 70_000_000_000,
        }
        mc = market_caps.get(ticker)
        if not mc:
            return None
        return {
            "ticker": ticker,
            "name": ticker,
            "market_cap": float(mc),
            "price": 100.0,
        }

    monkeypatch.setattr(snap_mod, "_ticker_info", fake_ticker_info)
    snap_mod.reset_caches()
    return snap_mod
