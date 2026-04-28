#!/usr/bin/env python3
"""
gov_contract_dashboard.py — Terminal dashboard for the Early Gov Contract Watch bot.

Displays a live snapshot of:
  - Current configuration
  - Recent large government contracts (from USASpending.gov)
  - Matched tickers with materiality scores
  - Current Alpaca positions (if API keys are configured)
  - Bot state (seen awards count, state file info)

Usage:
    python gov_contract_dashboard.py
"""

import json
import logging
import os

import yfinance as yf
from datetime import datetime

from rich.columns import Columns
from rich.console import Console
from rich.panel import Panel
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
# Helper renderers
# ---------------------------------------------------------------------------

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
            mtime = datetime.fromtimestamp(os.path.getmtime(STATE_FILE)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            grid.add_row("State file", STATE_FILE)
            grid.add_row("Last modified", mtime)
            grid.add_row("Seen award IDs", str(seen_count))
            grid.add_row("Last check", str(last_check))
        except Exception as exc:
            grid.add_row("Error reading state", str(exc))
    else:
        grid.add_row("State file", f"[dim]{STATE_FILE} (not found)[/dim]")

    return Panel(grid, title="[bold]Bot State[/bold]", border_style="cyan")


def _render_contracts_table(awards: list) -> Table:
    table = Table(
        title=f"Recent Large Contracts (last {DAYS_LOOKBACK} days, ≥ ${MIN_CONTRACT_AMOUNT:,.0f})",
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

    if not awards:
        table.add_row("[dim]No new contracts found[/dim]", "", "", "", "", "")
        return table

    for award in awards:
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


def _render_ticker_table(awards: list) -> Table:
    table = Table(
        title="Ticker Matches & Materiality",
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

    if not awards:
        table.add_row("[dim]No contracts to analyze[/dim]", "", "", "", "", "", "")
        return table

    for award in awards:
        recipient = award.get("Recipient Name", "")
        amount = float(award.get("Award Amount") or 0)
        ticker = get_ticker_for_company(recipient)

        if not ticker:
            table.add_row(
                recipient[:60],
                f"${amount:,.0f}",
                "[dim]—[/dim]",
                "[dim]—[/dim]",
                "[dim]—[/dim]",
                "[dim]no match[/dim]",
                "[dim]—[/dim]",
            )
            continue

        info = validate_ticker(ticker)
        if not info:
            table.add_row(
                recipient[:60],
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
        material = is_material_award(amount, mkt_cap)
        material_cell = "[green]✔ yes[/green]" if material else "[red]✘ no[/red]"

        try:
            price = yf.Ticker(ticker).info.get("regularMarketPrice") or "—"
            price_str = f"${price:,.2f}" if isinstance(price, (int, float)) else str(price)
        except Exception:
            price_str = "—"

        table.add_row(
            recipient[:60],
            f"${amount:,.0f}",
            ticker,
            f"${mkt_cap:,.0f}",
            f"{ratio:.3%}",
            material_cell,
            price_str,
        )

    return table


def _render_positions_panel() -> Panel:
    if not ALPACA_API_KEY:
        return Panel(
            "[dim]Alpaca API key not configured — skipping positions.[/dim]",
            title="[bold]Alpaca Positions[/bold]",
            border_style="blue",
        )

    try:
        from trader import AlpacaTrader

        trader = AlpacaTrader()
        positions = trader.client.get_all_positions()
        account = trader.account

        table = Table(
            show_header=True,
            header_style="bold blue",
            border_style="blue",
            expand=True,
        )
        table.add_column("Symbol", style="bold", no_wrap=True)
        table.add_column("Qty", justify="right")
        table.add_column("Avg Entry", justify="right")
        table.add_column("Mkt Value", justify="right")
        table.add_column("Unrealized P/L", justify="right")
        table.add_column("P/L %", justify="right")

        if positions:
            for p in positions:
                pl = float(p.unrealized_pl or 0)
                pl_pct = float(p.unrealized_plpc or 0) * 100
                pl_color = "green" if pl >= 0 else "red"
                table.add_row(
                    p.symbol,
                    str(p.qty),
                    f"${float(p.avg_entry_price):,.2f}",
                    f"${float(p.market_value):,.2f}",
                    f"[{pl_color}]${pl:,.2f}[/{pl_color}]",
                    f"[{pl_color}]{pl_pct:+.2f}%[/{pl_color}]",
                )
        else:
            table.add_row("[dim]No open positions[/dim]", "", "", "", "", "")

        buying_power = float(account.buying_power or 0)
        portfolio_val = float(account.portfolio_value or 0)
        footer = (
            f"[bold]Portfolio value:[/bold] ${portfolio_val:,.2f}   "
            f"[bold]Buying power:[/bold] ${buying_power:,.2f}   "
            f"[bold]Mode:[/bold] {'[yellow]PAPER[/yellow]' if ALPACA_PAPER else '[red]LIVE[/red]'}"
        )

        return Panel(
            Columns([table, Text("")]),
            title=f"[bold]Alpaca Positions[/bold]  —  {footer}",
            border_style="blue",
        )

    except Exception as exc:
        return Panel(
            f"[red]Could not connect to Alpaca:[/red] {exc}",
            title="[bold]Alpaca Positions[/bold]",
            border_style="red",
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_dashboard() -> None:
    console.rule("[bold white]Early Gov Contract Watch — Dashboard[/bold white]")
    console.print(
        f"[dim]Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}[/dim]\n"
    )

    # Top row: config + state side by side
    console.print(Columns([_render_config_panel(), _render_state_panel()]))

    # Fetch contracts
    console.print("\n[bold]Fetching recent contracts from USASpending.gov…[/bold]")
    awards = fetch_recent_large_contracts()
    console.print(f"[dim]Retrieved {len(awards)} new award(s).[/dim]\n")

    console.print(_render_contracts_table(awards))

    if awards:
        console.print("\n[bold]Resolving tickers & checking materiality…[/bold]")
        console.print(_render_ticker_table(awards))

    console.print("\n[bold]Loading Alpaca account positions…[/bold]")
    console.print(_render_positions_panel())

    console.rule()


if __name__ == "__main__":
    run_dashboard()
