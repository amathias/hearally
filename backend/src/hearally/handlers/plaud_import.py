"""Every 15 minutes: import new recordings from connected Plaud accounts.

Scheduled by EventBridge, not on the API. See hearally/plaud_import.py.
"""

from __future__ import annotations

from typing import Any


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, float]:
    del event, context
    from hearally.factory import build_plaud_importer

    return build_plaud_importer().run()
