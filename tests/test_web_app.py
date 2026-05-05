"""Tests for the Flask web app.

Hermetic: we inject a fake snapshot builder and a controller with a
deterministic `run_once` so no network call (USASpending, SEC, yfinance,
Alpaca) is ever made.
"""

from __future__ import annotations

import json
import os

import pytest

from web.app import create_app
from web.bot_controller import BotController


def _fake_snapshot(*, validate=False):
    return {
        "schema_version": "2.0",
        "generated_at": "2026-05-05T12:00:00",
        "config": {"alpaca_paper": True, "min_contract_amount": 10_000_000},
        "config_validation": {"issues": [], "warnings": ["paper-mode reminder"]},
        "health": {
            "config": {"status": "ok", "detail": ""},
            "bot_state": {"status": "fresh", "detail": ""},
            "usaspending": {"status": "fresh", "detail": "1 award"},
            "ticker_source": {"status": "ok", "detail": ""},
            "alpaca": {"status": "warn", "detail": "no key"},
        },
        "contracts": [{"Award ID": "X1", "Recipient Name": "ACME",
                       "Award Amount": 50_000_000, "Awarding Agency": "DoD",
                       "Description": "Test", "Action Date": "2026-05-04"}],
        "summary": {"matched": 1, "validated": 0, "material": 0,
                    "material_total": 0,
                    "stats": {"count": 1, "total": 50_000_000, "avg": 50_000_000},
                    "deltas": {}, "ambiguous_matches": 0,
                    "low_confidence_matches": 0},
        "analytics": {"trends": {"daily": [], "weekly": []},
                      "concentration": {}, "repeat_recipients": [],
                      "anomalies": []},
        "analyses": [{
            "recipient": "ACME", "amount": 50_000_000, "ticker": "ACM",
            "match": {"tier": "high", "score": 95.0, "ambiguous": False},
            "info": {"market_cap": 1_000_000_000, "price": 10.0},
            "material": True,
            "eligibility": {"status": "eligible", "reasons": []},
        }],
        "alpaca": {"configured": False, "account": None, "positions": [],
                   "orders": [], "lifecycle": {"submitted": 0, "filled": 0,
                                                "rejected": 0, "canceled": 0,
                                                "aging": 0},
                   "exposure_concentration": None, "drawdown_leaders": [],
                   "daily_buys_used": None, "held_symbols": [], "error": None},
        "two_phase": {"phase1_candidates": 1, "phase2_candidates": 0,
                      "phase2_threshold": 0.0075, "phase2_tickers": []},
        "errors": {},
        "toggles": {},
    }


@pytest.fixture
def client(tmp_path):
    ctl = BotController(
        run_once=lambda _t: {"awards_processed": 1, "buys": 0, "exit_scans": 0},
        trader_factory=lambda: object(),
        poll_interval_seconds=0,
    )
    app = create_app(
        controller=ctl,
        snapshot_builder=_fake_snapshot,
        dotenv_path=str(tmp_path / ".env"),
        auth_token="",          # gate disabled by default for existing tests
        trust_proxy=False,      # don't apply ProxyFix in unit tests
    )
    app.testing = True
    with app.test_client() as c:
        yield c, ctl, str(tmp_path / ".env")


def test_health_endpoint(client):
    c, _ctl, _ = client
    r = c.get("/api/health")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}


def test_pages_render(client):
    c, _ctl, _ = client
    for path in ("/", "/contracts", "/tickers", "/trading", "/control", "/config"):
        r = c.get(path)
        assert r.status_code == 200, f"{path} -> {r.status_code}"
        assert b"<html" in r.data.lower()


def test_snapshot_endpoint(client):
    c, _ctl, _ = client
    r = c.get("/api/snapshot?force=1")
    assert r.status_code == 200
    payload = r.get_json()
    assert payload["schema_version"] == "2.0"
    assert payload["summary"]["matched"] == 1


def test_bot_status_start_stop(client):
    c, _ctl, _ = client
    r = c.get("/api/bot/status")
    assert r.status_code == 200
    assert r.get_json()["running"] in (False, True)

    r = c.post("/api/bot/start")
    assert r.status_code == 200
    assert r.get_json()["status"]["running"] is True

    r = c.post("/api/bot/stop")
    assert r.status_code == 200
    assert r.get_json()["status"]["running"] is False


def test_bot_tick_runs_callable(client):
    c, ctl, _ = client
    r = c.post("/api/bot/tick")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["delta"]["awards_processed"] == 1
    assert ctl.status()["ticks"] == 1


def test_logs_endpoint(client):
    c, ctl, _ = client
    ctl.tick_once()
    r = c.get("/api/bot/logs?limit=50")
    assert r.status_code == 200
    assert "entries" in r.get_json()


def test_logs_clear(client):
    c, ctl, _ = client
    ctl.tick_once()
    r = c.post("/api/bot/logs/clear")
    assert r.status_code == 200
    assert ctl.logs() == []


def test_config_get_returns_editable_keys(client):
    c, _ctl, _ = client
    r = c.get("/api/config")
    assert r.status_code == 200
    body = r.get_json()
    assert "ALPACA_PAPER" in body["editable_keys"]
    assert "ALPACA_PAPER" in body["values"]


def test_config_set_writes_dotenv_and_env(client):
    c, _ctl, env_path = client
    r = c.post(
        "/api/config",
        data=json.dumps({"updates": {"POLL_INTERVAL_MINUTES": "10"}}),
        content_type="application/json",
    )
    assert r.status_code == 200, r.get_data(as_text=True)
    assert os.environ["POLL_INTERVAL_MINUTES"] == "10"
    with open(env_path) as f:
        assert "POLL_INTERVAL_MINUTES=10" in f.read()


def test_config_set_rejects_unknown_keys(client):
    c, _ctl, _ = client
    r = c.post(
        "/api/config",
        data=json.dumps({"updates": {"NOT_A_KEY": "1"}}),
        content_type="application/json",
    )
    assert r.status_code == 400


def test_config_set_blank_value_removes_key(client, tmp_path):
    c, _ctl, env_path = client
    # Seed file
    with open(env_path, "w") as f:
        f.write("MAX_DAILY_TRADES=5\n")
    r = c.post(
        "/api/config",
        data=json.dumps({"updates": {"MAX_DAILY_TRADES": ""}}),
        content_type="application/json",
    )
    assert r.status_code == 200
    with open(env_path) as f:
        assert "MAX_DAILY_TRADES" not in f.read()


def test_position_sell_propagates_trader_failure(client, monkeypatch):
    c, _ctl, _ = client

    class Boom:
        def sell_stock(self, _sym):  # pragma: no cover — guard for shape
            raise RuntimeError("api down")

    # Patch trader import path used inside the route.
    import sys
    fake_module = type("M", (), {"AlpacaTrader": Boom})()
    monkeypatch.setitem(sys.modules, "trader", fake_module)
    r = c.post("/api/positions/AAPL/sell")
    assert r.status_code == 500
    assert r.get_json()["ok"] is False


def test_position_sell_success(client, monkeypatch):
    c, _ctl, _ = client

    calls = []
    class Trader:
        def sell_stock(self, sym):
            calls.append(sym)
            return True
    import sys
    monkeypatch.setitem(sys.modules, "trader", type("M", (), {"AlpacaTrader": Trader})())
    r = c.post("/api/positions/aapl/sell")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True, "symbol": "AAPL"}
    assert calls == ["AAPL"]


def test_404_api_returns_json(client):
    c, _ctl, _ = client
    r = c.get("/api/does-not-exist")
    assert r.status_code == 404
    assert r.get_json()["ok"] is False


def test_404_html_redirects_home(client):
    c, _ctl, _ = client
    r = c.get("/no-such-page")
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/")


# ── DASHBOARD_TOKEN auth gate ─────────────────────────────────────────────


@pytest.fixture
def gated_client(tmp_path):
    """Client with DASHBOARD_TOKEN auth gate enabled."""
    ctl = BotController(
        run_once=lambda _t: {"awards_processed": 1},
        trader_factory=lambda: object(),
        poll_interval_seconds=0,
    )
    app = create_app(
        controller=ctl,
        snapshot_builder=_fake_snapshot,
        dotenv_path=str(tmp_path / ".env"),
        auth_token="s3cret-token-xyz",
        trust_proxy=False,
    )
    app.testing = True
    with app.test_client() as c:
        yield c


def test_gate_health_endpoint_is_public(gated_client):
    r = gated_client.get("/api/health")
    assert r.status_code == 200


def test_gate_login_page_is_public(gated_client):
    r = gated_client.get("/login")
    assert r.status_code == 200
    assert b"Dashboard sign-in" in r.data


def test_gate_static_assets_are_public(gated_client):
    # Even though there's no real file, the gate must not 401 the static prefix.
    r = gated_client.get("/static/dashboard.css")
    assert r.status_code in (200, 404)
    # Specifically: we did NOT get a 401 from the gate.
    assert r.status_code != 401


def test_gate_api_returns_401_without_token(gated_client):
    r = gated_client.get("/api/snapshot")
    assert r.status_code == 401
    assert r.get_json()["ok"] is False


def test_gate_api_accepts_header_token(gated_client):
    r = gated_client.get(
        "/api/snapshot",
        headers={"X-Dashboard-Token": "s3cret-token-xyz"},
    )
    assert r.status_code == 200


def test_gate_api_rejects_wrong_header_token(gated_client):
    r = gated_client.get(
        "/api/bot/status",
        headers={"X-Dashboard-Token": "wrong"},
    )
    assert r.status_code == 401


def test_gate_html_page_redirects_to_login(gated_client):
    r = gated_client.get("/contracts")
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert "/login" in loc
    # next= may be URL-encoded ("%2F") or not depending on werkzeug version.
    assert "next=/contracts" in loc or "next=%2Fcontracts" in loc


def test_gate_login_post_sets_cookie_and_redirects(gated_client):
    r = gated_client.post(
        "/login",
        data={"token": "s3cret-token-xyz", "next": "/trading"},
    )
    assert r.status_code == 302
    assert r.headers["Location"].endswith("/trading")
    set_cookie = r.headers.get("Set-Cookie", "")
    assert "dashboard_auth=s3cret-token-xyz" in set_cookie
    assert "HttpOnly" in set_cookie


def test_gate_login_post_rejects_bad_token(gated_client):
    r = gated_client.post("/login", data={"token": "nope"})
    assert r.status_code == 401


def test_gate_login_post_normalizes_open_redirect(gated_client):
    # next=evil.com (no leading /) must be ignored to prevent open-redirect.
    r = gated_client.post(
        "/login",
        data={"token": "s3cret-token-xyz", "next": "https://evil.example/"},
    )
    assert r.status_code == 302
    # Should redirect to "/" instead of the external URL.
    assert r.headers["Location"].endswith("/")


def test_gate_cookie_grants_access(gated_client):
    gated_client.set_cookie("dashboard_auth", "s3cret-token-xyz")
    r = gated_client.get("/contracts")
    assert r.status_code == 200


def test_logout_clears_cookie(gated_client):
    gated_client.set_cookie("dashboard_auth", "s3cret-token-xyz")
    r = gated_client.post("/logout")
    assert r.status_code == 302
    set_cookie = r.headers.get("Set-Cookie", "")
    # An empty/expired dashboard_auth cookie is set.
    assert "dashboard_auth=" in set_cookie


# ── ProxyFix wiring ───────────────────────────────────────────────────────


def test_proxy_fix_honors_x_forwarded_proto(tmp_path):
    """When trust_proxy=True, X-Forwarded-Proto should make request.is_secure True."""
    captured = {}

    def fake_snap(*, validate=False):
        captured["scheme"] = None  # filled by route below
        return _fake_snapshot()

    ctl = BotController(
        run_once=lambda _t: {"awards_processed": 0},
        trader_factory=lambda: object(),
        poll_interval_seconds=0,
    )
    app = create_app(
        controller=ctl,
        snapshot_builder=fake_snap,
        dotenv_path=str(tmp_path / ".env"),
        auth_token="",
        trust_proxy=True,
    )
    app.testing = True

    @app.get("/_test_scheme")
    def _scheme():
        from flask import request as _r
        return {"scheme": _r.scheme, "is_secure": _r.is_secure}

    with app.test_client() as c:
        r = c.get("/_test_scheme", headers={"X-Forwarded-Proto": "https"})
        assert r.status_code == 200
        assert r.get_json() == {"scheme": "https", "is_secure": True}


def test_proxy_fix_disabled_ignores_x_forwarded_proto(tmp_path):
    ctl = BotController(
        run_once=lambda _t: {"awards_processed": 0},
        trader_factory=lambda: object(),
        poll_interval_seconds=0,
    )
    app = create_app(
        controller=ctl,
        snapshot_builder=_fake_snapshot,
        dotenv_path=str(tmp_path / ".env"),
        auth_token="",
        trust_proxy=False,
    )
    app.testing = True

    @app.get("/_test_scheme")
    def _scheme():
        from flask import request as _r
        return {"scheme": _r.scheme, "is_secure": _r.is_secure}

    with app.test_client() as c:
        r = c.get("/_test_scheme", headers={"X-Forwarded-Proto": "https"})
        assert r.get_json()["is_secure"] is False  # header was ignored
