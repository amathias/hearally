"""Plaud sign-in (experimental), for the signed-in account holder only.

    POST /v1/me/plaud/start        the Plaud consent page to go to
    POST /v1/me/plaud/finish       {code, state} from Plaud's redirect
    POST /v1/me/plaud/status       connected or not
    POST /v1/me/plaud/check        what Plaud shares: recordings, audio link
    POST /v1/me/plaud/disconnect   forget the Plaud tokens

One route, ``POST /v1/me/plaud/{action}``, behind the Cognito authorizer. The
account is the token's ``sub`` and nothing else. See hearally/plaud_link.py.
Responses never carry a Plaud token or link, and nothing here logs a body.
"""

from __future__ import annotations

from typing import Any

from hearally.edge import EdgeVerificationError, EdgeVerifier
from hearally.handlers.pairing import _json, _private, _sub
from hearally.handlers.responses import json_response
from hearally.models import ValidationError
from hearally.plaud_link import (
    NotConnectedError,
    PlaudLinkError,
    PlaudLinkService,
    StateError,
)

ACTIONS = {"start", "finish", "status", "check", "disconnect"}
# Plaud's authorization codes are self-contained signed tokens, longer than
# most: the first real one did not fit in 512 characters (2026-09-29).
MAX_CODE = 4_096
MAX_STATE = 256
MAX_BODY_BYTES = 8_192


def handle(request: dict[str, Any], edge: EdgeVerifier, plaud: PlaudLinkService) -> dict[str, Any]:
    try:
        edge.verify(request.get("headers") or {})
    except EdgeVerificationError:
        return json_response(403, {"error": "forbidden"})
    sub = _sub(request)
    if not sub:
        return json_response(401, {"error": "unauthorized"})
    action = (request.get("pathParameters") or {}).get("action")
    if action not in ACTIONS:
        return json_response(404, {"error": "not_found"})
    try:
        if action == "start":
            return _private(json_response(200, {"authorize_url": plaud.start(sub)}))
        if action == "finish":
            body = _json(request, limit=MAX_BODY_BYTES)
            code, state = body.get("code"), body.get("state")
            if set(body) != {"code", "state"}:
                raise ValidationError("Expected exactly code and state")
            if not (isinstance(code, str) and 0 < len(code) <= MAX_CODE):
                raise ValidationError(f"The code from Plaud must be 1 to {MAX_CODE} characters")
            if not (isinstance(state, str) and 0 < len(state) <= MAX_STATE):
                raise ValidationError(f"The state must be 1 to {MAX_STATE} characters")
            plaud.finish(sub, str(code), str(state))
            return _private(json_response(200, plaud.status(sub)))
        if action == "status":
            return _private(json_response(200, plaud.status(sub)))
        if action == "check":
            return _private(json_response(200, plaud.check(sub)))
        return _private(json_response(200, {"disconnected": plaud.disconnect(sub)}))
    except StateError:
        return json_response(409, {"error": "sign_in_expired"})
    except NotConnectedError:
        return _private(json_response(409, {"error": "not_connected"}))
    except PlaudLinkError as exc:
        return _private(json_response(502, {"error": "plaud_error", "detail": str(exc)}))
    except ValidationError as exc:
        return json_response(400, {"error": "invalid_request", "detail": str(exc)})
    except Exception:
        return json_response(503, {"error": "temporarily_unavailable"})


_resources: tuple[EdgeVerifier, PlaudLinkService] | None = None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    global _resources
    if _resources is None:
        from hearally.factory import build_edge_verifier, build_plaud_link_service

        _resources = (build_edge_verifier(), build_plaud_link_service())
    return handle(event, *_resources)
