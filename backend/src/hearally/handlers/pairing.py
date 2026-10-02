"""Pairing a computer with the signed-in account.

    POST   /v1/me/acknowledgement          signed in: accept the acknowledgement
    POST   /v1/me/pairings                 signed in: issue a one-use code
    POST   /v1/pairings/redeem             the collector: trade the code for a token
    DELETE /v1/me/computers/{device_id}    signed in: revoke a computer

The signed-in routes sit behind API Gateway's Cognito authorizer, so the claims
are already verified; the account is the token's ``sub`` and nothing else.
Redeeming needs no sign-in: holding a live code is the authorisation, which is
why the code is long, used once, short-lived and stored only as a digest. The
API's WAF rate limit covers it too.
"""

from __future__ import annotations

import json
from typing import Any

from hearally.edge import EdgeVerificationError, EdgeVerifier
from hearally.handlers.requests import body_bytes
from hearally.handlers.responses import json_response
from hearally.models import ValidationError
from hearally.pairing import (
    AcknowledgementRequiredError,
    PairingService,
    PairingUnavailableError,
)

MAX_BODY_BYTES = 1_024


def _sub(request: dict[str, Any]) -> str:
    context = request.get("requestContext") or {}
    claims = ((context.get("authorizer") or {}).get("jwt") or {}).get("claims") or {}
    return str(claims.get("sub") or "") if isinstance(claims, dict) else ""


def _json(
    request: dict[str, Any], *, optional: bool = False, limit: int = MAX_BODY_BYTES
) -> dict[str, Any]:
    if optional and not request.get("body"):
        return {}
    raw = body_bytes(request)
    if len(raw) > limit:
        raise ValidationError("Request body is too large")
    try:
        data = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Request body is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    return data


def _private(response: dict[str, Any]) -> dict[str, Any]:
    response["headers"]["cache-control"] = "private, no-store"
    return response


def handle_create(
    request: dict[str, Any], edge: EdgeVerifier, pairing: PairingService, version: str
) -> dict[str, Any]:
    try:
        edge.verify(request.get("headers") or {})
    except EdgeVerificationError:
        return json_response(403, {"error": "forbidden"})
    sub = _sub(request)
    if not sub:
        # Unreachable while the authorizer is attached; fails closed if it is not.
        return json_response(401, {"error": "unauthorized"})
    try:
        body = _json(request, optional=True)
        if set(body) - {"acknowledgement_version"}:
            raise ValidationError("Unknown fields")
        issued = pairing.create_code(sub, body.get("acknowledgement_version"))
    except AcknowledgementRequiredError:
        return _private(
            json_response(409, {"error": "acknowledgement_required", "version": version})
        )
    except ValidationError as exc:
        return json_response(400, {"error": "invalid_request", "detail": str(exc)})
    except Exception:
        return json_response(503, {"error": "temporarily_unavailable"})
    return _private(json_response(201, issued))


def handle_acknowledge(
    request: dict[str, Any], edge: EdgeVerifier, pairing: PairingService, version: str
) -> dict[str, Any]:
    try:
        edge.verify(request.get("headers") or {})
    except EdgeVerificationError:
        return json_response(403, {"error": "forbidden"})
    sub = _sub(request)
    if not sub:
        return json_response(401, {"error": "unauthorized"})
    try:
        body = _json(request)
        if set(body) != {"acknowledgement_version"}:
            raise ValidationError("Expected exactly acknowledgement_version")
        new = pairing.acknowledge(sub, body["acknowledgement_version"])
    except AcknowledgementRequiredError:
        # The wording changed since the page loaded: it must be read again.
        return _private(
            json_response(409, {"error": "acknowledgement_changed", "version": version})
        )
    except ValidationError as exc:
        return json_response(400, {"error": "invalid_request", "detail": str(exc)})
    except Exception:
        return json_response(503, {"error": "temporarily_unavailable"})
    return _private(json_response(200, {"acknowledged": True, "new": new, "version": version}))


def handle_redeem(
    request: dict[str, Any], edge: EdgeVerifier, pairing: PairingService
) -> dict[str, Any]:
    try:
        edge.verify(request.get("headers") or {})
        body = _json(request)
        if set(body) != {"code", "computer_name"}:
            raise ValidationError("Expected exactly code and computer_name")
        paired = pairing.redeem(body["code"], body["computer_name"])
    except EdgeVerificationError:
        return json_response(403, {"error": "forbidden"})
    except PairingUnavailableError:
        # Wrong, expired and already used all look the same from outside.
        return json_response(404, {"error": "pairing_unavailable"})
    except ValidationError as exc:
        return json_response(400, {"error": "invalid_request", "detail": str(exc)})
    except Exception:
        return json_response(503, {"error": "temporarily_unavailable"})
    return _private(json_response(201, paired))


def handle_revoke(
    request: dict[str, Any], edge: EdgeVerifier, pairing: PairingService
) -> dict[str, Any]:
    try:
        edge.verify(request.get("headers") or {})
    except EdgeVerificationError:
        return json_response(403, {"error": "forbidden"})
    sub = _sub(request)
    if not sub:
        return json_response(401, {"error": "unauthorized"})
    device_id = (request.get("pathParameters") or {}).get("device_id")
    try:
        # Scoped by the signed-in sub: another account's computer is simply
        # not found, never revoked.
        revoked = pairing.revoke(sub, device_id)
    except Exception:
        return json_response(503, {"error": "temporarily_unavailable"})
    if not revoked:
        return json_response(404, {"error": "not_found"})
    return _private(json_response(200, {"revoked": True}))


_resources: tuple[EdgeVerifier, PairingService] | None = None


def _built() -> tuple[EdgeVerifier, PairingService]:
    global _resources
    if _resources is None:
        from hearally.factory import build_edge_verifier, build_pairing_service

        _resources = (build_edge_verifier(), build_pairing_service())
    return _resources


def create_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    from hearally.config import ACKNOWLEDGEMENT_VERSION

    edge, pairing = _built()
    return handle_create(event, edge, pairing, ACKNOWLEDGEMENT_VERSION)


def acknowledge_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    from hearally.config import ACKNOWLEDGEMENT_VERSION

    edge, pairing = _built()
    return handle_acknowledge(event, edge, pairing, ACKNOWLEDGEMENT_VERSION)


def redeem_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    edge, pairing = _built()
    return handle_redeem(event, edge, pairing)


def revoke_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    edge, pairing = _built()
    return handle_revoke(event, edge, pairing)
