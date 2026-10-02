from __future__ import annotations

import json
import unittest

from hearally.audio import AudioIngestEvent, UploadRequest, incoming_key
from hearally.audio_services import (
    AudioIngestService,
    UploadNotFoundError,
    UploadService,
    sha256_base64,
)
from hearally.models import ValidationError
from hearally.queueing import InMemoryProcessingQueue
from hearally.repositories import PENDING, QUEUED, InMemoryIngestRepository

EVENT_ID = "a" * 64
ACCOUNT = "3f1c2a90-1111-4222-8333-944455556666"
KEY = incoming_key(ACCOUNT, EVENT_ID)


def event_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 4,
        "event_id": EVENT_ID,
        "content_type": "audio/mp4",
        "byte_length": 2048,
        "recorded_at": "2026-09-23T15:04:05.000Z",
        "capture_mode": "phone_call",
        "source": "folder",
    }
    payload.update(overrides)
    return payload


def as_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(payload).encode()


def uploaded(
    *, length: int = 2048, content_type: str = "audio/mp4", checksum: str | None = None
) -> dict[str, dict[str, object]]:
    """The HeadObject response for a recording that went through the POST policy."""
    return {
        KEY: {
            "ContentLength": length,
            "ContentType": content_type,
            "ChecksumSHA256": sha256_base64(EVENT_ID) if checksum is None else checksum,
        }
    }


class NotFound(Exception):
    def __init__(self) -> None:
        super().__init__("not found")
        self.response = {"Error": {"Code": "404"}}


class FakeS3:
    """Stands in for boto3's S3 client: presigned POST and HEAD only."""

    def __init__(self, objects: dict[str, dict[str, object]] | None = None) -> None:
        self.objects = objects or {}
        self.posts: list[dict[str, object]] = []
        self.heads: list[dict[str, object]] = []

    def generate_presigned_post(
        self,
        Bucket: str,
        Key: str,
        Fields: dict[str, str],
        Conditions: list[object],
        ExpiresIn: int,
    ) -> dict[str, object]:
        self.posts.append(
            {
                "bucket": Bucket,
                "key": Key,
                "fields": Fields,
                "conditions": Conditions,
                "expires": ExpiresIn,
            }
        )
        return {
            "url": f"https://{Bucket}.s3.amazonaws.com/",
            "fields": {**Fields, "key": Key, "policy": "signed-policy", "x-amz-signature": "sig"},
        }

    def head_object(self, Bucket: str, Key: str, ChecksumMode: str = "") -> dict[str, object]:
        self.heads.append({"key": Key, "checksum_mode": ChecksumMode})
        if Key not in self.objects:
            raise NotFound()
        return self.objects[Key]


class AudioIngestEventTests(unittest.TestCase):
    def test_accepts_a_valid_event(self) -> None:
        event = AudioIngestEvent.from_json_bytes(as_bytes(event_payload()))
        self.assertEqual(event.capture_mode, "phone_call")

    def test_a_collector_cannot_name_an_account_or_an_object(self) -> None:
        """v4: both come from the paired computer, so claiming them is refused."""
        for field, value in (
            ("account_id", "someone-else"),
            ("object_key", "redacted/someone-elses-clip"),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                AudioIngestEvent.from_json_bytes(as_bytes(event_payload(**{field: value})))

    def test_the_key_is_namespaced_by_account(self) -> None:
        self.assertEqual(incoming_key("acct", EVENT_ID), f"incoming/acct/{EVENT_ID}")

    def test_event_id_must_be_a_sha256_digest(self) -> None:
        for value in ("abc", "A" * 64, "g" * 64, 12345):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                AudioIngestEvent.from_json_bytes(as_bytes(event_payload(event_id=value)))

    def test_wrong_json_types_are_validation_errors_not_crashes(self) -> None:
        """Review F10: a list in an enum field raised TypeError, surfacing as a 503."""
        for field in ("content_type", "capture_mode", "source"):
            for value in (["audio/mp4"], {"a": 1}, 7, None):
                with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                    AudioIngestEvent.from_json_bytes(as_bytes(event_payload(**{field: value})))

    def test_refuses_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            AudioIngestEvent.from_json_bytes(as_bytes(event_payload(transcript="hello")))

    def test_refuses_earlier_schemas(self) -> None:
        for version in (2, 3):
            with self.subTest(version=version), self.assertRaises(ValidationError):
                AudioIngestEvent.from_json_bytes(as_bytes(event_payload(schema_version=version)))

    def test_refuses_a_time_without_a_zone(self) -> None:
        """Quiet hours depend on knowing the account holder's local time."""
        with self.assertRaises(ValidationError):
            AudioIngestEvent.from_json_bytes(
                as_bytes(event_payload(recorded_at="2026-09-23T15:04:05"))
            )

    def test_refuses_unsupported_audio_types(self) -> None:
        with self.assertRaises(ValidationError):
            AudioIngestEvent.from_json_bytes(as_bytes(event_payload(content_type="video/mp4")))

    def test_byte_length_must_be_a_real_positive_integer(self) -> None:
        for value in (0, -1, True, "2048", 10**12):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                AudioIngestEvent.from_json_bytes(as_bytes(event_payload(byte_length=value)))

    def test_processing_message_groups_by_account(self) -> None:
        event = AudioIngestEvent.from_json_bytes(as_bytes(event_payload()))
        message = event.processing_message(ACCOUNT, "device-1")
        self.assertEqual(message["account_id"], ACCOUNT)
        self.assertEqual(message["collector_id"], "device-1")
        self.assertEqual(message["object_key"], KEY)

    def test_refuses_an_oversized_body(self) -> None:
        with self.assertRaises(ValidationError):
            AudioIngestEvent.from_json_bytes(b"{" + b" " * 5000 + b"}")


class UploadServiceTests(unittest.TestCase):
    """Review F03: a presigned PUT bound nothing. The POST policy binds everything."""

    def presign(self, s3: FakeS3, byte_length: int = 2048) -> dict[str, object]:
        return UploadService(s3, "hearally-prod-media").presign(
            UploadRequest(event_id=EVENT_ID, content_type="audio/mp4", byte_length=byte_length),
            ACCOUNT,
        )

    def test_policy_pins_the_key_to_the_event(self) -> None:
        s3 = FakeS3()
        upload = self.presign(s3)
        self.assertEqual(upload["object_key"], KEY)
        self.assertEqual(s3.posts[0]["key"], KEY)

    def test_policy_pins_the_exact_size(self) -> None:
        s3 = FakeS3()
        self.presign(s3, byte_length=4096)
        self.assertIn(["content-length-range", 4096, 4096], s3.posts[0]["conditions"])

    def test_policy_pins_the_sha256_the_event_id_claims(self) -> None:
        """S3 then refuses any bytes whose hash is not the event id."""
        s3 = FakeS3()
        upload = self.presign(s3)
        expected = sha256_base64(EVENT_ID)
        self.assertIn({"x-amz-checksum-sha256": expected}, s3.posts[0]["conditions"])
        self.assertIn({"x-amz-checksum-algorithm": "SHA256"}, s3.posts[0]["conditions"])
        self.assertEqual(upload["upload_fields"]["x-amz-checksum-sha256"], expected)

    def test_policy_pins_the_content_type(self) -> None:
        s3 = FakeS3()
        self.presign(s3)
        self.assertIn({"Content-Type": "audio/mp4"}, s3.posts[0]["conditions"])

    def test_policies_expire_quickly(self) -> None:
        s3 = FakeS3()
        self.presign(s3)
        self.assertLessEqual(s3.posts[0]["expires"], 900)

    def test_checksum_encoding_matches_what_s3_reports(self) -> None:
        # S3's ChecksumSHA256 is the base64 of the raw digest, not of the hex.
        self.assertEqual(sha256_base64("00" * 32), "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")


class AudioIngestServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryIngestRepository()
        self.queue = InMemoryProcessingQueue()

    def service(
        self, objects: dict[str, dict[str, object]] | None = None, queue: object = None
    ) -> AudioIngestService:
        s3 = FakeS3(uploaded() if objects is None else objects)
        return AudioIngestService(
            self.repository, queue or self.queue, s3, "bucket", clock=lambda: 1000
        )

    def event(self, **overrides: object) -> AudioIngestEvent:
        return AudioIngestEvent.from_json_bytes(as_bytes(event_payload(**overrides)))

    def test_queues_an_uploaded_recording_once(self) -> None:
        service = self.service()
        first = service.accept(self.event(), ACCOUNT, "laptop-1")
        second = service.accept(self.event(), ACCOUNT, "laptop-1")

        self.assertFalse(first.duplicate)
        self.assertTrue(second.duplicate)
        self.assertEqual(len(self.queue.messages), 1)
        self.assertEqual(self.repository.status(ACCOUNT, EVENT_ID), QUEUED)

    def test_two_accounts_with_the_same_recording_are_each_accepted(self) -> None:
        """Review F06: identical audio from two accounts collided on one key."""
        other = "9e8d7c6b-0000-4000-8000-000000000000"
        objects = {**uploaded(), incoming_key(other, EVENT_ID): uploaded()[KEY]}
        service = self.service(objects=objects)
        first = service.accept(self.event(), ACCOUNT, "laptop-1")
        second = service.accept(self.event(), other, "laptop-2")
        self.assertFalse(first.duplicate)
        self.assertFalse(second.duplicate)
        self.assertEqual([m["account_id"] for m in self.queue.messages], [ACCOUNT, other])

    def test_audio_under_another_account_is_not_accepted(self) -> None:
        """The HEAD is of this account's key: another account's upload is not found."""
        other_only = {incoming_key("someone-else", EVENT_ID): uploaded()[KEY]}
        with self.assertRaises(UploadNotFoundError):
            self.service(objects=other_only).accept(self.event(), ACCOUNT, "laptop-1")

    def test_verifies_with_checksums_enabled(self) -> None:
        s3 = FakeS3(uploaded())
        AudioIngestService(self.repository, self.queue, s3, "b").accept(self.event(), ACCOUNT, "c")
        self.assertEqual(s3.heads[0]["checksum_mode"], "ENABLED")

    def test_refuses_audio_that_was_never_uploaded(self) -> None:
        with self.assertRaises(UploadNotFoundError):
            self.service(objects={}).accept(self.event(), ACCOUNT, "laptop-1")
        self.assertEqual(self.queue.messages, [])

    def test_refuses_an_upload_whose_size_differs(self) -> None:
        """A truncated upload must not be transcribed as if it were complete."""
        with self.assertRaises(ValidationError):
            self.service().accept(self.event(byte_length=4096), ACCOUNT, "laptop-1")
        self.assertEqual(self.queue.messages, [])

    def test_refuses_an_upload_whose_type_differs(self) -> None:
        with self.assertRaises(ValidationError):
            self.service(objects=uploaded(content_type="audio/wav")).accept(
                self.event(), ACCOUNT, "c"
            )

    def test_refuses_bytes_that_do_not_hash_to_the_event_id(self) -> None:
        """Review F03: the stored checksum proves which bytes are really there."""
        wrong = sha256_base64("b" * 64)
        with self.assertRaises(ValidationError):
            self.service(objects=uploaded(checksum=wrong)).accept(self.event(), ACCOUNT, "c")
        self.assertEqual(self.queue.messages, [])

    def test_refuses_an_object_put_without_the_policy(self) -> None:
        """An object uploaded any other way carries no SHA-256 checksum at all."""
        objects = {KEY: {"ContentLength": 2048, "ContentType": "audio/mp4"}}
        with self.assertRaises(ValidationError):
            self.service(objects=objects).accept(self.event(), ACCOUNT, "c")

    def test_nothing_is_reserved_when_the_object_is_missing(self) -> None:
        with self.assertRaises(UploadNotFoundError):
            self.service(objects={}).accept(self.event(), ACCOUNT, "laptop-1")
        self.assertIsNone(self.repository.status(ACCOUNT, EVENT_ID))

    def test_releases_the_reservation_when_queueing_fails(self) -> None:
        class BrokenQueue:
            def send(self, message: dict[str, object]) -> None:
                raise RuntimeError("queue unavailable")

        with self.assertRaises(RuntimeError):
            self.service(queue=BrokenQueue()).accept(self.event(), ACCOUNT, "laptop-1")

        self.assertIsNone(self.repository.status(ACCOUNT, EVENT_ID))
        self.assertFalse(self.service().accept(self.event(), ACCOUNT, "laptop-1").duplicate)

    def test_a_process_killed_after_reserving_is_recovered_on_retry(self) -> None:
        """Review F07: the reserve succeeded, then the Lambda died before sending.

        No exception handler ran, so the reservation is left pending. The retry
        must queue the recording, not report a duplicate and lose it.
        """
        self.repository.reserve(
            event_id=EVENT_ID,
            account_id=ACCOUNT,
            collector_id="laptop-1",
            received_at=1000,
            ttl_epoch=2000,
        )
        self.assertEqual(self.repository.status(ACCOUNT, EVENT_ID), PENDING)

        result = self.service().accept(self.event(), ACCOUNT, "laptop-1")

        self.assertFalse(result.duplicate)
        self.assertEqual(len(self.queue.messages), 1)
        self.assertEqual(self.repository.status(ACCOUNT, EVENT_ID), QUEUED)

    def test_a_failed_recovery_leaves_the_reservation_for_the_next_retry(self) -> None:
        """A recovering attempt must not release a reservation it did not create."""

        class BrokenQueue:
            def send(self, message: dict[str, object]) -> None:
                raise RuntimeError("queue unavailable")

        self.repository.reserve(
            event_id=EVENT_ID,
            account_id=ACCOUNT,
            collector_id="laptop-1",
            received_at=1000,
            ttl_epoch=2000,
        )
        with self.assertRaises(RuntimeError):
            self.service(queue=BrokenQueue()).accept(self.event(), ACCOUNT, "laptop-1")

        self.assertEqual(self.repository.status(ACCOUNT, EVENT_ID), PENDING)
        self.assertFalse(self.service().accept(self.event(), ACCOUNT, "laptop-1").duplicate)


class RecorderTests(unittest.TestCase):
    """An account holder may have several Plauds; each recording names its recorder."""

    def parse(self, **overrides: object) -> AudioIngestEvent:
        return AudioIngestEvent.from_json_bytes(as_bytes(event_payload(**overrides)))

    def test_recorder_fields_are_optional(self) -> None:
        """A collector from before multiple recorders keeps working."""
        event = self.parse()
        self.assertIsNone(event.recorder_id)
        self.assertIsNone(event.recorder_model)

    def test_accepts_a_named_recorder(self) -> None:
        event = self.parse(recorder_id="kitchen-note-pro", recorder_model="note_pro")
        self.assertEqual(event.recorder_id, "kitchen-note-pro")
        self.assertEqual(event.recorder_model, "note_pro")

    def test_the_processing_job_says_which_recorder(self) -> None:
        message = self.parse(
            recorder_id="pin-1", recorder_model="notepin", capture_mode="in_person"
        ).processing_message(ACCOUNT, "laptop-1")
        self.assertEqual((message["recorder_id"], message["recorder_model"]), ("pin-1", "notepin"))

    def test_rejects_an_unknown_model_or_unsafe_id(self) -> None:
        for bad in (
            {"recorder_model": "iphone"},
            {"recorder_id": "a b/c"},
            {"recorder_id": ["x"]},
            {"recorder_model": ["note"]},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.parse(**bad)

    def test_a_notepin_cannot_have_recorded_a_phone_call(self) -> None:
        """It has no sensor for calls, so the claim is a misconfiguration."""
        for model in ("notepin", "notepin_s"):
            with self.subTest(model=model), self.assertRaises(ValidationError):
                self.parse(recorder_model=model, capture_mode="phone_call")

    def test_a_note_can_record_either_kind(self) -> None:
        for model in ("note", "note_pro"):
            for mode in ("phone_call", "in_person", "unknown"):
                with self.subTest(model=model, mode=mode):
                    self.assertEqual(
                        self.parse(recorder_model=model, capture_mode=mode).capture_mode, mode
                    )


class UnknownRecorderTests(unittest.TestCase):
    def test_an_unknown_model_may_have_recorded_a_call(self) -> None:
        """Refuse only models known to be incapable, never ones we know nothing about."""
        event = AudioIngestEvent.from_json_bytes(
            as_bytes(event_payload(recorder_model="other", capture_mode="phone_call"))
        )
        self.assertEqual(event.capture_mode, "phone_call")


if __name__ == "__main__":
    unittest.main()
