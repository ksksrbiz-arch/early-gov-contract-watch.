"""
CLI entry-point for dashboard v2.

Backward-compatible with the v1 flags (--refresh, --limit, --top,
--no-validate, --no-orders, --export) and adds v2 flags:

  --view {overview,contracts,tickers,trading,all}
  --sort {amount,date,recipient,agency,confidence,materiality}
  --ticker-sort {amount,confidence,materiality,recipient}
  --filter-agency TEXT
  --filter-recipient TEXT
  --min-amount AMOUNT
  --min-tier {none,low,medium,high}
  --material-only
  --profile {compact,full}
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.live import Live

from . import config_v2, snapshot
from .render import render_dashboard

console = Console()


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Terminal dashboard for the Early Gov Contract Watch bot (v2)."
        )
    )

    # v1-compatible flags
    parser.add_argument(
        "--refresh",
        type=int,
        default=None,
        metavar="SECONDS",
        help="Auto-refresh every N seconds (live mode). Omit for one-shot.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max contracts shown (default: 20).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="(Deprecated; kept for compatibility) — top-N rows in summary tables.",
    )
    parser.add_argument(
        "--no-validate",
        dest="validate",
        action="store_false",
        help="Skip yfinance ticker validation (faster, no market data).",
    )
    parser.add_argument(
        "--no-orders",
        dest="orders",
        action="store_false",
        help="Skip the Alpaca recent-orders table.",
    )
    parser.add_argument(
        "--export",
        type=str,
        default=None,
        metavar="FILE",
        help="Write the snapshot to a JSON file (compact or full per --profile).",
    )

    # v2 view + sort + filter flags
    parser.add_argument(
        "--view",
        choices=[
            config_v2.VIEW_ALL,
            config_v2.VIEW_OVERVIEW,
            config_v2.VIEW_CONTRACTS,
            config_v2.VIEW_TICKERS,
            config_v2.VIEW_TRADING,
        ],
        default=config_v2.VIEW_ALL,
        help="Focused view mode (default: all).",
    )
    parser.add_argument(
        "--sort",
        choices=[
            "amount",
            "date",
            "recipient",
            "agency",
            "confidence",
            "materiality",
        ],
        default="amount",
        help="Sort order for the contracts table (default: amount).",
    )
    parser.add_argument(
        "--ticker-sort",
        dest="ticker_sort",
        choices=["amount", "confidence", "materiality", "recipient"],
        default="materiality",
        help="Sort order for the ticker table (default: materiality).",
    )
    parser.add_argument(
        "--filter-agency",
        dest="filter_agency",
        default=None,
        help="Substring filter applied to the awarding-agency column.",
    )
    parser.add_argument(
        "--filter-recipient",
        dest="filter_recipient",
        default=None,
        help="Substring filter applied to the recipient column.",
    )
    parser.add_argument(
        "--min-amount",
        dest="min_amount",
        type=float,
        default=None,
        help="Minimum award amount to include in tables.",
    )
    parser.add_argument(
        "--min-tier",
        dest="min_tier",
        choices=["none", "low", "medium", "high"],
        default=None,
        help="Minimum match-confidence tier required to show a row.",
    )
    parser.add_argument(
        "--material-only",
        dest="material_only",
        action="store_true",
        help="Show only awards flagged as material vs market cap.",
    )
    parser.add_argument(
        "--profile",
        choices=[config_v2.PROFILE_COMPACT, config_v2.PROFILE_FULL],
        default=None,
        help="Export profile (defaults to DASHBOARD_EXPORT_PROFILE or 'full').",
    )

    parser.set_defaults(validate=True, orders=True, material_only=False)
    return parser.parse_args(argv)


def _filters_from_args(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "agency": args.filter_agency,
        "recipient": args.filter_recipient,
        "min_amount": args.min_amount,
        "min_tier": args.min_tier,
        "material_only": args.material_only,
    }


def _build(args: argparse.Namespace) -> Dict[str, Any]:
    toggles = config_v2.load_v2_toggles()
    if args.profile:
        toggles["export_profile"] = args.profile
    return snapshot.build_snapshot(
        validate=args.validate,
        fetch_orders=args.orders,
        toggles=toggles,
    )


def _maybe_export(args: argparse.Namespace, snap: Dict[str, Any]) -> None:
    if not args.export:
        return
    profile = args.profile or snap.get("toggles", {}).get(
        "export_profile", config_v2.PROFILE_FULL
    )
    payload = snapshot.snapshot_to_export(snap, profile=profile)
    try:
        with open(args.export, "w") as f:
            json.dump(payload, f, indent=2, default=str)
        console.print(
            f"[green]Snapshot ({profile}) written to {args.export}[/green]"
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Export failed (write error):[/red] {exc}")


def run_dashboard(argv: Optional[List[str]] = None) -> None:
    args = _parse_args(argv)
    filters = _filters_from_args(args)
    # Suppress noisy logging only while the interactive dashboard is running.
    logging.disable(logging.CRITICAL)

    if args.refresh:
        try:
            snap = _build(args)
            _maybe_export(args, snap)
            with Live(
                render_dashboard(
                    snap,
                    view=args.view,
                    refresh=args.refresh,
                    limit=args.limit,
                    sort_by=args.sort,
                    ticker_sort_by=args.ticker_sort,
                    filters=filters,
                    show_orders=args.orders,
                ),
                console=console,
                refresh_per_second=4,
                screen=False,
            ) as live:
                while True:
                    time.sleep(args.refresh)
                    snapshot.reset_caches()
                    snap = _build(args)
                    live.update(
                        render_dashboard(
                            snap,
                            view=args.view,
                            refresh=args.refresh,
                            limit=args.limit,
                            sort_by=args.sort,
                            ticker_sort_by=args.ticker_sort,
                            filters=filters,
                            show_orders=args.orders,
                        )
                    )
        except KeyboardInterrupt:
            console.print("\n[dim]Dashboard stopped.[/dim]")
    else:
        snap = _build(args)
        _maybe_export(args, snap)
        console.print(
            render_dashboard(
                snap,
                view=args.view,
                refresh=None,
                limit=args.limit,
                sort_by=args.sort,
                ticker_sort_by=args.ticker_sort,
                filters=filters,
                show_orders=args.orders,
            )
        )


def main() -> None:  # pragma: no cover — thin wrapper
    try:
        run_dashboard()
    except KeyboardInterrupt:
        sys.exit(0)
