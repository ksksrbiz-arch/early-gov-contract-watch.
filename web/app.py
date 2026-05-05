"""
Flask web app for the Early Gov Contract Watch dashboard.

This is the operator's primary control surface: render the snapshot, drive
the bot lifecycle (start / stop / run-now), tail logs, edit config, and
close positions manually — all without touching the command line.

Routes
------
HTML pages
    GET  /                  → overview dashboard
    GET  /contracts         → contracts table
    GET  /tickers           → ticker matches & materiality
    GET  /trading           → Alpaca account / positions / orders
    GET  /control           → start / stop / logs / run-now
    GET  /config            → view + edit env-var config

JSON API
    GET  /api/snapshot      → full v2 snapshot dict
    GET  /api/bot/status    → controller status
    POST /api/bot/start     → start the bot loop
    POST /api/bot/stop      → stop the bot loop
    POST /api/bot/tick      → run a single iteration synchronously
    GET  /api/bot/logs      → recent log entries (?since=ISO&limit=N)
    POST /api/bot/logs/clear→ clear log buffer
    GET  /api/config        → current config view
    POST /api/config        → update env-var config (writes .env)
    POST /api/positions/<symbol>/sell → liquidate position
    GET  /api/health        → liveness probe (always 200)
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any, Dict, Optional

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from .bot_controller import BotController, get_controller
from .config_io import (
    EDITABLE_KEYS,
    apply_env_updates,
    read_dotenv,
    update_dotenv,
)


_SNAPSHOT_LOCK = threading.Lock()
_SNAPSHOT_CACHE: Dict[str, Any] = {"ts": 0.0, "value": None, "ttl": 30.0}


def create_app(
    *,
    controller: Optional[BotController] = None,
    snapshot_builder=None,
    dotenv_path: Optional[str] = None,
) -> Flask:
    """Build the Flask app. Tests inject their own controller / builder."""
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )
    app.config["JSON_SORT_KEYS"] = False

    ctl = controller or get_controller()
    builder = snapshot_builder or _default_snapshot_builder
    env_path = dotenv_path or os.getenv("DASHBOARD_DOTENV_PATH", ".env")

    # ── HTML pages ───────────────────────────────────────────────────────

    @app.get("/")
    def page_overview():
        return render_template(
            "overview.html",
            view="overview",
            status=ctl.status(),
        )

    @app.get("/contracts")
    def page_contracts():
        return render_template(
            "contracts.html",
            view="contracts",
            status=ctl.status(),
        )

    @app.get("/tickers")
    def page_tickers():
        return render_template(
            "tickers.html",
            view="tickers",
            status=ctl.status(),
        )

    @app.get("/trading")
    def page_trading():
        return render_template(
            "trading.html",
            view="trading",
            status=ctl.status(),
        )

    @app.get("/control")
    def page_control():
        return render_template(
            "control.html",
            view="control",
            status=ctl.status(),
        )

    @app.get("/config")
    def page_config():
        env_values = read_dotenv(env_path)
        return render_template(
            "config.html",
            view="config",
            status=ctl.status(),
            editable_keys=EDITABLE_KEYS,
            env_values=env_values,
        )

    # ── snapshot endpoint (cached) ───────────────────────────────────────

    @app.get("/api/snapshot")
    def api_snapshot():
        force = request.args.get("force") in ("1", "true", "yes")
        validate = request.args.get("validate", "0") in ("1", "true", "yes")
        snap = _get_snapshot(builder, force=force, validate=validate)
        return jsonify(snap)

    # ── bot lifecycle ────────────────────────────────────────────────────

    @app.get("/api/bot/status")
    def api_bot_status():
        return jsonify(ctl.status())

    @app.post("/api/bot/start")
    def api_bot_start():
        started = ctl.start()
        return jsonify({"ok": True, "started": started, "status": ctl.status()})

    @app.post("/api/bot/stop")
    def api_bot_stop():
        stopped = ctl.stop()
        return jsonify({"ok": True, "stopped": stopped, "status": ctl.status()})

    @app.post("/api/bot/tick")
    def api_bot_tick():
        try:
            delta = ctl.tick_once()
        except Exception as exc:  # noqa: BLE001
            logging.getLogger(__name__).exception("Manual tick failed")
            return jsonify({"ok": False, "error": str(exc)}), 500
        # Force the next snapshot read to refresh.
        _invalidate_snapshot_cache()
        return jsonify({"ok": True, "delta": delta, "status": ctl.status()})

    @app.get("/api/bot/logs")
    def api_bot_logs():
        since = request.args.get("since")
        try:
            limit = int(request.args.get("limit", "200"))
        except ValueError:
            limit = 200
        return jsonify({"entries": ctl.logs(limit=limit, since=since)})

    @app.post("/api/bot/logs/clear")
    def api_bot_logs_clear():
        ctl.clear_logs()
        return jsonify({"ok": True})

    # ── config editor ────────────────────────────────────────────────────

    @app.get("/api/config")
    def api_config_get():
        return jsonify(
            {
                "editable_keys": EDITABLE_KEYS,
                "values": read_dotenv(env_path),
            }
        )

    @app.post("/api/config")
    def api_config_set():
        payload = request.get_json(silent=True) or {}
        updates = payload.get("updates") or {}
        if not isinstance(updates, dict):
            return jsonify({"ok": False, "error": "updates must be an object"}), 400
        rejected = [k for k in updates if k not in EDITABLE_KEYS]
        if rejected:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": f"Refusing to write keys: {sorted(rejected)}",
                    }
                ),
                400,
            )
        try:
            update_dotenv(env_path, updates)
            apply_env_updates(updates)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 500
        _invalidate_snapshot_cache()
        return jsonify({"ok": True, "values": read_dotenv(env_path)})

    # ── manual position close ────────────────────────────────────────────

    @app.post("/api/positions/<symbol>/sell")
    def api_position_sell(symbol: str):
        symbol = (symbol or "").strip().upper()
        if not symbol:
            abort(400)
        try:
            from trader import AlpacaTrader  # type: ignore

            trader = AlpacaTrader()
            ok = trader.sell_stock(symbol)
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 500
        _invalidate_snapshot_cache()
        return jsonify({"ok": bool(ok), "symbol": symbol})

    # ── healthcheck ──────────────────────────────────────────────────────

    @app.get("/api/health")
    def api_health():
        return jsonify({"ok": True})

    @app.errorhandler(404)
    def handle_404(_e):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "not found"}), 404
        return redirect(url_for("page_overview"))

    return app


# ---------------------------------------------------------------------------
# Snapshot cache + builder
# ---------------------------------------------------------------------------


def _default_snapshot_builder(*, validate: bool = False) -> Dict[str, Any]:
    from dashboard import build_snapshot  # type: ignore

    return build_snapshot(validate=validate, fetch_orders=True, fetch_alpaca=True)


def _get_snapshot(builder, *, force: bool, validate: bool) -> Dict[str, Any]:
    import time

    with _SNAPSHOT_LOCK:
        ttl = float(_SNAPSHOT_CACHE["ttl"])
        if (
            not force
            and _SNAPSHOT_CACHE["value"] is not None
            and (time.time() - _SNAPSHOT_CACHE["ts"]) < ttl
        ):
            return _SNAPSHOT_CACHE["value"]
    try:
        snap = builder(validate=validate)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).exception("Snapshot build failed")
        snap = {
            "schema_version": "2.0",
            "errors": {"snapshot": str(exc)},
            "config": {},
            "config_validation": {"issues": [str(exc)], "warnings": []},
            "health": {},
            "contracts": [],
            "summary": {"matched": 0, "validated": 0, "material": 0,
                        "stats": {}, "deltas": {}, "material_total": 0},
            "analytics": {"trends": {"daily": [], "weekly": []},
                          "concentration": {}, "repeat_recipients": [],
                          "anomalies": []},
            "analyses": [],
            "alpaca": {"configured": False, "account": None, "positions": [],
                       "orders": [], "lifecycle": {}, "exposure_concentration": None,
                       "drawdown_leaders": [], "daily_buys_used": None,
                       "held_symbols": [], "error": str(exc)},
            "two_phase": {"phase1_candidates": 0, "phase2_candidates": 0,
                          "phase2_threshold": 0, "phase2_tickers": []},
        }
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE["ts"] = time.time()
        _SNAPSHOT_CACHE["value"] = snap
    return snap


def _invalidate_snapshot_cache() -> None:
    with _SNAPSHOT_LOCK:
        _SNAPSHOT_CACHE["ts"] = 0.0
        _SNAPSHOT_CACHE["value"] = None


# ---------------------------------------------------------------------------
# Module entry-point
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the dashboard server (used by `python -m web` and the Dockerfile)."""
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    debug = os.getenv("DASHBOARD_DEBUG", "false").lower() == "true"

    autostart = os.getenv("BOT_AUTOSTART", "false").lower() == "true"
    if autostart:
        get_controller().start()

    app = create_app()
    app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)


if __name__ == "__main__":  # pragma: no cover
    main()
