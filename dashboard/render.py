"""
Rich rendering for dashboard v2.

All renderers consume the versioned snapshot dict produced by `snapshot.py`.
They never call upstream APIs or read environment variables directly — that
makes the views deterministic, testable, and reusable from non-CLI surfaces.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .config_v2 import (
    VIEW_ALL,
    VIEW_CONTRACTS,
    VIEW_OVERVIEW,
    VIEW_TICKERS,
    VIEW_TRADING,
)

# Severity → color mapping used across panels for consistency.
_HEALTH_COLOR = {
    "ok": "green",
    "degraded": "yellow",
    "unavailable": "red",
    "not_configured": "dim",
    "unknown": "dim",
}

_TIER_COLOR = {
    "high": "green",
    "medium": "yellow",
    "low": "red",
    "none": "dim",
}


def _safe(fn, *args, **kwargs):
    """Wrap a renderer; return an inline error panel if it raises."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return Panel(
            f"[red]Failed to render section:[/red] {exc}",
            border_style="red",
        )


# ---------------------------------------------------------------------------
# Header / health
# ---------------------------------------------------------------------------

def render_header(snapshot: Dict[str, Any], refresh: Optional[int]) -> Panel:
    generated = snapshot.get("generated_at", "")
    schema = snapshot.get("schema_version", "?")
    profile = snapshot.get("toggles", {}).get("export_profile", "")
    title = Text(
        "Early Gov Contract Watch — Dashboard v2",
        style="bold white on blue",
    )
    sub = Text(
        f"  Generated {generated}  •  schema {schema}",
        style="dim",
    )
    if profile:
        sub.append(f"  •  profile {profile}", style="dim")
    if refresh:
        sub.append(
            f"  •  Auto-refresh every {refresh}s  •  Ctrl+C to exit",
            style="dim",
        )
    return Panel(Group(title, sub), border_style="blue")


def render_health(snapshot: Dict[str, Any]) -> Panel:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_column(overflow="fold")

    for key, h in (snapshot.get("health") or {}).items():
        color = _HEALTH_COLOR.get(h.get("status", "unknown"), "dim")
        status = f"[{color}]● {h.get('status', 'unknown')}[/{color}]"
        msg = h.get("message") or ""
        if h.get("error"):
            msg = f"{msg}  [red]({h['error']})[/red]"
        table.add_row(key, status, msg)
    return Panel(table, title="[bold]Section Health[/bold]", border_style="cyan")


# ---------------------------------------------------------------------------
# Config + bot state panels
# ---------------------------------------------------------------------------

def render_config(snapshot: Dict[str, Any]) -> Panel:
    cfg = snapshot.get("config") or {}
    val = snapshot.get("config_validation") or {"issues": [], "warnings": []}
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()

    rows = [
        ("Alpaca mode", "[yellow]PAPER[/yellow]" if cfg.get("alpaca_paper") else "[red]LIVE[/red]"),
        ("API key set", "[green]yes[/green]" if cfg.get("alpaca_api_key_set") else "[red]no[/red]"),
        ("P1 buy notional", f"${cfg.get('buy_notional', 0):,.0f}"),
        ("P2 buy notional", f"${(cfg.get('phase2') or {}).get('buy_notional', 0):,.0f}"),
        ("Min contract", f"${cfg.get('min_contract_amount', 0):,.0f}"),
        ("Days lookback", str(cfg.get("days_lookback"))),
        ("Poll interval", f"{cfg.get('poll_interval_minutes')} min"),
        ("Max daily trades", str(cfg.get("max_daily_trades"))),
        ("P1 take-profit / stop", f"{cfg.get('take_profit_pct')}% / {cfg.get('stop_loss_pct')}%"),
        ("P1 hold hours", f"{(cfg.get('phase1') or {}).get('hold_hours', 48)} h"),
        ("P2 take-profit / trailing", f"{(cfg.get('phase2') or {}).get('take_profit_pct', 20)}% / {(cfg.get('phase2') or {}).get('trailing_stop_pct', 8)}%"),
        ("P2 trailing days", f"{(cfg.get('phase2') or {}).get('trailing_stop_days', 14)} days"),
        ("Materiality (P1)", f"{cfg.get('materiality_threshold', 0):.1%}"),
        ("Materiality (P2)", f"{(cfg.get('phase2') or {}).get('materiality_threshold', 0):.1%}"),
    ]
    for label, value in rows:
        grid.add_row(label, value)

    if val.get("issues"):
        grid.add_row("", "")
        grid.add_row("[red bold]Issues[/red bold]", "")
        for i in val["issues"]:
            grid.add_row("", f"[red]• {i}[/red]")
    if val.get("warnings"):
        grid.add_row("", "")
        grid.add_row("[yellow bold]Warnings[/yellow bold]", "")
        for w in val["warnings"]:
            grid.add_row("", f"[yellow]• {w}[/yellow]")

    return Panel(grid, title="[bold]Configuration[/bold]", border_style="cyan")


def render_bot_state(snapshot: Dict[str, Any]) -> Panel:
    state = (snapshot.get("health") or {}).get("bot_state") or {}
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold cyan", no_wrap=True)
    grid.add_column()
    color = _HEALTH_COLOR.get(state.get("status", "unknown"), "dim")
    grid.add_row(
        "Status", f"[{color}]● {state.get('status', 'unknown')}[/{color}]  {state.get('message', '')}"
    )
    details = state.get("details") or {}
    for k in ("path", "last_modified", "age_minutes", "seen_award_ids", "last_check"):
        if k in details:
            grid.add_row(k.replace("_", " "), str(details[k]))
    if state.get("error"):
        grid.add_row("Error", f"[red]{state['error']}[/red]")
    return Panel(grid, title="[bold]Bot State[/bold]", border_style="cyan")


# ---------------------------------------------------------------------------
# Summary + analytics
# ---------------------------------------------------------------------------

def render_summary(snapshot: Dict[str, Any]) -> Panel:
    summary = snapshot.get("summary") or {}
    stats = summary.get("stats") or {}
    deltas = summary.get("deltas") or {}

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold green", no_wrap=True)
    grid.add_column(justify="right")

    def _delta_str(field: str) -> str:
        d = deltas.get(field) or {}
        if not d:
            return ""
        diff = d.get("diff", 0)
        pct = d.get("pct", 0)
        color = "green" if diff >= 0 else "red"
        if isinstance(pct, float) and pct == float("inf"):
            return f"  [{color}](+new)[/{color}]"
        return f"  [{color}]({diff:+,.0f}, {pct:+.1f}%)[/{color}]"

    grid.add_row("Contracts fetched", f"{int(stats.get('count', 0)):,}{_delta_str('count')}")
    grid.add_row("Total $ value", f"${stats.get('total', 0):,.0f}{_delta_str('total')}")
    grid.add_row("Average size", f"${stats.get('avg', 0):,.0f}{_delta_str('avg')}")
    grid.add_row("Median size", f"${stats.get('median', 0):,.0f}")
    grid.add_row("Unique recipients", f"{stats.get('unique_recipients', 0):,}")
    grid.add_row("Unique agencies", f"{stats.get('unique_agencies', 0):,}")
    grid.add_row("Ticker matches", f"{summary.get('matched', 0):,}")
    grid.add_row("Validated tickers", f"{summary.get('validated', 0):,}")
    grid.add_row(
        "Material awards",
        f"[bold]{summary.get('material', 0):,}[/bold]  "
        f"(${summary.get('material_total', 0):,.0f})",
    )
    grid.add_row(
        "Ambiguous matches",
        f"[yellow]{summary.get('ambiguous_matches', 0):,}[/yellow]",
    )
    grid.add_row(
        "Low-confidence matches",
        f"[red]{summary.get('low_confidence_matches', 0):,}[/red]",
    )

    return Panel(grid, title="[bold]Summary[/bold]", border_style="green")


def render_concentration(snapshot: Dict[str, Any]) -> Table:
    conc = (snapshot.get("analytics") or {}).get("concentration") or {}
    table = Table(
        title="Concentration (HHI / top-share)",
        show_header=True,
        header_style="bold magenta",
        border_style="magenta",
        expand=True,
    )
    table.add_column("Dimension", style="bold")
    table.add_column("Unique", justify="right")
    table.add_column("HHI", justify="right")
    table.add_column("Top share", justify="right")
    table.add_column("Top entries", overflow="fold")

    if not conc:
        table.add_row("[dim]disabled[/dim]", "", "", "", "")
        return table

    for label, key in (("Recipient", "recipient"), ("Agency", "agency")):
        block = conc.get(key) or {}
        top_str = ", ".join(
            f"{t['name']} (${t['total']:,.0f})" for t in block.get("top", [])[:3]
        )
        table.add_row(
            label,
            f"{block.get('unique', 0):,}",
            f"{block.get('hhi', 0):,.0f}",
            f"{block.get('top_share', 0):.1%}",
            top_str or "[dim]—[/dim]",
        )
    return table


def render_trend(snapshot: Dict[str, Any]) -> Table:
    trends = (snapshot.get("analytics") or {}).get("trends") or {}
    daily = trends.get("daily") or []
    table = Table(
        title="Daily Trend (most recent first)",
        show_header=True,
        header_style="bold magenta",
        border_style="magenta",
        expand=True,
    )
    table.add_column("Date", no_wrap=True)
    table.add_column("Awards", justify="right")
    table.add_column("Total $", justify="right", style="green")
    if not daily:
        table.add_row("[dim]No trend data[/dim]", "", "")
        return table
    for row in daily[:14]:
        table.add_row(row["date"], f"{row['count']:,}", f"${row['total']:,.0f}")
    return table


def render_anomalies(snapshot: Dict[str, Any]) -> Table:
    anomalies = (snapshot.get("analytics") or {}).get("anomalies") or []
    table = Table(
        title="Anomaly Flags",
        show_header=True,
        header_style="bold red",
        border_style="red",
        expand=True,
    )
    table.add_column("Award ID", no_wrap=True, style="dim")
    table.add_column("Recipient", overflow="fold")
    table.add_column("Amount", justify="right")
    table.add_column("Reasons", overflow="fold")
    if not anomalies:
        table.add_row("[dim]No anomalies detected[/dim]", "", "", "")
        return table
    for a in anomalies[:20]:
        table.add_row(
            str(a.get("award_id") or ""),
            str(a.get("recipient") or ""),
            f"${a.get('amount', 0):,.0f}",
            "; ".join(a.get("reasons", [])),
        )
    return table


def render_repeat(snapshot: Dict[str, Any]) -> Table:
    rows = (snapshot.get("analytics") or {}).get("repeat_recipients") or []
    table = Table(
        title="Repeat Recipients (≥2 awards)",
        show_header=True,
        header_style="bold magenta",
        border_style="magenta",
        expand=True,
    )
    table.add_column("Recipient", overflow="fold")
    table.add_column("Awards", justify="right")
    table.add_column("Total $", justify="right", style="green")
    table.add_column("Avg", justify="right")
    table.add_column("Max", justify="right")
    if not rows:
        table.add_row("[dim]No repeat recipients[/dim]", "", "", "", "")
        return table
    for r in rows:
        table.add_row(
            r["recipient"],
            f"{r['awards']:,}",
            f"${r['total']:,.0f}",
            f"${r['avg']:,.0f}",
            f"${r['max']:,.0f}",
        )
    return table


# ---------------------------------------------------------------------------
# Contracts and ticker tables (with filtering / sorting)
# ---------------------------------------------------------------------------

def _filter_analyses(
    analyses: List[Dict[str, Any]],
    *,
    agency: Optional[str] = None,
    recipient: Optional[str] = None,
    min_amount: Optional[float] = None,
    min_tier: Optional[str] = None,
    material_only: bool = False,
) -> List[Dict[str, Any]]:
    tier_rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    out = analyses
    if agency:
        a_low = agency.lower()
        out = [x for x in out if a_low in str(x.get("agency") or "").lower()]
    if recipient:
        r_low = recipient.lower()
        out = [x for x in out if r_low in str(x.get("recipient") or "").lower()]
    if min_amount is not None:
        out = [x for x in out if float(x.get("amount") or 0) >= min_amount]
    if min_tier and min_tier in tier_rank:
        threshold = tier_rank[min_tier]
        out = [
            x for x in out if tier_rank.get(x["match"].get("tier", "none"), 0) >= threshold
        ]
    if material_only:
        out = [x for x in out if x.get("material")]
    return out


def _sort_analyses(
    analyses: List[Dict[str, Any]],
    sort_by: str,
) -> List[Dict[str, Any]]:
    if sort_by == "amount":
        return sorted(analyses, key=lambda x: float(x.get("amount") or 0), reverse=True)
    if sort_by == "date":
        return sorted(analyses, key=lambda x: str(x.get("action_date") or ""), reverse=True)
    if sort_by == "recipient":
        return sorted(analyses, key=lambda x: str(x.get("recipient") or "").lower())
    if sort_by == "agency":
        return sorted(analyses, key=lambda x: str(x.get("agency") or "").lower())
    if sort_by == "confidence":
        tier_rank = {"high": 3, "medium": 2, "low": 1, "none": 0}
        return sorted(
            analyses,
            key=lambda x: (
                tier_rank.get(x["match"].get("tier", "none"), 0),
                float(x.get("amount") or 0),
            ),
            reverse=True,
        )
    if sort_by == "materiality":
        return sorted(
            analyses,
            key=lambda x: (
                1 if x.get("material") else 0,
                float(x.get("amount") or 0),
            ),
            reverse=True,
        )
    return analyses


def render_contracts(
    snapshot: Dict[str, Any],
    *,
    limit: int = 20,
    sort_by: str = "amount",
    filters: Optional[Dict[str, Any]] = None,
) -> Table:
    analyses = snapshot.get("analyses") or []
    filters = filters or {}
    filtered = _filter_analyses(analyses, **filters)
    sorted_rows = _sort_analyses(filtered, sort_by)[:limit]

    cfg = snapshot.get("config") or {}
    title = (
        f"Recent Large Contracts (top {min(limit, len(filtered))} of "
        f"{len(filtered)}/{len(analyses)})"
        f" — last {cfg.get('days_lookback')} days, "
        f"≥ ${cfg.get('min_contract_amount', 0):,.0f}"
    )
    table = Table(
        title=title,
        show_header=True,
        header_style="bold magenta",
        border_style="magenta",
        expand=True,
    )
    table.add_column("Award ID", style="dim", no_wrap=True)
    table.add_column("Date", no_wrap=True)
    table.add_column("Recipient", overflow="fold")
    table.add_column("Amount", justify="right", style="green")
    table.add_column("Agency", overflow="fold")
    table.add_column("Description", overflow="fold", max_width=50)

    if not sorted_rows:
        table.add_row("[dim]No contracts match[/dim]", "", "", "", "", "")
        return table

    for a in sorted_rows:
        desc = (a.get("description") or "")[:80]
        table.add_row(
            str(a.get("award_id") or ""),
            str(a.get("action_date") or ""),
            str(a.get("recipient") or ""),
            f"${float(a.get('amount') or 0):,.0f}",
            str(a.get("agency") or ""),
            desc,
        )
    return table


def render_tickers(
    snapshot: Dict[str, Any],
    *,
    limit: int = 50,
    sort_by: str = "materiality",
    filters: Optional[Dict[str, Any]] = None,
    show_alternatives: bool = True,
) -> Table:
    analyses = snapshot.get("analyses") or []
    filters = filters or {}
    filtered = _filter_analyses(analyses, **filters)
    sorted_rows = _sort_analyses(filtered, sort_by)[:limit]

    table = Table(
        title=f"Ticker Matches & Materiality (showing {len(sorted_rows)}/{len(analyses)})",
        show_header=True,
        header_style="bold yellow",
        border_style="yellow",
        expand=True,
    )
    table.add_column("Recipient", overflow="fold")
    table.add_column("Amount", justify="right")
    table.add_column("Ticker", style="bold", no_wrap=True)
    table.add_column("Tier", justify="center")
    table.add_column("Score", justify="right")
    table.add_column("Mkt Cap", justify="right")
    table.add_column("Ratio", justify="right")
    table.add_column("Material", justify="center")
    table.add_column("Eligibility", overflow="fold")

    if not sorted_rows:
        table.add_row("[dim]No analyses[/dim]", "", "", "", "", "", "", "", "")
        return table

    for a in sorted_rows:
        recipient = (a.get("recipient") or "")[:60]
        match = a.get("match") or {}
        tier = match.get("tier") or "none"
        tier_color = _TIER_COLOR.get(tier, "dim")
        score = match.get("score")
        score_str = f"{score:.1f}" if isinstance(score, (int, float)) else "—"
        info = a.get("info") or {}
        mkt_cap = info.get("market_cap")
        ratio = (
            float(a.get("amount") or 0) / mkt_cap if mkt_cap else 0
        )
        ticker = match.get("ticker")
        tier_cell = f"[{tier_color}]{tier}[/{tier_color}]"
        if match.get("ambiguous"):
            tier_cell += " [yellow]⚠[/yellow]"
        material_cell = (
            "[green]✔[/green]" if a.get("material") else "[red]✘[/red]"
        )
        elig = a.get("eligibility") or {}
        elig_status = elig.get("status", "skipped")
        elig_color = {
            "eligible": "green",
            "blocked": "yellow",
            "skipped": "dim",
        }.get(elig_status, "dim")
        elig_reason = "; ".join(elig.get("reasons", [])) or "—"
        table.add_row(
            recipient,
            f"${float(a.get('amount') or 0):,.0f}",
            ticker or "[dim]—[/dim]",
            tier_cell,
            score_str,
            f"${mkt_cap:,.0f}" if mkt_cap else "[dim]—[/dim]",
            f"{ratio:.3%}" if mkt_cap else "[dim]—[/dim]",
            material_cell,
            f"[{elig_color}]{elig_status}[/{elig_color}]: {elig_reason}",
        )
        if show_alternatives and match.get("alternatives"):
            alt = match["alternatives"][0]
            table.add_row(
                "[dim]  alt[/dim]",
                "",
                f"[dim]{alt['ticker']}[/dim]",
                "[dim]—[/dim]",
                f"[dim]{alt['score']:.1f}[/dim]",
                "",
                "",
                "",
                f"[dim]{alt['title']}[/dim]",
            )
    return table


# ---------------------------------------------------------------------------
# Trading section
# ---------------------------------------------------------------------------

def render_alpaca(snapshot: Dict[str, Any], *, show_orders: bool = True) -> Group:
    alpaca = snapshot.get("alpaca") or {}
    blocks: List[Any] = []

    if not alpaca.get("configured"):
        return Group(
            Panel(
                "[dim]Alpaca API key not configured — skipping account, "
                "positions, and orders.[/dim]",
                title="[bold]Alpaca[/bold]",
                border_style="blue",
            )
        )
    if alpaca.get("error"):
        return Group(
            Panel(
                f"[red]Alpaca section error:[/red] {alpaca['error']}",
                title="[bold]Alpaca[/bold]",
                border_style="red",
            )
        )

    account = alpaca.get("account") or {}
    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="bold blue", no_wrap=True)
    summary.add_column()
    cfg = snapshot.get("config") or {}
    summary.add_row(
        "Mode",
        "[yellow]PAPER[/yellow]" if cfg.get("alpaca_paper") else "[red]LIVE[/red]",
    )
    summary.add_row("Portfolio value", f"${account.get('portfolio_value', 0):,.2f}")
    summary.add_row("Equity", f"${account.get('equity', 0):,.2f}")
    summary.add_row("Buying power", f"${account.get('buying_power', 0):,.2f}")
    day_pl = float(account.get("day_pl", 0))
    day_pl_pct = float(account.get("day_pl_pct", 0))
    color = "green" if day_pl >= 0 else "red"
    summary.add_row(
        "Today's P/L",
        f"[{color}]${day_pl:+,.2f} ({day_pl_pct:+.2f}%)[/{color}]",
    )
    used = alpaca.get("daily_buys_used")
    max_trades = cfg.get("max_daily_trades", 0)
    if used is None:
        trades_str = "[dim]unknown[/dim]"
    else:
        remaining = max(int(max_trades) - int(used), 0)
        c = "green" if remaining > 0 else "red"
        trades_str = (
            f"[{c}]{used}/{max_trades} used ({remaining} remaining)[/{c}]"
        )
    summary.add_row("Daily trades", trades_str)

    expo = alpaca.get("exposure_concentration") or {}
    if expo:
        summary.add_row(
            "Exposure HHI",
            f"{expo.get('hhi', 0):,.0f}  "
            f"(top {expo.get('top_share', 0):.0%})",
        )

    positions = alpaca.get("positions") or []
    pos_table = Table(
        title=f"Open Positions ({len(positions)})",
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
    pos_table.add_column("P/L", justify="right")
    pos_table.add_column("P/L %", justify="right")
    if positions:
        for p in positions:
            pl = float(p.get("unrealized_pl") or 0)
            plpc = float(p.get("unrealized_plpc") or 0)
            c = "green" if pl >= 0 else "red"
            pos_table.add_row(
                p["symbol"],
                f"{p['qty']:g}",
                f"${p['avg_entry_price']:,.2f}",
                f"${p['current_price']:,.2f}" if p.get("current_price") else "—",
                f"${p['market_value']:,.2f}",
                f"[{c}]${pl:+,.2f}[/{c}]",
                f"[{c}]{plpc:+.2f}%[/{c}]",
            )
    else:
        pos_table.add_row("[dim]No open positions[/dim]", "", "", "", "", "", "")

    blocks.append(
        Columns(
            [
                Panel(
                    summary,
                    title="[bold]Alpaca Account[/bold]",
                    border_style="blue",
                ),
                pos_table,
            ]
        )
    )

    # Drawdown leaders
    leaders = alpaca.get("drawdown_leaders") or []
    if leaders:
        dd = Table(
            title="Drawdown Leaders",
            show_header=True,
            header_style="bold red",
            border_style="red",
            expand=True,
        )
        dd.add_column("Symbol", style="bold")
        dd.add_column("P/L", justify="right")
        dd.add_column("P/L %", justify="right")
        dd.add_column("Mkt Value", justify="right")
        for r in leaders:
            dd.add_row(
                r["symbol"],
                f"[red]${float(r.get('unrealized_pl') or 0):+,.2f}[/red]",
                f"[red]{float(r.get('unrealized_plpc') or 0):+.2f}%[/red]",
                f"${float(r.get('market_value') or 0):,.2f}",
            )
        blocks.append(dd)

    if show_orders:
        lifecycle = alpaca.get("lifecycle") or {}
        lc = Table.grid(padding=(0, 3))
        lc.add_column(style="bold blue")
        lc.add_column(justify="right")
        for k in ("submitted", "filled", "rejected", "canceled", "aging"):
            lc.add_row(k, str(lifecycle.get(k, 0)))
        lc_panel = Panel(
            lc, title="[bold]Order Lifecycle (14d)[/bold]", border_style="blue"
        )

        orders = alpaca.get("orders") or []
        order_table = Table(
            title=f"Recent Orders ({len(orders)})",
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
        if not orders:
            order_table.add_row("[dim]No recent orders[/dim]", "", "", "", "", "")
        else:
            for o in orders[:15]:
                submitted = o.get("submitted_at") or ""
                if submitted:
                    try:
                        submitted = datetime.fromisoformat(
                            submitted.replace("Z", "+00:00")
                        ).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        pass
                side = o.get("side", "")
                side_color = "green" if side == "BUY" else "red"
                qty_or_n = (
                    f"${o['notional']:,.2f}"
                    if o.get("notional")
                    else str(o.get("qty") or "—")
                )
                filled = (
                    f"${o['filled_dollar']:,.2f}"
                    if o.get("filled_dollar")
                    else "—"
                )
                order_table.add_row(
                    str(submitted),
                    str(o.get("symbol") or ""),
                    f"[{side_color}]{side}[/{side_color}]",
                    qty_or_n,
                    str(o.get("status") or ""),
                    filled,
                )
        blocks.append(Columns([lc_panel, order_table]))

    return Group(*blocks)


# ---------------------------------------------------------------------------
# Two-phase engine panel
# ---------------------------------------------------------------------------

def render_two_phase(snapshot: Dict[str, Any]) -> Group:
    """Render Phase 1 / Phase 2 configuration and live candidacy summary."""
    cfg = snapshot.get("config") or {}
    p1 = cfg.get("phase1") or {}
    p2 = cfg.get("phase2") or {}
    tp = snapshot.get("two_phase") or {}

    # ── Phase 1 config ───────────────────────────────────────────────────────
    p1_grid = Table.grid(padding=(0, 2))
    p1_grid.add_column(style="bold cyan", no_wrap=True)
    p1_grid.add_column()
    p1_grid.add_row("Buy notional",    f"${p1.get('buy_notional', 0):,.0f}")
    p1_grid.add_row("Hold hours",      f"{p1.get('hold_hours', 48)} h")
    p1_grid.add_row("Take profit",     f"{p1.get('take_profit_pct', 12)}%")
    p1_grid.add_row("Stop loss",       f"{p1.get('stop_loss_pct', 5)}%")
    p1_grid.add_row("Volume spike ×",  f"{p1.get('volume_spike_multiplier', 2.0):.1f}×")
    p1_grid.add_row("Max spread",      f"{p1.get('max_spread_pct', 0.005):.2%}")
    p1_grid.add_row(
        "Candidates",
        f"[bold]{tp.get('phase1_candidates', 0)}[/bold] material award(s) in window",
    )
    p1_panel = Panel(p1_grid, title="[bold green]Phase 1 — Quick Profit[/bold green]", border_style="green")

    # ── Phase 2 config ───────────────────────────────────────────────────────
    p2_grid = Table.grid(padding=(0, 2))
    p2_grid.add_column(style="bold cyan", no_wrap=True)
    p2_grid.add_column()
    p2_grid.add_row("Buy notional",     f"${p2.get('buy_notional', 0):,.0f}")
    p2_grid.add_row("Materiality ≥",    f"{p2.get('materiality_threshold', 0.0075):.2%}")
    p2_grid.add_row("Take profit",      f"{p2.get('take_profit_pct', 20)}%")
    p2_grid.add_row("Trailing stop",    f"{p2.get('trailing_stop_pct', 8)}%")
    p2_grid.add_row("Max hold",         f"{p2.get('trailing_stop_days', 14)} days")
    p2_tickers = tp.get("phase2_tickers") or []
    ticker_str = ", ".join(p2_tickers[:10]) if p2_tickers else "[dim]none[/dim]"
    p2_grid.add_row(
        "Candidates",
        f"[bold]{tp.get('phase2_candidates', 0)}[/bold] award(s) — {ticker_str}",
    )
    p2_panel = Panel(p2_grid, title="[bold blue]Phase 2 — Large Profit[/bold blue]", border_style="blue")

    return Group(
        Rule("[bold]Two-Phase Profit Engine[/bold]", style="green"),
        Columns([p1_panel, p2_panel], equal=True, expand=True),
    )

def render_dashboard(
    snapshot: Dict[str, Any],
    *,
    view: str = VIEW_ALL,
    refresh: Optional[int] = None,
    limit: int = 20,
    sort_by: str = "amount",
    ticker_sort_by: str = "materiality",
    filters: Optional[Dict[str, Any]] = None,
    show_orders: bool = True,
) -> Group:
    blocks: List[Any] = [
        _safe(render_header, snapshot, refresh),
        _safe(render_health, snapshot),
    ]

    if view in (VIEW_ALL, VIEW_OVERVIEW):
        blocks.append(
            Columns(
                [_safe(render_config, snapshot), _safe(render_bot_state, snapshot)],
                equal=True,
                expand=True,
            )
        )
        blocks.append(Rule(style="dim"))
        blocks.append(
            Columns(
                [
                    _safe(render_summary, snapshot),
                    _safe(render_concentration, snapshot),
                ],
                equal=True,
                expand=True,
            )
        )
        blocks.append(_safe(render_trend, snapshot))
        blocks.append(_safe(render_repeat, snapshot))
        blocks.append(_safe(render_anomalies, snapshot))

    if view in (VIEW_ALL, VIEW_CONTRACTS):
        blocks.append(Rule(style="dim"))
        blocks.append(
            _safe(
                render_contracts,
                snapshot,
                limit=limit,
                sort_by=sort_by,
                filters=filters,
            )
        )

    if view in (VIEW_ALL, VIEW_TICKERS):
        blocks.append(Rule(style="dim"))
        blocks.append(
            _safe(
                render_tickers,
                snapshot,
                limit=max(limit, 30),
                sort_by=ticker_sort_by,
                filters=filters,
            )
        )

    if view in (VIEW_ALL, VIEW_TRADING):
        blocks.append(Rule(style="dim"))
        blocks.append(_safe(render_two_phase, snapshot))
        blocks.append(_safe(render_alpaca, snapshot, show_orders=show_orders))

    return Group(*blocks)
