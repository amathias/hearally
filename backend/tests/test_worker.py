"""The transcription worker: dispatching batches and collecting results."""

from __future__ import annotations

import io
import json
import unittest
from typing import Any

from test_judgement import candidate

from hearally.audio import incoming_key
from hearally.judgement import CALL_TODAY, DAILY, PRIORITY, Decision
from hearally.transcription import redacted_key, transcript_key
from hearally.worker import Batch, Collector, Dispatcher, recording_sort_key

ACCOUNT = "3f1c2a90-1111-4222-8333-944455556666"
EVENT = "e" * 64
OTHER_EVENT = "f" * 64
NOW = 1_790_800_000


def message(event: str = EVENT) -> dict[str, Any]:
    return {
        "account_id": ACCOUNT,
        "event_id": event,
        "recorded_at": "2026-09-30T19:39:00+00:00",
        "capture_mode": "unknown",
        "source": "plaud",
    }


class Queue:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self.messages = [(f"r{i}", m) for i, m in enumerate(messages)]
        self.deleted: list[str] = []
        self.received = 0

    def receive(self, limit: int) -> list[tuple[str, dict[str, Any]]]:
        self.received += 1
        return self.messages[:limit]

    def delete(self, receipts: Any) -> None:
        self.deleted += list(receipts)


class Batches:
    def __init__(self) -> None:
        self.items: dict[str, Batch] = {}

    def open_batches(self) -> list[Batch]:
        return list(self.items.values())

    def save(self, batch: Batch) -> None:
        self.items[batch.job] = batch

    def finish(self, job: str) -> None:
        self.items.pop(job)


class Jobs:
    def __init__(self) -> None:
        self.launched: list[str] = []
        self.states: dict[str, str] = {}
        self.fail_launch = False
        self.key_ready = True

    def ready(self) -> bool:
        return self.key_ready

    def launch(self, manifest_url: str) -> str:
        if self.fail_launch:
            raise RuntimeError("Nebius unreachable")
        self.launched.append(manifest_url)
        return f"aijob-{len(self.launched)}"

    def state(self, job_id: str) -> str:
        return self.states.get(job_id, "RUNNING")


class S3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def generate_presigned_url(self, op: str, Params: dict[str, str], ExpiresIn: int) -> str:  # noqa: N803
        return f"https://bucket.s3.amazonaws.com/{Params['Key']}?{op}"

    def put_object(self, Bucket: str, Key: str, Body: bytes, **_: Any) -> None:  # noqa: N803
        self.objects[Key] = Body

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if Key not in self.objects:
            raise RuntimeError("An error occurred (NoSuchKey)")
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        if Key not in self.objects:
            raise RuntimeError("An error occurred (404) when calling HeadObject: Not Found")
        return {}

    def delete_object(self, Bucket: str, Key: str) -> None:  # noqa: N803
        self.objects.pop(Key, None)


class Account:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.budget: set[str] = set()
        self.status: dict[str, dict[str, Any]] = {}

    def priority_available(self, account_id: str, day: str) -> bool:
        return day not in self.budget

    def claim_priority(self, account_id: str, day: str, now: int) -> bool:
        if day in self.budget:
            return False
        self.budget.add(day)
        return True

    def put(self, account_id: str, sort_key: str, item: dict[str, Any]) -> None:
        self.items[sort_key] = item

    def set_recording_status(self, account_id: str, sort_key: str, fields: dict[str, Any]) -> None:
        self.status.setdefault(sort_key, {}).update(fields)


def transcript(event: str = EVENT) -> bytes:
    words = [{"w": "warrant", "start": 0.1, "end": 0.5, "c": 0.9}] * 10
    return json.dumps(
        {
            "version": 1,
            "id": event,
            "model": "parakeet",
            "duration_s": 60,
            "confidence_available": True,
            "segments": [
                {
                    "id": 1,
                    "start": 0,
                    "end": 5,
                    "text": "Pay with gift cards.",
                    "confidence": 0.5,
                    "words": words,
                }
            ],
        }
    ).encode()


def pressure(grade: str = PRIORITY) -> Decision:
    return Decision(
        candidate=candidate(category="scam_pressure", segment_ids=[1], scam_signals=["urgency"]),
        outcome="send",
        grade=grade,
        quotable=True,
        clip_allowed=True,
    )


class DispatcherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.queue = Queue([message(), message(OTHER_EVENT), message()])  # one repeated
        self.batches, self.jobs, self.s3 = Batches(), Jobs(), S3()

    def dispatcher(self) -> Dispatcher:
        return Dispatcher(self.queue, self.batches, self.jobs, self.s3, "media", clock=lambda: NOW)

    def test_waiting_recordings_become_one_batch_and_one_job(self) -> None:
        self.assertEqual(self.dispatcher().run(), {"dispatched": 2})
        [batch] = self.batches.open_batches()
        self.assertEqual((batch.nebius_job, batch.attempts), ("aijob-1", 1))
        self.assertEqual({r["event_id"] for r in batch.recordings}, {EVENT, OTHER_EVENT})
        manifest = json.loads(self.s3.objects[f"jobs/{batch.job}/manifest.json"])
        self.assertIn("redacted_url", manifest["recordings"][0])
        self.assertEqual(self.queue.deleted, ["r0", "r1", "r2"])

    def test_messages_stay_queued_if_the_job_cannot_start(self) -> None:
        self.jobs.fail_launch = True
        with self.assertRaises(RuntimeError):
            self.dispatcher().run()
        self.assertEqual(self.queue.deleted, [])
        self.assertEqual(self.batches.open_batches(), [])

    def test_without_a_key_nothing_leaves_the_queue(self) -> None:
        self.jobs.key_ready = False
        with self.assertRaises(RuntimeError):
            self.dispatcher().run()
        self.assertEqual((self.queue.received, self.queue.deleted), (0, []))

    def test_one_batch_at_a_time(self) -> None:
        self.dispatcher().run()
        self.assertEqual(self.dispatcher().run(), {"dispatched": 0, "waiting_for_batch": 1})
        self.assertEqual(self.queue.received, 1)

    def test_an_empty_queue_starts_nothing(self) -> None:
        self.queue.messages = []
        self.assertEqual(self.dispatcher().run(), {"dispatched": 0})
        self.assertEqual(self.jobs.launched, [])


class CollectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.batches, self.jobs, self.s3, self.account = Batches(), Jobs(), S3(), Account()
        self.decisions: list[Decision] = [pressure()]
        Dispatcher(
            Queue([message()]), self.batches, self.jobs, self.s3, "media", clock=lambda: NOW
        ).run()
        self.s3.objects[incoming_key(ACCOUNT, EVENT)] = b"original audio"
        self.s3.objects[transcript_key(ACCOUNT, EVENT)] = transcript()
        self.s3.objects[redacted_key(ACCOUNT, EVENT)] = b"silenced copy"
        self.judged_with: list[bool] = []

    def collector(self, now: int = NOW + 1800) -> Collector:
        def judge(segments: Any, *, priority_available: bool) -> list[Decision]:
            self.judged_with.append(priority_available)
            return list(self.decisions)

        return Collector(
            self.batches, self.jobs, self.s3, "media", self.account, judge, clock=lambda: now
        )

    def test_a_running_job_is_left_alone(self) -> None:
        self.assertEqual(self.collector().run()["transcribed"], 0)
        self.assertEqual(len(self.batches.open_batches()), 1)

    def test_a_finished_job_becomes_items_and_the_audio_is_cleaned_up(self) -> None:
        self.jobs.states["aijob-1"] = "COMPLETED"
        totals = self.collector().run()
        self.assertEqual((totals["transcribed"], totals["items"]), (1, 1))
        [item_key] = [k for k in self.account.items if k.startswith("ITEM#")]
        self.assertEqual(self.account.items[item_key]["grade"], PRIORITY)
        # Kept 90 days, as the privacy policy says.
        self.assertEqual(self.account.items[item_key]["ttl_epoch"], NOW + 1800 + 90 * 86400)
        self.assertTrue(any(k.startswith("ALERT#") for k in self.account.items))
        # The original goes once the silenced copy exists; the transcript goes.
        self.assertNotIn(incoming_key(ACCOUNT, EVENT), self.s3.objects)
        self.assertNotIn(transcript_key(ACCOUNT, EVENT), self.s3.objects)
        self.assertIn(redacted_key(ACCOUNT, EVENT), self.s3.objects)
        status = self.account.status[recording_sort_key(message())]
        self.assertEqual((status["status"], status["items"]), ("transcribed", 1))
        self.assertEqual(self.batches.open_batches(), [])
        self.assertFalse(any(k.startswith("jobs/") for k in self.s3.objects))

    def test_without_the_silenced_copy_the_original_is_not_deleted_early(self) -> None:
        del self.s3.objects[redacted_key(ACCOUNT, EVENT)]
        self.jobs.states["aijob-1"] = "COMPLETED"
        self.collector().run()
        self.assertIn(incoming_key(ACCOUNT, EVENT), self.s3.objects)
        self.assertNotIn(transcript_key(ACCOUNT, EVENT), self.s3.objects)

    def test_the_daily_priority_budget_is_enforced_in_code(self) -> None:
        self.account.budget.add("2026-09-30")
        self.jobs.states["aijob-1"] = "COMPLETED"
        self.collector().run()
        [item] = [v for k, v in self.account.items.items() if k.startswith("ITEM#")]
        self.assertEqual(item["grade"], CALL_TODAY)
        self.assertEqual(self.judged_with, [False])
        self.assertFalse(any(k.startswith("ALERT#") for k in self.account.items))

    def test_held_back_moments_are_stored_apart(self) -> None:
        self.decisions = [
            Decision(candidate(), "held_back", None, False, False, reasons=("family_conflict",)),
            pressure(DAILY),
        ]
        self.jobs.states["aijob-1"] = "COMPLETED"
        self.collector().run()
        [held] = [v for k, v in self.account.items.items() if k.startswith("HELDBACK#")]
        self.assertEqual(held["reason"], "family_conflict")
        self.assertEqual(self.account.status[recording_sort_key(message())]["held_back"], 1)

    def test_a_failed_job_is_retried_once_then_its_recordings_marked_failed(self) -> None:
        self.jobs.states["aijob-1"] = "FAILED"
        self.assertEqual(self.collector().run()["relaunched"], 1)
        [batch] = self.batches.open_batches()
        self.assertEqual((batch.nebius_job, batch.attempts), ("aijob-2", 2))
        self.jobs.states["aijob-2"] = "ERROR"
        self.assertEqual(self.collector().run()["failed"], 1)
        self.assertEqual(self.account.status[recording_sort_key(message())]["status"], "failed")
        self.assertEqual(self.batches.open_batches(), [])

    def test_a_missing_transcript_fails_that_recording_only(self) -> None:
        del self.s3.objects[transcript_key(ACCOUNT, EVENT)]
        self.jobs.states["aijob-1"] = "COMPLETED"
        self.assertEqual(self.collector().run()["failed"], 1)
        self.assertEqual(self.account.status[recording_sort_key(message())]["status"], "failed")

    def test_short_of_time_it_stops_and_resumes_next_run(self) -> None:
        self.jobs.states["aijob-1"] = "COMPLETED"
        self.collector().run(time_left=lambda: 30)
        self.assertEqual(self.judged_with, [])
        self.assertEqual(len(self.batches.open_batches()), 1)
        self.collector().run()
        self.assertEqual(self.judged_with, [True])

    def test_a_job_stuck_for_hours_counts_as_failed(self) -> None:
        self.collector(now=NOW + 5 * 3600).run()
        [batch] = self.batches.open_batches()
        self.assertEqual(batch.attempts, 2)


if __name__ == "__main__":
    unittest.main()
