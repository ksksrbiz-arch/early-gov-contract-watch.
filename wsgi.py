"""
WSGI entry point for production servers (gunicorn / Render).

Usage:
    gunicorn --bind 0.0.0.0:$PORT wsgi:app

Optionally autostarts the bot loop when `BOT_AUTOSTART=true`.
"""

from __future__ import annotations

import os

from web.app import create_app
from web.bot_controller import get_controller


def _maybe_autostart() -> None:
    if os.getenv("BOT_AUTOSTART", "false").lower() == "true":
        get_controller().start()


_maybe_autostart()
app = create_app()
