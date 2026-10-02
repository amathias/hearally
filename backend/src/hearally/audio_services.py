from __future__ import annotations

import base64
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hearally.audio import AudioIngestEvent, UploadRequest, incoming_key
from hearally.models import ValidationError
from hearally.queueing import ProcessingQueue
from hearally.repositories import PENDING, IngestRepository

# Long enough to push a long recording over a home connection, short enough
# that a leaked policy is useless by the time anyone could act on it.
UPLOAD_URL_SECONDS = 900


class UploadNotFoundError(ValidationError):
    """The collector announced an object that is not in the bucket."""


@dataclass(frozen=True, slots=True)
class IngestResult:
    accepted: bool
    duplicate: bool
    event_id: str


def sha256_base64(event_id: str) -> str:
    """The event id is a hex SHA-256; S3 wants the same digest base64-encoded."""
    return base64.b64encode(bytes.fromhex(event_id)).decode("ascii")


class UploadService:
    """Issues presigned POST policies for raw audio under incoming/.

    A POST policy, not a presigned PUT. Both were tested against S3 on
    2026-09-23: a presigned PUT signs neither the checksum nor the length, so an
    uploader can drop those headers and put any bytes at the key, including
    replacing a recording after it was verified. A POST policy is itself signed,
    and S3 refuses any upload that breaks it. This policy pins three things:

    - the exact key, derived from the account and the event id
    - the exact size the collector declared
    - the SHA-256 the event id claims, which S3 checks against the bytes

    So the only bytes that can ever exist at incoming/<account>/<id> are the
    bytes whose hash is <id>. A second upload can only be identical, which makes it harmless.
    """

    def __init__(
        self, s3_client: Any, bucket: str, *, expires_in: int = UPLOAD_URL_SECONDS
    ) -> None:
        self._s3 = s3_client
        self._bucket = bucket
        self._expires_in = expires_in

    def presign(self, request: UploadRequest, account_id: str) -> dict[str, Any]:
        key = incoming_key(account_id, request.event_id)
        checksum = sha256_base64(request.event_id)
        fields = {
            "Content-Type": request.content_type,
            "x-amz-checksum-algorithm": "SHA256",
            "x-amz-checksum-sha256": checksum,
        }
        post = self._s3.generate_presigned_post(
            self._bucket,
            key,
            Fields=fields,
            Conditions=[
                {"Content-Type": request.content_type},
                {"x-amz-checksum-algorithm": "SHA256"},
                {"x-amz-checksum-sha256": checksum},
                ["content-length-range", request.byte_length, request.byte_length],
            ],
            ExpiresIn=self._expires_in,
        )
        return {
            "upload_url": post["url"],
            "upload_fields": post["fields"],
            "object_key": key,
            "expires_in": self._expires_in,
        }


class AudioIngestService:
    """Accepts an announced recording and queues it for transcription."""

    def __init__(
        self,
        repository: IngestRepository,
        queue: ProcessingQueue,
        s3_client: Any,
        bucket: str,
        *,
        ttl_seconds: int = 604_800,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._queue = queue
        self._s3 = s3_client
        self._bucket = bucket
        self._ttl_seconds = ttl_seconds
        self._clock = clock

    def _verify_object(self, event: AudioIngestEvent, key: str) -> None:
        """Check the object exists and is exactly what the event claims.

        The checksum comparison is what proves the upload went through the POST
        policy: an object put any other way has no SHA-256 checksum stored.
        """
        try:
            head = self._s3.head_object(Bucket=self._bucket, Key=key, ChecksumMode="ENABLED")
        except Exception as exc:
            if _is_not_found(exc):
                raise UploadNotFoundError("The announced audio was not uploaded") from exc
            raise
        if head.get("ContentLength") != event.byte_length:
            raise ValidationError("Uploaded audio does not match byte_length")
        if head.get("ContentType") != event.content_type:
            raise ValidationError("Uploaded audio does not match content_type")
        if head.get("ChecksumSHA256") != sha256_base64(event.event_id):
            raise ValidationError("Uploaded audio does not match event_id")

    def accept(self, event: AudioIngestEvent, account_id: str, collector_id: str) -> IngestResult:
        """Accept a recording for the account its computer is paired to.

        ``account_id`` comes from the authenticated computer, never the request.
        """
        self._verify_object(event, incoming_key(account_id, event.event_id))

        now = int(self._clock())
        reserved = self._repository.reserve(
            event_id=event.event_id,
            account_id=account_id,
            collector_id=collector_id,
            received_at=now,
            ttl_epoch=now + self._ttl_seconds,
        )

        # Not newly reserved, but still pending: an earlier attempt reserved and
        # never confirmed the send, most likely because its Lambda was killed.
        # Send again rather than report a duplicate and lose the recording. Two
        # concurrent sends collapse in the FIFO queue's deduplication window.
        if not reserved and self._repository.status(account_id, event.event_id) != PENDING:
            return IngestResult(accepted=True, duplicate=True, event_id=event.event_id)

        try:
            self._queue.send(event.processing_message(account_id, collector_id))
        except Exception:
            # Only the attempt that created the reservation may remove it. A
            # recovering retry leaves it pending, so the next retry tries again.
            if reserved:
                self._repository.release(account_id, event.event_id)
            raise

        self._repository.mark_queued(account_id, event.event_id)
        return IngestResult(accepted=True, duplicate=False, event_id=event.event_id)


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = str((response.get("Error") or {}).get("Code") or "")
    return code in {"404", "NoSuchKey", "NotFound"}
