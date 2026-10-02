"""Import new recordings from each connected Plaud account (ARCHITECTURE.md section 2).

Runs every 15 minutes. For each account connected through Plaud sign-in:

1. Skip it unless the account holder has accepted the signup acknowledgement,
   exactly as pairing a computer requires.
2. List recordings that started after the account connected. Nothing from
   before the connection is ever fetched.
3. For each one not yet imported, ask Plaud for its audio link, download the
   audio, and store it at ``incoming/<account>/<sha256>``: the same key, the
   same stored checksum and the same intake (``AudioIngestService.accept``) as
   a collector upload. Reservation and the FIFO queue make a repeat harmless.
4. Mark it imported, so it is never fetched twice.

Nothing here logs a token, a link, a recording's name or anything said. The
report counts only.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from hearally.audio import CONTENT_TYPES, MAX_AUDIO_BYTES, AudioIngestEvent, incoming_key
from hearally.pairing import recording_note
from hearally.plaud_link import (
    Http,
    McpSession,
    NotConnectedError,
    PlaudLinkError,
    PlaudLinkRepository,
    PlaudLinkService,
    find_url,
    first_of,
    records_in,
    seconds_of,
)

LOGGER = logging.getLogger(__name__)

# Per account per run, so one busy account cannot use up the run.
MAX_PER_ACCOUNT = 3
# Plaud lists a recording that is still syncing with a duration of a few
# milliseconds and no audio yet. Wait for it to finish.
MIN_DURATION_MS = 2_000
COLLECTOR_ID = "plaud-mcp"
RECORDER_ID = "plaud-cloud"
DOWNLOAD_TIMEOUT = 120
CHUNK = 1 << 20

# What Plaud's storage may call the audio, mapped to what the intake accepts.
_TYPES = {"audio/x-ogg": "audio/ogg", "application/ogg": "audio/ogg", "audio/mp3": "audio/mpeg"}


class Ingest(Protocol):
    def accept(self, event: AudioIngestEvent, account_id: str, collector_id: str) -> Any: ...


class Consent(Protocol):
    def needs_acknowledgement(self, account_id: str) -> bool: ...


class Recordings(Protocol):
    def note_recording(self, account_id: str, recording: dict[str, Any]) -> None: ...


def plaud_time(value: Any) -> datetime | None:
    """Plaud's start_at, "2026-09-27T10:50:00", has no zone. It is read as UTC,
    which is how Plaud's servers keep time; a zone, if Plaud adds one, wins."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class PlaudImporter:
    def __init__(
        self,
        repository: PlaudLinkRepository,
        links: PlaudLinkService,
        http: Http,
        download: Callable[[str, Any], str],
        s3: Any,
        bucket: str,
        ingest: Ingest,
        consent: Consent,
        recordings: Recordings,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repo = repository
        self._links = links
        self._http = http
        self._download = download
        self._s3 = s3
        self._bucket = bucket
        self._ingest = ingest
        self._consent = consent
        self._recordings = recordings
        self._clock = clock

    def run(self) -> dict[str, float]:
        totals: dict[str, float] = {"accounts": 0, "imported": 0, "skipped": 0, "failed": 0}
        for sub in self._repo.connected_accounts():
            totals["accounts"] += 1
            try:
                result = self._account(sub)
            except Exception as exc:
                # One account's trouble must not stop the others.
                LOGGER.warning("Plaud import failed for an account: %s", type(exc).__name__)
                totals["failed"] += 1
                continue
            for key, value in result.items():
                if key == "newest_after_connected_h":
                    totals[key] = value
                else:
                    totals[key] = totals.get(key, 0) + value
        return totals

    def _account(self, sub: str) -> dict[str, float]:
        # Counts of what happened and why, never names or content: enough to
        # see why a recording was or was not brought in.
        result: dict[str, float] = {"imported": 0, "skipped": 0, "failed": 0}
        link = self._repo.get_link(sub)
        if not link or self._consent.needs_acknowledgement(sub):
            result["skipped"] += 1
            return result
        since = datetime.fromtimestamp(int(link.get("connected_epoch", 0)), UTC)
        try:
            mcp = McpSession(self._http, self._links.access_token(sub))
            mcp.open()
            listing = mcp.tool("list_files", {"date_from": since.date().isoformat()})
        except NotConnectedError:
            result["skipped"] += 1
            return result
        done = self._repo.imported_ids(sub)
        new = []
        why = dict.fromkeys(("listed", "already", "no_time", "before", "syncing"), 0)
        newest: datetime | None = None
        for record in records_in(listing):
            why["listed"] += 1
            file_id = first_of(record, "id", "file_id")
            started = plaud_time(first_of(record, "start_at", "created_at"))
            duration = record.get("duration")
            if started is not None and (newest is None or started > newest):
                newest = started
            if not isinstance(file_id, str) or started is None:
                why["no_time"] += 1
            elif file_id in done:
                why["already"] += 1
            elif started < since:
                why["before"] += 1
            elif not isinstance(duration, (int, float)) or duration < MIN_DURATION_MS:
                why["syncing"] += 1
            else:
                new.append((started, file_id, seconds_of(record)))
        result.update(why)
        if newest is not None:
            result["newest_after_connected_h"] = round((newest - since).total_seconds() / 3600, 1)
        # Oldest first, so a backlog arrives in the order it was said.
        for started, file_id, duration_s in sorted(new)[:MAX_PER_ACCOUNT]:
            try:
                self._import(sub, mcp, file_id, started, duration_s)
                result["imported"] += 1
            except Exception as exc:
                LOGGER.warning("A Plaud recording could not be imported: %s", type(exc).__name__)
                result["failed"] += 1
        return result

    def _import(
        self,
        sub: str,
        mcp: McpSession,
        file_id: str,
        started: datetime,
        duration_s: float | None = None,
    ) -> None:
        url = find_url(mcp.tool("get_file", {"file_id": file_id}))
        if not url:
            raise PlaudLinkError("get_file gave no link")
        with tempfile.TemporaryFile() as audio:
            content_type = self._download(url, audio)
            content_type = _TYPES.get(content_type, content_type)
            if content_type not in CONTENT_TYPES:
                raise PlaudLinkError(f"unsupported audio type {content_type[:40]}")
            size = audio.tell()
            if not 0 < size <= MAX_AUDIO_BYTES:
                raise PlaudLinkError("audio is empty or too large")
            audio.seek(0)
            digest = hashlib.sha256()
            while chunk := audio.read(CHUNK):
                digest.update(chunk)
            event_id = digest.hexdigest()
            audio.seek(0)
            # Stored with its SHA-256, as the collector's upload policy does,
            # so the intake's checks treat it exactly like a collector upload.
            self._s3.put_object(
                Bucket=self._bucket,
                Key=incoming_key(sub, event_id),
                Body=audio,
                ContentType=content_type,
                ContentLength=size,
                ChecksumSHA256=base64.b64encode(digest.digest()).decode(),
            )
        event = AudioIngestEvent(
            event_id=event_id,
            content_type=content_type,
            byte_length=size,
            recorded_at=started.isoformat(),
            capture_mode="unknown",
            source="plaud",
            recorder_id=RECORDER_ID,
            recorder_model="other",
        )
        self._ingest.accept(event, sub, COLLECTOR_ID)
        now = int(self._clock())
        self._repo.mark_imported(sub, file_id, event_id, now)
        self._recordings.note_recording(
            sub,
            recording_note(
                event_id=event_id,
                recorded_at=event.recorded_at,
                source="plaud",
                via="plaud",
                byte_length=size,
                duration_s=duration_s,
                now=now,
            ),
        )


def urllib_download(url: str, out: Any) -> str:
    """Stream ``url`` into ``out``; returns the content type. HTTPS only."""
    import urllib.request

    if not url.startswith("https://"):
        raise PlaudLinkError("refusing a link that is not HTTPS")
    request = urllib.request.Request(url)  # noqa: S310 - HTTPS checked
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:  # noqa: S310
        total = 0
        while chunk := response.read(CHUNK):
            total += len(chunk)
            if total > MAX_AUDIO_BYTES:
                raise PlaudLinkError("audio is too large")
            out.write(chunk)
        return str(response.headers.get_content_type())
