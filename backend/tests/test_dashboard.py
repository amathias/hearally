from __future__ import annotations

import json
import unittest
from decimal import Decimal
from typing import Any

from hearally.dashboard import CAPABILITIES, SECTIONS, section
from hearally.edge import EdgeVerifier
from hearally.handlers.dashboard import handle

SUB = "11111111-2222-3333-4444-555555555555"
EDGE = "edge-token"


class FakeReader:
    """A table holding records for two different people."""

    def __init__(self, records: dict[tuple[str, str], list[dict[str, Any]]] | None = None) -> None:
        self.records = records or {}
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.subs_read: set[str] = set()

    def query(self, sub: str, prefix: str, limit: int) -> list[dict[str, Any]]:
        self.subs_read.add(sub)
        return list(self.records.get((sub, prefix), []))[:limit]

    def get(self, sub: str, sort_key: str) -> dict[str, Any] | None:
        self.subs_read.add(sub)
        return self.items.get((sub, sort_key))


def request(
    section_name: str = "overview",
    *,
    sub: str | None = SUB,
    edge: bool = True,
    email: str = "someone@example.com",
) -> dict[str, Any]:
    claims = {"sub": sub, "email": email, "email_verified": "true"} if sub else None
    context: dict[str, Any] = {"authorizer": {"jwt": {"claims": claims}}} if claims else {}
    return {
        "headers": {"x-hearally-edge": EDGE} if edge else {},
        "pathParameters": {"section": section_name},
        "requestContext": context,
    }


def call(reader: FakeReader, **kwargs: Any) -> tuple[int, dict[str, Any], dict[str, str]]:
    response = handle(request(**kwargs), EdgeVerifier(lambda: EDGE), reader)
    return response["statusCode"], json.loads(response["body"]), response["headers"]


class DashboardAccessTests(unittest.TestCase):
    def test_reads_only_the_signed_in_persons_records(self) -> None:
        """Scoped by the verified token alone; nothing in the request can widen it."""
        reader = FakeReader(
            {
                (SUB, "ITEM#"): [{"id": "mine", "summary": "Mine"}],
                ("someone-else", "ITEM#"): [{"id": "theirs", "summary": "Not yours"}],
            }
        )
        status, body, _ = call(reader, section_name="summaries")
        self.assertEqual(status, 200)
        self.assertEqual([s["id"] for s in body["summaries"]], ["mine"])
        self.assertEqual(reader.subs_read, {SUB})

    def test_every_section_reads_only_the_signed_in_person(self) -> None:
        for name in SECTIONS:
            with self.subTest(section=name):
                reader = FakeReader()
                call(reader, section_name=name)
                self.assertLessEqual(reader.subs_read, {SUB})

    def test_no_verified_identity_is_refused_not_defaulted(self) -> None:
        status, _, _ = call(FakeReader(), sub=None)
        self.assertEqual(status, 401)

    def test_a_request_that_skipped_the_edge_is_forbidden(self) -> None:
        status, _, _ = call(FakeReader(), edge=False)
        self.assertEqual(status, 403)

    def test_an_unknown_section_is_not_found(self) -> None:
        status, _, _ = call(FakeReader(), section_name="admin")
        self.assertEqual(status, 404)

    def test_personal_data_is_never_cached(self) -> None:
        _, _, headers = call(FakeReader())
        self.assertEqual(headers["cache-control"], "private, no-store")

    def test_a_storage_failure_is_a_bare_503(self) -> None:
        class Broken(FakeReader):
            def query(self, sub: str, prefix: str, limit: int) -> list[dict[str, Any]]:
                raise RuntimeError("arn:aws:dynamodb:...:table/hearally-prod")

        status, body, _ = call(Broken(), section_name="summaries")
        self.assertEqual(status, 503)
        self.assertNotIn("arn", json.dumps(body))


class DashboardContentTests(unittest.TestCase):
    def test_nothing_saved_means_empty_not_sample_data(self) -> None:
        body = section("overview", SUB, {}, FakeReader())
        self.assertEqual(
            body["counts"],
            {
                "summaries": 0,
                "alerts": 0,
                "allies": 0,
                "allies_confirmed": 0,
                "computers": 0,
                "recordings": 0,
            },
        )
        self.assertIsNone(body["latest_recording"])
        self.assertEqual(body["latest_summaries"], [])

    def test_fields_outside_the_allowlist_never_reach_the_browser(self) -> None:
        reader = FakeReader(
            {
                (SUB, "ALLY#"): [
                    {
                        "sk": "ALLY#a1",
                        "id": "a1",
                        "name": "Dana",
                        "phone": "+15550100",
                        "consent_evidence": "signed-link-token",
                    }
                ]
            }
        )
        [ally] = section("people", SUB, {}, reader)["allies"]
        self.assertEqual(ally, {"id": "a1", "name": "Dana"})

    def test_paired_computers_are_listed_without_their_credential(self) -> None:
        reader = FakeReader(
            {
                (SUB, "COMPUTER#"): [
                    {
                        "sk": "COMPUTER#c1",
                        "id": "c1",
                        "name": "Kitchen laptop",
                        "status": "active",
                        "paired_at": "2026-09-24T18:00:00Z",
                        "credential_digest": "f" * 64,
                    }
                ],
                (SUB, "RECORDING#"): [
                    {
                        "sk": "RECORDING#2026-09-30T19:39:00+00:00#abc",
                        "id": "abc",
                        "recorded_at": "2026-09-30T19:39:00+00:00",
                        "source": "plaud",
                        "via": "plaud",
                        "duration_s": 62,
                        "status": "received",
                        "event_id_full": "not for the browser",
                    }
                ],
            }
        )
        body = section("integrations", SUB, {}, reader, acknowledgement_version="2026-09-24")
        [computer] = body["computers"]
        self.assertNotIn("credential_digest", computer)
        self.assertEqual(computer["name"], "Kitchen laptop")
        [recording] = body["recordings"]
        self.assertEqual((recording["via"], recording["duration_s"]), ("plaud", 62))
        self.assertNotIn("event_id_full", recording)
        self.assertFalse(body["acknowledged"])

        reader.items[(SUB, "CONSENT#2026-09-24")] = {"version": "2026-09-24"}
        body = section("integrations", SUB, {}, reader, acknowledgement_version="2026-09-24")
        self.assertTrue(body["acknowledged"])

    def test_settings_stored_beside_an_ally_are_not_counted_as_allies(self) -> None:
        reader = FakeReader(
            {
                (SUB, "ALLY#"): [
                    {"sk": "ALLY#a1", "id": "a1", "name": "Dana"},
                    {"sk": "ALLY#a1#SCHEDULE", "id": "a1"},
                    {"sk": "ALLY#a1#HEARS", "id": "a1"},
                ]
            }
        )
        self.assertEqual(len(section("people", SUB, {}, reader)["allies"]), 1)

    def test_dynamodb_numbers_become_json_numbers(self) -> None:
        reader = FakeReader({(SUB, "ITEM#"): [{"id": "i", "spoken_at": Decimal("1700000000")}]})
        [item] = section("summaries", SUB, {}, reader)["summaries"]
        self.assertEqual(json.dumps(item), '{"id": "i", "spoken_at": 1700000000}')

    def test_account_comes_from_the_verified_token(self) -> None:
        body = section(
            "account", SUB, {"email": "a@example.com", "email_verified": "true"}, FakeReader()
        )
        self.assertEqual(body["email"], "a@example.com")
        self.assertTrue(body["email_verified"])
        self.assertEqual(body["plan"], "public_preview")

    def test_preferences_say_editing_is_not_available_yet(self) -> None:
        body = section("preferences", SUB, {}, FakeReader())
        self.assertFalse(body["editing_available"])
        self.assertEqual(body["dignity_filter"], "default")

    def test_every_capability_states_its_status(self) -> None:
        for capability in CAPABILITIES:
            with self.subTest(capability=capability["id"]):
                self.assertIn(
                    capability["status"], {"available", "testing", "in_progress", "not_yet"}
                )
                self.assertTrue(capability["detail"])


if __name__ == "__main__":
    unittest.main()
