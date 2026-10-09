"""Allies: the people an account holder chooses, and what reaches them.

Design sections 3, 4 and 7. An ally has no account and no app: their whole
interface is an **ally link**, a long random token (only its SHA-256 is
stored) that opens hearally.com/a/. Nothing reaches an ally until they have
confirmed through that link that they understand what Hearally is and is not.

What each ally hears is set per ally:

    off               nothing
    alerts_only       priority heads-ups only
    alerts_and_items  heads-ups, "worth a call today" and the daily list

Delivery, in code rather than prompts (AGENTS.md):

- A priority heads-up goes at once, any hour, to every confirmed ally who
  hears heads-ups. The one-a-day budget is enforced before this, by the
  worker's conditional write.
- "Worth a call today" goes to the first ally, at once in waking hours and
  held to the end of their quiet hours otherwise.
- Everything else waits for the ally's daily list.

The worker plans deliveries into an ``OUTBOX`` partition; the ``deliver``
job sends what is due, by email for now (text messages wait on carrier
approval, ARCHITECTURE.md section 8). Every message says Hearally is not an emergency
service and offers "this was nothing". Nothing here logs an address, a token,
a summary or anything said.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hearally.calls import MONTHLY_CALLS, Caller, CallMessage, month
from hearally.learning import PREFIX as LEARNED_PREFIX
from hearally.learning import new_element_line, pattern_from
from hearally.models import ValidationError
from hearally.topics import SORT_KEY as TOPICS_KEY
from hearally.topics import parse_topics

LOGGER = logging.getLogger(__name__)

LEVELS = ("off", "alerts_only", "alerts_and_items")
HEARS_ALERTS = frozenset({"alerts_only", "alerts_and_items"})
MAX_ALLIES = 5
LINK_SECONDS = 180 * 86400
SITE = "https://hearally.com"
DEFAULTS = {
    "timezone": "America/Chicago",
    "quiet_start": "21:00",
    "quiet_end": "08:00",
    "digest_time": "18:00",
}
DISMISS_REASONS = frozenset(
    {"handled_it", "known_caller", "was_me", "tv_or_radio", "not_a_concern", "other"}
)
NOT_EMERGENCY = "Hearally is not an emergency service. In an emergency, call 911."

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class AllyNotFoundError(Exception):
    pass


class LinkInvalidError(Exception):
    """Unknown, expired or revoked: all look the same from outside."""


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def _text(value: Any, field: str, *, required: bool, limit: int) -> str:
    if value is None or value == "":
        if required:
            raise ValidationError(f"{field} is required")
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be text")
    cleaned = " ".join(value.split())
    if len(cleaned) > limit or not cleaned.isprintable():
        raise ValidationError(f"{field} must be at most {limit} printable characters")
    return cleaned


def _email(value: Any) -> str:
    email = _text(value, "email", required=True, limit=254).lower()
    if not _EMAIL.fullmatch(email):
        raise ValidationError("email does not look like an email address")
    return email


def _level(value: Any) -> str:
    if value not in LEVELS:
        raise ValidationError(f"level must be one of {', '.join(LEVELS)}")
    return str(value)


def _timezone(value: Any) -> str:
    if value in (None, ""):
        return DEFAULTS["timezone"]
    if not isinstance(value, str) or len(value) > 64:
        raise ValidationError("timezone is not recognised")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValidationError("timezone is not recognised") from exc
    return value


def _clock_time(value: Any, field: str) -> str:
    if value in (None, ""):
        return DEFAULTS[field]
    if not isinstance(value, str) or not _HHMM.fullmatch(value):
        raise ValidationError(f"{field} must be HH:MM")
    return value


# ---------------------------------------------------------------- timing


def _at(local_day: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return local_day.replace(hour=h, minute=m, second=0, microsecond=0)


def in_quiet_hours(ally: Mapping[str, Any], now: float) -> bool:
    tz = ZoneInfo(ally.get("timezone") or DEFAULTS["timezone"])
    local = datetime.fromtimestamp(now, tz)
    start = _at(local, ally.get("quiet_start") or DEFAULTS["quiet_start"])
    end = _at(local, ally.get("quiet_end") or DEFAULTS["quiet_end"])
    if start <= end:
        return start <= local < end
    return local >= start or local < end  # across midnight


def end_of_quiet(ally: Mapping[str, Any], now: float) -> float:
    """When the ally's quiet hours next end (now, if they are not in them)."""
    if not in_quiet_hours(ally, now):
        return now
    tz = ZoneInfo(ally.get("timezone") or DEFAULTS["timezone"])
    local = datetime.fromtimestamp(now, tz)
    end = _at(local, ally.get("quiet_end") or DEFAULTS["quiet_end"])
    if end <= local:
        end += timedelta(days=1)
    return end.timestamp()


def next_digest(ally: Mapping[str, Any], now: float) -> float:
    """The ally's next daily-list time, in their own time zone."""
    tz = ZoneInfo(ally.get("timezone") or DEFAULTS["timezone"])
    local = datetime.fromtimestamp(now, tz)
    at = _at(local, ally.get("digest_time") or DEFAULTS["digest_time"])
    if at <= local:
        at += timedelta(days=1)
    return at.timestamp()


# ---------------------------------------------------------------- storage


class AllyRepository(Protocol):
    def display_name(self, sub: str) -> str: ...
    def set_display_name(self, sub: str, name: str) -> None: ...
    def allies(self, sub: str) -> list[dict[str, Any]]: ...
    def ally(self, sub: str, ally_id: str) -> dict[str, Any] | None: ...
    def put_ally(self, sub: str, ally: dict[str, Any]) -> None: ...
    def update_ally(self, sub: str, ally_id: str, fields: dict[str, Any]) -> None: ...
    def delete_ally(self, sub: str, ally_id: str) -> None: ...
    def put_link(self, token_digest: str, sub: str, ally_id: str, expires: int) -> None: ...
    def link(self, token_digest: str) -> dict[str, Any] | None: ...
    def delete_link(self, token_digest: str) -> None: ...
    def items(self, sub: str, limit: int) -> list[dict[str, Any]]: ...
    def put_outbox(self, entry: dict[str, Any]) -> None: ...
    def due_outbox(self, now: float, limit: int) -> list[dict[str, Any]]: ...
    def claim_outbox(self, sort_key: str) -> dict[str, Any] | None: ...
    def put(self, sub: str, sort_key: str, item: dict[str, Any]) -> None: ...
    def delete(self, sub: str, sort_key: str) -> bool: ...
    def held_back(self, sub: str) -> list[dict[str, Any]]: ...
    def claim_call(self, sub: str, month: str, limit: int, expires: int) -> bool: ...
    def release_call(self, sub: str, month: str) -> None: ...


class InMemoryAllyRepository:
    def __init__(self) -> None:
        self.records: dict[tuple[str, str], dict[str, Any]] = {}

    def _user(self, sub: str) -> str:
        return f"USER#{sub}"

    def display_name(self, sub: str) -> str:
        return str(self.records.get((self._user(sub), "PROFILE"), {}).get("display_name", ""))

    def set_display_name(self, sub: str, name: str) -> None:
        self.records.setdefault((self._user(sub), "PROFILE"), {})["display_name"] = name

    def allies(self, sub: str) -> list[dict[str, Any]]:
        return sorted(
            (
                dict(v)
                for (pk, sk), v in self.records.items()
                if pk == self._user(sub) and sk.startswith("ALLY#") and sk.count("#") == 1
            ),
            key=lambda a: a.get("created_epoch", 0),
        )

    def ally(self, sub: str, ally_id: str) -> dict[str, Any] | None:
        found = self.records.get((self._user(sub), f"ALLY#{ally_id}"))
        return dict(found) if found else None

    def put_ally(self, sub: str, ally: dict[str, Any]) -> None:
        self.records[(self._user(sub), f"ALLY#{ally['id']}")] = dict(ally)

    def update_ally(self, sub: str, ally_id: str, fields: dict[str, Any]) -> None:
        key = (self._user(sub), f"ALLY#{ally_id}")
        if key not in self.records:
            raise AllyNotFoundError
        self.records[key].update(fields)

    def delete_ally(self, sub: str, ally_id: str) -> None:
        self.records.pop((self._user(sub), f"ALLY#{ally_id}"), None)

    def put_link(self, token_digest: str, sub: str, ally_id: str, expires: int) -> None:
        self.records[(f"LINKTOKEN#{token_digest}", "META")] = {
            "account_id": sub,
            "ally_id": ally_id,
            "expires_epoch": expires,
        }

    def link(self, token_digest: str) -> dict[str, Any] | None:
        found = self.records.get((f"LINKTOKEN#{token_digest}", "META"))
        return dict(found) if found else None

    def delete_link(self, token_digest: str) -> None:
        self.records.pop((f"LINKTOKEN#{token_digest}", "META"), None)

    def items(self, sub: str, limit: int) -> list[dict[str, Any]]:
        found = [
            dict(v, sk=sk)
            for (pk, sk), v in self.records.items()
            if pk == self._user(sub) and sk.startswith("ITEM#")
        ]
        return sorted(found, key=lambda i: i["sk"], reverse=True)[:limit]

    def put_outbox(self, entry: dict[str, Any]) -> None:
        self.records[("OUTBOX", entry["sk"])] = dict(entry)

    def due_outbox(self, now: float, limit: int) -> list[dict[str, Any]]:
        due = [
            dict(v)
            for (pk, sk), v in self.records.items()
            if pk == "OUTBOX" and v["due_epoch"] <= now
        ]
        return sorted(due, key=lambda e: e["sk"])[:limit]

    def claim_outbox(self, sort_key: str) -> dict[str, Any] | None:
        return self.records.pop(("OUTBOX", sort_key), None)

    def put(self, sub: str, sort_key: str, item: dict[str, Any]) -> None:
        self.records[(self._user(sub), sort_key)] = dict(item)

    def delete(self, sub: str, sort_key: str) -> bool:
        return self.records.pop((self._user(sub), sort_key), None) is not None

    def held_back(self, sub: str) -> list[dict[str, Any]]:
        return [
            dict(v, sk=sk)
            for (pk, sk), v in self.records.items()
            if pk == self._user(sub) and sk.startswith("HELDBACK#")
        ]

    def claim_call(self, sub: str, month: str, limit: int, expires: int) -> bool:
        budget = self.records.setdefault((self._user(sub), f"CALLBUDGET#{month}"), {"used": 0})
        if budget["used"] >= limit:
            return False
        budget.update(used=budget["used"] + 1, ttl_epoch=expires)
        return True

    def release_call(self, sub: str, month: str) -> None:
        budget = self.records.get((self._user(sub), f"CALLBUDGET#{month}"))
        if budget and budget["used"] > 0:
            budget["used"] -= 1


class DynamoAllyRepository:
    """``USER#<sub>``: ``PROFILE``, ``ALLY#<id>``, ``SENT#…``, ``FEEDBACK#…``.
    ``LINKTOKEN#<sha256>`` / ``META``: an ally link (digest only).
    ``OUTBOX`` / ``<due>#<account>#<ally>#<n>``: what is waiting to be sent."""

    def __init__(self, table: Any) -> None:
        self._table = table

    def _query(self, pk: str, prefix: str, **extra: Any) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        kwargs: dict[str, Any] = {
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :sk)",
            "ExpressionAttributeValues": {":pk": pk, ":sk": prefix},
            **extra,
        }
        while True:
            page = self._table.query(**kwargs)
            items += page.get("Items", [])
            if "LastEvaluatedKey" not in page or len(items) >= extra.get("Limit", 10**6):
                return items
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def display_name(self, sub: str) -> str:
        item = self._table.get_item(Key={"pk": f"USER#{sub}", "sk": "PROFILE"}).get("Item") or {}
        return str(item.get("display_name", ""))

    def set_display_name(self, sub: str, name: str) -> None:
        self._table.update_item(
            Key={"pk": f"USER#{sub}", "sk": "PROFILE"},
            UpdateExpression="SET display_name = :n",
            ExpressionAttributeValues={":n": name},
        )

    def allies(self, sub: str) -> list[dict[str, Any]]:
        found = [
            _plain(i) for i in self._query(f"USER#{sub}", "ALLY#") if str(i["sk"]).count("#") == 1
        ]
        return sorted(found, key=lambda a: a.get("created_epoch", 0))

    def ally(self, sub: str, ally_id: str) -> dict[str, Any] | None:
        item = self._table.get_item(
            Key={"pk": f"USER#{sub}", "sk": f"ALLY#{ally_id}"}, ConsistentRead=True
        ).get("Item")
        return _plain(item) if item else None

    def put_ally(self, sub: str, ally: dict[str, Any]) -> None:
        self._table.put_item(Item={"pk": f"USER#{sub}", "sk": f"ALLY#{ally['id']}", **ally})

    def update_ally(self, sub: str, ally_id: str, fields: dict[str, Any]) -> None:
        try:
            self._table.update_item(
                Key={"pk": f"USER#{sub}", "sk": f"ALLY#{ally_id}"},
                UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
                ConditionExpression="attribute_exists(pk)",
                ExpressionAttributeNames={f"#{k}": k for k in fields},
                ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException as exc:
            raise AllyNotFoundError from exc

    def delete_ally(self, sub: str, ally_id: str) -> None:
        self._table.delete_item(Key={"pk": f"USER#{sub}", "sk": f"ALLY#{ally_id}"})

    def put_link(self, token_digest: str, sub: str, ally_id: str, expires: int) -> None:
        self._table.put_item(
            Item={
                "pk": f"LINKTOKEN#{token_digest}",
                "sk": "META",
                "account_id": sub,
                "ally_id": ally_id,
                "expires_epoch": expires,
                "ttl_epoch": expires,
            }
        )

    def link(self, token_digest: str) -> dict[str, Any] | None:
        item = self._table.get_item(Key={"pk": f"LINKTOKEN#{token_digest}", "sk": "META"}).get(
            "Item"
        )
        return _plain(item) if item else None

    def delete_link(self, token_digest: str) -> None:
        self._table.delete_item(Key={"pk": f"LINKTOKEN#{token_digest}", "sk": "META"})

    def items(self, sub: str, limit: int) -> list[dict[str, Any]]:
        page = self._table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :sk)",
            ExpressionAttributeValues={":pk": f"USER#{sub}", ":sk": "ITEM#"},
            ScanIndexForward=False,
            Limit=limit,
        )
        return [_plain(i) for i in page.get("Items", [])]

    def put_outbox(self, entry: dict[str, Any]) -> None:
        self._table.put_item(Item={"pk": "OUTBOX", **entry})

    def due_outbox(self, now: float, limit: int) -> list[dict[str, Any]]:
        page = self._table.query(
            KeyConditionExpression="pk = :pk AND sk <= :now",
            ExpressionAttributeValues={":pk": "OUTBOX", ":now": f"{int(now):012d}~"},
            Limit=limit,
        )
        return [_plain(i) for i in page.get("Items", [])]

    def claim_outbox(self, sort_key: str) -> dict[str, Any] | None:
        """Take an entry exactly once: the delete that succeeds sends it."""
        try:
            old = self._table.delete_item(
                Key={"pk": "OUTBOX", "sk": sort_key},
                ConditionExpression="attribute_exists(pk)",
                ReturnValues="ALL_OLD",
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return None
        attrs = old.get("Attributes")
        return _plain(attrs) if attrs else None

    def put(self, sub: str, sort_key: str, item: dict[str, Any]) -> None:
        self._table.put_item(Item={"pk": f"USER#{sub}", "sk": sort_key, **item})

    def delete(self, sub: str, sort_key: str) -> bool:
        old = self._table.delete_item(
            Key={"pk": f"USER#{sub}", "sk": sort_key}, ReturnValues="ALL_OLD"
        )
        return bool(old.get("Attributes"))

    def held_back(self, sub: str) -> list[dict[str, Any]]:
        return [_plain(i) for i in self._query(f"USER#{sub}", "HELDBACK#")]

    def claim_call(self, sub: str, month: str, limit: int, expires: int) -> bool:
        """The month's call budget, as a conditional counter: the update that
        would pass ``limit`` fails, so two runs cannot both take the last call."""
        try:
            self._table.update_item(
                Key={"pk": f"USER#{sub}", "sk": f"CALLBUDGET#{month}"},
                UpdateExpression="ADD used :one SET ttl_epoch = :ttl",
                ConditionExpression="attribute_not_exists(used) OR used < :limit",
                ExpressionAttributeValues={":one": 1, ":limit": limit, ":ttl": expires},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def release_call(self, sub: str, month: str) -> None:
        """Give back a call that could not be placed."""
        try:
            self._table.update_item(
                Key={"pk": f"USER#{sub}", "sk": f"CALLBUDGET#{month}"},
                UpdateExpression="ADD used :minus",
                ConditionExpression="used > :zero",
                ExpressionAttributeValues={":minus": -1, ":zero": 0},
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return


def _plain(item: Mapping[str, Any]) -> dict[str, Any]:
    from decimal import Decimal

    out: dict[str, Any] = {}
    for k, v in item.items():
        if isinstance(v, Decimal):
            out[k] = int(v) if v == v.to_integral_value() else float(v)
        else:
            out[k] = v
    return out


# ---------------------------------------------------------------- email


class Mailer(Protocol):
    def send(self, *, to: str, subject: str, text: str, html_body: str, purpose: str) -> str: ...


class SesMailer:
    def __init__(
        self, client: Any, *, from_email: str, configuration_set: str | None = None
    ) -> None:
        self._client = client
        self._from = from_email
        self._set = configuration_set

    def send(self, *, to: str, subject: str, text: str, html_body: str, purpose: str) -> str:
        kwargs: dict[str, Any] = {
            "FromEmailAddress": self._from,
            "Destination": {"ToAddresses": [to]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": subject, "Charset": "UTF-8"},
                    "Body": {
                        "Text": {"Data": text, "Charset": "UTF-8"},
                        "Html": {"Data": html_body, "Charset": "UTF-8"},
                    },
                }
            },
            "EmailTags": [{"Name": "purpose", "Value": purpose}],
        }
        if self._set:
            kwargs["ConfigurationSetName"] = self._set
        return str(self._client.send_email(**kwargs)["MessageId"])


# Brand colours (brand/palette.css). Amber names the ally: the darker
# amber-text shade, because brand amber on paper fails contrast.
_INK, _AMBER, _AMBER_TEXT = "#14243A", "#E8934A", "#996131"
_SAND, _PAPER, _LINE, _TEAL, _MUTED = "#F6F1E8", "#FDFCFA", "#E8E2D8", "#1F6F6B", "#5A6672"


def _html(
    paragraphs: Sequence[str],
    link: str,
    link_text: str,
    *,
    title: str = "",
    greet: str = "",
    bullets: Sequence[str] = (),
    after: Sequence[str] = (),
) -> str:
    """The email's HTML: a table layout, inline styles and no images, so it
    looks the same in every mail app, with images blocked or not."""
    esc = html.escape
    sans = "font-family:Arial,Helvetica,sans-serif"
    head = (
        f'<tr><td style="background:{_INK};padding:18px 24px;border-radius:12px 12px 0 0">'
        f'<span style="{sans};font-size:22px;font-weight:700;color:{_AMBER}">hearally</span>'
        "</td></tr>"
    )
    parts = []
    if title:
        parts.append(
            f'<p style="margin:0 0 14px;font-family:Georgia,serif;font-size:22px;'
            f'line-height:1.25;font-weight:700">{esc(title)}</p>'
        )
    if greet:
        parts.append(
            f'<p style="margin:0 0 14px">Hi <span style="color:{_AMBER_TEXT};font-weight:700">'
            f"{esc(greet)}</span>,</p>"
        )
    parts += [f'<p style="margin:0 0 14px">{esc(p)}</p>' for p in paragraphs]
    if bullets:
        parts.append(
            '<ul style="margin:0 0 14px;padding-left:20px">'
            + "".join(f'<li style="margin:0 0 8px">{esc(b)}</li>' for b in bullets)
            + "</ul>"
        )
    parts += [f'<p style="margin:0 0 14px">{esc(p)}</p>' for p in after]
    parts.append(
        f'<p style="margin:22px 0 4px"><a href="{esc(link, quote=True)}" style="display:'
        f"inline-block;background:{_TEAL};color:#ffffff;padding:12px 20px;border-radius:8px;"
        f'text-decoration:none;font-weight:700">{esc(link_text)}</a></p>'
    )
    card = (
        f'<tr><td style="background:{_PAPER};padding:24px;border:1px solid {_LINE};'
        f"border-top:0;border-radius:0 0 12px 12px;{sans};font-size:16px;line-height:1.5;"
        f'color:{_INK}">{"".join(parts)}</td></tr>'
    )
    foot = (
        f'<tr><td style="padding:16px 24px;{sans};font-size:13px;line-height:1.5;'
        f'color:{_MUTED}">{esc(NOT_EMERGENCY)}</td></tr>'
    )
    return (
        f'<div style="background:{_SAND};padding:24px 12px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="max-width:560px;margin:0 auto">{head}{card}{foot}</table></div>'
    )


def ally_link(token: str) -> str:
    # The token rides in the fragment, which browsers never send to a server,
    # so it stays out of every access log; the page posts it to the API.
    return f"{SITE}/a/#t={token}"


# ---------------------------------------------------------------- the account holder's side


@dataclass(frozen=True)
class AllyInput:
    name: str
    email: str
    relationship: str
    level: str
    timezone: str

    @classmethod
    def parse(cls, data: Mapping[str, Any]) -> AllyInput:
        unknown = set(data) - {"name", "email", "relationship", "level", "timezone"}
        if unknown:
            raise ValidationError("Unknown fields")
        return cls(
            name=_text(data.get("name"), "name", required=True, limit=60),
            email=_email(data.get("email")),
            relationship=_text(data.get("relationship"), "relationship", required=False, limit=40),
            level=_level(data.get("level", "alerts_and_items")),
            timezone=_timezone(data.get("timezone")),
        )


class AllyService:
    def __init__(
        self, repo: AllyRepository, mailer: Mailer, clock: Callable[[], float] = time.time
    ) -> None:
        self._repo = repo
        self._mailer = mailer
        self._clock = clock

    def set_display_name(self, sub: str, name: Any) -> str:
        cleaned = _text(name, "display_name", required=True, limit=40)
        self._repo.set_display_name(sub, cleaned)
        return cleaned

    def _issue_link(self, sub: str, ally: dict[str, Any]) -> str:
        if ally.get("link_digest"):
            self._repo.delete_link(ally["link_digest"])
        token = secrets.token_urlsafe(32)
        token_digest = digest(token)
        self._repo.put_link(token_digest, sub, ally["id"], int(self._clock()) + LINK_SECONDS)
        self._repo.update_ally(sub, ally["id"], {"link_digest": token_digest})
        return token

    def _invite(self, sub: str, ally: dict[str, Any]) -> None:
        holder = self._repo.display_name(sub)
        if not holder:
            raise ValidationError("Set how your allies know you first")
        token = self._issue_link(sub, ally)
        link = ally_link(token)
        paragraphs = [
            f"{holder} uses Hearally, which passes along things that were mentioned out loud "
            f"near their Plaud recorder to the people they choose. {holder} would like you to "
            "be one of them.",
            "Before anything reaches you, please read what Hearally is and is not, and say "
            "whether you agree. You can stop at any time.",
        ]
        text = "\n\n".join([f"Hi {ally['name']},", *paragraphs])
        self._mailer.send(
            to=ally["email"],
            subject=f"{holder} would like you as an ally on Hearally",
            text=text + f"\n\nRead and decide: {link}\n\n{NOT_EMERGENCY}",
            html_body=_html(
                paragraphs,
                link,
                "Read and decide",
                title=f"{holder} would like you as an ally",
                greet=ally["name"],
            ),
            purpose="ally-invite",
        )
        self._repo.update_ally(sub, ally["id"], {"invited_at": iso(self._clock())})

    def add(self, sub: str, data: Mapping[str, Any]) -> dict[str, Any]:
        given = AllyInput.parse(data)
        current = self._repo.allies(sub)
        if len(current) >= MAX_ALLIES:
            raise ValidationError(f"You can have up to {MAX_ALLIES} allies")
        if any(a.get("email") == given.email for a in current):
            raise ValidationError("That email is already one of your allies")
        now = self._clock()
        ally: dict[str, Any] = {
            "id": secrets.token_hex(8),
            "name": given.name,
            "email": given.email,
            "relationship": given.relationship,
            "level": given.level,
            "first": not any(a.get("first") for a in current),
            "status": "invited",
            "created_epoch": int(now),
            "timezone": given.timezone,
            "quiet_start": DEFAULTS["quiet_start"],
            "quiet_end": DEFAULTS["quiet_end"],
            "digest_time": DEFAULTS["digest_time"],
        }
        self._repo.put_ally(sub, ally)
        self._invite(sub, ally)
        return self._repo.ally(sub, ally["id"]) or ally

    def update(self, sub: str, ally_id: str, data: Mapping[str, Any]) -> dict[str, Any]:
        if set(data) - {"level", "first"}:
            raise ValidationError("Only level and first can be changed")
        ally = self._repo.ally(sub, ally_id)
        if not ally:
            raise AllyNotFoundError
        fields: dict[str, Any] = {}
        if "level" in data:
            fields["level"] = _level(data["level"])
        if data.get("first") is True:
            for other in self._repo.allies(sub):
                if other.get("first") and other["id"] != ally_id:
                    self._repo.update_ally(sub, other["id"], {"first": False})
            fields["first"] = True
        if fields:
            self._repo.update_ally(sub, ally_id, fields)
        return self._repo.ally(sub, ally_id) or ally

    def remove(self, sub: str, ally_id: str) -> None:
        """Instant revocation: the ally's link stops working at once."""
        ally = self._repo.ally(sub, ally_id)
        if not ally:
            raise AllyNotFoundError
        if ally.get("link_digest"):
            self._repo.delete_link(ally["link_digest"])
        self._repo.delete_ally(sub, ally_id)

    def set_topics(self, sub: str, topics: Any) -> list[str]:
        """Topics that never leave (topics.py). Replaces the whole list."""
        cleaned = parse_topics(topics)
        self._repo.put(sub, TOPICS_KEY, {"topics": cleaned, "updated_at": iso(self._clock())})
        return cleaned

    def release(self, sub: str, moment_id: Any) -> dict[str, Any]:
        """Send a held-back moment anyway, at the account holder's word.

        It becomes an ordinary item in the next daily list: never a heads-up,
        never sooner. The held-back record goes, so it cannot be sent twice."""
        if not isinstance(moment_id, str) or not re.fullmatch(r"[0-9a-f]{16}", moment_id):
            raise ValidationError("id is not recognised")
        found = next((m for m in self._repo.held_back(sub) if m.get("id") == moment_id), None)
        if not found:
            raise AllyNotFoundError
        if not found.get("summary"):
            raise ValidationError("This moment was held back before details were kept")
        if not self._repo.delete(sub, str(found["sk"])):
            raise AllyNotFoundError  # released a moment ago
        now = self._clock()
        item = {
            "id": moment_id,
            "spoken_at": found["spoken_at"],
            "category": found.get("category", "other"),
            "grade": "daily",
            "summary": found["summary"],
            "signals": [],
            "released": True,
            "language": "en",
            "ttl_epoch": found.get("ttl_epoch", int(now) + 90 * 86400),
        }
        sort_key = f"ITEM#{found['spoken_at']}#{moment_id}"
        self._repo.put(sub, sort_key, item)
        plan_deliveries(self._repo, sub, [{**item, "sk": sort_key}], now)
        return item

    def forget(self, sub: str, pattern_id: Any) -> None:
        """Remove one thing Hearally learned from "this was nothing"."""
        if not isinstance(pattern_id, str) or not re.fullmatch(r"[0-9a-f]{16}", pattern_id):
            raise ValidationError("pattern_id is not recognised")
        if not self._repo.delete(sub, f"{LEARNED_PREFIX}{pattern_id}"):
            raise AllyNotFoundError

    def resend(self, sub: str, ally_id: str) -> None:
        ally = self._repo.ally(sub, ally_id)
        if not ally:
            raise AllyNotFoundError
        if ally.get("status") == "declined":
            raise ValidationError("This person said no thanks; add them again only if they ask")
        self._invite(sub, ally)


# ---------------------------------------------------------------- the ally's side


class AllyLinkService:
    def __init__(self, repo: AllyRepository, clock: Callable[[], float] = time.time) -> None:
        self._repo = repo
        self._clock = clock

    def resolve(self, token: Any) -> tuple[str, dict[str, Any]]:
        """The account and ally a live link belongs to (for ally_phone.py)."""
        return self._resolve(token)

    def _resolve(self, token: Any) -> tuple[str, dict[str, Any]]:
        if not isinstance(token, str) or not 20 <= len(token) <= 100:
            raise LinkInvalidError
        link = self._repo.link(digest(token))
        if not link or link.get("expires_epoch", 0) <= self._clock():
            raise LinkInvalidError
        ally = self._repo.ally(link["account_id"], link["ally_id"])
        if not ally:
            raise LinkInvalidError
        return link["account_id"], ally

    def view(self, token: Any) -> dict[str, Any]:
        sub, ally = self._resolve(token)
        view: dict[str, Any] = {
            "holder": self._repo.display_name(sub),
            "ally": ally["name"],
            "status": ally.get("status"),
            "level": ally.get("level"),
            "settings": {k: ally.get(k) or v for k, v in DEFAULTS.items()},
            "phone": {
                "last4": ally.get("phone_last4") or "",
                "verified": bool(ally.get("phone") and ally.get("phone_verified_at")),
                "calls": bool(ally.get("calls")),
                "pending": bool(ally.get("pending_phone"))
                and int(ally.get("pending_expires") or 0) > self._clock(),
            },
            "items": [],
        }
        if ally.get("status") == "confirmed" and ally.get("level") != "off":
            since = ally.get("confirmed_at", "")
            for item in self._repo.items(sub, 50):
                if item.get("spoken_at", "") < since[:19]:
                    continue
                if ally.get("level") == "alerts_only" and item.get("grade") != "priority":
                    continue
                view["items"].append(
                    {k: item.get(k) for k in ("id", "spoken_at", "category", "grade", "summary")}
                )
        return view

    def _set(self, token: Any, fields: dict[str, Any], record: str) -> None:
        sub, ally = self._resolve(token)
        self._repo.update_ally(sub, ally["id"], fields)
        now = self._clock()
        # Append-only consent record (ARCHITECTURE.md section 7).
        # Its own prefix: CONSENT# is the account holder's acknowledgement.
        self._repo.put(
            sub,
            f"ALLYCONSENT#{ally['id']}#{iso(now)}",
            {"ally_id": ally["id"], "decision": record, "at": iso(now)},
        )

    def confirm(self, token: Any) -> None:
        self._set(token, {"status": "confirmed", "confirmed_at": iso(self._clock())}, "confirmed")

    def settings(self, token: Any, data: Any) -> dict[str, str]:
        """The ally's own quiet hours, daily-list time and time zone."""
        sub, ally = self._resolve(token)
        if not isinstance(data, Mapping) or set(data) - set(DEFAULTS):
            raise ValidationError("Unknown fields")
        fields = {
            "timezone": _timezone(data.get("timezone", ally.get("timezone"))),
            "quiet_start": _clock_time(
                data.get("quiet_start", ally.get("quiet_start")), "quiet_start"
            ),
            "quiet_end": _clock_time(data.get("quiet_end", ally.get("quiet_end")), "quiet_end"),
            "digest_time": _clock_time(
                data.get("digest_time", ally.get("digest_time")), "digest_time"
            ),
        }
        self._repo.update_ally(sub, ally["id"], fields)
        return fields

    def decline(self, token: Any) -> None:
        self._set(token, {"status": "declined"}, "declined")

    def stop(self, token: Any) -> None:
        """The ally stops receiving anything. The account holder sees it."""
        self._set(token, {"status": "stopped", "level": "off"}, "stopped")

    def dismiss(self, token: Any, item_id: Any, reason: Any) -> None:
        """'This was nothing': kept with the item, and learned from
        (learning.py, ARCHITECTURE.md section 7)."""
        sub, ally = self._resolve(token)
        if not isinstance(item_id, str) or not re.fullmatch(r"[0-9a-f]{16}", item_id):
            raise ValidationError("item_id is not recognised")
        if reason not in DISMISS_REASONS:
            raise ValidationError("reason is not recognised")
        # Only an item this ally's page shows them can be dismissed.
        shown = {i["id"]: i for i in self.view(token)["items"]}
        if item_id not in shown:
            raise ValidationError("item_id is not recognised")
        item = next(
            (i for i in self._repo.items(sub, 50) if i.get("id") == item_id), shown[item_id]
        )
        now = self._clock()
        pattern = pattern_from(item, reason=reason, ally=ally, now=now)
        if pattern:
            self._repo.put(sub, f"{LEARNED_PREFIX}{pattern['id']}", pattern)
        self._repo.put(
            sub,
            f"FEEDBACK#{item_id}#{ally['id']}",
            {
                "item_id": item_id,
                "ally_id": ally["id"],
                "reason": reason,
                "at": iso(now),
                "ttl_epoch": int(now) + 90 * 86400,
            },
        )


# ---------------------------------------------------------------- planning and sending


def plan_deliveries(
    repo: AllyRepository, sub: str, items: Sequence[Mapping[str, Any]], now: float
) -> int:
    """Queue what each confirmed ally should get for these new items."""
    allies = [
        a for a in repo.allies(sub) if a.get("status") == "confirmed" and a.get("level") != "off"
    ]
    if not allies:
        return 0
    first = next((a for a in allies if a.get("first")), None)
    queued = 0
    for item in items:
        grade = item.get("grade")
        for ally in allies:
            level = ally.get("level")
            if grade == "priority" and level in HEARS_ALERTS:
                kind, due = "heads_up", now
            elif level != "alerts_and_items":
                continue
            elif grade == "call_today" and first and ally["id"] == first["id"]:
                kind, due = "call_today", end_of_quiet(ally, now)
            elif grade in ("call_today", "daily"):
                kind, due = "digest", next_digest(ally, now)
            else:
                continue
            repo.put_outbox(
                {
                    "sk": f"{int(due):012d}#{sub}#{ally['id']}#{item['id']}",
                    "due_epoch": int(due),
                    "account_id": sub,
                    "ally_id": ally["id"],
                    "item_sk": item["sk"],
                    "kind": kind,
                    "ttl_epoch": int(due) + 7 * 86400,
                }
            )
            queued += 1
            if kind == "heads_up" and wants_call(ally) and not in_quiet_hours(ally, now):
                repo.put_outbox(
                    {
                        "sk": f"{int(due):012d}#{sub}#{ally['id']}#{item['id']}#call",
                        "due_epoch": int(due),
                        "account_id": sub,
                        "ally_id": ally["id"],
                        "item_sk": item["sk"],
                        "kind": "call",
                        "ttl_epoch": int(due) + 7 * 86400,
                    }
                )
                queued += 1
    return queued


def wants_call(ally: Mapping[str, Any]) -> bool:
    """A confirmed ally who hears heads-ups, proved their number, and chose calls."""
    return bool(
        ally.get("status") == "confirmed"
        and ally.get("level") in HEARS_ALERTS
        and ally.get("calls")
        and ally.get("phone")
        and ally.get("phone_verified_at")
    )


TITLES = {
    "heads_up": "A call worth knowing about",
    "call_today": "Worth a call today",
    "digest": "Today's list",
}

SUBJECTS = {
    "heads_up": "Hearally: a call worth knowing about",
    "call_today": "Hearally: worth a call today",
    "digest": "Hearally: today's list",
}


class Deliverer:
    def __init__(
        self,
        repo: AllyRepository,
        mailer: Mailer,
        clock: Callable[[], float] = time.time,
        caller: Caller | None = None,
    ) -> None:
        self._repo = repo
        self._mailer = mailer
        self._clock = clock
        self._caller = caller

    def run(self, limit: int = 200) -> dict[str, int]:
        now = self._clock()
        totals = {"sent": 0, "skipped": 0}
        # A daily list is one email per ally and time; anything else, one each.
        groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
        for entry in self._repo.due_outbox(now, limit):
            claimed = self._repo.claim_outbox(entry["sk"])
            if not claimed:
                continue  # another run took it
            batch = str(claimed["due_epoch"]) if claimed["kind"] == "digest" else claimed["sk"]
            key = (claimed["account_id"], claimed["ally_id"], claimed["kind"], batch)
            groups.setdefault(key, []).append(claimed)
        for (sub, ally_id, kind, _), entries in groups.items():
            ally = self._repo.ally(sub, ally_id)
            # Checked again at sending time: revoked, stopped or lowered since.
            if not ally or ally.get("status") != "confirmed" or ally.get("level") == "off":
                totals["skipped"] += len(entries)
                continue
            items = {i["sk"]: i for i in self._repo.items(sub, 100)}
            chosen = [items[e["item_sk"]] for e in entries if e["item_sk"] in items]
            if not chosen:
                totals["skipped"] += len(entries)
                continue
            if kind == "call":
                totals[
                    "sent" if self._call(sub, ally, chosen[0], entries[0], now) else "skipped"
                ] += 1
                continue
            self._send(sub, ally, kind, chosen, now)
            totals["sent"] += 1
        return totals

    def _call(
        self,
        sub: str,
        ally: dict[str, Any],
        item: dict[str, Any],
        entry: dict[str, Any],
        now: float,
    ) -> bool:
        """One heads-up call, if everything still allows it. Logged either way."""

        def log(status: str, call_id: str = "") -> None:
            sent_id = secrets.token_hex(8)
            record: dict[str, Any] = {
                "id": sent_id,
                "sent_at": iso(now),
                "channel": "call",
                "ally_name": ally["name"],
                "status": status,
                "kind": "heads_up",
                "item_ids": [item["id"]],
                "ttl_epoch": int(now) + 90 * 86400,
            }
            if call_id:
                record["call_id"] = call_id
            self._repo.put(sub, f"SENT#{iso(now)}#{sent_id}", record)

        # Checked again at calling time: turned off, or quiet hours began.
        if not wants_call(ally) or in_quiet_hours(ally, now) or self._caller is None:
            return False
        this_month = month(now)
        if not self._repo.claim_call(sub, this_month, MONTHLY_CALLS, int(now) + 120 * 86400):
            log("monthly_limit_reached")
            return False
        message = CallMessage(
            ally_name=str(ally["name"]),
            holder=self._repo.display_name(sub) or "someone you know",
            summary=str(item["summary"]),
            new_element=new_element_line(item.get("new_signals") or []),
        )
        key = hashlib.sha256(str(entry["sk"]).encode()).hexdigest()[:32]
        try:
            call_id = self._caller.call(str(ally["phone"]), message, idempotency_key=key)
        except Exception as exc:
            LOGGER.warning("Heads-up call failed: %s", type(exc).__name__)
            self._repo.release_call(sub, this_month)
            log("failed")
            return False
        log("placed", call_id)
        return True

    def _send(
        self, sub: str, ally: dict[str, Any], kind: str, items: list[dict[str, Any]], now: float
    ) -> None:
        holder = self._repo.display_name(sub) or "Someone you know"
        token = secrets.token_urlsafe(32)
        token_digest = digest(token)
        # A fresh link each time; the previous one keeps working until it expires.
        self._repo.put_link(token_digest, sub, ally["id"], int(now) + LINK_SECONDS)
        link = ally_link(token)
        bullets: list[str] = []
        if kind == "digest":
            intro = [f"From {holder}'s recordings today:"]
            bullets = [str(i["summary"]) for i in items]
        elif kind == "call_today":
            intro = [
                f"{holder} mentioned something that may be worth a call today:",
                items[0]["summary"],
            ]
        else:
            intro = [f"About {holder}: {items[0]['summary']}"]
        if kind != "digest" and items[0].get("new_signals"):
            intro.append(new_element_line(items[0]["new_signals"]))
        closing = (
            "Hearally reports what was said, and it can be wrong. "
            "If it was nothing, you can say so with one tap."
        )
        lines = [*intro, *(f"• {b}" for b in bullets), closing]
        self._mailer.send(
            to=ally["email"],
            subject=SUBJECTS[kind],
            text="\n\n".join(lines) + f"\n\nOpen, or say it was nothing: {link}\n\n{NOT_EMERGENCY}",
            html_body=_html(
                intro,
                link,
                "Open, or say it was nothing",
                title=TITLES[kind],
                greet=str(ally["name"]),
                bullets=bullets,
                after=[closing],
            ),
            purpose=f"ally-{kind}",
        )
        sent_id = secrets.token_hex(8)
        self._repo.put(
            sub,
            f"SENT#{iso(now)}#{sent_id}",
            {
                "id": sent_id,
                "sent_at": iso(now),
                "channel": "email",
                "ally_name": ally["name"],
                "status": "sent",
                "kind": kind,
                "item_ids": [i["id"] for i in items],
                "ttl_epoch": int(now) + 90 * 86400,
            },
        )
