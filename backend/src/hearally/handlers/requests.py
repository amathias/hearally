from __future__ import annotations

import base64
from typing import Any

from hearally.models import ValidationError


def body_bytes(request: dict[str, Any]) -> bytes:
    """The raw request body, decoded if API Gateway base64-encoded it."""
    body = request.get("body")
    if not isinstance(body, str):
        raise ValidationError("Request body is required")
    if request.get("isBase64Encoded"):
        try:
            return base64.b64decode(body, validate=True)
        except ValueError as exc:
            raise ValidationError("Request body is not valid base64") from exc
    return body.encode("utf-8")
