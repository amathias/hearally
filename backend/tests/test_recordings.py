"""Listening to the silenced copy: only your own, only while it is kept."""

from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from typing import Any

from hearally.edge import EdgeVerifier
from hearally.handlers.recordings import KEPT_SECONDS, handle

SUB = "3f1c2a90-1111-4222-8333-944455556666"
EDGE = "edge-token"
EVENT = "ab" * 32
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC).timestamp()


class Reader:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.records = records
        self.subs: list[str] = []

    def query(self, sub: str, prefix: str, limit: int) -> list[dict[str, Any]]:
        self.subs.append(sub)
        return list(self.records) if prefix == "RECORDING#" else []

    def get(self, sub: str, sort_key: str) -> dict[str, Any] | None:
        return None


def recording(**extra: Any) -> dict[str, Any]:
    return {
        "id": EVENT[:16],
        "status": "transcribed",
        "redacted": True,
        "event_id": EVENT,
        "transcribed_at": "2026-10-06T10:00:00Z",
        **extra,
    }


def request(body: dict[str, Any], sub: str | None = SUB) -> dict[str, Any]:
    req: dict[str, Any] = {"headers": {"x-hearally-edge": EDGE}, "body": json.dumps(body)}
    if sub:
        req["requestContext"] = {"authorizer": {"jwt": {"claims": {"sub": sub}}}}
    return req


class ListenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signed: list[str] = []

    def call(self, records: list[dict[str, Any]], body: dict[str, Any], **kw: Any) -> Any:
        def presign(key: str) -> str:
            self.signed.append(key)
            return "https://media.example/" + key

        return handle(
            request(body, **kw), EdgeVerifier(lambda: EDGE), Reader(records), presign, lambda: NOW
        )

    def test_the_silenced_copy_of_your_own_recording(self) -> None:
        response = self.call([recording()], {"id": EVENT[:16]})
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(self.signed, [f"redacted/{SUB}/{EVENT}.ogg"])
        self.assertEqual(response["headers"]["cache-control"], "private, no-store")

    def test_nothing_without_a_silenced_copy(self) -> None:
        for record in (recording(redacted=False), recording(event_id=None), recording(id="0" * 16)):
            with self.subTest(record=record):
                self.assertEqual(self.call([record], {"id": EVENT[:16]})["statusCode"], 404)
        self.assertEqual(self.signed, [])

    def test_an_event_that_does_not_match_the_recording_is_refused(self) -> None:
        response = self.call([recording(event_id="cd" * 32)], {"id": EVENT[:16]})
        self.assertEqual(response["statusCode"], 404)

    def test_gone_after_seven_days(self) -> None:
        old = datetime.fromtimestamp(NOW - KEPT_SECONDS - 60, UTC).isoformat()
        response = self.call([recording(transcribed_at=old)], {"id": EVENT[:16]})
        self.assertEqual(response["statusCode"], 410)

    def test_bad_requests(self) -> None:
        self.assertEqual(self.call([recording()], {"id": "../x"})["statusCode"], 400)
        self.assertEqual(self.call([recording()], {"id": EVENT[:16]}, sub=None)["statusCode"], 401)


if __name__ == "__main__":
    unittest.main()
