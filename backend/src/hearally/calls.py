"""Phone calls to allies for heads-ups (ARCHITECTURE.md section 8).

An ally can choose to be phoned for a heads-up, once they have proved the
number is theirs with a code by text (ally_phone.py). The call is placed by
CALL-E: an automated voice reads the heads-up, says Hearally is not an
emergency service, points to the email, and ends. It never asks for anything.

Limits, enforced in code:
- only heads-ups, and only to confirmed allies who hear them and turned calls on;
- never during the ally's quiet hours (the email still goes at once);
- at most MONTHLY_CALLS per account per calendar month (UTC) during the public
  preview, as a conditional counter, claimed before each call.

The client is adapted from an earlier prototype's CALL-E client.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

MONTHLY_CALLS = 10
BASE_URL = "https://api.heycall-e.com"
E164 = re.compile(r"\+[1-9]\d{7,14}")
_CALL_ID = re.compile(r"call_[A-Za-z0-9_-]+")


def month(now: float) -> str:
    return datetime.fromtimestamp(now, UTC).strftime("%Y-%m")


class CallError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(f"CALL-E request failed: {code}")
        self.code = code


@dataclass(frozen=True, slots=True)
class CallMessage:
    """What one heads-up call says."""

    ally_name: str
    holder: str
    summary: str
    new_element: str = ""


class Caller(Protocol):
    def call(self, phone: str, message: CallMessage, *, idempotency_key: str) -> str: ...


def task(message: CallMessage) -> str:
    """The instructions CALL-E's voice follows: a short notice, a brief summary,
    and where to look. It reads; it never advises, never says a scam happened,
    and asks for nothing (the claim rules)."""
    extra = f" {message.new_element}" if message.new_element else ""
    return (
        "You are placing a short automated call for Hearally. Speak calmly and clearly. "
        "Say exactly this, then end the call:\n\n"
        f'"Hi {message.ally_name}, this is Hearally with a notice about {message.holder}. '
        f"{message.summary}{extra} Please check your email from Hearally for the details: "
        "the link in it opens your Hearally page. Hearally is not an emergency service. "
        'Goodbye."\n\n'
        "If the person asks a question, say that the details are in their email from "
        "Hearally, then end the call. Do not add advice, do not say what anyone should do, "
        "do not say that a scam or any danger is happening, and do not ask for any "
        "information, codes, passwords or payment details."
    )


class CalleCaller:
    """CALL-E's asynchronous Calls API: one POST per call, no webhook."""

    def __init__(
        self,
        api_key: Callable[[], str],
        *,
        base_url: str = BASE_URL,
        timeout: float = 20.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/v1/calls"
        self._timeout = timeout
        self._opener = opener

    def call(self, phone: str, message: CallMessage, *, idempotency_key: str) -> str:
        if not E164.fullmatch(phone):
            raise CallError("invalid_phone_number")
        key = self._api_key().strip()
        if not key:
            raise CallError("not_configured")
        payload = {
            "task": task(message),
            "recipients": [{"phones": [phone], "locale": "en-US", "region": "US"}],
            "result_schema": {
                "type": "object",
                "required": ["message_delivered"],
                "properties": {"message_delivered": {"type": "boolean"}},
                "additionalProperties": False,
            },
            "metadata": {"source": "hearally_heads_up"},
        }
        request = urllib.request.Request(
            self._url,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
                "User-Agent": "Hearally/0.1",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            code = "http_error"
            try:
                candidate = (json.loads(exc.read().decode("utf-8")).get("error") or {}).get("code")
                if isinstance(candidate, str) and candidate:
                    code = candidate
            except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
                pass
            raise CallError(code) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            raise CallError("unavailable") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CallError("invalid_response") from exc
        call_id = result.get("id") if isinstance(result, dict) else None
        if not isinstance(call_id, str) or not _CALL_ID.fullmatch(call_id):
            raise CallError("invalid_response")
        if result.get("status") not in {"queued", "in_progress", "completed"}:
            raise CallError("rejected")
        return call_id
