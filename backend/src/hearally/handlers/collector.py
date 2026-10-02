"""Handlers for the collector: POST /v1/uploads and POST /v1/ingest.

Every request must pass two independent checks before its body is even
parsed: it arrived through CloudFront (the edge token), and it carries the
device token of a computer paired with an account and not since revoked (see
pairing.py). The account every recording belongs to is that computer's
account; nothing in the request can choose another. Failing either check
returns a bare status with no detail, because detail only helps someone
probing the API.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from hearally.audio import MAX_REQUEST_BYTES, AudioIngestEvent, UploadRequest
from hearally.audio_services import AudioIngestService, UploadNotFoundError, UploadService
from hearally.edge import EdgeVerificationError, EdgeVerifier
from hearally.handlers.requests import body_bytes
from hearally.handlers.responses import json_response
from hearally.models import ValidationError
from hearally.pairing import (
    DeviceAuthenticationError,
    PairedComputer,
    PairingService,
    recording_note,
)

LOGGER = logging.getLogger(__name__)


def _header(headers: dict[str, str], name: str) -> str:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return ""


def _guarded(
    request: dict[str, Any],
    edge: EdgeVerifier,
    pairing: PairingService,
    work: Callable[[bytes, PairedComputer, dict[str, str]], dict[str, Any]],
) -> dict[str, Any]:
    headers = request.get("headers") or {}
    try:
        edge.verify(headers)
        computer = pairing.authenticate(headers)
        body = body_bytes(request)
        if len(body) > MAX_REQUEST_BYTES:
            return json_response(413, {"error": "request_too_large"})
        return work(body, computer, headers)
    except EdgeVerificationError:
        return json_response(403, {"error": "forbidden"})
    except DeviceAuthenticationError:
        return json_response(401, {"error": "authentication_failed"})
    except UploadNotFoundError:
        return json_response(409, {"error": "upload_missing"})
    except ValidationError as exc:
        # Validation messages describe the request's shape, never its content,
        # so the collector author can see what to fix.
        return json_response(400, {"error": "invalid_request", "detail": str(exc)})
    except Exception:
        return json_response(503, {"error": "temporarily_unavailable"})


def handle_upload(
    request: dict[str, Any],
    edge: EdgeVerifier,
    pairing: PairingService,
    uploads: UploadService,
) -> dict[str, Any]:
    def work(body: bytes, computer: PairedComputer, headers: dict[str, str]) -> dict[str, Any]:
        upload = UploadRequest.from_json_bytes(body)
        if _header(headers, "idempotency-key") != upload.event_id:
            raise ValidationError("Idempotency-Key must match event_id")
        return json_response(200, uploads.presign(upload, computer.account_id))

    return _guarded(request, edge, pairing, work)


def handle_ingest(
    request: dict[str, Any],
    edge: EdgeVerifier,
    pairing: PairingService,
    service: AudioIngestService,
) -> dict[str, Any]:
    def work(body: bytes, computer: PairedComputer, headers: dict[str, str]) -> dict[str, Any]:
        event = AudioIngestEvent.from_json_bytes(body)
        if _header(headers, "idempotency-key") != event.event_id:
            raise ValidationError("Idempotency-Key must match event_id")
        result = service.accept(event, computer.account_id, computer.device_id)
        try:
            # Only a first acceptance counts towards the Plaud's recordings.
            pairing.record_activity(
                computer, None if result.duplicate else event.recorder_id, event.recorder_model
            )
            if not result.duplicate:
                pairing.note_recording(
                    computer.account_id,
                    recording_note(
                        event_id=event.event_id,
                        recorded_at=event.recorded_at,
                        source="folder",
                        via=f"computer:{computer.device_id}",
                        byte_length=event.byte_length,
                        duration_s=None,
                        now=int(time.time()),
                    ),
                )
        except Exception as exc:
            # Bookkeeping for the dashboard. The recording is already queued,
            # so a failure here must not make the collector send it again.
            LOGGER.warning("Could not record collector activity: %s", type(exc).__name__)
        return json_response(
            202,
            {
                "accepted": result.accepted,
                "duplicate": result.duplicate,
                "event_id": result.event_id,
            },
        )

    return _guarded(request, edge, pairing, work)


_resources: tuple[EdgeVerifier, PairingService, UploadService, AudioIngestService] | None = None


def _built() -> tuple[EdgeVerifier, PairingService, UploadService, AudioIngestService]:
    global _resources
    if _resources is None:
        from hearally.factory import build_collector_resources

        _resources = build_collector_resources()
    return _resources


def upload_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    edge, pairing, uploads, _ = _built()
    return handle_upload(event, edge, pairing, uploads)


def ingest_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    del context
    edge, pairing, _, service = _built()
    return handle_ingest(event, edge, pairing, service)
