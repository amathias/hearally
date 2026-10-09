"""POST /v1/me/recordings/listen: play the silenced copy of a recording.

The account holder hears what Hearally keeps: the recording with account and
card numbers silenced in the audio (ARCHITECTURE.md section 3). The answer is
a link to that one file that expires in ten minutes. The account is the
token's ``sub``, so the file is always the caller's own: the key is built from
it, never taken from the request. The link is never logged.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from hearally.dashboard import AccountReader
from hearally.edge import EdgeVerificationError, EdgeVerifier
from hearally.handlers.pairing import _json, _private, _sub
from hearally.handlers.responses import json_response
from hearally.transcription import redacted_key

# The silenced copy is deleted after 7 days (the media bucket's lifecycle).
KEPT_SECONDS = 7 * 86400
LINK_SECONDS = 600
_ID = re.compile(r"[0-9a-f]{16}")
_EVENT = re.compile(r"[0-9a-f]{16,128}")


def _epoch(value: Any) -> float | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def handle(
    request: dict[str, Any],
    edge: EdgeVerifier,
    reader: AccountReader,
    presign: Callable[[str], str],
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    try:
        edge.verify(request.get("headers") or {})
    except EdgeVerificationError:
        return json_response(403, {"error": "forbidden"})
    sub = _sub(request)
    if not sub:
        return json_response(401, {"error": "unauthorized"})
    try:
        recording_id = _json(request, limit=512).get("id")
    except Exception:
        return json_response(400, {"error": "invalid_request"})
    if not isinstance(recording_id, str) or not _ID.fullmatch(recording_id):
        return json_response(400, {"error": "invalid_request"})

    found = next(
        (r for r in reader.query(sub, "RECORDING#", 50) if r.get("id") == recording_id), None
    )
    event = str((found or {}).get("event_id") or "")
    if not found or not found.get("redacted") or not _EVENT.fullmatch(event):
        return json_response(404, {"error": "not_found"})
    if not event.startswith(recording_id):
        return json_response(404, {"error": "not_found"})
    transcribed = _epoch(found.get("transcribed_at"))
    if transcribed is None or clock() - transcribed > KEPT_SECONDS:
        return json_response(410, {"error": "no_longer_kept"})

    return _private(json_response(200, {"url": presign(redacted_key(sub, event))}))


_resources: tuple[EdgeVerifier, AccountReader, Callable[[str], str]] | None = None


def listen_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    global _resources
    if _resources is None:
        import os

        import boto3
        from botocore.config import Config

        from hearally.config import Settings
        from hearally.dashboard import DynamoAccountReader
        from hearally.factory import build_edge_verifier

        bucket = os.environ["MEDIA_BUCKET"]
        # Virtual-hosted links, the origin the site's media-src allows.
        s3 = boto3.client(
            "s3", config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"})
        )

        def sign(key: str) -> str:
            return str(
                s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bucket, "Key": key, "ResponseContentType": "audio/ogg"},
                    ExpiresIn=LINK_SECONDS,
                )
            )

        table = boto3.resource("dynamodb").Table(Settings.from_environment().table_name)
        _resources = (build_edge_verifier(), DynamoAccountReader(table), sign)
    edge, reader, signer = _resources
    return handle(event, edge, reader, signer)
