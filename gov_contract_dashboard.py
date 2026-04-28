#!/usr/bin/env python3
"""
gov_contract_dashboard.py — thin shim entry-point for dashboard v2.

The real implementation lives in the `dashboard/` package. This module is
preserved so existing workflows (`python gov_contract_dashboard.py ...`)
continue to work without changes. All v1 CLI flags remain supported, plus
the v2 flags described in `dashboard/cli.py`.

Examples
--------
    # One-shot snapshot (all sections, v1-compatible default)
    python gov_contract_dashboard.py

    # Live auto-refresh every 60 seconds
    python gov_contract_dashboard.py --refresh 60

    # Skip yfinance market-data validation (much faster)
    python gov_contract_dashboard.py --no-validate

    # Focused view + filtering + compact export
    python gov_contract_dashboard.py --view tickers --min-tier high \
        --material-only --export snapshot.json --profile compact
"""

from __future__ import annotations

import sys

from dashboard.cli import run_dashboard


def main() -> None:
    try:
        run_dashboard()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
