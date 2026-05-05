"""
web — Flask-based control dashboard for the Early Gov Contract Watch bot.

This package wraps the existing snapshot data layer (`dashboard/`) and the
bot loop (`main.py`) behind an HTTP UI so the operator can run, monitor, and
configure the platform from a browser instead of the command line.
"""

from .app import create_app
from .bot_controller import BotController, get_controller

__all__ = ["create_app", "BotController", "get_controller"]
