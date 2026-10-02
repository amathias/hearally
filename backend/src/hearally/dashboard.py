"""What the signed-in account holder's dashboard shows.

Every read is scoped to ``USER#<sub>``, where ``sub`` comes from a Cognito token
that API Gateway has already verified. Nothing the browser sends can widen that
scope: there is no account id in the request to tamper with.

Records are projected through an allowlist of fields before they leave. The
table holds things the dashboard must never echo back, such as consent
evidence and delivery internals, and a new attribute added to an item later
must not appear in the browser just because it exists.

Where nothing has been saved, the response says so. There is no sample data.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

SECTIONS = frozenset(
    {"overview", "activity", "summaries", "people", "integrations", "preferences", "account"}
)

# Fields each record type may expose to the browser. Anything else is dropped.
_FIELDS: Mapping[str, frozenset[str]] = {
    "ITEM#": frozenset({"id", "spoken_at", "category", "grade", "summary", "language"}),
    "ALERT#": frozenset({"id", "created_at", "summary", "status", "acknowledged_at"}),
    "SENT#": frozenset({"id", "sent_at", "channel", "ally_name", "status", "kind"}),
    "ALLY#": frozenset(
        {
            "id",
            "name",
            "email",
            "relationship",
            "language",
            "status",
            "level",
            "first",
            "invited_at",
            "confirmed_at",
        }
    ),
    # A Plaud recorder, noted as its recordings arrive.
    "DEVICE#": frozenset({"id", "model", "label", "last_seen_at", "recordings"}),
    # A paired computer. Never its credential digest.
    "COMPUTER#": frozenset({"id", "name", "status", "paired_at", "last_seen_at"}),
    "HELDBACK#": frozenset({"id", "spoken_at", "reason"}),
    # A received recording: when, how long, how it came, where it has got to.
    "RECORDING#": frozenset(
        {
            "id",
            "recorded_at",
            "received_at",
            "source",
            "via",
            "duration_s",
            "byte_length",
            "status",
            "items",
            "held_back",
            "transcribed_at",
        }
    ),
}

LIST_LIMIT = 50

# The state of each capability, stated plainly so the dashboard never offers a
# button that does nothing. Changed by hand as features ship.
CAPABILITIES: tuple[dict[str, str], ...] = (
    {
        "id": "plaud",
        "name": "Plaud sign-in",
        "status": "testing",
        "detail": "Sign in to Plaud under Integrations, and new recordings come in about every "
        "15 minutes with no computer. New, and still being tested.",
    },
    {
        "id": "collector",
        "name": "Upload from a computer",
        "status": "available",
        "detail": "The Hearally collector sends recordings you export from Plaud, from a folder "
        "on your computer.",
    },
    {
        "id": "transcription",
        "name": "Transcription and summaries",
        "status": "available",
        "detail": "Recordings are transcribed about every half hour, and what is worth passing "
        "on appears under Summaries. Account numbers are silenced in the audio, and the "
        "original is deleted.",
    },
    {
        "id": "allies",
        "name": "Allies",
        "status": "in_progress",
        "detail": "Choosing your allies, and what each one hears, is being built.",
    },
    {
        "id": "messages",
        "name": "Messages to allies",
        "status": "not_yet",
        "detail": "Email is set up but not switched on. Text messages are waiting for carrier "
        "approval of Hearally's sending number.",
    },
)


class AccountReader(Protocol):
    def query(self, sub: str, prefix: str, limit: int) -> list[dict[str, Any]]: ...

    def get(self, sub: str, sort_key: str) -> dict[str, Any] | None: ...


def _project(record: Mapping[str, Any], prefix: str) -> dict[str, Any]:
    allowed = _FIELDS[prefix]
    return {key: _plain(value) for key, value in record.items() if key in allowed}


def _plain(value: Any) -> Any:
    # DynamoDB returns numbers as Decimal, which JSON cannot encode.
    from decimal import Decimal

    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_plain(v) for v in value]
    return value


def _records(reader: AccountReader, sub: str, prefix: str) -> list[dict[str, Any]]:
    records = reader.query(sub, prefix, LIST_LIMIT)
    if prefix == "ALLY#":
        # ALLY#<id> is the ally; ALLY#<id>#SCHEDULE and similar are settings
        # stored beside it, not more allies.
        records = [r for r in records if str(r.get("sk", "")).count("#") == 1]
    return [_project(r, prefix) for r in records]


def section(
    name: str,
    sub: str,
    claims: Mapping[str, Any],
    reader: AccountReader,
    *,
    acknowledgement_version: str = "",
) -> dict[str, Any]:
    """Build one dashboard section. ``name`` must already be in ``SECTIONS``."""
    if name == "overview":
        summaries = _records(reader, sub, "ITEM#")
        alerts = _records(reader, sub, "ALERT#")
        allies = _records(reader, sub, "ALLY#")
        computers = _records(reader, sub, "COMPUTER#")
        recordings = _records(reader, sub, "RECORDING#")
        return {
            "counts": {
                "summaries": len(summaries),
                "alerts": len(alerts),
                "allies": len(allies),
                "computers": sum(1 for c in computers if c.get("status") == "active"),
                "recordings": len(recordings),
            },
            "latest_recording": recordings[0] if recordings else None,
            "acknowledged": reader.get(sub, f"CONSENT#{acknowledgement_version}") is not None,
            "acknowledgement_version": acknowledgement_version,
            "latest_summaries": summaries[:5],
            "capabilities": list(CAPABILITIES),
        }
    if name == "activity":
        return {"alerts": _records(reader, sub, "ALERT#"), "sent": _records(reader, sub, "SENT#")}
    if name == "summaries":
        return {
            "summaries": _records(reader, sub, "ITEM#"),
            "held_back": _records(reader, sub, "HELDBACK#"),
        }
    if name == "people":
        profile = reader.get(sub, "PROFILE") or {}
        return {
            "allies": _records(reader, sub, "ALLY#"),
            "display_name": str(profile.get("display_name", "")),
        }
    if name == "integrations":
        return {
            "computers": _records(reader, sub, "COMPUTER#"),
            "recordings": _records(reader, sub, "RECORDING#"),
            # Whether pairing will ask for the acknowledgement first.
            "acknowledged": reader.get(sub, f"CONSENT#{acknowledgement_version}") is not None,
            "acknowledgement_version": acknowledgement_version,
        }
    if name == "preferences":
        blocklist = reader.get(sub, "BLOCKLIST")
        dignity = reader.get(sub, "DIGNITY")
        return {
            "blocklist_topics": list(_plain((blocklist or {}).get("topics", []))),
            "dignity_filter": "custom" if dignity else "default",
            "editing_available": False,
        }
    # account
    consents = reader.query(sub, "CONSENT#", LIST_LIMIT)
    return {
        "email": str(claims.get("email", "")),
        "email_verified": str(claims.get("email_verified", "")).lower() == "true",
        "acknowledgements": len(consents),
        # The newest version agreed to, and when: CONSENT#<version> sorts by date.
        "agreement": (
            {
                "version": str(consents[0].get("version", "")),
                "accepted_at": consents[0].get("accepted_at"),
            }
            if consents
            else None
        ),
        "plan": "public_preview",
    }


class DynamoAccountReader:
    def __init__(self, table: Any) -> None:
        self._table = table

    def query(self, sub: str, prefix: str, limit: int) -> list[dict[str, Any]]:
        from boto3.dynamodb.conditions import Key

        response = self._table.query(
            KeyConditionExpression=Key("pk").eq(f"USER#{sub}") & Key("sk").begins_with(prefix),
            Limit=limit,
            # Newest first: sort keys carry an ISO timestamp after the prefix.
            ScanIndexForward=False,
        )
        return list(response.get("Items", []))

    def get(self, sub: str, sort_key: str) -> dict[str, Any] | None:
        response = self._table.get_item(Key={"pk": f"USER#{sub}", "sk": sort_key})
        item = response.get("Item")
        return dict(item) if item else None
