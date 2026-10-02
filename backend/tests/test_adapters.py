"""The production adapters, driven through fakes of the boto3 surface they use.

Review F14: every other test used the in-memory repository and queue, so the
code that actually runs against DynamoDB and SQS was never exercised. These
tests pin the calls each adapter makes: the conditions, the keys, and the
deduplication fields that the crash recovery in audio_services depends on.
"""

from __future__ import annotations

import json
import unittest
from typing import Any

from hearally.queueing import SqsProcessingQueue
from hearally.repositories import PENDING, QUEUED, DynamoIngestRepository


class ConditionalCheckFailedException(Exception):
    pass


class _Exceptions:
    ConditionalCheckFailedException = ConditionalCheckFailedException


class _Client:
    exceptions = _Exceptions()


class _Meta:
    client = _Client()


class FakeTable:
    """Enough of a DynamoDB Table to honour the two conditions the adapter uses."""

    meta = _Meta()

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.reads: list[dict[str, Any]] = []

    @staticmethod
    def _k(key: dict[str, str]) -> tuple[str, str]:
        return key["pk"], key["sk"]

    def put_item(self, Item: dict[str, Any], ConditionExpression: str) -> None:
        assert ConditionExpression == "attribute_not_exists(pk)"
        key = self._k(Item)
        if key in self.items:
            raise ConditionalCheckFailedException()
        self.items[key] = dict(Item)

    def get_item(self, Key: dict[str, str], ConsistentRead: bool = False) -> dict[str, Any]:
        self.reads.append({"key": Key, "consistent": ConsistentRead})
        item = self.items.get(self._k(Key))
        return {} if item is None else {"Item": item}

    def update_item(
        self,
        Key: dict[str, str],
        UpdateExpression: str,
        ConditionExpression: str,
        ExpressionAttributeNames: dict[str, str],
        ExpressionAttributeValues: dict[str, Any],
    ) -> None:
        assert ConditionExpression == "attribute_exists(pk)"
        key = self._k(Key)
        if key not in self.items:
            raise ConditionalCheckFailedException()
        self.items[key][ExpressionAttributeNames["#status"]] = ExpressionAttributeValues[":queued"]

    def delete_item(self, Key: dict[str, str]) -> None:
        self.items.pop(self._k(Key), None)


def reserve(repository: DynamoIngestRepository, event_id: str = "e1") -> bool:
    return repository.reserve(
        event_id=event_id, account_id="acct", collector_id="col", received_at=1, ttl_epoch=2
    )


class DynamoIngestRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = FakeTable()
        self.repository = DynamoIngestRepository(self.table)

    def test_a_reservation_starts_pending(self) -> None:
        self.assertTrue(reserve(self.repository))
        self.assertEqual(self.repository.status("acct", "e1"), PENDING)
        self.assertEqual(self.table.items[("INGEST#acct#e1", "META")]["account_id"], "acct")

    def test_a_second_reservation_is_refused(self) -> None:
        reserve(self.repository)
        self.assertFalse(reserve(self.repository))

    def test_the_same_recording_for_another_account_is_its_own_reservation(self) -> None:
        """Review F06: reservations were keyed by content hash alone."""
        reserve(self.repository)
        self.assertTrue(
            self.repository.reserve(
                event_id="e1", account_id="other", collector_id="c", received_at=1, ttl_epoch=2
            )
        )

    def test_status_is_read_consistently(self) -> None:
        """An eventually consistent read could miss the item that just blocked reserve()."""
        reserve(self.repository)
        self.repository.status("acct", "e1")
        self.assertTrue(self.table.reads[-1]["consistent"])

    def test_mark_queued_moves_the_status_on(self) -> None:
        reserve(self.repository)
        self.repository.mark_queued("acct", "e1")
        self.assertEqual(self.repository.status("acct", "e1"), QUEUED)

    def test_mark_queued_never_recreates_a_released_reservation(self) -> None:
        """update_item upserts by default; the condition stops a half-empty item appearing."""
        reserve(self.repository)
        self.repository.release("acct", "e1")
        self.repository.mark_queued("acct", "e1")
        self.assertIsNone(self.repository.status("acct", "e1"))

    def test_an_item_from_before_status_existed_reads_as_queued(self) -> None:
        self.table.items[("INGEST#acct#old", "META")] = {"pk": "INGEST#acct#old", "sk": "META"}
        self.assertEqual(self.repository.status("acct", "old"), QUEUED)

    def test_status_of_an_unknown_event_is_none(self) -> None:
        self.assertIsNone(self.repository.status("acct", "missing"))


class FakeSqs:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_message(self, **kwargs: Any) -> None:
        self.sent.append(kwargs)


class SqsProcessingQueueTests(unittest.TestCase):
    def test_deduplicates_per_account_and_recording(self) -> None:
        """Crash recovery re-sends and the FIFO queue collapses the repeat, but the
        same recording held by another account is a job of its own."""
        sqs = FakeSqs()
        queue = SqsProcessingQueue(sqs, "url")
        queue.send({"event_id": "e1", "account_id": "acct"})
        queue.send({"event_id": "e1", "account_id": "acct"})
        queue.send({"event_id": "e1", "account_id": "other"})
        ids = [m["MessageDeduplicationId"] for m in sqs.sent]
        self.assertEqual(ids[0], ids[1])
        self.assertNotEqual(ids[0], ids[2])
        self.assertLessEqual(len(ids[0]), 128)

    def test_groups_by_account(self) -> None:
        sqs = FakeSqs()
        SqsProcessingQueue(sqs, "url").send({"event_id": "e1", "account_id": "acct"})
        self.assertEqual(sqs.sent[0]["MessageGroupId"], "acct")

    def test_body_is_compact_json(self) -> None:
        sqs = FakeSqs()
        SqsProcessingQueue(sqs, "url").send({"event_id": "e1", "account_id": "acct"})
        self.assertEqual(json.loads(sqs.sent[0]["MessageBody"])["event_id"], "e1")


if __name__ == "__main__":
    unittest.main()
