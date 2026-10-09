"""An ally's phone number, and their choice to be called (ARCHITECTURE.md section 8).

Only the ally can add their number, from their own page: Hearally texts a
six-digit code through AWS End User Messaging Notify, and the number counts
as theirs once they type it back. Only then can they turn on calls for
heads-ups (calls.py), and every change is recorded, append-only.

The code is never stored: only a keyed hash of it (HMAC with a secret pepper),
for ten minutes, with five tries. At most DAILY_CODES texts per ally a day.
Numbers are US only during the preview, as the Notify configuration is.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable
from typing import Any, Protocol

from hearally.models import ValidationError

CODE_SECONDS = 600
MAX_ATTEMPTS = 5
DAILY_CODES = 3
# The wording an ally agrees to when turning calls on (web/public/a/ally.js).
CALLS_CONSENT_VERSION = "2026-10-06"


class SmsSender(Protocol):
    def send_verification(
        self, *, phone_e164: str, code: str, challenge_id: str, expires_at: int, locale: str
    ) -> str: ...


class Links(Protocol):
    def resolve(self, token: Any) -> tuple[str, dict[str, Any]]: ...


class Repo(Protocol):
    def update_ally(self, sub: str, ally_id: str, fields: dict[str, Any]) -> None: ...
    def put(self, sub: str, sort_key: str, item: dict[str, Any]) -> None: ...


def normalize_phone(value: Any) -> str:
    """A US number in E.164. Accepts the ways people type one."""
    if not isinstance(value, str) or len(value) > 40:
        raise ValidationError("Enter a US mobile number")
    digits = re.sub(r"[\s().\-]", "", value.strip())
    if re.fullmatch(r"\d{10}", digits):
        digits = "+1" + digits
    elif re.fullmatch(r"1\d{10}", digits):
        digits = "+" + digits
    if not re.fullmatch(r"\+1[2-9]\d{2}[2-9]\d{6}", digits):
        raise ValidationError("Enter a US mobile number, like 555 201 0123")
    return digits


def _iso(epoch: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


class AllyPhoneService:
    def __init__(
        self,
        links: Links,
        repo: Repo,
        sms: SmsSender,
        pepper: Callable[[], str],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._links = links
        self._repo = repo
        self._sms = sms
        self._pepper = pepper
        self._clock = clock

    def _ally(self, token: Any) -> tuple[str, dict[str, Any]]:
        sub, ally = self._links.resolve(token)
        if ally.get("status") != "confirmed":
            raise ValidationError("Confirm you are an ally first")
        return sub, ally

    def _digest(self, sub: str, ally_id: str, challenge: str, phone: str, code: str) -> str:
        message = f"{sub}:{ally_id}:{challenge}:{phone}:{code}".encode()
        return hmac.new(self._pepper().encode(), message, hashlib.sha256).hexdigest()

    def _record(self, sub: str, ally_id: str, decision: str, **extra: Any) -> None:
        now = self._clock()
        self._repo.put(
            sub,
            f"ALLYCONSENT#{ally_id}#{_iso(now)}",
            {"ally_id": ally_id, "decision": decision, "at": _iso(now), **extra},
        )

    def start(self, token: Any, phone: Any) -> dict[str, Any]:
        """Text a code to ``phone``."""
        sub, ally = self._ally(token)
        number = normalize_phone(phone)
        now = int(self._clock())
        recent = [t for t in ally.get("phone_codes_sent") or [] if int(t) > now - 86400]
        if len(recent) >= DAILY_CODES:
            raise ValidationError("That is enough codes for today. Please try again tomorrow.")
        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge = secrets.token_hex(8)
        expires = now + CODE_SECONDS
        self._sms.send_verification(
            phone_e164=number, code=code, challenge_id=challenge, expires_at=expires, locale="en-US"
        )
        self._repo.update_ally(
            sub,
            ally["id"],
            {
                "pending_phone": number,
                "pending_challenge": challenge,
                "pending_digest": self._digest(sub, ally["id"], challenge, number, code),
                "pending_expires": expires,
                "pending_attempts": 0,
                "phone_codes_sent": [*recent, now],
            },
        )
        return {"sent_to": "•••" + number[-4:], "expires_at": _iso(expires)}

    def confirm(self, token: Any, code: Any) -> dict[str, Any]:
        """The ally types the code back: the number is now theirs."""
        sub, ally = self._ally(token)
        now = int(self._clock())
        number = str(ally.get("pending_phone") or "")
        if not number or int(ally.get("pending_expires") or 0) <= now:
            raise ValidationError("That code has expired. Ask for a new one.")
        attempts = int(ally.get("pending_attempts") or 0)
        if attempts >= MAX_ATTEMPTS:
            raise ValidationError("Too many tries. Ask for a new code.")
        typed = re.sub(r"\s", "", code) if isinstance(code, str) else ""
        expected = str(ally.get("pending_digest") or "")
        candidate = self._digest(
            sub, ally["id"], str(ally.get("pending_challenge") or ""), number, typed
        )
        if not re.fullmatch(r"\d{6}", typed) or not hmac.compare_digest(candidate, expected):
            self._repo.update_ally(sub, ally["id"], {"pending_attempts": attempts + 1})
            raise ValidationError("That code is not right. Please check it and try again.")
        changed = number != ally.get("phone")
        self._repo.update_ally(
            sub,
            ally["id"],
            {
                "phone": number,
                "phone_last4": number[-4:],
                "phone_verified_at": _iso(now),
                # A new number starts with calls off: the ally turns them on for it.
                "calls": bool(ally.get("calls")) and not changed,
                "pending_phone": "",
                "pending_challenge": "",
                "pending_digest": "",
                "pending_expires": 0,
                "pending_attempts": 0,
            },
        )
        self._record(sub, ally["id"], "phone_verified", phone_last4=number[-4:])
        return {"phone_last4": number[-4:]}

    def set_calls(self, token: Any, on: Any) -> dict[str, Any]:
        sub, ally = self._ally(token)
        if not isinstance(on, bool):
            raise ValidationError("calls must be true or false")
        if on and not (ally.get("phone") and ally.get("phone_verified_at")):
            raise ValidationError("Add and confirm your number first")
        self._repo.update_ally(sub, ally["id"], {"calls": on})
        self._record(
            sub,
            ally["id"],
            "calls_on" if on else "calls_off",
            consent_version=CALLS_CONSENT_VERSION,
            phone_last4=str(ally.get("phone_last4") or ""),
        )
        return {"calls": on}

    def remove(self, token: Any) -> None:
        sub, ally = self._ally(token)
        self._repo.update_ally(
            sub,
            ally["id"],
            {"phone": "", "phone_last4": "", "phone_verified_at": "", "calls": False},
        )
        self._record(sub, ally["id"], "phone_removed")
