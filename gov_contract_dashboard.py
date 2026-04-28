#!/usr/bin/env python3
"""
gov_contract_dashboard.py — Terminal dashboard for the Early Gov Contract Watch bot.

Comprehensive snapshot (or live view) of:
  - Active configuration
  - Bot state (state.json info)
  - Recent large government contracts (from USASpending.gov)
  - Summary statistics & aggregations (top agencies / top recipients)
  - Ticker matches with materiality scoring
  - Alpaca account, positions, recent orders, daily-trade budget

Examples
--------
    # One-shot snapshot
    python gov_contract_dashboard.py

    # Live auto-refresh every 60 seconds
    python gov_contract_dashboard.py --refresh 60

    # Skip yfinance validation (much faster)
    python gov_contract_dashboard.py --no-validate

    # Limit contracts shown and export full snapshot to JSON
    python gov_contract_dashboard.py --limit 25 --export snapshot.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import yfinance as yf
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from config import (
    ALPACA_API_KEY,
    ALPACA_PAPER,
    BUY_NOTIONAL,
    DAYS_LOOKBACK,
    MATERIALITY_THRESHOLD,
    MAX_DAILY_TRADES,
    MIN_CONTRACT_AMOUNT,
    POLL_INTERVAL_MINUTES,
    SELL_AFTER_DAYS,
    STATE_FILE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
)
from ticker_lookup import get_ticker_for_company, is_material_award, validate_ticker
from usaspending_fetcher import fetch_recent_large_contracts

logging.disable(logging.CRITICAL)

console = Console()


# ---------------------------------------------------------------------------
# Caches (avoid repeated yfinance / SEC calls during a single dashboard run)
# ---------------------------------------------------------------------------

_TICKER_INFO_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}
_RECIPIENT_TICKER_CACHE: Dict[str, Optional[str]] = {}


def _resolve_ticker(name: str) -> Optional[str]:
    if not name:
        return None
    if name in _RECIPIENT_TICKER_CACHE:
        return _RECIPIENT_TICKER_CACHE[name]
    try:
        ticker = get_ticker_for_company(name)
    except Exception:
        ticker = None
    _RECIPIENT_TICKER_CACHE[name] = ticker
    return ticker


def _ticker_info(ticker: str) -> Optional[Dict[str, Any]]:
    """Single yfinance lookup per ticker, returning name, market cap, and price."""
    if not ticker:
        return None
    if ticker in _TICKER_INFO_CACHE:
        return _TICKER_INFO_CACHE[ticker]
    try:
        raw = yf.Ticker(ticker).info or {}
        price = raw.get("regularMarketPrice")
        mkt_cap = raw.get("marketCap") or 0
        if price and mkt_cap > 0:
            info = {
                "ticker": ticker,
                "name": raw.get("shortName"),
                "market_cap": mkt_cap,
                "price": price,
            }
        else:
            info = None
    except Exception:
        info = None
    _TICKER_INFO_CACHE[ticker] = info
    return info


def _safe(fn, *args, **kwargs):
    """Run a renderer; return an error panel if it raises."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return Panel(
            f"[red]Failed to render section:[/red] {exc}",
            border_style="red",
        )


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def _render_header(generated_at: datetime, refresh: Optional[int]) -> Panel:
    title = Text("Early Gov Contract Watch — Dashboard", style="bold white on blue")
    sub = Text(
        f"  Generated {generated_at.strftime('%Y-%m-%d %H:%M:%S')}",
        style="dim",
    )
    if refresh:
        sub.append(f"  •  Auto-refresh every {refresh}s  •  Ctrl+C to exit", style="dim")
    return Panel(Group(title, sub), border_style="blue")


def _render_config_panel() -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()

    rows = [
        ("Alpaca mode", "[yellow]PAPER[/yellow]" if ALPACA_PAPER else "[red]LIVE[/red]"),
        ("API key set", "[green]yes[/green]" if ALPACA_API_KEY else "[red]no[/red]"),
        ("Buy notional", f"${BUY_NOTIONAL:,.0f}"),
        ("Min contract", f"${MIN_CONTRACT_AMOUNT:,.0f}"),
        ("Days lookback", str(DAYS_LOOKBACK)),
        ("Poll interval", f"{POLL_INTERVAL_MINUTES} min"),
        ("Max daily trades", str(MAX_DAILY_TRADES)),
        ("Take-profit", f"{TAKE_PROFIT_PCT}%"),
        ("Stop-loss", f"{STOP_LOSS_PCT}%"),
        ("Sell after", f"{SELL_AFTER_DAYS} days"),
        ("Materiality", f"{MATERIALITY_THRESHOLD:.1%}"),
    ]
    for label, value in rows:
        grid.add_row(label, value)

    return Panel(grid, title="[bold]Configuration[/bold]", border_style="cyan")


def _render_state_panel() -> Panel:
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()

    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            seen_count = len(state.get("seen_award_ids", []))
            last_check = state.get("last_check") or "n/a"
            mtime_dt = datetime.fromtimestamp(os.path.getmtime(STATE_FILE))
            age_min = (datetime.now() - mtime_dt).total_seconds() / 60
            # Health indicator based on age vs poll interval
            if age_min <= POLL_INTERVAL_MINUTES * 1.5:
                health = "[green]● fresh[/green]"
            elif age_min <= POLL_INTERVAL_MINUTES * 4:
                health = "[yellow]● stale[/yellow]"
            else:
                health = "[red]● cold[/red]"
            grid.add_row("State file", STATE_FILE)
            grid.add_row("Last modified", mtime_dt.strftime("%Y-%m-%d %H:%M:%S"))
            grid.add_row("Age", f"{age_min:.1f} min  {health}")
            grid.add_row("Seen award IDs", str(seen_count))
            grid.add_row("Last check", str(last_check))
        except Exception as exc:
            grid.add_row("Error reading state", str(exc))
    else:
        grid.add_row("State file", f"[dim]{STATE_FILE} (not found)[/dim]")
        grid.add_row("Status", "[yellow]bot has not run yet[/yellow]")

    return Panel(grid, title="[bold]Bot State[/bold]", border_style="cyan")


def _render_summary_panel(awards: List[dict], analyses: List[dict]) -> Panel:
    total_amount = sum(float(a.get("Award Amount") or 0) for a in awards)
    matched = [x for x in analyses if x.get("ticker")]
    valid = [x for x in matched if x.get("info")]
    material = [x for x in valid if x.get("material")]
    total_material_amount = sum(x["amount"] for x in material)

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold green", no_wrap=True)
    grid.add_column(justify="right")

    grid.add_row("Contracts fetched", f"{len(awards):,}")
    grid.add_row("Total $ value", f"${total_amount:,.0f}")
    grid.add_row(
        "Average size",
        f"${(total_amount / len(awards)):,.0f}" if awards else "—",
    )
    grid.add_row("Ticker matches", f"{len(matched):,}")
    grid.add_row("Validated tickers", f"{len(valid):,}")
    grid.add_row(
        "Material awards",
        f"[bold]{len(material):,}[/bold]  (${total_material_amount:,.0f})",
    )

    return Panel(grid, title="[bold]Summary[/bold]", border_style="green")


def _render_top_table(
    awards: List[dict], key: str, title: str, top_n: int
) -> Table:
    totals: Dict[str, float] = defaultdict(float)
    counts: Dict[str, int] = defaultdict(int)
    for a in awards:
        name = a.get(key) or "(unknown)"
        totals[name] += float(a.get("Award Amount") or 0)
        counts[name] += 1
    ranked = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    table = Table(
        title=title,
        show_header=True,
        header_style="bold magenta",
        border_style="magenta",
        expand=True,
    )
    table.add_column("Name", overflow="fold")
    table.add_column("Awards", justify="right")
    table.add_column("Total $", justify="right", style="green")

    if not ranked:
        table.add_row("[dim]No data[/dim]", "", "")
        return table

    for name, total in ranked:
        table.add_row(name, str(counts[name]), f"${total:,.0f}")
    return table


def _render_contracts_table(awards: List[dict], limit: int) -> Table:
    sorted_awards = sorted(
        awards, key=lambda a: float(a.get("Award Amount") or 0), reverse=True
    )[:limit]
    suffix = f" (top {limit} of {len(awards)})" if len(awards) > limit else ""

    table = Table(
        title=(
            f"Recent Large Contracts{suffix} — last {DAYS_LOOKBACK} days, "
            f"≥ ${MIN_CONTRACT_AMOUNT:,.0f}"
        ),
        show_header=True,
        header_style="bold magenta",
        border_style="magenta",
        expand=True,
    )
    table.add_column("Award ID", style="dim", no_wrap=True)
    table.add_column("Action Date", no_wrap=True)
    table.add_column("Recipient", overflow="fold")
    table.add_column("Amount", justify="right", style="green")
    table.add_column("Agency", overflow="fold")
    table.add_column("Description", overflow="fold", max_width=50)

    if not sorted_awards:
        table.add_row("[dim]No new contracts found[/dim]", "", "", "", "", "")
        return table

    for award in sorted_awards:
        amount = float(award.get("Award Amount") or 0)
        description = (award.get("Description") or "")[:80]
        table.add_row(
            str(award.get("Award ID") or ""),
            str(award.get("Action Date") or ""),
            str(award.get("Recipient Name") or ""),
            f"${amount:,.0f}",
            str(award.get("Awarding Agency") or ""),
            description,
        )

    return table


def _build_analyses(awards: List[dict], validate: bool) -> List[dict]:
    """Resolve tickers (and optionally validate via yfinance) for every award."""
    out: List[dict] = []
    for award in awards:
        recipient = award.get("Recipient Name", "") or ""
        amount = float(award.get("Award Amount") or 0)
        ticker = _resolve_ticker(recipient)

        info = None
        material = False
        if ticker and validate:
            # Prefer the lightweight project helper for the materiality check,
            # but use the cached full info for price/market cap display.
            info = _ticker_info(ticker)
            if info:
                material = is_material_award(amount, info["market_cap"])
            else:
                # Fall back to the project's validate_ticker (covers cases where
                # we only need market cap and not price).
                fallback = None
                try:
                    fallback = validate_ticker(ticker)
                except Exception:
                    fallback = None
                if fallback:
                    info = {
                        "ticker": ticker,
                        "name": fallback.get("name"),
                        "market_cap": fallback["market_cap"],
                        "price": None,
                    }
                    material = is_material_award(amount, fallback["market_cap"])
        out.append(
            {
                "recipient": recipient,
                "amount": amount,
                "ticker": ticker,
                "info": info,
                "material": material,
            }
        )
    return out


def _render_ticker_table(analyses: List[dict], validate: bool) -> Table:
    title = "Ticker Matches & Materiality"
    if not validate:
        title += "  [dim](--no-validate: market data skipped)[/dim]"
    table = Table(
        title=title,
        show_header=True,
        header_style="bold yellow",
        border_style="yellow",
        expand=True,
    )
    table.add_column("Recipient", overflow="fold")
    table.add_column("Amount", justify="right")
    table.add_column("Ticker", style="bold", no_wrap=True)
    table.add_column("Mkt Cap", justify="right")
    table.add_column("Ratio", justify="right")
    table.add_column("Material?", justify="center")
    table.add_column("Price", justify="right")

    if not analyses:
        table.add_row("[dim]No contracts to analyze[/dim]", "", "", "", "", "", "")
        return table

    # Sort: material first (desc by ratio), then matched-but-not-material, then no-match
    def sort_key(x: dict):
        if x.get("material"):
            ratio = x["amount"] / x["info"]["market_cap"] if x.get("info") else 0
            return (0, -ratio)
        if x.get("ticker"):
            return (1, -x["amount"])
        return (2, -x["amount"])

    for a in sorted(analyses, key=sort_key):
        recipient = (a["recipient"] or "")[:60]
        amount = a["amount"]
        ticker = a["ticker"]
        info = a["info"]

        if not ticker:
            table.add_row(
                recipient,
                f"${amount:,.0f}",
                "[dim]—[/dim]",
                "[dim]—[/dim]",
                "[dim]—[/dim]",
                "[dim]no match[/dim]",
                "[dim]—[/dim]",
            )
            continue

        if not validate:
            table.add_row(
                recipient,
                f"${amount:,.0f}",
                ticker,
                "[dim]skipped[/dim]",
                "[dim]skipped[/dim]",
                "[dim]skipped[/dim]",
                "[dim]skipped[/dim]",
            )
            continue

        if not info:
            table.add_row(
                recipient,
                f"${amount:,.0f}",
                ticker,
                "[dim]—[/dim]",
                "[dim]—[/dim]",
                "[red]invalid[/red]",
                "[dim]—[/dim]",
            )
            continue

        mkt_cap = info["market_cap"]
        ratio = amount / mkt_cap if mkt_cap else 0
        material_cell = (
            "[green]✔ yes[/green]" if a["material"] else "[red]✘ no[/red]"
        )
        price = info.get("price")
        price_str = f"${price:,.2f}" if isinstance(price, (int, float)) else "—"

        table.add_row(
            recipient,
            f"${amount:,.0f}",
            ticker,
            f"${mkt_cap:,.0f}",
            f"{ratio:.3%}",
            material_cell,
            price_str,
        )

    return table


# ---------------------------------------------------------------------------
# Alpaca panels
# ---------------------------------------------------------------------------

def _render_alpaca_section(show_orders: bool) -> Group:
    if not ALPACA_API_KEY:
        return Group(
            Panel(
                "[dim]Alpaca API key not configured — skipping account, "
                "positions, and orders.[/dim]",
                title="[bold]Alpaca[/bold]",
                border_style="blue",
            )
        )

    try:
        from alpaca.trading.enums import OrderSide
        from trader import AlpacaTrader
    except Exception as exc:
        return Group(
            Panel(
                f"[red]Could not import Alpaca client:[/red] {exc}",
                title="[bold]Alpaca[/bold]",
                border_style="red",
            )
        )

    try:
        trader = AlpacaTrader()
        positions = list(trader.client.get_all_positions())
        account = trader.account
    except Exception as exc:
        return Group(
            Panel(
                f"[red]Could not connect to Alpaca:[/red] {exc}",
                title="[bold]Alpaca[/bold]",
                border_style="red",
            )
        )

    # Account summary
    portfolio_val = float(account.portfolio_value or 0)
    buying_power = float(account.buying_power or 0)
    equity = float(getattr(account, "equity", 0) or 0)
    last_equity = float(getattr(account, "last_equity", 0) or 0)
    day_pl = equity - last_equity
    day_pl_pct = (day_pl / last_equity * 100) if last_equity else 0
    day_color = "green" if day_pl >= 0 else "red"

    # Daily trades remaining
    today_buys = 0
    try:
        for o in trader.client.get_orders():
            if (
                o.side == OrderSide.BUY
                and o.filled_at
                and o.filled_at.date() == datetime.now().date()
            ):
                today_buys += 1
    except Exception:
        today_buys = -1
    if today_buys < 0:
        trades_str = "[dim]unknown[/dim]"
    else:
        remaining = max(MAX_DAILY_TRADES - today_buys, 0)
        color = "green" if remaining > 0 else "red"
        trades_str = (
            f"[{color}]{today_buys}/{MAX_DAILY_TRADES} used "
            f"({remaining} remaining)[/{color}]"
        )

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold blue", no_wrap=True)
    summary.add_column()
    summary.add_row(
        "Mode",
        "[yellow]PAPER[/yellow]" if ALPACA_PAPER else "[red]LIVE[/red]",
    )
    summary.add_row("Portfolio value", f"${portfolio_val:,.2f}")
    summary.add_row("Equity", f"${equity:,.2f}")
    summary.add_row("Buying power", f"${buying_power:,.2f}")
    summary.add_row(
        "Today's P/L",
        f"[{day_color}]${day_pl:+,.2f} ({day_pl_pct:+.2f}%)[/{day_color}]",
    )
    summary.add_row("Daily trades", trades_str)
    summary_panel = Panel(
        summary, title="[bold]Alpaca Account[/bold]", border_style="blue"
    )

    # Positions table
    pos_table = Table(
        title="Open Positions",
        show_header=True,
        header_style="bold blue",
        border_style="blue",
        expand=True,
    )
    pos_table.add_column("Symbol", style="bold", no_wrap=True)
    pos_table.add_column("Qty", justify="right")
    pos_table.add_column("Avg Entry", justify="right")
    pos_table.add_column("Current", justify="right")
    pos_table.add_column("Mkt Value", justify="right")
    pos_table.add_column("Unrealized P/L", justify="right")
    pos_table.add_column("P/L %", justify="right")

    if positions:
        for p in positions:
            pl = float(p.unrealized_pl or 0)
            pl_pct = float(p.unrealized_plpc or 0) * 100
            pl_color = "green" if pl >= 0 else "red"
            current = float(getattr(p, "current_price", 0) or 0)
            pos_table.add_row(
                p.symbol,
                str(p.qty),
                f"${float(p.avg_entry_price):,.2f}",
                f"${current:,.2f}" if current else "—",
                f"${float(p.market_value):,.2f}",
                f"[{pl_color}]${pl:,.2f}[/{pl_color}]",
                f"[{pl_color}]{pl_pct:+.2f}%[/{pl_color}]",
            )
    else:
        pos_table.add_row("[dim]No open positions[/dim]", "", "", "", "", "", "")

    blocks: List[Any] = [Columns([summary_panel, pos_table])]

    # Recent orders
    if show_orders:
        try:
            from alpaca.trading.enums import QueryOrderStatus
            from alpaca.trading.requests import GetOrdersRequest

            req = GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                limit=15,
                after=datetime.now(timezone.utc) - timedelta(days=14),
            )
            orders = list(trader.client.get_orders(filter=req))
        except Exception:
            try:
                orders = list(trader.client.get_orders())[:15]
            except Exception:
                orders = []

        order_table = Table(
            title="Recent Orders (last 14 days)",
            show_header=True,
            header_style="bold blue",
            border_style="blue",
            expand=True,
        )
        order_table.add_column("Submitted", no_wrap=True)
        order_table.add_column("Symbol", style="bold")
        order_table.add_column("Side")
        order_table.add_column("Qty/Notional", justify="right")
        order_table.add_column("Status")
        order_table.add_column("Filled $", justify="right")

        if orders:
            for o in orders:
                submitted = getattr(o, "submitted_at", None) or getattr(o, "created_at", None)
                submitted_str = (
                    submitted.strftime("%Y-%m-%d %H:%M") if submitted else "—"
                )
                side = str(getattr(o.side, "value", o.side)).upper()
                side_color = "green" if side == "BUY" else "red"
                qty_or_notional = (
                    f"${float(o.notional):,.2f}"
                    if getattr(o, "notional", None)
                    else str(getattr(o, "qty", "—"))
                )
                status = str(getattr(o.status, "value", o.status))
                filled_avg = getattr(o, "filled_avg_price", None)
                filled_qty = getattr(o, "filled_qty", None)
                if filled_avg and filled_qty:
                    try:
                        filled_dollar = (
                            f"${float(filled_avg) * float(filled_qty):,.2f}"
                        )
                    except Exception:
                        filled_dollar = "—"
                else:
                    filled_dollar = "—"
                order_table.add_row(
                    submitted_str,
                    o.symbol,
                    f"[{side_color}]{side}[/{side_color}]",
                    qty_or_notional,
                    status,
                    filled_dollar,
                )
        else:
            order_table.add_row("[dim]No recent orders[/dim]", "", "", "", "", "")
        blocks.append(order_table)

    return Group(*blocks)


# ---------------------------------------------------------------------------
# Snapshot building & rendering
# ---------------------------------------------------------------------------

def _build_snapshot(
    awards: List[dict], analyses: List[dict]
) -> Dict[str, Any]:
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": {
            "alpaca_paper": ALPACA_PAPER,
            "buy_notional": BUY_NOTIONAL,
            "min_contract_amount": MIN_CONTRACT_AMOUNT,
            "days_lookback": DAYS_LOOKBACK,
            "poll_interval_minutes": POLL_INTERVAL_MINUTES,
            "max_daily_trades": MAX_DAILY_TRADES,
            "materiality_threshold": MATERIALITY_THRESHOLD,
            "take_profit_pct": TAKE_PROFIT_PCT,
            "stop_loss_pct": STOP_LOSS_PCT,
            "sell_after_days": SELL_AFTER_DAYS,
        },
        "contracts": awards,
        "analyses": [
            {
                "recipient": a["recipient"],
                "amount": a["amount"],
                "ticker": a["ticker"],
                "market_cap": (a["info"] or {}).get("market_cap"),
                "price": (a["info"] or {}).get("price"),
                "material": a["material"],
            }
            for a in analyses
        ],
    }


def _render_dashboard(args: argparse.Namespace) -> Group:
    generated_at = datetime.now()

    # Top row: header
    header = _safe(_render_header, generated_at, args.refresh)

    # Config + state side by side
    config_row = Columns(
        [_safe(_render_config_panel), _safe(_render_state_panel)],
        equal=True,
        expand=True,
    )

    # Fetch contracts (isolated)
    try:
        awards = fetch_recent_large_contracts() or []
    except Exception as exc:  # noqa: BLE001
        awards = []
        fetch_err: Optional[str] = str(exc)
    else:
        fetch_err = None

    analyses = _build_analyses(awards, validate=args.validate)

    # Summary + top tables
    summary_row = Columns(
        [
            _safe(_render_summary_panel, awards, analyses),
            _safe(_render_top_table, awards, "Awarding Agency", "Top Agencies", args.top),
            _safe(_render_top_table, awards, "Recipient Name", "Top Recipients", args.top),
        ],
        equal=True,
        expand=True,
    )

    contracts_table = _safe(_render_contracts_table, awards, args.limit)
    ticker_table = _safe(_render_ticker_table, analyses, args.validate)
    alpaca_section = _safe(_render_alpaca_section, args.orders)

    blocks: List[Any] = [header, config_row, Rule(style="dim"), summary_row]
    if fetch_err:
        blocks.append(
            Panel(
                f"[red]Failed to fetch contracts:[/red] {fetch_err}",
                border_style="red",
            )
        )
    blocks.extend([contracts_table, ticker_table, Rule(style="dim"), alpaca_section])
    return Group(*blocks)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Terminal dashboard for the Early Gov Contract Watch bot."
    )
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
        help="Max contracts shown in the contracts table (default: 20).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="Rows in Top Agencies / Top Recipients tables (default: 5).",
    )
    parser.add_argument(
        "--no-validate",
        dest="validate",
        action="store_false",
        help="Skip yfinance ticker validation (much faster, no market data).",
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
        help="Write the snapshot (contracts + analyses + config) to a JSON file.",
    )
    parser.set_defaults(validate=True, orders=True)
    return parser.parse_args(argv)


def _maybe_export(args: argparse.Namespace) -> None:
    if not args.export:
        return
    try:
        awards = fetch_recent_large_contracts() or []
    except Exception as exc:
        console.print(f"[red]Export failed (fetch error):[/red] {exc}")
        return
    analyses = _build_analyses(awards, validate=args.validate)
    snapshot = _build_snapshot(awards, analyses)
    try:
        with open(args.export, "w") as f:
            json.dump(snapshot, f, indent=2, default=str)
        console.print(f"[green]Snapshot written to {args.export}[/green]")
    except Exception as exc:
        console.print(f"[red]Export failed (write error):[/red] {exc}")


def run_dashboard(argv: Optional[List[str]] = None) -> None:
    args = _parse_args(argv)

    if args.export and not args.refresh:
        _maybe_export(args)

    if args.refresh:
        try:
            with Live(
                _render_dashboard(args),
                console=console,
                refresh_per_second=4,
                screen=False,
            ) as live:
                while True:
                    time.sleep(args.refresh)
                    # Reset per-cycle caches so live data stays accurate.
                    _TICKER_INFO_CACHE.clear()
                    _RECIPIENT_TICKER_CACHE.clear()
                    live.update(_render_dashboard(args))
        except KeyboardInterrupt:
            console.print("\n[dim]Dashboard stopped.[/dim]")
    else:
        console.print(_render_dashboard(args))


if __name__ == "__main__":
    try:
        run_dashboard()
    except KeyboardInterrupt:
        sys.exit(0)
