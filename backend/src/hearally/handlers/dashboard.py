"""GET /v1/me/{section}: the signed-in account holder's dashboard.

API Gateway's JWT authorizer has already verified the Cognito token before
this runs, so the claims in the request context are trustworthy. The account
is identified by the token's ``sub`` and nothing else.
"""

from __future__ import annotations

from typing import Any

from hearally.config import ACKNOWLEDGEMENT_VERSION
from hearally.dashboard import SECTIONS, AccountReader, section
from hearally.edge import EdgeVerificationError, EdgeVerifier
from hearally.handlers.responses import json_response


def _claims(request: dict[str, Any]) -> dict[str, Any] | None:
    context = request.get("requestContext") or {}
    jwt = (context.get("authorizer") or {}).get("jwt") or {}
    claims = jwt.get("claims")
    return claims if isinstance(claims, dict) else None


def handle(request: dict[str, Any], edge: EdgeVerifier, reader: AccountReader) -> dict[str, Any]:
    try:
        edge.verify(request.get("headers") or {})
    except EdgeVerificationError:
        return json_response(403, {"error": "forbidden"})

    claims = _claims(request)
    sub = str((claims or {}).get("sub") or "")
    if not sub:
        # Unreachable while the authorizer is attached to the route; refusing
        # here means a misconfigured route fails closed rather than open.
        return json_response(401, {"error": "unauthorized"})

    name = str((request.get("pathParameters") or {}).get("section") or "")
    if name not in SECTIONS:
        return json_response(404, {"error": "not_found"})

    try:
        body = section(
            name, sub, claims or {}, reader, acknowledgement_version=ACKNOWLEDGEMENT_VERSION
        )
    except Exception:
        return json_response(503, {"error": "temporarily_unavailable"})

    response = json_response(200, body)
    # Personal data: never cached by the browser, CloudFront or anything between.
    response["headers"]["cache-control"] = "private, no-store"
    return response


_resources: tuple[EdgeVerifier, AccountReader] | None = None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    global _resources
    if _resources is None:
        import boto3

        from hearally.config import Settings
        from hearally.dashboard import DynamoAccountReader
        from hearally.factory import build_edge_verifier

        table = boto3.resource("dynamodb").Table(Settings.from_environment().table_name)
        _resources = (build_edge_verifier(), DynamoAccountReader(table))
    edge, reader = _resources
    return handle(event, edge, reader)
