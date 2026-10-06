"""Allies: managed by the account holder, and the ally's own link.

    POST /v1/me/allies/{action}   signed in: add, update, remove, resend, name, forget
    POST /v1/ally/{action}        an ally link: view, confirm, decline, stop, dismiss
    deliver_handler               every 10 minutes: send what is due

The ally routes need no sign-in: holding a live ally link is the
authorisation, so the token travels in the request body (from the page's URL
fragment), never in a URL, and only its digest is stored. Wrong, expired and
revoked links all get the same answer. See hearally/allies.py.
"""

from __future__ import annotations

import logging
from typing import Any

from hearally.allies import (
    AllyLinkService,
    AllyNotFoundError,
    AllyService,
    LinkInvalidError,
)
from hearally.edge import EdgeVerificationError, EdgeVerifier
from hearally.handlers.pairing import _json, _private, _sub
from hearally.handlers.responses import json_response
from hearally.models import ValidationError

LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

MANAGE = {"add", "update", "remove", "resend", "name", "forget"}
LINK = {"view", "confirm", "decline", "stop", "dismiss"}
MAX_BODY = 4_096


def _public(ally: dict[str, Any]) -> dict[str, Any]:
    # Never the link digest: it is the key to the ally's page.
    return {k: v for k, v in ally.items() if k != "link_digest"}


def handle_manage(
    request: dict[str, Any], edge: EdgeVerifier, allies: AllyService
) -> dict[str, Any]:
    try:
        edge.verify(request.get("headers") or {})
    except EdgeVerificationError:
        return json_response(403, {"error": "forbidden"})
    sub = _sub(request)
    if not sub:
        return json_response(401, {"error": "unauthorized"})
    action = (request.get("pathParameters") or {}).get("action")
    if action not in MANAGE:
        return json_response(404, {"error": "not_found"})
    try:
        body = _json(request, limit=MAX_BODY)
        if action == "name":
            name = allies.set_display_name(sub, body.get("display_name"))
            return _private(json_response(200, {"display_name": name}))
        if action == "add":
            return _private(json_response(201, {"ally": _public(allies.add(sub, body))}))
        if action == "forget":
            # Something learned from "this was nothing", not an ally.
            allies.forget(sub, body.get("pattern_id"))
            return _private(json_response(200, {"done": True}))
        ally_id = body.pop("ally_id", None)
        if not isinstance(ally_id, str) or not ally_id:
            raise ValidationError("ally_id is required")
        if action == "update":
            updated = allies.update(sub, ally_id, body)
            return _private(json_response(200, {"ally": _public(updated)}))
        if body:
            raise ValidationError("Unknown fields")
        if action == "remove":
            allies.remove(sub, ally_id)
        else:
            allies.resend(sub, ally_id)
        return _private(json_response(200, {"done": True}))
    except AllyNotFoundError:
        return json_response(404, {"error": "not_found"})
    except ValidationError as exc:
        return json_response(400, {"error": "invalid_request", "detail": str(exc)})
    except Exception as exc:
        LOGGER.warning("Ally change failed: %s", type(exc).__name__)
        return json_response(503, {"error": "temporarily_unavailable"})


def handle_link(
    request: dict[str, Any], edge: EdgeVerifier, links: AllyLinkService
) -> dict[str, Any]:
    try:
        edge.verify(request.get("headers") or {})
    except EdgeVerificationError:
        return json_response(403, {"error": "forbidden"})
    action = (request.get("pathParameters") or {}).get("action")
    if action not in LINK:
        return json_response(404, {"error": "not_found"})
    try:
        body = _json(request, limit=MAX_BODY)
        token = body.get("token")
        if action == "view":
            return _private(json_response(200, links.view(token)))
        if action == "dismiss":
            links.dismiss(token, body.get("item_id"), body.get("reason"))
        elif action == "confirm":
            links.confirm(token)
        elif action == "decline":
            links.decline(token)
        else:
            links.stop(token)
        return _private(json_response(200, {"done": True}))
    except LinkInvalidError:
        return json_response(404, {"error": "link_not_valid"})
    except ValidationError as exc:
        return json_response(400, {"error": "invalid_request", "detail": str(exc)})
    except Exception as exc:
        LOGGER.warning("Ally link action failed: %s", type(exc).__name__)
        return json_response(503, {"error": "temporarily_unavailable"})


_manage: tuple[EdgeVerifier, AllyService] | None = None
_link: tuple[EdgeVerifier, AllyLinkService] | None = None


def manage_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    global _manage
    if _manage is None:
        from hearally.factory import build_ally_service, build_edge_verifier

        _manage = (build_edge_verifier(), build_ally_service())
    return handle_manage(event, *_manage)


def link_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    global _link
    if _link is None:
        from hearally.factory import build_ally_link_service, build_edge_verifier

        _link = (build_edge_verifier(), build_ally_link_service())
    return handle_link(event, *_link)


def deliver_handler(event: dict[str, Any], context: Any) -> dict[str, int]:
    del event, context
    from hearally.factory import build_deliverer

    result = build_deliverer().run()
    LOGGER.info("Deliver: %s", result)
    return result
