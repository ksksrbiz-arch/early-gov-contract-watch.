#!/usr/bin/env python3
"""
web_app.py — entry point for the web dashboard.

Run locally:
    python web_app.py

Environment variables (all optional):
    HOST               Bind address         (default: 0.0.0.0)
    PORT               HTTP port            (default: 8000)
    BOT_AUTOSTART      Start bot on launch  (default: false)
    DASHBOARD_DEBUG    Flask debug mode     (default: false)
"""

from web.app import main

if __name__ == "__main__":
    main()
