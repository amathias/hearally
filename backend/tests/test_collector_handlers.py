from __future__ import annotations

import json
import unittest

from test_audio import EVENT_ID, FakeS3, event_payload, sha256_base64

from hearally.audio import incoming_key
from hearally.audio_services import AudioIngestService, UploadService
from hearally.edge import EdgeVerifier
from hearally.handlers.collector import handle_ingest, handle_upload
from hearally.pairing import InMemoryPairingRepository, PairingService
from hearally.queueing import InMemoryProcessingQueue
from hearally.repositories import InMemoryIngestRepository

NOW = 1_795_525_800
EDGE = "edge-token"
ACCOUNT = "3f1c2a90-1111-4222-8333-944455556666"
VERSION = "2026-09-24"


def pair(pairing: PairingService, account: str = ACCOUNT) -> str:
    code = pairing.create_code(account, VERSION)["code"]
    return pairing.redeem(code, "Kitchen laptop")["token"]


def request(
    body: dict[str, object], token: str | None, *, idempotency_key: str | None = None
) -> dict[str, object]:
    headers = {
        "x-hearally-edge": EDGE,
        "idempotency-key": idempotency_key
        if idempotency_key is not None
        else str(body.get("event_id")),
    }
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return {"headers": headers, "body": json.dumps(body)}


def stored(account: str = ACCOUNT) -> dict[str, dict[str, object]]:
    return {
        incoming_key(account, EVENT_ID): {
            "ContentLength": 2048,
            "ContentType": "audio/mp4",
            "ChecksumSHA256": sha256_base64(EVENT_ID),
        }
    }


class CollectorHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.edge = EdgeVerifier(lambda: EDGE)
        self.repository = InMemoryPairingRepository()
        self.pairing = PairingService(
            self.repository, acknowledgement_version=VERSION, clock=lambda: NOW
        )
        self.token = pair(self.pairing)
        self.s3 = FakeS3(stored())
        self.queue = InMemoryProcessingQueue()
        self.uploads = UploadService(self.s3, "bucket")
        self.service = AudioIngestService(
            InMemoryIngestRepository(), self.queue, self.s3, "bucket", clock=lambda: NOW
        )

    def upload(self, req: dict[str, object]) -> dict[str, object]:
        return handle_upload(req, self.edge, self.pairing, self.uploads)

    def ingest(self, req: dict[str, object]) -> dict[str, object]:
        return handle_ingest(req, self.edge, self.pairing, self.service)

    def test_upload_is_keyed_to_the_paired_account(self) -> None:
        response = self.upload(
            request(
                {"event_id": EVENT_ID, "content_type": "audio/mp4", "byte_length": 2048}, self.token
            )
        )
        self.assertEqual(response["statusCode"], 200)
        body = json.loads(response["body"])
        self.assertEqual(body["object_key"], incoming_key(ACCOUNT, EVENT_ID))
        self.assertIn("policy", body["upload_fields"])

    def test_ingest_queues_the_recording_for_the_paired_account(self) -> None:
        response = self.ingest(request(event_payload(), self.token))
        self.assertEqual(response["statusCode"], 202)
        self.assertEqual(len(self.queue.messages), 1)
        self.assertEqual(self.queue.messages[0]["account_id"], ACCOUNT)

    def test_the_dashboard_learns_the_computer_and_the_plaud(self) -> None:
        payload = event_payload(recorder_id="note-pro", recorder_model="note_pro")
        self.ingest(request(payload, self.token))
        self.ingest(request(payload, self.token))  # a resend is not a second recording
        recorder = self.repository.items[(f"USER#{ACCOUNT}", "DEVICE#note-pro")]
        self.assertEqual((recorder["model"], recorder["recordings"]), ("note_pro", 1))
        computer = next(
            v for (pk, sk), v in self.repository.items.items() if sk.startswith("COMPUTER#")
        )
        self.assertIn("last_seen_at", computer)
        # One note per recording for the dashboard's list, without its content.
        notes = [v for (pk, sk), v in self.repository.items.items() if sk.startswith("RECORDING#")]
        self.assertEqual(len(notes), 1)
        self.assertEqual(
            (notes[0]["source"], notes[0]["via"], notes[0]["status"]),
            ("folder", f"computer:{computer['id']}", "received"),
        )

    def test_bookkeeping_failure_does_not_fail_an_accepted_recording(self) -> None:
        def broken(*args: object) -> None:
            raise RuntimeError("table busy")

        self.repository.seen = broken  # type: ignore[method-assign]
        with self.assertLogs("hearally.handlers.collector", "WARNING") as logs:
            response = self.ingest(request(event_payload(), self.token))
        self.assertEqual(response["statusCode"], 202)
        self.assertEqual(
            logs.output,
            [
                "WARNING:hearally.handlers.collector:"
                "Could not record collector activity: RuntimeError"
            ],
        )

    def test_malformed_types_are_a_400_not_a_retryable_503(self) -> None:
        """Review F10: the collector retries a 503, so a bad request must not be one."""
        for field, value in (
            ("content_type", ["audio/mp4"]),
            ("capture_mode", {"x": 1}),
            ("source", ["folder"]),
        ):
            with self.subTest(field=field):
                response = self.ingest(request(event_payload(**{field: value}), self.token))
                self.assertEqual(response["statusCode"], 400)

    def test_resending_the_same_recording_is_accepted_as_a_duplicate(self) -> None:
        self.ingest(request(event_payload(), self.token))
        response = self.ingest(request(event_payload(), self.token))
        self.assertEqual(response["statusCode"], 202)
        self.assertTrue(json.loads(response["body"])["duplicate"])
        self.assertEqual(len(self.queue.messages), 1)

    def test_request_that_skipped_the_edge_is_forbidden(self) -> None:
        req = request(event_payload(), self.token)
        del req["headers"]["x-hearally-edge"]  # type: ignore[attr-defined]
        self.assertEqual(self.ingest(req)["statusCode"], 403)
        self.assertEqual(self.queue.messages, [])

    def test_no_token_or_a_wrong_one_is_unauthorised(self) -> None:
        account, device, _secret = self.token.split(".")[1:]
        for token in (None, "", "hd1.x.y.z", f"hd1.{account}.{device}.{'A' * 43}"):
            with self.subTest(token=token):
                self.assertEqual(self.ingest(request(event_payload(), token))["statusCode"], 401)
        self.assertEqual(self.queue.messages, [])

    def test_a_revoked_computer_is_refused_at_its_next_request(self) -> None:
        device_id = self.token.split(".")[2]
        self.assertTrue(self.pairing.revoke(ACCOUNT, device_id))
        self.assertEqual(self.ingest(request(event_payload(), self.token))["statusCode"], 401)

    def test_one_account_cannot_use_another_accounts_computer_token_to_reach_its_audio(
        self,
    ) -> None:
        """Changing the account in a token breaks it: the secret is for one record."""
        other_token = pair(self.pairing, "9e8d7c6b-0000-4000-8000-000000000000")
        parts = other_token.split(".")
        forged = ".".join([parts[0], ACCOUNT, parts[2], parts[3]])
        self.assertEqual(self.ingest(request(event_payload(), forged))["statusCode"], 401)

    def test_idempotency_key_must_match_the_event(self) -> None:
        response = self.ingest(request(event_payload(), self.token, idempotency_key="b" * 64))
        self.assertEqual(response["statusCode"], 400)

    def test_announcing_audio_that_was_never_uploaded_is_a_conflict(self) -> None:
        self.s3.objects.clear()
        self.assertEqual(self.ingest(request(event_payload(), self.token))["statusCode"], 409)

    def test_an_unexpected_failure_leaks_nothing(self) -> None:
        class ExplodingS3(FakeS3):
            def head_object(
                self, Bucket: str, Key: str, ChecksumMode: str = ""
            ) -> dict[str, object]:
                raise RuntimeError("internal detail: arn:aws:s3:::bucket/secret")

        service = AudioIngestService(
            InMemoryIngestRepository(), self.queue, ExplodingS3(), "bucket"
        )
        response = handle_ingest(
            request(event_payload(), self.token), self.edge, self.pairing, service
        )
        self.assertEqual(response["statusCode"], 503)
        self.assertNotIn("arn", response["body"])
        self.assertNotIn("secret", response["body"])


if __name__ == "__main__":
    unittest.main()
