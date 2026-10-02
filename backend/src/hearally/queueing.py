from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol


class ProcessingQueue(Protocol):
    def send(self, message: dict[str, Any]) -> None: ...


class InMemoryProcessingQueue:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    def send(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


class SqsProcessingQueue:
    def __init__(self, client: Any, queue_url: str) -> None:
        self._client = client
        self._queue_url = queue_url

    def send(self, message: dict[str, Any]) -> None:
        self._client.send_message(
            QueueUrl=self._queue_url,
            MessageBody=json.dumps(message, separators=(",", ":"), ensure_ascii=False),
            # One group per account keeps each person's recordings in order
            # without serialising everyone behind one queue.
            MessageGroupId=message["account_id"],
            # Per account and recording: the same recording held by two
            # accounts is two jobs. Hashed to stay inside SQS's 128 characters.
            MessageDeduplicationId=hashlib.sha256(
                f"{message['account_id']}:{message['event_id']}".encode()
            ).hexdigest(),
        )
