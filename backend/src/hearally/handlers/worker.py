"""The transcription worker's two scheduled steps (see hearally/worker.py).

    dispatch_handler   every 30 minutes: queue -> one batch -> GPU job
    collect_handler    every 10 minutes: finished job -> judged items

Neither is on the API. Each returns counts, which is all it logs.
"""

from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)


def dispatch_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    del event, context
    from hearally.factory import build_dispatcher

    result = build_dispatcher().run()
    LOGGER.info("Dispatch: %s", result)
    return result


def collect_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    del event
    from hearally.factory import build_collector

    def time_left() -> float:
        return context.get_remaining_time_in_millis() / 1000 if context else 1e9

    result = build_collector().run(time_left)
    LOGGER.info("Collect: %s", result)
    return result
