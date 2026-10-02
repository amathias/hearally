"""Ingest reservations: which recordings have been accepted, and how far each got.

A reservation moves through two states:

    pending  written before the job is queued
    queued   written after the queue confirms the send

Two states rather than one flag because a Lambda can die between the two steps
(a timeout, an out-of-memory kill), and an exception handler never runs then. A
single "seen" flag would make the retry look like a duplicate, return success to
the collector, and lose the recording. A reservation still pending tells the
retry that the send was never confirmed, so it sends again.

Reservations are keyed by account and recording together. Two account holders
can hold the same recording, a voicemail forwarded to both, say, and each must
be accepted and processed for its own account.
"""

from __future__ import annotations

from typing import Any, Protocol

PENDING = "pending"
QUEUED = "queued"


class IngestRepository(Protocol):
    def reserve(
        self,
        *,
        event_id: str,
        account_id: str,
        collector_id: str,
        received_at: int,
        ttl_epoch: int,
    ) -> bool:
        """Record a new reservation as pending. False if one already exists."""
        ...

    def status(self, account_id: str, event_id: str) -> str | None: ...

    def mark_queued(self, account_id: str, event_id: str) -> None: ...

    def release(self, account_id: str, event_id: str) -> None: ...


class InMemoryIngestRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def reserve(
        self,
        *,
        event_id: str,
        account_id: str,
        collector_id: str,
        received_at: int,
        ttl_epoch: int,
    ) -> bool:
        if (account_id, event_id) in self.items:
            return False
        self.items[(account_id, event_id)] = {
            "event_id": event_id,
            "account_id": account_id,
            "collector_id": collector_id,
            "received_at": received_at,
            "ttl_epoch": ttl_epoch,
            "status": PENDING,
        }
        return True

    def status(self, account_id: str, event_id: str) -> str | None:
        item = self.items.get((account_id, event_id))
        return None if item is None else str(item["status"])

    def mark_queued(self, account_id: str, event_id: str) -> None:
        if (account_id, event_id) in self.items:
            self.items[(account_id, event_id)]["status"] = QUEUED

    def release(self, account_id: str, event_id: str) -> None:
        self.items.pop((account_id, event_id), None)


class DynamoIngestRepository:
    def __init__(self, table: Any) -> None:
        self._table = table

    @staticmethod
    def _key(account_id: str, event_id: str) -> dict[str, str]:
        return {"pk": f"INGEST#{account_id}#{event_id}", "sk": "META"}

    def reserve(
        self,
        *,
        event_id: str,
        account_id: str,
        collector_id: str,
        received_at: int,
        ttl_epoch: int,
    ) -> bool:
        try:
            self._table.put_item(
                Item={
                    **self._key(account_id, event_id),
                    "account_id": account_id,
                    "collector_id": collector_id,
                    "received_at": received_at,
                    "ttl_epoch": ttl_epoch,
                    "status": PENDING,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
            return True
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def status(self, account_id: str, event_id: str) -> str | None:
        # Strongly consistent: an eventually consistent read could miss the
        # reservation that just made reserve() return False.
        item = self._table.get_item(Key=self._key(account_id, event_id), ConsistentRead=True).get(
            "Item"
        )
        if item is None:
            return None
        return str(item.get("status", QUEUED))

    def mark_queued(self, account_id: str, event_id: str) -> None:
        # attribute_exists stops this recreating a reservation that was released
        # in the meantime: update_item would otherwise upsert a half-empty item.
        try:
            self._table.update_item(
                Key=self._key(account_id, event_id),
                UpdateExpression="SET #status = :queued",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":queued": QUEUED},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return

    def release(self, account_id: str, event_id: str) -> None:
        self._table.delete_item(Key=self._key(account_id, event_id))
