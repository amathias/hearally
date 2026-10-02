"""Weekly: find new scam warnings and add them to the library (section 7b).

Runs on a schedule, not behind the API. Until a Tavily key has been stored
with scripts/set-secrets.ps1, it records that it skipped and does nothing.
Logs counts and card ids only, never page text.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import Any

from hearally.judgement import NemotronClient
from hearally.scam_discovery import TavilyClient, discover
from hearally.scam_library import DynamoScamCardStore, EmbeddingClient, load_library
from hearally.secret_cache import current_field

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)


def run(secrets: Any, table: Any, *, tavily_secret: str, nebius_secret: str) -> dict[str, Any]:
    tavily_key = current_field(secrets, tavily_secret, "api_key")
    try:
        tavily_key()
    except Exception as exc:
        LOGGER.warning("Scam refresh skipped: no Tavily key (%s)", type(exc).__name__)
        return {"skipped": "no_tavily_key"}
    nebius_key = current_field(secrets, nebius_secret, "api_key")

    store = DynamoScamCardStore(table)
    report = discover(
        tavily=TavilyClient(tavily_key),
        nemotron=NemotronClient(nebius_key),
        embedder=EmbeddingClient(nebius_key),
        library=load_library(extra=store.load()),
        store=store,
    )
    summary = asdict(report)
    LOGGER.info("Scam refresh: %s", summary)
    return summary


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del event, context
    import boto3

    from hearally.config import Settings

    return run(
        boto3.client("secretsmanager"),
        boto3.resource("dynamodb").Table(Settings.from_environment().table_name),
        tavily_secret=os.environ["TAVILY_SECRET_ID"],
        nebius_secret=os.environ["NEBIUS_SECRET_ID"],
    )
