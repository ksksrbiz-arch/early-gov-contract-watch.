"""Tests for `web.config_io`.

Verifies dotenv read/write round-trips, secret masking, comment preservation,
and the env-mirror behavior.
"""

from __future__ import annotations

import os

import pytest

from web.config_io import (
    EDITABLE_KEYS,
    apply_env_updates,
    read_dotenv,
    update_dotenv,
)


def test_update_dotenv_creates_file_with_new_keys(tmp_path):
    env = tmp_path / ".env"
    update_dotenv(str(env), {"POLL_INTERVAL_MINUTES": "15"})
    assert env.read_text().strip() == "POLL_INTERVAL_MINUTES=15"


def test_update_dotenv_preserves_comments_and_unknowns(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# header\n"
        "FOO=bar  # not editable\n"
        "POLL_INTERVAL_MINUTES=30\n"
    )
    update_dotenv(str(env), {"POLL_INTERVAL_MINUTES": "60"})
    text = env.read_text()
    assert "# header" in text
    assert "FOO=bar" in text
    assert "POLL_INTERVAL_MINUTES=60" in text
    assert "POLL_INTERVAL_MINUTES=30" not in text


def test_update_dotenv_quotes_values_with_spaces(tmp_path):
    env = tmp_path / ".env"
    update_dotenv(str(env), {"SLACK_WEBHOOK": "with spaces"})
    assert 'SLACK_WEBHOOK="with spaces"' in env.read_text()


def test_update_dotenv_blank_removes_key(tmp_path):
    env = tmp_path / ".env"
    env.write_text("MAX_DAILY_TRADES=2\nLOG_LEVEL=INFO\n")
    update_dotenv(str(env), {"MAX_DAILY_TRADES": ""})
    text = env.read_text()
    assert "MAX_DAILY_TRADES" not in text
    assert "LOG_LEVEL=INFO" in text


def test_read_dotenv_masks_secrets(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ALPACA_API_KEY=AKsupersecretvalue123\nALPACA_PAPER=true\n")
    # Make sure we don't read leaked env vars during the test.
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    values = read_dotenv(str(env))
    assert values["ALPACA_API_KEY"]["is_secret"] is True
    assert values["ALPACA_API_KEY"]["value"] == ""
    assert "*" in values["ALPACA_API_KEY"]["masked"]
    assert values["ALPACA_PAPER"]["value"] == "true"


def test_apply_env_updates_mirrors_to_environ(monkeypatch):
    monkeypatch.delenv("MAX_DAILY_TRADES", raising=False)
    apply_env_updates({"MAX_DAILY_TRADES": "9"})
    assert os.environ["MAX_DAILY_TRADES"] == "9"
    apply_env_updates({"MAX_DAILY_TRADES": ""})
    assert "MAX_DAILY_TRADES" not in os.environ


def test_editable_keys_includes_phase1_and_phase2_levers():
    needed = {
        "QUICK_BUY_NOTIONAL", "LARGE_BUY_NOTIONAL",
        "PHASE2_MATERIALITY_THRESHOLD", "TRAILING_STOP_PCT",
        "TRAILING_STOP_DAYS", "VOLUME_SPIKE_MULTIPLIER",
    }
    assert needed.issubset(set(EDITABLE_KEYS))


def test_round_trip_known_keys(tmp_path):
    env = tmp_path / ".env"
    update_dotenv(str(env), {"QUICK_BUY_NOTIONAL": "500", "LARGE_BUY_NOTIONAL": "3000"})
    values = read_dotenv(str(env))
    assert values["QUICK_BUY_NOTIONAL"]["value"] == "500"
    assert values["LARGE_BUY_NOTIONAL"]["value"] == "3000"
