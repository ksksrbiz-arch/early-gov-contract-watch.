"""
dashboard — gov_contract_dashboard v2 package.

Separates data collection (snapshot.py, health.py, analytics.py, confidence.py,
eligibility.py) from rendering (render.py) and CLI (cli.py) so the same
snapshot can power terminal output, JSON exports, and future UI surfaces.

The versioned snapshot schema is the single source of truth for every view.
"""

from .snapshot import (
    SNAPSHOT_SCHEMA_VERSION,
    build_snapshot,
    snapshot_to_export,
)

__all__ = [
    "SNAPSHOT_SCHEMA_VERSION",
    "build_snapshot",
    "snapshot_to_export",
]
