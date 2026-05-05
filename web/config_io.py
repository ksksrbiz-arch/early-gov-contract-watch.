"""
Config I/O for the web dashboard.

The dashboard lets the operator edit the bot's runtime config from a
browser. To stay simple and stateless we persist edits to a local `.env`
file (the same file `python-dotenv` already loads on bot startup) and
mirror the change into `os.environ` so the bot loop picks up the new
value at the next iteration without a restart.

We deliberately keep the editable-key list narrow and never let the UI
write arbitrary keys.
"""

from __future__ import annotations

import os
import re
from typing import Dict, Iterable, List


# Whitelist of env vars the UI is allowed to read and write.
# Secrets (ALPACA_API_KEY, ALPACA_SECRET_KEY, SLACK_WEBHOOK) are *readable*
# in masked form via the API but write-back is gated to keep accidents rare.
EDITABLE_KEYS: List[str] = [
    # Trading
    "ALPACA_PAPER",
    "MIN_CONTRACT_AMOUNT",
    "POLL_INTERVAL_MINUTES",
    "MAX_DAILY_TRADES",
    "DAYS_LOOKBACK",
    "MATERIALITY_THRESHOLD",
    "LOG_LEVEL",
    # Phase 1 — Quick Profit
    "QUICK_BUY_NOTIONAL",
    "QUICK_HOLD_HOURS",
    "QUICK_TAKE_PROFIT_PCT",
    "QUICK_STOP_LOSS_PCT",
    "VOLUME_SPIKE_MULTIPLIER",
    "MAX_SPREAD_PCT",
    # Phase 2 — Large Profit
    "LARGE_BUY_NOTIONAL",
    "PHASE2_MATERIALITY_THRESHOLD",
    "PHASE2_TAKE_PROFIT_PCT",
    "TRAILING_STOP_PCT",
    "TRAILING_STOP_DAYS",
    # Backwards-compat
    "BUY_NOTIONAL",
    "TAKE_PROFIT_PCT",
    "STOP_LOSS_PCT",
    "SELL_AFTER_DAYS",
    # Dashboard toggles
    "DASHBOARD_ENABLE_ANALYTICS",
    "DASHBOARD_ENABLE_ANOMALIES",
    "DASHBOARD_ENABLE_HISTORY",
    "DASHBOARD_TICKER_MIN_CONFIDENCE",
    "DASHBOARD_TREND_DAYS",
    "DASHBOARD_TREND_WEEKS",
    # Notifications
    "SLACK_WEBHOOK",
    # Credentials (handle with care — masked on read)
    "ALPACA_API_KEY",
    "ALPACA_SECRET_KEY",
]

_SECRET_KEYS = {"ALPACA_API_KEY", "ALPACA_SECRET_KEY", "SLACK_WEBHOOK"}

# Match `KEY=VALUE` lines (allow whitespace around `=`, ignore comments).
_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "*" * len(value)
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


def read_dotenv(path: str) -> Dict[str, Dict[str, str]]:
    """Return `{KEY: {value, masked, set}}` for every editable key.

    The merged view prefers values from the on-disk `.env` and falls back
    to the live `os.environ` so the UI shows what the bot will see on the
    next reload. Secrets are returned in masked form.
    """
    file_values = _read_env_file(path)
    out: Dict[str, Dict[str, str]] = {}
    for key in EDITABLE_KEYS:
        raw = file_values.get(key, os.environ.get(key, ""))
        is_secret = key in _SECRET_KEYS
        out[key] = {
            "value": "" if is_secret else raw,
            "masked": _mask(raw) if is_secret else raw,
            "is_secret": is_secret,
            "set": bool(raw),
            "source": "file" if key in file_values else ("env" if raw else "none"),
        }
    return out


def update_dotenv(path: str, updates: Dict[str, str]) -> None:
    """Update or insert *updates* in the .env file at *path*.

    Preserves comments and unknown keys. Empty-string values delete the key
    so the operator can revert to environment defaults.
    """
    if not updates:
        return
    existing_lines = _read_lines(path)
    seen_keys = set()
    new_lines: List[str] = []
    for line in existing_lines:
        m = _LINE_RE.match(line)
        if not m:
            new_lines.append(line.rstrip("\n"))
            continue
        key = m.group(1)
        if key in updates:
            seen_keys.add(key)
            new_value = updates[key]
            if new_value == "":
                # Drop the line entirely.
                continue
            new_lines.append(f"{key}={_quote_if_needed(new_value)}")
        else:
            new_lines.append(line.rstrip("\n"))

    for key, val in updates.items():
        if key in seen_keys or val == "":
            continue
        new_lines.append(f"{key}={_quote_if_needed(val)}")

    contents = "\n".join(new_lines).rstrip() + "\n"
    _atomic_write(path, contents)


def apply_env_updates(updates: Dict[str, str]) -> None:
    """Mirror updates into `os.environ` so the running bot picks them up."""
    for key, val in updates.items():
        if val == "":
            os.environ.pop(key, None)
        else:
            os.environ[key] = val


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _read_env_file(path: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in _read_lines(path):
        m = _LINE_RE.match(line)
        if not m:
            continue
        key, raw = m.group(1), m.group(2)
        if raw and raw[0] == raw[-1] and raw[0] in ("'", '"') and len(raw) >= 2:
            raw = raw[1:-1]
        out[key] = raw
    return out


def _read_lines(path: str) -> Iterable[str]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return f.readlines()


def _quote_if_needed(value: str) -> str:
    # Quote when the value contains whitespace, '#', or shell-special chars.
    if any(ch.isspace() for ch in value) or "#" in value:
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _atomic_write(path: str, contents: str) -> None:
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(contents)
    os.replace(tmp, path)
