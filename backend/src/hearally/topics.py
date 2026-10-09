"""Topics that never leave (ARCHITECTURE.md section 6).

The account holder names things they never want shared. An item that
mentions one, in its summary or in what was said, is held back from every
ally and listed for the account holder alone, like the dignity filter's own
holds. The same two exceptions apply: a caller pressing for money or details,
and a request for help, are always passed on.

Plain code, applied after the model: the model is never told the topics.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

from hearally.models import ValidationError

MAX_TOPICS = 20
MAX_TOPIC_LENGTH = 40
SORT_KEY = "BLOCKLIST"


def parse_topics(value: Any) -> list[str]:
    """The account holder's list, cleaned: trimmed, no blanks, no repeats
    (ignoring case), at most MAX_TOPICS of at most MAX_TOPIC_LENGTH each."""
    if not isinstance(value, list) or not all(isinstance(t, str) for t in value):
        raise ValidationError("topics must be a list of words or phrases")
    topics: list[str] = []
    seen: set[str] = set()
    for raw in value:
        topic = " ".join(raw.split())
        if not topic:
            continue
        if len(topic) > MAX_TOPIC_LENGTH:
            raise ValidationError(f"Each topic can be at most {MAX_TOPIC_LENGTH} characters")
        if topic.casefold() in seen:
            continue
        seen.add(topic.casefold())
        topics.append(topic)
    if len(topics) > MAX_TOPICS:
        raise ValidationError(f"At most {MAX_TOPICS} topics")
    return topics


def _pattern(topic: str) -> re.Pattern[str]:
    # Whole words, any case, with a plain plural: "lawyer" finds "lawyers".
    words = r"\s+".join(re.escape(w) for w in topic.split())
    return re.compile(rf"(?<!\w){words}(?:s|es)?(?!\w)", re.IGNORECASE)


def mentioned(texts: Iterable[str], topics: Sequence[str]) -> str | None:
    """The first of ``topics`` that any of ``texts`` mentions, or None."""
    joined = "\n".join(texts)
    for topic in topics:
        if _pattern(topic).search(joined):
            return topic
    return None
