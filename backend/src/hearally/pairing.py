"""Pairing a computer with a signed-in account, and knowing it afterwards.

The collector runs on the account holder's computer. It proves which account
it works for like this:

1. Signed in to the dashboard, the account holder asks for a pairing code. It
   is random (192 bits), used once, and expires after ten minutes.
2. They paste it into the collector's setup. The collector trades it for a
   device token, which only the collector ever sees. The code is consumed and
   the computer is recorded against the account, in one transaction.
3. Every upload carries the device token. The account comes from the paired
   computer's record, never from the request, so a collector cannot claim
   another account's recordings.
4. The dashboard lists paired computers, and revoking one stops it at its next
   request.

Only SHA-256 digests of codes and device secrets are stored. The token is

    hd1.<account sub>.<device id>.<secret>

The sub and device id only locate the record; the secret is what proves
possession. They are in the token so that authenticating is a single read,
with no secondary index to keep in step.

Tokens are sent as a bearer credential over TLS rather than used to sign
requests: a server holding only a digest cannot check a signature made with the
secret, and the uploads themselves are already bound to their bytes by the
presigned POST policy.

Adapted from an earlier prototype's pairing design, with
authentication wired through to ingest.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from hearally.models import ValidationError

PAIRING_TTL_SECONDS = 600
TOKEN_VERSION = "hd1"
ACTIVE = "active"
REVOKED = "revoked"

_CODE = re.compile(r"^[A-Za-z0-9_-]{32}$")
_SUB = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_DEVICE_ID = re.compile(r"^[0-9a-f]{32}$")
_SECRET = re.compile(r"^[A-Za-z0-9_-]{43}$")
MAX_COMPUTER_NAME = 60


class PairingUnavailableError(Exception):
    """The code does not exist, has expired, or was already used."""


class AcknowledgementRequiredError(Exception):
    """The account holder has not accepted the current acknowledgement."""


class DeviceAuthenticationError(Exception):
    """The request carries no valid token for an active, paired computer."""


@dataclass(frozen=True, slots=True)
class PairedComputer:
    account_id: str
    device_id: str


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def make_token(account_id: str, device_id: str, secret: str) -> str:
    return f"{TOKEN_VERSION}.{account_id}.{device_id}.{secret}"


def parse_token(token: str) -> tuple[str, str, str]:
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != TOKEN_VERSION:
        raise DeviceAuthenticationError("Malformed device token")
    _, account_id, device_id, secret = parts
    if not (
        _SUB.fullmatch(account_id) and _DEVICE_ID.fullmatch(device_id) and _SECRET.fullmatch(secret)
    ):
        raise DeviceAuthenticationError("Malformed device token")
    return account_id, device_id, secret


def iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def computer_name(value: Any) -> str:
    """A label for the dashboard's list, from the computer's own name."""
    if not isinstance(value, str):
        raise ValidationError("computer_name must be a string")
    cleaned = " ".join(value.split())
    if not 1 <= len(cleaned) <= MAX_COMPUTER_NAME or not cleaned.isprintable():
        raise ValidationError(f"computer_name must be 1-{MAX_COMPUTER_NAME} printable characters")
    return cleaned


class PairingRepository(Protocol):
    def acknowledged(self, account_id: str, version: str) -> bool: ...

    def record_acknowledgement(self, account_id: str, version: str, now: int) -> None: ...

    def issue(self, code_digest: str, account_id: str, now: int, expires: int) -> None: ...

    def redeem(self, code_digest: str, device: dict[str, Any], now: int) -> str:
        """Consume the code and store the computer. Returns the account id."""
        ...

    def computer(self, account_id: str, device_id: str) -> dict[str, Any] | None: ...

    def revoke(self, account_id: str, device_id: str) -> bool: ...

    def seen(self, account_id: str, device_id: str, now: int) -> None: ...

    def note_recorder(
        self, account_id: str, recorder_id: str, model: str | None, now: int
    ) -> None: ...

    def note_recording(self, account_id: str, recording: dict[str, Any]) -> None: ...


def recording_note(
    *,
    event_id: str,
    recorded_at: str,
    source: str,
    via: str,
    byte_length: int,
    duration_s: float | None,
    now: int,
) -> dict[str, Any]:
    """What the dashboard lists for one received recording: when it was made,
    how long it is, how it arrived, and where it has got to. Never its content,
    its file name or anything said."""
    note: dict[str, Any] = {
        "id": event_id[:16],
        "recorded_at": recorded_at,
        "source": source,
        "via": via[:60],
        "byte_length": byte_length,
        "received_at": iso(now),
        "status": "received",
        # The list of recordings is kept as long as their summaries (90 days).
        "ttl_epoch": now + 90 * 86400,
    }
    if duration_s is not None:
        note["duration_s"] = int(round(duration_s))
    return note


def recording_key(recording: dict[str, Any]) -> str:
    # Newest first in the dashboard: the sort key starts with when it was made.
    return f"RECORDING#{recording['recorded_at']}#{recording['id']}"


class PairingService:
    def __init__(
        self,
        repository: PairingRepository,
        *,
        acknowledgement_version: str,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._repository = repository
        self._acknowledgement_version = acknowledgement_version
        self._clock = clock

    def needs_acknowledgement(self, account_id: str) -> bool:
        return not self._repository.acknowledged(account_id, self._acknowledgement_version)

    def acknowledge(self, account_id: str, acknowledgement_version: Any) -> bool:
        """Record acceptance of the current acknowledgement, from wherever the
        dashboard asks for it (Today, or before connecting Plaud). Returns
        whether it was new. Accepting an older version is refused."""
        if acknowledgement_version != self._acknowledgement_version:
            raise AcknowledgementRequiredError()
        if not self.needs_acknowledgement(account_id):
            return False
        self._repository.record_acknowledgement(
            account_id, self._acknowledgement_version, int(self._clock())
        )
        return True

    def create_code(self, account_id: str, acknowledgement_version: Any = None) -> dict[str, Any]:
        """Issue a pairing code, recording the acknowledgement first if it is new.

        Nothing reaches Hearally until the account holder has accepted the
        acknowledgement, including "I will only record conversations I have the
        legal right to record". Pairing is the first step that leads to an
        upload, so it is where the acceptance is required and recorded.
        """
        now = int(self._clock())
        if self.needs_acknowledgement(account_id):
            if acknowledgement_version != self._acknowledgement_version:
                raise AcknowledgementRequiredError()
            self._repository.record_acknowledgement(account_id, self._acknowledgement_version, now)

        code = secrets.token_urlsafe(24)
        expires = now + PAIRING_TTL_SECONDS
        self._repository.issue(digest(code), account_id, now, expires)
        return {"code": code, "expires_at": iso(expires)}

    def redeem(self, code: Any, name: Any) -> dict[str, str]:
        if not isinstance(code, str) or not _CODE.fullmatch(code):
            # Shape only: a wrong code of the right shape is indistinguishable
            # from an expired one, and both get the same answer.
            raise ValidationError("code is not a pairing code")
        label = computer_name(name)
        now = int(self._clock())
        device_id = uuid4().hex
        secret = secrets.token_urlsafe(32)
        account_id = self._repository.redeem(
            digest(code),
            {
                "id": device_id,
                "name": label,
                "status": ACTIVE,
                "paired_at": iso(now),
                "credential_digest": digest(secret),
            },
            now,
        )
        return {"token": make_token(account_id, device_id, secret), "device_id": device_id}

    def authenticate(self, headers: Mapping[str, str]) -> PairedComputer:
        supplied = ""
        for key, value in headers.items():
            if key.lower() == "authorization":
                supplied = value
        scheme, _, token = supplied.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise DeviceAuthenticationError("Missing device token")
        account_id, device_id, secret = parse_token(token.strip())
        record = self._repository.computer(account_id, device_id)
        stored = str((record or {}).get("credential_digest") or "")
        # Compared even when there is no record, so a missing computer and a
        # wrong secret take the same time.
        matches = hmac.compare_digest(stored or "0" * 64, digest(secret))
        if not record or record.get("status") != ACTIVE or not stored or not matches:
            raise DeviceAuthenticationError("Device token not accepted")
        return PairedComputer(account_id=account_id, device_id=device_id)

    def revoke(self, account_id: str, device_id: Any) -> bool:
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
            return False
        return self._repository.revoke(account_id, device_id)

    def note_recording(self, account_id: str, recording: dict[str, Any]) -> None:
        """A received recording, for the dashboard's list (``recording_note``)."""
        self._repository.note_recording(account_id, recording)

    def record_activity(
        self, computer: PairedComputer, recorder_id: str | None, model: str | None
    ) -> None:
        """What the dashboard shows: when each computer and Plaud last sent."""
        now = int(self._clock())
        self._repository.seen(computer.account_id, computer.device_id, now)
        if recorder_id:
            self._repository.note_recorder(computer.account_id, recorder_id, model, now)


def _user(account_id: str) -> str:
    return f"USER#{account_id}"


class InMemoryPairingRepository:
    def __init__(self) -> None:
        self.codes: dict[str, dict[str, Any]] = {}
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def acknowledged(self, account_id: str, version: str) -> bool:
        return (_user(account_id), f"CONSENT#{version}") in self.items

    def record_acknowledgement(self, account_id: str, version: str, now: int) -> None:
        self.items.setdefault(
            (_user(account_id), f"CONSENT#{version}"),
            {"version": version, "accepted_at": iso(now), "method": "dashboard"},
        )

    def issue(self, code_digest: str, account_id: str, now: int, expires: int) -> None:
        self.codes[code_digest] = {"account_id": account_id, "expires_epoch": expires}

    def redeem(self, code_digest: str, device: dict[str, Any], now: int) -> str:
        pairing = self.codes.get(code_digest)
        if not pairing or pairing["expires_epoch"] <= now:
            raise PairingUnavailableError()
        del self.codes[code_digest]
        account_id = str(pairing["account_id"])
        self.items[(_user(account_id), f"COMPUTER#{device['id']}")] = dict(device)
        return account_id

    def computer(self, account_id: str, device_id: str) -> dict[str, Any] | None:
        item = self.items.get((_user(account_id), f"COMPUTER#{device_id}"))
        return dict(item) if item else None

    def revoke(self, account_id: str, device_id: str) -> bool:
        item = self.items.get((_user(account_id), f"COMPUTER#{device_id}"))
        if not item:
            return False
        item["status"] = REVOKED
        item.pop("credential_digest", None)
        return True

    def seen(self, account_id: str, device_id: str, now: int) -> None:
        item = self.items.get((_user(account_id), f"COMPUTER#{device_id}"))
        if item:
            item["last_seen_at"] = iso(now)

    def note_recorder(self, account_id: str, recorder_id: str, model: str | None, now: int) -> None:
        item = self.items.setdefault(
            (_user(account_id), f"DEVICE#{recorder_id}"), {"id": recorder_id, "recordings": 0}
        )
        item["model"] = model or "other"
        item["last_seen_at"] = iso(now)
        item["recordings"] += 1

    def note_recording(self, account_id: str, recording: dict[str, Any]) -> None:
        self.items.setdefault((_user(account_id), recording_key(recording)), dict(recording))


class DynamoPairingRepository:
    """Pairing codes live under PAIR#<digest>; everything else under USER#<sub>.

    That split is what the IAM conditions in infra/modules/application rely on:
    each handler is confined to the partition prefixes it needs.
    """

    def __init__(self, table: Any) -> None:
        self._table = table

    def acknowledged(self, account_id: str, version: str) -> bool:
        key = {"pk": _user(account_id), "sk": f"CONSENT#{version}"}
        return "Item" in self._table.get_item(Key=key, ConsistentRead=True)

    def record_acknowledgement(self, account_id: str, version: str, now: int) -> None:
        try:
            self._table.put_item(
                Item={
                    "pk": _user(account_id),
                    "sk": f"CONSENT#{version}",
                    "version": version,
                    "accepted_at": iso(now),
                    "method": "dashboard",
                },
                # The first acceptance is the record; a repeat does not move it.
                ConditionExpression="attribute_not_exists(pk)",
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return

    def issue(self, code_digest: str, account_id: str, now: int, expires: int) -> None:
        self._table.put_item(
            Item={
                "pk": f"PAIR#{code_digest}",
                "sk": "CODE",
                "account_id": account_id,
                "issued_epoch": now,
                "expires_epoch": expires,
                # DynamoDB's own expiry removes codes nobody used.
                "ttl_epoch": expires,
            },
            ConditionExpression="attribute_not_exists(pk)",
        )

    def redeem(self, code_digest: str, device: dict[str, Any], now: int) -> str:
        key = {"pk": f"PAIR#{code_digest}", "sk": "CODE"}
        pairing = self._table.get_item(Key=key, ConsistentRead=True).get("Item")
        if not pairing or int(pairing.get("expires_epoch", 0)) <= now:
            raise PairingUnavailableError()
        account_id = str(pairing["account_id"])
        try:
            # One transaction: the code is consumed if and only if the computer
            # is stored. Two redemptions racing for one code cannot both win,
            # because the loser's delete condition fails and cancels its put.
            self._table.meta.client.transact_write_items(
                TransactItems=[
                    {
                        "Delete": {
                            "TableName": self._table.name,
                            "Key": key,
                            "ConditionExpression": "attribute_exists(pk) AND expires_epoch > :now",
                            "ExpressionAttributeValues": {":now": now},
                        }
                    },
                    {
                        "Put": {
                            "TableName": self._table.name,
                            "Item": {
                                "pk": _user(account_id),
                                "sk": f"COMPUTER#{device['id']}",
                                **device,
                            },
                            "ConditionExpression": "attribute_not_exists(pk)",
                        }
                    },
                ]
            )
        except self._table.meta.client.exceptions.TransactionCanceledException as exc:
            reasons = getattr(exc, "response", {}).get("CancellationReasons", [])
            # Only a failed condition means the code is gone. Throttling or a
            # conflict is temporary, and must not tell someone their code expired.
            if any(r.get("Code") == "ConditionalCheckFailed" for r in reasons):
                raise PairingUnavailableError() from exc
            raise
        return account_id

    def computer(self, account_id: str, device_id: str) -> dict[str, Any] | None:
        key = {"pk": _user(account_id), "sk": f"COMPUTER#{device_id}"}
        # Consistent, so a revocation takes effect on the very next request.
        item = self._table.get_item(Key=key, ConsistentRead=True).get("Item")
        return dict(item) if item else None

    def revoke(self, account_id: str, device_id: str) -> bool:
        try:
            self._table.update_item(
                Key={"pk": _user(account_id), "sk": f"COMPUTER#{device_id}"},
                # The digest goes too: a revoked computer keeps nothing that
                # could ever match again.
                UpdateExpression="SET #status = :revoked REMOVE credential_digest",
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":revoked": REVOKED},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def seen(self, account_id: str, device_id: str, now: int) -> None:
        try:
            self._table.update_item(
                Key={"pk": _user(account_id), "sk": f"COMPUTER#{device_id}"},
                UpdateExpression="SET last_seen_at = :now",
                # Never recreate a computer that was removed meanwhile.
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeValues={":now": iso(now)},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return

    def note_recorder(self, account_id: str, recorder_id: str, model: str | None, now: int) -> None:
        self._table.update_item(
            Key={"pk": _user(account_id), "sk": f"DEVICE#{recorder_id}"},
            # Placeholders throughout: DynamoDB reserves hundreds of words.
            UpdateExpression="SET #id = :id, #model = :model, #seen = :now ADD #count :one",
            ExpressionAttributeNames={
                "#id": "id",
                "#model": "model",
                "#seen": "last_seen_at",
                "#count": "recordings",
            },
            ExpressionAttributeValues={
                ":id": recorder_id,
                ":model": model or "other",
                ":now": iso(now),
                ":one": 1,
            },
        )

    def note_recording(self, account_id: str, recording: dict[str, Any]) -> None:
        # update_item, which the upload grant already has, and only if it is
        # new: a repeat of the same recording never resets its status.
        fields = {k: v for k, v in recording.items()}
        names = {f"#{k}": k for k in fields}
        values = {f":{k}": v for k, v in fields.items()}
        try:
            self._table.update_item(
                Key={"pk": _user(account_id), "sk": recording_key(recording)},
                UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
                ConditionExpression="attribute_not_exists(pk)",
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return
