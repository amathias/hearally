"""Learning from "this was nothing" (ARCHITECTURE.md section 7).

When an ally says an item that interrupted them was nothing, what set it off
is kept as a pattern: the item's category and its pressure signals. A later
item of the same category whose signals are all in a pattern brings nothing
new, so it goes to the daily list instead. It never disappears. One with a new
signal (secrecy, say, where the dismissed call had only urgency) is graded as
usual, and the message says what was new.

Patterns last 90 days from the latest dismissal, and the account holder sees
them all under "What Hearally has learned" and can remove any of them.

Like every product rule, this is code, never a prompt: the model is not told
about patterns and cannot argue its way past one.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

LEARNED_SECONDS = 90 * 86400

# Only items that interrupted an ally teach anything. The daily list is where
# a pattern sends things, so a daily-list item has nowhere lower to go.
TEACHES = frozenset({"priority", "call_today"})

PREFIX = "SUPPRESSION#"

# How the email names a new element (judgement.SCAM_SIGNALS).
SIGNAL_WORDS = {
    "asks_for_account_details": "asking for account details",
    "asks_for_payment": "asking for payment",
    "urgency": "pressure to act fast",
    "secrecy": "asking to keep it secret",
    "impersonation": "claiming to be someone official",
    "gift_cards": "gift cards",
    "remote_access": "remote access to a computer",
}


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat().replace("+00:00", "Z")


def pattern_id(category: str, signals: Iterable[str]) -> str:
    """The same trigger always gives the same id, so dismissing it again
    renews the pattern rather than adding a second one."""
    key = category + "|" + ",".join(sorted(set(signals)))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def pattern_from(
    item: Mapping[str, Any], *, reason: str, ally: Mapping[str, Any], now: float
) -> dict[str, Any] | None:
    """The pattern an ally's "this was nothing" creates, or None when the item
    did not interrupt anyone."""
    if item.get("grade") not in TEACHES or not item.get("category"):
        return None
    signals = sorted({str(s) for s in item.get("signals") or []})
    category = str(item["category"])
    return {
        "id": pattern_id(category, signals),
        "category": category,
        "signals": signals,
        "reason": reason,
        "ally_id": ally.get("id"),
        "ally_name": ally.get("name"),
        "item_id": item.get("id"),
        "learned_at": _iso(now),
        "expires_at": _iso(now + LEARNED_SECONDS),
        "ttl_epoch": int(now) + LEARNED_SECONDS,
    }


@dataclass(frozen=True, slots=True)
class Learned:
    # The pattern this item matches, which sends it to the daily list.
    pattern: Mapping[str, Any] | None
    # With no match but a pattern of the same kind: what this item has that
    # the dismissed one did not. The message names these.
    new_signals: tuple[str, ...] = ()


def check(
    patterns: Iterable[Mapping[str, Any]], category: str, signals: Iterable[str], now: float
) -> Learned:
    signal_set = frozenset(signals)
    # DynamoDB's TTL can lag by a day or two: an expired pattern is ignored here.
    same = [
        p for p in patterns if p.get("category") == category and float(p.get("ttl_epoch", 0)) > now
    ]
    if not same:
        return Learned(None)
    for p in same:
        if signal_set <= frozenset(p.get("signals") or []):
            return Learned(p)
    closest = max(same, key=lambda p: len(signal_set & frozenset(p.get("signals") or [])))
    return Learned(None, tuple(sorted(signal_set - frozenset(closest.get("signals") or []))))


def new_element_line(signals: Iterable[str]) -> str:
    words = [SIGNAL_WORDS.get(s, s.replace("_", " ")) for s in signals]
    if not words:
        return ""
    listed = words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]
    return (
        "Something like this was marked as nothing before. "
        f"This time there was also {listed}, so it is passed on as usual."
    )
