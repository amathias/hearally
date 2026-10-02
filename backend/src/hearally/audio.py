"""The audio ingest contract, schema version 4.

Audio never travels through the API. A collector asks for a presigned URL
(`UploadRequest`), puts the bytes into S3 under
``incoming/<account>/<event_id>``, then announces the recording
(`AudioIngestEvent`). The event id is the SHA-256 of the audio, so the same
recording is recognised however many times it is sent.

Version 4 removed ``account_id`` and ``object_key`` from what the collector
sends. The account is the one the computer was paired to (see pairing.py), and
the key is derived from it, so a collector cannot name another account or
another account's audio.

Raw audio under ``incoming/`` is unredacted. The pipeline deletes it once the
redacted copy exists; an S3 lifecycle rule is the backstop if that fails, which
can take up to about 48 hours, not 24, because S3 rounds to midnight UTC.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from hearally.models import ValidationError

SCHEMA_VERSION = 4
MAX_AUDIO_BYTES = 512 * 1024 * 1024
MAX_REQUEST_BYTES = 4_096

CONTENT_TYPES = frozenset(
    {"audio/mpeg", "audio/mp4", "audio/wav", "audio/ogg", "audio/opus", "audio/flac"}
)
CAPTURE_MODES = frozenset({"phone_call", "in_person", "unknown"})
SOURCES = frozenset({"folder", "plaud"})

# An account holder may have several Plaud recorders, often a Note for calls
# and a NotePin for the day. Each recording says which one made it.
RECORDER_MODELS = frozenset({"note", "note_pro", "notepin", "notepin_s", "other"})
# These cannot record a phone call: they have no vibration sensor, which is how
# the Note models read a call off the back of the phone. The rule names models
# known to be incapable rather than those known to be capable, so "other", a
# recorder we know nothing about, is not refused.
CANNOT_RECORD_CALLS = frozenset({"notepin", "notepin_s"})

_EVENT_ID = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{3,128}$")


def incoming_key(account_id: str, event_id: str) -> str:
    """Where a recording's unredacted audio lives. Derived, never supplied.

    Namespaced by account: two account holders who both have a copy of the same
    recording, a voicemail forwarded to both, say, must not share one object.
    """
    return f"incoming/{account_id}/{event_id}"


def _load(body: bytes) -> dict[str, Any]:
    if len(body) > MAX_REQUEST_BYTES:
        raise ValidationError("Request body is too large")
    try:
        data = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("Request body is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")
    return data


def _reject_unknown(data: dict[str, Any], allowed: frozenset[str]) -> None:
    # Unknown fields are refused rather than ignored: a field we silently drop
    # today is a field someone assumes we honour tomorrow.
    extra = set(data) - allowed
    if extra:
        raise ValidationError(f"Unknown fields: {', '.join(sorted(extra))}")


def _event_id(value: Any) -> str:
    if not isinstance(value, str) or not _EVENT_ID.fullmatch(value):
        raise ValidationError("event_id must be a lowercase SHA-256 hex digest")
    return value


def _choice(value: Any, allowed: frozenset[str], message: str) -> str:
    # The type check comes first. A list or object is unhashable, and testing it
    # for set membership raises TypeError, which would surface as a retryable 503
    # instead of the 400 a malformed request deserves.
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(message)
    return value


def _content_type(value: Any) -> str:
    return _choice(value, CONTENT_TYPES, "content_type is not a supported audio type")


def _byte_length(value: Any) -> int:
    # bool is an int in Python; true must not pass as a length of 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("byte_length must be an integer")
    if not 0 < value <= MAX_AUDIO_BYTES:
        raise ValidationError("byte_length is out of range")
    return value


@dataclass(frozen=True, slots=True)
class UploadRequest:
    event_id: str
    content_type: str
    byte_length: int

    _FIELDS = frozenset({"event_id", "content_type", "byte_length"})

    @classmethod
    def from_json_bytes(cls, body: bytes) -> UploadRequest:
        data = _load(body)
        _reject_unknown(data, cls._FIELDS)
        return cls(
            event_id=_event_id(data.get("event_id")),
            content_type=_content_type(data.get("content_type")),
            byte_length=_byte_length(data.get("byte_length")),
        )


@dataclass(frozen=True, slots=True)
class AudioIngestEvent:
    event_id: str
    content_type: str
    byte_length: int
    recorded_at: str
    capture_mode: str
    source: str
    # Optional, so a collector that predates multiple recorders still works.
    recorder_id: str | None = None
    recorder_model: str | None = None

    _FIELDS = frozenset(
        {
            "schema_version",
            "event_id",
            "content_type",
            "byte_length",
            "recorded_at",
            "capture_mode",
            "source",
            "recorder_id",
            "recorder_model",
        }
    )

    @classmethod
    def from_json_bytes(cls, body: bytes) -> AudioIngestEvent:
        data = _load(body)
        _reject_unknown(data, cls._FIELDS)

        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValidationError(f"schema_version must be {SCHEMA_VERSION}")

        event_id = _event_id(data.get("event_id"))

        recorded_at = data.get("recorded_at")
        if not isinstance(recorded_at, str):
            raise ValidationError("recorded_at must be an ISO-8601 date-time")
        try:
            parsed = datetime.fromisoformat(recorded_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValidationError("recorded_at must be an ISO-8601 date-time") from exc
        if parsed.tzinfo is None:
            # A naive time is ambiguous across the account holder's time zone,
            # and quiet hours depend on getting that right.
            raise ValidationError("recorded_at must include a time zone")

        capture_mode = _choice(
            data.get("capture_mode"), CAPTURE_MODES, "capture_mode is not recognised"
        )
        source = _choice(data.get("source"), SOURCES, "source is not recognised")

        recorder_id = data.get("recorder_id")
        if recorder_id is not None and (
            not isinstance(recorder_id, str) or not _SAFE_ID.fullmatch(recorder_id)
        ):
            raise ValidationError("recorder_id must be 3-128 safe identifier characters")

        recorder_model = data.get("recorder_model")
        if recorder_model is not None:
            recorder_model = _choice(
                recorder_model, RECORDER_MODELS, "recorder_model is not recognised"
            )
            # A NotePin cannot record a call. A recording claiming to be one is
            # misconfigured, and trusting the claim would send the judgement
            # looking for a caller who was never there.
            if capture_mode == "phone_call" and recorder_model in CANNOT_RECORD_CALLS:
                raise ValidationError("this recorder model cannot record phone calls")

        return cls(
            event_id=event_id,
            content_type=_content_type(data.get("content_type")),
            byte_length=_byte_length(data.get("byte_length")),
            recorded_at=parsed.isoformat(),
            capture_mode=str(capture_mode),
            source=str(source),
            recorder_id=recorder_id,
            recorder_model=recorder_model,
        )

    def processing_message(self, account_id: str, collector_id: str) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "event_id": self.event_id,
            "account_id": account_id,
            "collector_id": collector_id,
            "object_key": incoming_key(account_id, self.event_id),
            "content_type": self.content_type,
            "byte_length": self.byte_length,
            "recorded_at": self.recorded_at,
            "capture_mode": self.capture_mode,
            "source": self.source,
            "recorder_id": self.recorder_id,
            "recorder_model": self.recorder_model,
        }
