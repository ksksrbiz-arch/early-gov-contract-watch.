"""
Versioned snapshot assembly for dashboard v2.

The snapshot is the single source of truth for every view (terminal output,
JSON export, future UI). It is *pure data* — no Rich objects, no CLI state —
so it stays serializable and testable.

Schema version 2.0 layout:

    {
      "schema_version": "2.0",
      "generated_at": "...",
      "config": { ... },
      "config_validation": {"issues": [...], "warnings": [...]},
      "health": {
        "config": SectionHealth,
        "bot_state": SectionHealth,
        "usaspending": SectionHealth,
        "ticker_source": SectionHealth,
        "alpaca": SectionHealth
      },
      "contracts": [ ...USASpending awards... ],
      "summary": {
        "stats": {...},
        "deltas": {...},        # vs previous snapshot if history enabled
        "matched": int,
        "validated": int,
        "material": int,
        "material_total": float
      },
      "analytics": {
        "trends": {"daily": [...], "weekly": [...]},
        "concentration": {"agency": {...}, "recipient": {...}},
        "repeat_recipients": [...],
        "anomalies": [...]
      },
      "analyses": [ {recipient, amount, ticker, match, info, material, eligibility} ],
      "alpaca": {
        "configured": bool,
        "account": {...} | None,
        "positions": [...],
        "orders": [...],
        "lifecycle": {"submitted": n, "filled": n, "rejected": n, "canceled": n, "aging": n},
        "exposure_concentration": {"hhi": ..., "top_share": ..., "top": [...]},
        "drawdown_leaders": [...]
      },
      "errors": {section: error_string}
    }

Rendering and CLI never reach past this contract.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from . import analytics, config_v2, eligibility, health
from .confidence import (
    REASON_NONE,
    TIER_NONE,
    classify_match,
)

logger = logging.getLogger(__name__)

# Bumped on any breaking change to the snapshot layout.
SNAPSHOT_SCHEMA_VERSION = "2.0"

# Internal caches for one snapshot build (cleared by callers between refreshes).
_TICKER_INFO_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}
_COMPANY_LIST_CACHE: Dict[str, List[Tuple[str, str]]] = {}
_CACHE_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------

def reset_caches() -> None:
    """Drop ticker info and company list caches (used between live refreshes)."""
    with _CACHE_LOCK:
        _TICKER_INFO_CACHE.clear()
        _COMPANY_LIST_CACHE.clear()


def _load_companies() -> List[Tuple[str, str]]:
    """Load (title, ticker) pairs once per build, mirroring ticker_lookup."""
    with _CACHE_LOCK:
        cached = _COMPANY_LIST_CACHE.get("default")
        if cached is not None:
            return cached
    try:
        from ticker_lookup import load_tickers  # local import — avoids hard dep at import time
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("ticker_lookup unavailable: %s", exc)
        with _CACHE_LOCK:
            _COMPANY_LIST_CACHE["default"] = []
        return []
    try:
        raw = load_tickers() or {}
        companies = [
            (v["title"], v["ticker"])
            for v in raw.values()
            if isinstance(v, dict) and v.get("ticker") and v.get("title")
        ]
    except Exception as exc:
        logger.warning("Failed to load SEC tickers: %s", exc)
        companies = []
    with _CACHE_LOCK:
        _COMPANY_LIST_CACHE["default"] = companies
    return companies


def _ticker_info(ticker: str) -> Optional[Dict[str, Any]]:
    """yfinance lookup, cached for the life of one snapshot build."""
    if not ticker:
        return None
    with _CACHE_LOCK:
        if ticker in _TICKER_INFO_CACHE:
            return _TICKER_INFO_CACHE[ticker]
    info: Optional[Dict[str, Any]]
    try:
        import yfinance as yf  # type: ignore

        raw = yf.Ticker(ticker).info or {}
        price = raw.get("regularMarketPrice")
        mkt_cap = raw.get("marketCap") or 0
        if price and mkt_cap > 0:
            info = {
                "ticker": ticker,
                "name": raw.get("shortName"),
                "market_cap": float(mkt_cap),
                "price": float(price),
            }
        else:
            info = None
    except Exception as exc:  # noqa: BLE001
        logger.debug("yfinance lookup failed for %s: %s", ticker, exc)
        info = None
    with _CACHE_LOCK:
        _TICKER_INFO_CACHE[ticker] = info
    return info


# ---------------------------------------------------------------------------
# Config view
# ---------------------------------------------------------------------------

def _config_view() -> Dict[str, Any]:
    """Snapshot-ready, env-only view of the bot's runtime config."""
    from config import (  # type: ignore
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

    return {
        "alpaca_api_key_set": bool(ALPACA_API_KEY),
        "alpaca_paper": bool(ALPACA_PAPER),
        "buy_notional": float(BUY_NOTIONAL),
        "min_contract_amount": float(MIN_CONTRACT_AMOUNT),
        "days_lookback": int(DAYS_LOOKBACK),
        "poll_interval_minutes": int(POLL_INTERVAL_MINUTES),
        "max_daily_trades": int(MAX_DAILY_TRADES),
        "take_profit_pct": float(TAKE_PROFIT_PCT),
        "stop_loss_pct": float(STOP_LOSS_PCT),
        "sell_after_days": int(SELL_AFTER_DAYS),
        "materiality_threshold": float(MATERIALITY_THRESHOLD),
        "state_file": STATE_FILE,
    }


# ---------------------------------------------------------------------------
# Per-recipient analyses
# ---------------------------------------------------------------------------

def _build_analyses(
    awards: List[dict],
    *,
    validate: bool,
    held_symbols: List[str],
    daily_buys_used: Optional[int],
    max_daily_trades: int,
    min_confidence_tier: str,
) -> List[Dict[str, Any]]:
    """
    Per-award ticker resolution + market-data + materiality + eligibility.

    Mirrors `ticker_lookup.get_ticker_for_company` for the chosen ticker, then
    layers v2 confidence/eligibility metadata on top so renderers don't repeat
    business logic.
    """
    companies = _load_companies()
    out: List[Dict[str, Any]] = []
    for award in awards:
        recipient = (award.get("Recipient Name") or "").strip()
        try:
            amount = float(award.get("Award Amount") or 0)
        except (TypeError, ValueError):
            amount = 0.0

        match = classify_match(recipient, companies)
        ticker = match.ticker

        info: Optional[Dict[str, Any]] = None
        material = False
        if ticker and validate:
            info = _ticker_info(ticker)
            if info is None:
                # Fall back to project's lightweight validate_ticker (no price).
                try:
                    from ticker_lookup import validate_ticker  # type: ignore

                    fallback = validate_ticker(ticker)
                except Exception:
                    fallback = None
                if fallback:
                    info = {
                        "ticker": ticker,
                        "name": fallback.get("name"),
                        "market_cap": float(fallback.get("market_cap") or 0),
                        "price": None,
                    }
            if info and info.get("market_cap"):
                try:
                    from ticker_lookup import is_material_award  # type: ignore

                    material = bool(
                        is_material_award(amount, info["market_cap"])
                    )
                except Exception:
                    material = False

        elig = eligibility.evaluate_eligibility(
            ticker=ticker,
            confidence_tier=match.tier,
            has_market_data=info is not None and info.get("price") is not None,
            is_material=material,
            held_symbols=held_symbols,
            daily_buys_used=daily_buys_used,
            max_daily_trades=max_daily_trades,
            min_confidence_tier=min_confidence_tier,
        )

        out.append(
            {
                "award_id": award.get("Award ID"),
                "action_date": award.get("Action Date"),
                "agency": award.get("Awarding Agency"),
                "description": award.get("Description"),
                "recipient": recipient,
                "amount": amount,
                "ticker": ticker,
                "match": match.to_dict(),
                "info": info,
                "material": material,
                "eligibility": elig.to_dict(),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Alpaca section
# ---------------------------------------------------------------------------

def _alpaca_section(api_key_present: bool, fetch_orders: bool) -> Dict[str, Any]:
    """Best-effort Alpaca section — never raises; returns degraded shapes."""
    section: Dict[str, Any] = {
        "configured": api_key_present,
        "account": None,
        "positions": [],
        "orders": [],
        "lifecycle": {
            "submitted": 0,
            "filled": 0,
            "rejected": 0,
            "canceled": 0,
            "aging": 0,
        },
        "exposure_concentration": None,
        "drawdown_leaders": [],
        "daily_buys_used": None,
        "held_symbols": [],
        "error": None,
    }
    if not api_key_present:
        return section

    try:
        from alpaca.trading.enums import OrderSide  # type: ignore

        from trader import AlpacaTrader  # type: ignore
    except Exception as exc:  # noqa: BLE001
        section["error"] = f"alpaca import failed: {exc}"
        return section

    try:
        trader = AlpacaTrader()
        positions = list(trader.client.get_all_positions())
        account = trader.account
    except Exception as exc:  # noqa: BLE001
        section["error"] = f"alpaca connect failed: {exc}"
        return section

    portfolio_value = float(getattr(account, "portfolio_value", 0) or 0)
    equity = float(getattr(account, "equity", 0) or 0)
    last_equity = float(getattr(account, "last_equity", 0) or 0)
    section["account"] = {
        "portfolio_value": portfolio_value,
        "equity": equity,
        "last_equity": last_equity,
        "buying_power": float(getattr(account, "buying_power", 0) or 0),
        "cash": float(getattr(account, "cash", 0) or 0),
        "day_pl": equity - last_equity,
        "day_pl_pct": (
            (equity - last_equity) / last_equity * 100 if last_equity else 0.0
        ),
    }

    pos_rows: List[Dict[str, Any]] = []
    held_symbols: List[str] = []
    total_market_value = 0.0
    for p in positions:
        try:
            mv = float(getattr(p, "market_value", 0) or 0)
            pl = float(getattr(p, "unrealized_pl", 0) or 0)
            plpc = float(getattr(p, "unrealized_plpc", 0) or 0) * 100
            row = {
                "symbol": p.symbol,
                "qty": float(p.qty),
                "avg_entry_price": float(getattr(p, "avg_entry_price", 0) or 0),
                "current_price": float(getattr(p, "current_price", 0) or 0),
                "market_value": mv,
                "unrealized_pl": pl,
                "unrealized_plpc": plpc,
            }
            pos_rows.append(row)
            held_symbols.append(p.symbol)
            total_market_value += mv
        except Exception:  # noqa: BLE001
            continue
    section["positions"] = pos_rows
    section["held_symbols"] = held_symbols

    # Exposure concentration over portfolio market value.
    if total_market_value > 0 and pos_rows:
        shares = [
            (r["symbol"], r["market_value"] / total_market_value) for r in pos_rows
        ]
        shares.sort(key=lambda s: s[1], reverse=True)
        hhi = sum((s * 100) ** 2 for _, s in shares)
        section["exposure_concentration"] = {
            "hhi": hhi,
            "top_share": shares[0][1] if shares else 0.0,
            "top": [
                {"symbol": sym, "share": share}
                for sym, share in shares[: min(5, len(shares))]
            ],
        }

    # Drawdown leaders (largest unrealized losses first).
    section["drawdown_leaders"] = sorted(
        [r for r in pos_rows if r["unrealized_pl"] < 0],
        key=lambda r: r["unrealized_pl"],
    )[:5]

    # Orders + lifecycle counters.
    today_buys = 0
    if fetch_orders:
        try:
            from alpaca.trading.enums import QueryOrderStatus  # type: ignore
            from alpaca.trading.requests import GetOrdersRequest  # type: ignore

            req = GetOrdersRequest(
                status=QueryOrderStatus.ALL,
                limit=50,
                after=datetime.now(timezone.utc) - timedelta(days=14),
            )
            orders = list(trader.client.get_orders(filter=req))
        except Exception:
            try:
                orders = list(trader.client.get_orders())[:50]
            except Exception:
                orders = []

        order_rows: List[Dict[str, Any]] = []
        lifecycle = section["lifecycle"]
        now_utc = datetime.now(timezone.utc)
        for o in orders:
            try:
                side = str(getattr(o.side, "value", o.side)).upper()
                status = str(getattr(o.status, "value", o.status)).lower()
                submitted = getattr(o, "submitted_at", None) or getattr(
                    o, "created_at", None
                )
                filled_at = getattr(o, "filled_at", None)
                filled_avg = getattr(o, "filled_avg_price", None)
                filled_qty = getattr(o, "filled_qty", None)
                try:
                    filled_dollar = (
                        float(filled_avg) * float(filled_qty)
                        if filled_avg and filled_qty
                        else None
                    )
                except Exception:
                    filled_dollar = None
                row = {
                    "symbol": getattr(o, "symbol", None),
                    "side": side,
                    "status": status,
                    "submitted_at": (
                        submitted.isoformat() if submitted else None
                    ),
                    "filled_at": filled_at.isoformat() if filled_at else None,
                    "qty": str(getattr(o, "qty", "") or ""),
                    "notional": (
                        float(o.notional) if getattr(o, "notional", None) else None
                    ),
                    "filled_dollar": filled_dollar,
                }
                order_rows.append(row)

                if status == "filled":
                    lifecycle["filled"] += 1
                elif status in ("rejected",):
                    lifecycle["rejected"] += 1
                elif status in ("canceled", "cancelled", "expired"):
                    lifecycle["canceled"] += 1
                elif status in ("new", "accepted", "pending_new", "partially_filled"):
                    lifecycle["submitted"] += 1
                    if submitted and (
                        now_utc - submitted
                    ) > timedelta(minutes=15):
                        lifecycle["aging"] += 1

                if (
                    side == "BUY"
                    and filled_at
                    and filled_at.date() == datetime.now().date()
                ):
                    today_buys += 1
            except Exception:  # noqa: BLE001
                continue
        section["orders"] = order_rows
        section["daily_buys_used"] = today_buys

    return section


# ---------------------------------------------------------------------------
# History (for trend deltas)
# ---------------------------------------------------------------------------

def _load_previous_history(history_file: str) -> Optional[Dict[str, Any]]:
    if not history_file or not os.path.exists(history_file):
        return None
    try:
        with open(history_file) as f:
            data = json.load(f)
    except Exception:
        return None
    entries = data.get("entries") if isinstance(data, dict) else None
    if not entries:
        return None
    return entries[-1]


def _append_history(
    history_file: str,
    entry: Dict[str, Any],
    *,
    limit: int,
) -> None:
    if not history_file or limit <= 0:
        return
    data: Dict[str, Any] = {"entries": []}
    if os.path.exists(history_file):
        try:
            with open(history_file) as f:
                loaded = json.load(f)
            if isinstance(loaded, dict) and isinstance(loaded.get("entries"), list):
                data = loaded
        except Exception:
            data = {"entries": []}
    entries: List[Dict[str, Any]] = data.get("entries", [])
    entries.append(entry)
    data["entries"] = entries[-limit:]
    try:
        with open(history_file, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not write history file %s: %s", history_file, exc)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_snapshot(
    *,
    validate: bool = True,
    fetch_orders: bool = True,
    fetch_alpaca: bool = True,
    toggles: Optional[Dict[str, Any]] = None,
    awards_override: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """
    Build the full v2 snapshot.

    Parameters mirror existing CLI flags so the legacy entry-point keeps
    working unchanged. `awards_override` lets tests inject deterministic data.
    """
    toggles = toggles or config_v2.load_v2_toggles()
    config_view = _config_view()
    cfg_issues, cfg_warnings = config_v2.validate_config(config_view)
    errors: Dict[str, str] = {}

    # 1. Fetch upstream contracts (or accept injected fixture).
    fetch_error: Optional[str] = None
    if awards_override is not None:
        awards: List[dict] = list(awards_override)
    else:
        try:
            from usaspending_fetcher import (  # type: ignore
                fetch_recent_large_contracts,
            )

            awards = fetch_recent_large_contracts() or []
        except Exception as exc:  # noqa: BLE001
            fetch_error = str(exc)
            errors["usaspending"] = fetch_error
            awards = []

    # 2. Alpaca section (must run before analyses for eligibility context).
    if fetch_alpaca and config_view["alpaca_api_key_set"]:
        alpaca_section = _alpaca_section(api_key_present=True, fetch_orders=fetch_orders)
    else:
        alpaca_section = {
            "configured": config_view["alpaca_api_key_set"],
            "account": None,
            "positions": [],
            "orders": [],
            "lifecycle": {
                "submitted": 0,
                "filled": 0,
                "rejected": 0,
                "canceled": 0,
                "aging": 0,
            },
            "exposure_concentration": None,
            "drawdown_leaders": [],
            "daily_buys_used": None,
            "held_symbols": [],
            "error": None,
        }
    if alpaca_section.get("error"):
        errors["alpaca"] = alpaca_section["error"]

    # 3. Per-award analyses with confidence + eligibility.
    analyses = _build_analyses(
        awards,
        validate=validate,
        held_symbols=alpaca_section.get("held_symbols", []) or [],
        daily_buys_used=alpaca_section.get("daily_buys_used"),
        max_daily_trades=int(config_view["max_daily_trades"]),
        min_confidence_tier=str(toggles.get("ticker_min_confidence", "medium")),
    )

    matched = [a for a in analyses if a.get("ticker")]
    validated = [a for a in matched if a.get("info")]
    material = [a for a in validated if a.get("material")]
    material_total = float(sum(a["amount"] for a in material))

    # 4. Analytics + history-based deltas.
    if toggles.get("enable_analytics", True):
        stats = analytics.basic_stats(awards)
        trend_block = {
            "daily": analytics.daily_trend(
                awards, days=int(toggles.get("trend_days", 14))
            ),
            "weekly": analytics.weekly_trend(
                awards, weeks=int(toggles.get("trend_weeks", 4))
            ),
        }
        concentration_block = {
            "agency": analytics.concentration(awards, "Awarding Agency", top_n=5),
            "recipient": analytics.concentration(awards, "Recipient Name", top_n=5),
        }
        repeat = analytics.repeat_recipients(awards, min_awards=2, top_n=10)
        anomalies = (
            analytics.anomaly_flags(awards)
            if toggles.get("enable_anomalies", True)
            else []
        )
    else:
        stats = analytics.basic_stats(awards)
        trend_block = {"daily": [], "weekly": []}
        concentration_block = {}
        repeat = []
        anomalies = []

    previous_entry = (
        _load_previous_history(toggles.get("history_file", ""))
        if toggles.get("enable_history", True)
        else None
    )
    deltas = analytics.trend_deltas(
        stats, previous_entry.get("stats") if previous_entry else None
    )

    summary = {
        "stats": stats,
        "deltas": deltas,
        "matched": len(matched),
        "validated": len(validated),
        "material": len(material),
        "material_total": material_total,
        "ambiguous_matches": sum(
            1 for a in analyses if a["match"].get("ambiguous")
        ),
        "low_confidence_matches": sum(
            1
            for a in analyses
            if a["match"].get("ticker") and a["match"].get("tier") == "low"
        ),
    }

    # 5. Health checks (after we know fetch outcomes).
    health_block = {
        "config": health.config_health(cfg_issues, cfg_warnings).to_dict(),
        "bot_state": health.state_file_health(
            config_view["state_file"], int(config_view["poll_interval_minutes"])
        ).to_dict(),
        "usaspending": health.usaspending_health(awards, fetch_error).to_dict(),
        "ticker_source": health.ticker_source_health().to_dict(),
        "alpaca": health.alpaca_health(
            api_key_present=config_view["alpaca_api_key_set"],
            error=alpaca_section.get("error"),
        ).to_dict(),
    }

    snapshot: Dict[str, Any] = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "config": config_view,
        "config_validation": {"issues": cfg_issues, "warnings": cfg_warnings},
        "health": health_block,
        "contracts": awards,
        "summary": summary,
        "analytics": {
            "trends": trend_block,
            "concentration": concentration_block,
            "repeat_recipients": repeat,
            "anomalies": anomalies,
        },
        "analyses": analyses,
        "alpaca": alpaca_section,
        "errors": errors,
        "toggles": toggles,
    }

    # 6. Persist a compact entry to history for next-run delta comparisons.
    if toggles.get("enable_history", True):
        history_entry = {
            "generated_at": snapshot["generated_at"],
            "stats": stats,
            "matched": summary["matched"],
            "material": summary["material"],
            "material_total": material_total,
        }
        _append_history(
            toggles.get("history_file", "dashboard_history.json"),
            history_entry,
            limit=int(toggles.get("history_limit", 30)),
        )

    return snapshot


def snapshot_to_export(
    snapshot: Dict[str, Any],
    *,
    profile: str = config_v2.PROFILE_FULL,
) -> Dict[str, Any]:
    """
    Produce a profile-shaped export of the snapshot.

    `compact` strips bulky raw payloads (full contracts, raw orders) but keeps
    the versioned schema and aggregated views. `full` returns the snapshot
    unchanged so downstream tooling has access to everything.
    """
    if profile not in (config_v2.PROFILE_COMPACT, config_v2.PROFILE_FULL):
        profile = config_v2.PROFILE_FULL

    if profile == config_v2.PROFILE_FULL:
        out = dict(snapshot)
        out["export_profile"] = profile
        return out

    # Compact profile.
    compact_analyses = [
        {
            "recipient": a["recipient"],
            "amount": a["amount"],
            "ticker": a["ticker"],
            "tier": a["match"].get("tier"),
            "score": a["match"].get("score"),
            "ambiguous": a["match"].get("ambiguous"),
            "material": a["material"],
            "eligibility_status": a["eligibility"]["status"],
            "market_cap": (a.get("info") or {}).get("market_cap"),
            "price": (a.get("info") or {}).get("price"),
        }
        for a in snapshot.get("analyses", [])
    ]
    alpaca = snapshot.get("alpaca") or {}
    return {
        "schema_version": snapshot.get("schema_version"),
        "export_profile": profile,
        "generated_at": snapshot.get("generated_at"),
        "config": snapshot.get("config"),
        "config_validation": snapshot.get("config_validation"),
        "health": snapshot.get("health"),
        "summary": snapshot.get("summary"),
        "analytics": snapshot.get("analytics"),
        "analyses": compact_analyses,
        "alpaca": {
            "configured": alpaca.get("configured"),
            "account": alpaca.get("account"),
            "lifecycle": alpaca.get("lifecycle"),
            "exposure_concentration": alpaca.get("exposure_concentration"),
            "drawdown_leaders": alpaca.get("drawdown_leaders"),
            "daily_buys_used": alpaca.get("daily_buys_used"),
        },
        "errors": snapshot.get("errors", {}),
    }
