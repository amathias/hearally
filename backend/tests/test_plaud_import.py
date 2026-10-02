"""Importing new recordings from a connected Plaud account."""

from __future__ import annotations

import base64
import hashlib
import json
import unittest
from datetime import UTC, datetime
from typing import Any

from test_plaud_link import ACCOUNT, AUDIO, FakePlaud

from hearally.audio import incoming_key
from hearally.plaud_import import PlaudImporter, plaud_time
from hearally.plaud_link import InMemoryPlaudLinkRepository, PlaudLinkService

CONNECTED = int(datetime(2026, 9, 26, 12, 0, tzinfo=UTC).timestamp())
NOW = CONNECTED + 3 * 86400


class Plaud(FakePlaud):
    """Three recordings: one from before the connection, one after, and one
    still syncing (a few milliseconds long, no audio yet)."""

    def __init__(self) -> None:
        super().__init__()
        self.listed_with: list[dict[str, Any]] = []

    def result(self, message: dict[str, Any]) -> dict[str, Any]:
        params = message.get("params") or {}
        if params.get("name") == "list_files":
            self.listed_with.append(params["arguments"])
            files = [
                {"id": "new", "name": "x", "start_at": "2026-09-27T10:50:00", "duration": 90360},
                {"id": "old", "name": "x", "start_at": "2026-09-26T09:00:00", "duration": 60000},
                {"id": "syncing", "name": "x", "start_at": "2026-09-28T08:00:00", "duration": 9},
            ]
            text = "Untrusted.\n<untrusted-data-1>" + json.dumps({"type": "list", "data": files})
            return {"content": [{"type": "text", "text": text + "</untrusted-data-1>"}]}
        if params.get("name") == "get_file":
            payload = {"id": params["arguments"]["file_id"], "audio_url": AUDIO}
            return {"content": [{"type": "text", "text": json.dumps(payload)}]}
        return super().result(message)


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}

    def put_object(self, **kwargs: Any) -> None:
        self.objects[kwargs["Key"]] = {**kwargs, "Body": kwargs["Body"].read()}


class Ingest:
    def __init__(self) -> None:
        self.accepted: list[tuple[Any, str, str]] = []

    def accept(self, event: Any, account_id: str, collector_id: str) -> None:
        self.accepted.append((event, account_id, collector_id))


class Consent:
    def __init__(self, given: bool = True) -> None:
        self.given = given

    def needs_acknowledgement(self, account_id: str) -> bool:
        return not self.given


class Recordings:
    def __init__(self) -> None:
        self.noted: list[dict[str, Any]] = []

    def note_recording(self, account_id: str, recording: dict[str, Any]) -> None:
        self.noted.append(recording)


BYTES = b"OggS fake audio"


class PlaudImporterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plaud = Plaud()
        self.repo = InMemoryPlaudLinkRepository()
        self.repo.save_link(
            ACCOUNT,
            {
                "access_token": "at-1",
                "refresh_token": "rt-1",
                "access_expires_epoch": NOW + 3600,
                "connected_epoch": CONNECTED,
            },
        )
        self.s3 = FakeS3()
        self.ingest = Ingest()
        self.consent = Consent()
        self.recordings = Recordings()
        self.content_type = "audio/ogg"

    def importer(self) -> PlaudImporter:
        def download(url: str, out: Any) -> str:
            self.assertEqual(url, AUDIO)
            out.write(BYTES)
            return self.content_type

        return PlaudImporter(
            self.repo,
            PlaudLinkService(self.repo, self.plaud, lambda: NOW),
            self.plaud,
            download,
            self.s3,
            "media",
            self.ingest,
            self.consent,
            self.recordings,
            clock=lambda: NOW,
        )

    def test_imports_only_what_was_recorded_after_connecting(self) -> None:
        totals = self.importer().run()
        self.assertEqual(
            totals,
            {
                "accounts": 1,
                "imported": 1,
                "skipped": 0,
                "failed": 0,
                "listed": 3,
                "already": 0,
                "no_time": 0,
                "before": 1,
                "syncing": 1,
                "newest_after_connected_h": 44.0,
            },
        )
        self.assertEqual(self.plaud.listed_with, [{"date_from": "2026-09-26"}])
        event_id = hashlib.sha256(BYTES).hexdigest()
        stored = self.s3.objects[incoming_key(ACCOUNT, event_id)]
        self.assertEqual(stored["Body"], BYTES)
        self.assertEqual(stored["ContentType"], "audio/ogg")
        self.assertEqual(
            stored["ChecksumSHA256"], base64.b64encode(hashlib.sha256(BYTES).digest()).decode()
        )
        event, account, collector = self.ingest.accepted[0]
        self.assertEqual((account, collector, event.source), (ACCOUNT, "plaud-mcp", "plaud"))
        self.assertEqual(event.recorded_at, "2026-09-27T10:50:00+00:00")
        self.assertEqual(self.repo.imported_ids(ACCOUNT), {"new"})
        [note] = self.recordings.noted
        self.assertEqual(
            {k: note[k] for k in ("recorded_at", "source", "via", "duration_s", "status")},
            {
                "recorded_at": "2026-09-27T10:50:00+00:00",
                "source": "plaud",
                "via": "plaud",
                "duration_s": 90,
                "status": "received",
            },
        )
        self.assertNotIn("name", note)

    def test_a_second_run_imports_nothing_new(self) -> None:
        self.importer().run()
        totals = self.importer().run()
        self.assertEqual(totals["imported"], 0)
        self.assertEqual(len(self.ingest.accepted), 1)

    def test_nothing_is_imported_before_the_acknowledgement(self) -> None:
        self.consent.given = False
        totals = self.importer().run()
        self.assertEqual((totals["imported"], totals["skipped"]), (0, 1))
        self.assertEqual(self.plaud.listed_with, [])

    def test_an_unsupported_audio_type_is_not_marked_imported(self) -> None:
        self.content_type = "text/html"
        totals = self.importer().run()
        self.assertEqual((totals["imported"], totals["failed"]), (0, 1))
        self.assertEqual(self.repo.imported_ids(ACCOUNT), set())
        self.assertEqual(self.s3.objects, {})

    def test_a_disconnected_account_is_not_imported(self) -> None:
        self.repo.delete_link(ACCOUNT)
        self.assertEqual(self.importer().run()["accounts"], 0)

    def test_plaud_times_without_a_zone_are_utc(self) -> None:
        self.assertEqual(
            plaud_time("2026-09-27T10:50:00"), datetime(2026, 9, 27, 10, 50, tzinfo=UTC)
        )
        self.assertIsNone(plaud_time("yesterday"))


if __name__ == "__main__":
    unittest.main()
