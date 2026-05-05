"""Tests for `web.bot_controller.BotController`.

The controller wraps a stoppable thread around a `run_once` callable. The
tests inject a deterministic callable so they don't touch the network and
finish in well under a second.
"""

from __future__ import annotations

import threading
import time

from web.bot_controller import BotController


def test_tick_once_invokes_callable_and_updates_counters():
    calls = {"n": 0}

    def fake_run_once(_trader):
        calls["n"] += 1
        return {"awards_processed": 3, "buys": 1, "sells": 0, "exit_scans": 1}

    ctl = BotController(
        run_once=fake_run_once,
        trader_factory=lambda: "fake-trader",
        poll_interval_seconds=1,
    )
    delta = ctl.tick_once()
    assert delta == {"awards_processed": 3, "buys": 1, "sells": 0, "exit_scans": 1}
    s = ctl.status()
    assert s["ticks"] == 1
    assert s["awards_processed"] == 3
    assert s["buys"] == 1
    assert s["exit_scans"] == 1
    assert s["last_tick_at"] is not None
    assert calls["n"] == 1


def test_start_runs_loop_and_stop_signals_clean_shutdown():
    seen = []
    barrier = threading.Event()

    def fake_run_once(_trader):
        seen.append(time.time())
        if len(seen) >= 2:
            barrier.set()
        return {"awards_processed": 1}

    ctl = BotController(
        run_once=fake_run_once,
        trader_factory=lambda: object(),
        poll_interval_seconds=0,  # don't sleep between ticks
    )
    assert ctl.start() is True
    # Second start while alive returns False (no double-spawn).
    assert ctl.start() is False
    barrier.wait(timeout=2.0)
    assert ctl.is_running()
    assert ctl.stop(timeout=2.0)
    assert not ctl.is_running()
    assert ctl.status()["ticks"] >= 2
    assert ctl.status()["awards_processed"] >= 2


def test_trader_factory_failure_does_not_crash_the_loop():
    def fake_run_once(trader):
        # Trader is None when the factory raised.
        assert trader is None
        return {"awards_processed": 0}

    def boom():
        raise RuntimeError("alpaca down")

    ctl = BotController(
        run_once=fake_run_once,
        trader_factory=boom,
        poll_interval_seconds=0,
    )
    delta = ctl.tick_once()
    assert delta == {"awards_processed": 0}
    assert "alpaca down" in (ctl.status()["last_error"] or "")


def test_logs_capture_logger_records():
    import logging

    def fake_run_once(_t):
        logging.getLogger("test.logger").info("hello from tick")
        return {"awards_processed": 0}

    ctl = BotController(
        run_once=fake_run_once,
        trader_factory=lambda: object(),
        poll_interval_seconds=0,
    )
    ctl.tick_once()
    entries = ctl.logs(limit=50)
    assert any("hello from tick" in e["message"] for e in entries)


def test_run_once_exception_records_error_but_keeps_running():
    n = {"i": 0}

    def fake_run_once(_t):
        n["i"] += 1
        if n["i"] == 1:
            raise ValueError("first call boom")
        return {"awards_processed": 0}

    ctl = BotController(
        run_once=fake_run_once,
        trader_factory=lambda: object(),
        poll_interval_seconds=0,
    )
    ctl.start()
    deadline = time.time() + 2.0
    while time.time() < deadline and ctl.status()["ticks"] < 2:
        time.sleep(0.05)
    ctl.stop(timeout=2.0)
    assert ctl.status()["ticks"] >= 2
    assert "first call boom" in (ctl.status()["last_error"] or "")
