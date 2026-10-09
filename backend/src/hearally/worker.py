"""The transcription worker: from the processing queue to items on the dashboard.

Two scheduled steps, because a GPU job takes longer than a Lambda can wait
(finding a GPU alone took 20 to 35 minutes on 2026-09-25):

``Dispatcher`` (every 30 minutes). If no batch is already running, it takes
what is waiting on the processing queue, writes one batch's manifest
(transcription.prepare_batch), starts the Parakeet job on Nebius, records the
batch, and only then deletes the queue messages. If starting the job fails,
the messages stay queued and come back on the next run.

``Collector`` (every 10 minutes). For each open batch whose job has finished,
recording by recording: read the transcript, judge it (judgement.judge),
store what it decided, delete the unredacted original once the job's silenced
copy exists, delete the transcript, and mark the recording transcribed. A
failed job is retried once with fresh links; after that its recordings are
marked failed and their audio is left to the lifecycle rule.

Product rules live in code and conditional writes, never in prompts
(AGENTS.md): the one-a-day priority budget is a conditional put on
``ALERTBUDGET#<date>``, and what allies said was nothing (learning.py) is
applied here, after the model has spoken. Nothing here logs a transcript, a summary, a link or
anything said; the run reports counts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from hearally.audio import incoming_key
from hearally.judgement import CALL_TODAY, DAILY, NEVER_HELD, PRIORITY, Decision
from hearally.learning import PREFIX as LEARNED_PREFIX
from hearally.learning import TEACHES, check
from hearally.topics import SORT_KEY as TOPICS_KEY
from hearally.topics import mentioned
from hearally.transcription import (
    Recording,
    parse_transcript,
    prepare_batch,
    redacted_key,
    transcript_key,
)

LOGGER = logging.getLogger(__name__)

MAX_PER_BATCH = 50
# How long summaries, held-back moments and heads-ups are kept (the privacy
# policy, /privacy/). DynamoDB's TTL deletes them; it can lag by a day or two.
ITEM_RETENTION_SECONDS = 90 * 86400
# One retry with fresh links; a job that fails twice is not tried again.
MAX_ATTEMPTS = 2
# A job still not finished after this is treated as failed: the job itself
# is cancelled by its own timeout long before (transcriber JOB_TIMEOUT).
STALE_SECONDS = 4 * 60 * 60
RUNNING = frozenset({"CREATING", "PROVISIONING", "PENDING", "STARTING", "RUNNING", "UNKNOWN"})
FAILED = frozenset({"FAILED", "ERROR", "CANCELLED"})


class Jobs(Protocol):
    """The GPU job on Nebius, behind an interface (AGENTS.md: providers
    behind interfaces). See hearally/nebius_jobs.py."""

    def ready(self) -> bool: ...
    def launch(self, manifest_url: str) -> str: ...
    def state(self, job_id: str) -> str: ...


class Judge(Protocol):
    def __call__(self, segments: Sequence[Any], *, priority_available: bool) -> list[Decision]: ...


@dataclass
class Batch:
    job: str
    nebius_job: str
    created_epoch: int
    attempts: int
    recordings: list[dict[str, Any]]
    done: list[str]

    def to_item(self) -> dict[str, Any]:
        return {
            "pk": "WORKER",
            "sk": f"BATCH#{self.job}",
            "job": self.job,
            "nebius_job": self.nebius_job,
            "created_epoch": self.created_epoch,
            "attempts": self.attempts,
            "recordings": self.recordings,
            "done": self.done,
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> Batch:
        return cls(
            job=str(item["job"]),
            nebius_job=str(item["nebius_job"]),
            created_epoch=int(item["created_epoch"]),
            attempts=int(item["attempts"]),
            recordings=[dict(r) for r in item.get("recordings", [])],
            done=[str(d) for d in item.get("done", [])],
        )


class BatchStore(Protocol):
    def open_batches(self) -> list[Batch]: ...
    def save(self, batch: Batch) -> None: ...
    def finish(self, job: str) -> None: ...


class DynamoBatchStore:
    """``WORKER`` / ``BATCH#<job>``: one item per open batch, deleted when done."""

    def __init__(self, table: Any) -> None:
        self._table = table

    def open_batches(self) -> list[Batch]:
        page = self._table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={":pk": "WORKER", ":sk": "BATCH#"},
            ConsistentRead=True,
        )
        return [Batch.from_item(i) for i in page.get("Items", [])]

    def save(self, batch: Batch) -> None:
        self._table.put_item(Item=batch.to_item())

    def finish(self, job: str) -> None:
        self._table.delete_item(Key={"pk": "WORKER", "sk": f"BATCH#{job}"})


class Account(Protocol):
    """What the worker writes under the account holder's ``USER#`` partition."""

    def claim_priority(self, account_id: str, day: str, now: int) -> bool: ...
    def priority_available(self, account_id: str, day: str) -> bool: ...
    def put(self, account_id: str, sort_key: str, item: dict[str, Any]) -> None: ...
    def set_recording_status(
        self, account_id: str, sort_key: str, fields: dict[str, Any]
    ) -> None: ...
    def learned(self, account_id: str) -> list[dict[str, Any]]: ...
    def topics(self, account_id: str) -> list[str]: ...
    def note_learned(self, account_id: str, pattern_id: str, at: str) -> None: ...


class DynamoAccount:
    def __init__(self, table: Any) -> None:
        self._table = table

    def priority_available(self, account_id: str, day: str) -> bool:
        item = self._table.get_item(
            Key={"pk": f"USER#{account_id}", "sk": f"ALERTBUDGET#{day}"}, ConsistentRead=True
        ).get("Item")
        return item is None

    def claim_priority(self, account_id: str, day: str, now: int) -> bool:
        """The one-a-day priority budget, as a conditional write."""
        try:
            self._table.put_item(
                Item={
                    "pk": f"USER#{account_id}",
                    "sk": f"ALERTBUDGET#{day}",
                    "claimed_epoch": now,
                    "ttl_epoch": now + 3 * 86400,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def put(self, account_id: str, sort_key: str, item: dict[str, Any]) -> None:
        self._table.put_item(Item={"pk": f"USER#{account_id}", "sk": sort_key, **item})

    def learned(self, account_id: str) -> list[dict[str, Any]]:
        page = self._table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={":pk": f"USER#{account_id}", ":sk": LEARNED_PREFIX},
        )
        return list(page.get("Items", []))

    def topics(self, account_id: str) -> list[str]:
        item = self._table.get_item(
            Key={"pk": f"USER#{account_id}", "sk": TOPICS_KEY}, ConsistentRead=True
        ).get("Item")
        return [str(t) for t in (item or {}).get("topics", [])]

    def note_learned(self, account_id: str, pattern_id: str, at: str) -> None:
        """Count a use. Never renews the pattern, and never recreates one the
        account holder has just removed."""
        try:
            self._table.update_item(
                Key={"pk": f"USER#{account_id}", "sk": f"{LEARNED_PREFIX}{pattern_id}"},
                UpdateExpression="SET matched = if_not_exists(matched, :zero) + :one, "
                "last_matched_at = :at",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues={":zero": 0, ":one": 1, ":at": at},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return

    def set_recording_status(self, account_id: str, sort_key: str, fields: dict[str, Any]) -> None:
        # Only a recording the dashboard already lists; never creates one.
        try:
            self._table.update_item(
                Key={"pk": f"USER#{account_id}", "sk": sort_key},
                UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={f"#{k}": k for k in fields},
                ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return


class Queue(Protocol):
    def receive(self, limit: int) -> list[tuple[str, dict[str, Any]]]: ...
    def delete(self, receipts: Sequence[str]) -> None: ...


class SqsWorkQueue:
    def __init__(self, client: Any, url: str) -> None:
        self._client = client
        self._url = url

    def receive(self, limit: int) -> list[tuple[str, dict[str, Any]]]:
        found: list[tuple[str, dict[str, Any]]] = []
        while len(found) < limit:
            page = self._client.receive_message(
                QueueUrl=self._url,
                MaxNumberOfMessages=min(10, limit - len(found)),
                WaitTimeSeconds=1,
            ).get("Messages", [])
            if not page:
                break
            for m in page:
                try:
                    found.append((m["ReceiptHandle"], json.loads(m["Body"])))
                except (KeyError, json.JSONDecodeError):
                    # Unreadable: leave it, and the dead-letter queue takes it.
                    continue
        return found

    def delete(self, receipts: Sequence[str]) -> None:
        for i in range(0, len(receipts), 10):
            self._client.delete_message_batch(
                QueueUrl=self._url,
                Entries=[
                    {"Id": str(n), "ReceiptHandle": r} for n, r in enumerate(receipts[i : i + 10])
                ],
            )


def _recording_entry(message: dict[str, Any]) -> dict[str, Any] | None:
    account, event = message.get("account_id"), message.get("event_id")
    if not isinstance(account, str) or not isinstance(event, str):
        return None
    return {
        "account_id": account,
        "event_id": event,
        "recorded_at": str(message.get("recorded_at", "")),
        "capture_mode": str(message.get("capture_mode", "unknown")),
        "source": str(message.get("source", "")),
    }


def recording_sort_key(entry: dict[str, Any]) -> str:
    """The dashboard's RECORDING# key for a queued recording (pairing.recording_key)."""
    return f"RECORDING#{entry['recorded_at']}#{entry['event_id'][:16]}"


class Dispatcher:
    def __init__(
        self,
        queue: Queue,
        batches: BatchStore,
        jobs: Jobs,
        s3: Any,
        bucket: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._queue = queue
        self._batches = batches
        self._jobs = jobs
        self._s3 = s3
        self._bucket = bucket
        self._clock = clock

    def run(self) -> dict[str, int]:
        # Checked before anything leaves the queue: a missing key must not
        # use up a recording's receives and send it to the dead-letter queue
        # (it did, 2026-09-30). Raising still trips the error alarm.
        if not self._jobs.ready():
            raise RuntimeError("Nebius worker key is not configured")
        if self._batches.open_batches():
            # One batch at a time: a second GPU would only cost more.
            return {"dispatched": 0, "waiting_for_batch": 1}
        received = self._queue.receive(MAX_PER_BATCH)
        if not received:
            return {"dispatched": 0}
        entries: dict[tuple[str, str], dict[str, Any]] = {}
        for _receipt, message in received:
            entry = _recording_entry(message)
            if entry:
                entries[(entry["account_id"], entry["event_id"])] = entry
        now = int(self._clock())
        job = f"batch-{now}"
        recordings = list(entries.values())
        if recordings:
            prepared = prepare_batch(
                self._s3,
                self._bucket,
                job,
                [Recording(r["account_id"], r["event_id"]) for r in recordings],
            )
            # If this fails, nothing is deleted and the messages come back.
            nebius_job = self._jobs.launch(prepared.manifest_url)
            self._batches.save(Batch(job, nebius_job, now, 1, recordings, done=[]))
        self._queue.delete([receipt for receipt, _ in received])
        return {"dispatched": len(recordings)}


class Collector:
    def __init__(
        self,
        batches: BatchStore,
        jobs: Jobs,
        s3: Any,
        bucket: str,
        account: Account,
        judge: Judge,
        clock: Callable[[], float] = time.time,
        plan: Callable[[str, list[dict[str, Any]], float], int] | None = None,
    ) -> None:
        self._plan = plan
        self._batches = batches
        self._jobs = jobs
        self._s3 = s3
        self._bucket = bucket
        self._account = account
        self._judge = judge
        self._clock = clock

    def run(self, time_left: Callable[[], float] = lambda: 1e9) -> dict[str, int]:
        totals = {"batches": 0, "transcribed": 0, "failed": 0, "items": 0, "relaunched": 0}
        for batch in self._batches.open_batches():
            totals["batches"] += 1
            state = self._jobs.state(batch.nebius_job)
            stale = self._clock() - batch.created_epoch > STALE_SECONDS
            if state in RUNNING and not stale:
                continue
            if state == "COMPLETED":
                for entry in batch.recordings:
                    if entry["event_id"] in batch.done:
                        continue
                    if time_left() < 120:
                        # Judging can take a minute or two; finish next run.
                        self._batches.save(batch)
                        return totals
                    ok, items = self._recording(entry)
                    totals["transcribed" if ok else "failed"] += 1
                    totals["items"] += items
                    batch.done.append(entry["event_id"])
                    self._batches.save(batch)
                self._clean_job(batch.job)
                self._batches.finish(batch.job)
            elif batch.attempts < MAX_ATTEMPTS:
                self._relaunch(batch)
                totals["relaunched"] += 1
            else:
                for entry in batch.recordings:
                    if entry["event_id"] not in batch.done:
                        self._account.set_recording_status(
                            entry["account_id"], recording_sort_key(entry), {"status": "failed"}
                        )
                        totals["failed"] += 1
                self._clean_job(batch.job)
                self._batches.finish(batch.job)
        return totals

    def _relaunch(self, batch: Batch) -> None:
        # Fresh links: the first ones may have expired while the job failed.
        prepared = prepare_batch(
            self._s3,
            self._bucket,
            batch.job,
            [Recording(r["account_id"], r["event_id"]) for r in batch.recordings],
        )
        batch.nebius_job = self._jobs.launch(prepared.manifest_url)
        batch.attempts += 1
        batch.created_epoch = int(self._clock())
        self._batches.save(batch)

    def _clean_job(self, job: str) -> None:
        for key in (f"jobs/{job}/manifest.json", f"jobs/{job}/report.json"):
            self._s3.delete_object(Bucket=self._bucket, Key=key)

    def _exists(self, key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
        except Exception as exc:
            if "404" in str(exc) or "Not Found" in str(exc) or "NoSuchKey" in str(exc):
                return False
            raise
        return True

    def _recording(self, entry: dict[str, Any]) -> tuple[bool, int]:
        account, event = entry["account_id"], entry["event_id"]
        key = transcript_key(account, event)
        try:
            body = self._s3.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        except Exception as exc:
            if "NoSuchKey" not in str(exc) and "404" not in str(exc):
                raise
            # The job did not transcribe this one (its report says why).
            self._account.set_recording_status(
                account, recording_sort_key(entry), {"status": "failed"}
            )
            return False, 0
        segments, _ = parse_transcript(body, expected_id=event)
        day = datetime.fromtimestamp(self._clock(), UTC).date().isoformat()
        decisions = self._judge(
            segments, priority_available=self._account.priority_available(account, day)
        )
        stored = self._store(account, entry, decisions, day, segments)

        # The silenced copy is what Hearally keeps; the original goes as soon
        # as it exists (AGENTS.md). Without it, the original is left to the
        # lifecycle rule rather than kept unredacted any longer than that.
        redacted = self._exists(redacted_key(account, event))
        if redacted:
            self._s3.delete_object(Bucket=self._bucket, Key=incoming_key(account, event))
        # Raw transcripts are never kept.
        self._s3.delete_object(Bucket=self._bucket, Key=key)
        status: dict[str, Any] = {
            "status": "transcribed",
            "items": stored["items"],
            "held_back": stored["held_back"],
            "transcribed_at": datetime.fromtimestamp(self._clock(), UTC)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        if redacted:
            # So the account holder can listen to the silenced copy
            # (handlers/recordings.py); the dashboard never shows event_id.
            status.update({"redacted": True, "event_id": event})
        self._account.set_recording_status(account, recording_sort_key(entry), status)
        return True, stored["items"]

    def _store(
        self,
        account: str,
        entry: dict[str, Any],
        decisions: list[Decision],
        day: str,
        segments: Sequence[Any] = (),
    ) -> dict[str, int]:
        counts = {"items": 0, "held_back": 0}
        stored: list[dict[str, Any]] = []
        now = int(self._clock())
        expires = now + ITEM_RETENTION_SECONDS
        spoken_at = entry["recorded_at"]
        patterns = self._account.learned(account) if decisions else []
        topics = self._account.topics(account) if decisions else []
        said = {s.id: s.text for s in segments}
        for n, d in enumerate(decisions):
            item_id = hashlib.sha256(f"{entry['event_id']}:{n}".encode()).hexdigest()[:16]
            held_reason = d.reasons[0] if d.outcome == "held_back" and d.reasons else ""
            # Topics that never leave (topics.py): the account holder's own
            # list, checked against the summary and what was said.
            if d.outcome != "held_back" and topics and d.candidate.category not in NEVER_HELD:
                texts = [d.candidate.summary] + [said.get(i, "") for i in d.candidate.segment_ids]
                if mentioned(texts, topics):
                    held_reason = "your_topic"
            if d.outcome == "held_back" or held_reason:
                # Kept for the account holder alone, who can send it anyway.
                self._account.put(
                    account,
                    f"HELDBACK#{spoken_at}#{item_id}",
                    {
                        "id": item_id,
                        "spoken_at": spoken_at,
                        "reason": held_reason,
                        "category": d.candidate.category,
                        "summary": d.candidate.summary,
                        "ttl_epoch": expires,
                    },
                )
                counts["held_back"] += 1
                continue
            grade = d.grade
            signals = sorted(d.candidate.scam_signals)
            # What allies said was nothing, before the budget: an item that
            # goes to the daily list must not spend today's heads-up.
            learned = check(patterns, d.candidate.category, signals, now)
            if learned.pattern and grade in TEACHES:
                grade = DAILY
            if grade == PRIORITY and not self._account.claim_priority(account, day, now):
                grade = CALL_TODAY  # another heads-up already used today's
            item = {
                "id": item_id,
                "spoken_at": spoken_at,
                "category": d.candidate.category,
                "grade": grade,
                "summary": d.candidate.summary,
                "signals": signals,
                "language": "en",
                "ttl_epoch": expires,
            }
            if d.pattern:
                item["named_script"] = d.pattern.pattern_id
            if learned.pattern and d.grade in TEACHES:
                item["learned"] = str(learned.pattern["id"])
                self._account.note_learned(
                    account,
                    str(learned.pattern["id"]),
                    datetime.fromtimestamp(now, UTC).isoformat().replace("+00:00", "Z"),
                )
            elif learned.new_signals and grade in TEACHES:
                item["new_signals"] = list(learned.new_signals)
            self._account.put(account, f"ITEM#{spoken_at}#{item_id}", item)
            stored.append({**item, "sk": f"ITEM#{spoken_at}#{item_id}"})
            if grade == PRIORITY:
                # Raised, not sent: allies and delivery are not built yet.
                self._account.put(
                    account,
                    f"ALERT#{spoken_at}#{item_id}",
                    {
                        "id": item_id,
                        "created_at": datetime.fromtimestamp(now, UTC)
                        .isoformat()
                        .replace("+00:00", "Z"),
                        "summary": d.candidate.summary,
                        "status": "raised",
                        "ttl_epoch": expires,
                    },
                )
            counts["items"] += 1
        if self._plan and stored:
            # What each confirmed ally should get, queued for the deliver job.
            self._plan(account, stored, now)
        return counts
