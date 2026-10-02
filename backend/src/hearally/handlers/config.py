from __future__ import annotations

from typing import Any

from hearally.config import Settings
from hearally.edge import EdgeVerificationError, EdgeVerifier
from hearally.factory import build_edge_verifier, build_public_settings
from hearally.handlers.responses import json_response


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    return handle(
        build_public_settings(),
        headers=event.get("headers") or {},
        edge=build_edge_verifier(),
    )


def handle(
    settings: Settings,
    *,
    headers: dict[str, str] | None = None,
    edge: EdgeVerifier | None = None,
) -> dict[str, Any]:
    if edge is not None:
        try:
            edge.verify(headers or {})
        except EdgeVerificationError:
            # Deliberately vague, and deliberately not 401: there is nothing the
            # caller can supply to fix it, and naming the header would tell an
            # attacker exactly what to forge.
            return json_response(403, {"error": "forbidden"})

    response = json_response(200, settings.public_info())
    response["headers"]["cache-control"] = "public, max-age=300"
    return response
