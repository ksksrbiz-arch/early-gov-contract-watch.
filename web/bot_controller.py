"""
BotController — thread-safe wrapper around the trading loop.

The web dashboard needs to start, stop, and observe the bot without ever
shelling out. This module owns:

  * a single background thread running a stoppable version of the
    poll → process → exit-scan loop from `main.py`,
  * a bounded in-memory log buffer the UI can tail,
  * status fields the UI polls (`running`, `last_tick_at`, `last_error`,
    counters for awards seen / buys / sells / exits).

Thread model
------------
The controller is process-singleton; `get_controller()` returns the same
instance for every request. Mutations are guarded by `self._lock`. The
worker thread checks `self._stop_event` between iterations so a stop request
never has to wait a full poll interval.
"""

from __future__ import annotations

import logging
import threading
import time
import traceback
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional


_LOG_BUFFER_LINES = 500


class _BufferingHandler(logging.Handler):
    """Logging handler that mirrors records into a bounded deque."""

    def __init__(self, buffer: Deque[Dict[str, Any]]):
        super().__init__()
        self._buffer = buffer

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        self._buffer.append(
            {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
            }
        )


class BotController:
    """Owns the bot's background thread and exposes lifecycle controls."""

    def __init__(
        self,
        *,
        run_once: Optional[Callable[[Any], Dict[str, int]]] = None,
        trader_factory: Optional[Callable[[], Any]] = None,
        poll_interval_seconds: Optional[int] = None,
        log_buffer_lines: int = _LOG_BUFFER_LINES,
    ):
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self._run_once = run_once or _default_run_once
        self._trader_factory = trader_factory or _default_trader_factory
        self._poll_interval_seconds = poll_interval_seconds

        self._log_buffer: Deque[Dict[str, Any]] = deque(maxlen=log_buffer_lines)
        self._handler = _BufferingHandler(self._log_buffer)
        self._handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
        )
        self._handler.setLevel(logging.INFO)
        # Attach to the root logger so anything `main`, `trader`, etc. logs
        # is captured for the dashboard's live log tail. Lower the root level
        # to INFO if it's been left at the default (WARNING), but never raise it.
        root_logger = logging.getLogger()
        if root_logger.level > logging.INFO or root_logger.level == logging.NOTSET:
            root_logger.setLevel(logging.INFO)
        root_logger.addHandler(self._handler)

        # Status counters — all reads/writes go through `_lock`.
        self._status = {
            "started_at": None,
            "stopped_at": None,
            "last_tick_at": None,
            "last_error": None,
            "ticks": 0,
            "awards_processed": 0,
            "buys": 0,
            "sells": 0,
            "exit_scans": 0,
        }

    # ── public API ───────────────────────────────────────────────────────

    def start(self) -> bool:
        """Start the worker thread. Returns True if it was started fresh."""
        with self._lock:
            if self._thread and self._thread.is_alive():
                return False
            self._stop_event.clear()
            self._status.update(
                started_at=_now_iso(),
                stopped_at=None,
                last_error=None,
            )
            t = threading.Thread(
                target=self._run_loop, name="bot-controller", daemon=True
            )
            self._thread = t
        t.start()
        logging.getLogger("web.bot_controller").info("Bot started")
        return True

    def stop(self, timeout: float = 5.0) -> bool:
        """Signal the worker to stop and wait up to *timeout* seconds."""
        with self._lock:
            t = self._thread
            if not t or not t.is_alive():
                return False
            self._stop_event.set()
        t.join(timeout=timeout)
        with self._lock:
            self._status["stopped_at"] = _now_iso()
        logging.getLogger("web.bot_controller").info("Bot stopped")
        return True

    def is_running(self) -> bool:
        with self._lock:
            t = self._thread
        return bool(t and t.is_alive())

    def status(self) -> Dict[str, Any]:
        with self._lock:
            snapshot = dict(self._status)
        snapshot["running"] = self.is_running()
        return snapshot

    def logs(self, limit: int = 200, since: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return the last *limit* log entries, optionally newer than *since* ISO ts."""
        items = list(self._log_buffer)
        if since:
            items = [e for e in items if e["ts"] > since]
        if limit and len(items) > limit:
            items = items[-limit:]
        return items

    def clear_logs(self) -> None:
        self._log_buffer.clear()

    def tick_once(self) -> Dict[str, int]:
        """Run a single poll iteration synchronously (used by tests / 'Run now')."""
        trader = self._build_trader()
        return self._tick(trader)

    # ── internals ────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        log = logging.getLogger("web.bot_controller")
        trader = self._build_trader()
        while not self._stop_event.is_set():
            try:
                self._tick(trader)
            except Exception as exc:  # noqa: BLE001 — defensive, log + continue
                with self._lock:
                    self._status["last_error"] = f"{type(exc).__name__}: {exc}"
                log.error("Bot tick failed: %s\n%s", exc, traceback.format_exc())
            interval = self._resolve_poll_interval()
            # Sleep in short increments so stop is responsive.
            slept = 0.0
            step = 0.5
            while slept < interval and not self._stop_event.is_set():
                time.sleep(step)
                slept += step

    def _tick(self, trader: Any) -> Dict[str, int]:
        delta = self._run_once(trader) or {}
        with self._lock:
            self._status["ticks"] += 1
            self._status["last_tick_at"] = _now_iso()
            self._status["awards_processed"] += int(delta.get("awards_processed", 0))
            self._status["buys"] += int(delta.get("buys", 0))
            self._status["sells"] += int(delta.get("sells", 0))
            self._status["exit_scans"] += int(delta.get("exit_scans", 0))
        return delta

    def _build_trader(self) -> Any:
        try:
            return self._trader_factory()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._status["last_error"] = f"trader init failed: {exc}"
            logging.getLogger("web.bot_controller").warning(
                "Trader factory failed: %s — bot will run without an Alpaca client", exc
            )
            return None

    def _resolve_poll_interval(self) -> float:
        if self._poll_interval_seconds is not None:
            return float(self._poll_interval_seconds)
        try:
            from config import POLL_INTERVAL_MINUTES  # local import — picks up env edits
            return float(POLL_INTERVAL_MINUTES) * 60.0
        except Exception:
            return 1800.0


# ---------------------------------------------------------------------------
# Default factory + tick implementation (delegates to main.py's pure helpers)
# ---------------------------------------------------------------------------


def _default_trader_factory() -> Any:
    from trader import AlpacaTrader  # type: ignore
    return AlpacaTrader()


def _default_run_once(trader: Any) -> Dict[str, int]:
    """Run one poll iteration: fetch awards, process each, then exit-scan.

    Returns lifecycle deltas so the controller can update its counters.
    """
    from main import process_award, process_exits  # type: ignore
    from usaspending_fetcher import fetch_recent_large_contracts  # type: ignore

    awards = fetch_recent_large_contracts() or []
    buys = 0
    for award in awards:
        try:
            if process_award(award, trader):
                buys += 1
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("web.bot_controller").error(
                "process_award failed: %s", exc
            )

    exit_scans = 0
    if trader is not None:
        try:
            process_exits(trader)
            exit_scans = 1
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("web.bot_controller").error(
                "process_exits failed: %s", exc
            )

    return {
        "awards_processed": len(awards),
        "buys": buys,
        "sells": 0,
        "exit_scans": exit_scans,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_CONTROLLER_LOCK = threading.Lock()
_CONTROLLER: Optional[BotController] = None


def get_controller() -> BotController:
    global _CONTROLLER
    with _CONTROLLER_LOCK:
        if _CONTROLLER is None:
            _CONTROLLER = BotController()
        return _CONTROLLER


def reset_controller_for_tests(controller: Optional[BotController] = None) -> None:
    """Replace the singleton (test-only helper)."""
    global _CONTROLLER
    with _CONTROLLER_LOCK:
        _CONTROLLER = controller
