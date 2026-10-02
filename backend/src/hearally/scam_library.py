"""Known scam scripts, retrieved to ground the judgement (docs/ARCHITECTURE.md section 5).

A library of short cards, each describing a scam that public guidance (the FTC,
AARP, the IRS and others) warns about, written in our own words and linking
its sources. When a call is judged, the caller's words are embedded and the
closest cards are handed to Nemotron as reference. This is retrieval-augmented
generation without a vector database: the library is a few hundred cards at
most, so its vectors are a file and the search is a loop.

Retrieval always finds a nearest card, even for a call from the doctor's office,
so what it returns is evidence, never a verdict:

- Only cards scoring at least ``REFERENCE_SCORE`` are shown to the model.
- The model may only name a card it was shown (enforced by the schema and again
  in parse_candidates).
- A heads-up names the card only when the model matched it, the call carried
  concrete scam signals, and the retrieval score reached ``CITE_SCORE``.

Retrieval never changes a grade. It explains a heads-up; it does not cause one.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import struct
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from hearally.nebius import BASE_URL, urllib_post

EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B"

# Scores are cosine similarity. Calibrated against scripted scam and ordinary
# calls with docs/eval/scam-retrieval.md; change them only with a new run.
REFERENCE_SCORE = 0.50
CITE_SCORE = 0.60
TOP_K = 3

# Every card links at least one of these. Discovered cards (section 7b) are
# only kept when their sources are on this list, so the library cannot be
# seeded from an arbitrary web page. The publisher a heads-up names comes from
# this map, never from a model.
TRUSTED_PUBLISHERS: Mapping[str, str] = {
    "consumer.ftc.gov": "Federal Trade Commission",
    "www.ftc.gov": "Federal Trade Commission",
    "www.aarp.org": "AARP",
    "www.irs.gov": "Internal Revenue Service",
    "oig.ssa.gov": "Social Security Administration Office of the Inspector General",
    "www.ssa.gov": "Social Security Administration",
    "www.uscourts.gov": "United States Courts",
    "www.ic3.gov": "FBI Internet Crime Complaint Center",
    "www.fbi.gov": "FBI",
    "www.medicare.gov": "Medicare.gov",
    "oig.hhs.gov": "HHS Office of Inspector General",
    "www.cms.gov": "Centers for Medicare & Medicaid Services",
    "www.fcc.gov": "Federal Communications Commission",
    "www.consumerfinance.gov": "Consumer Financial Protection Bureau",
    "www.usa.gov": "USA.gov",
    "www.bbb.org": "Better Business Bureau",
}
TRUSTED_SOURCE_DOMAINS = frozenset(TRUSTED_PUBLISHERS)
PAYMENT_METHODS = frozenset(
    {
        "gift_cards",
        "wire_transfer",
        "cryptocurrency",
        "cash_courier",
        "cash_mail",
        "bank_transfer",
        "payment_app",
        "card_details",
        "account_access",
        "one_time_code",
        "personal_details",
        "remote_access",
        "check",
    }
)

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
_CARDS_FILE = Path(__file__).with_name("scam_cards.json")
_VECTORS_FILE = Path(__file__).with_name("scam_embeddings.json")


class LibraryError(ValueError):
    """A card or the vectors file is malformed."""


@dataclass(frozen=True, slots=True)
class Source:
    publisher: str
    title: str
    url: str


@dataclass(frozen=True, slots=True)
class Card:
    id: str
    name: str
    description: str
    tells: tuple[str, ...]
    asks_for: tuple[str, ...]
    payment: frozenset[str]
    sources: tuple[Source, ...]

    def text(self) -> str:
        """What is embedded, and what the model is shown."""
        return (
            f"{self.name}: {self.description} "
            f"Signs: {'; '.join(self.tells)}. "
            f"Asks for: {'; '.join(self.asks_for)}."
        )

    def fingerprint(self) -> str:
        """Changes whenever the embedded text does, so stale vectors are caught."""
        return hashlib.sha256(f"{EMBEDDING_MODEL}\n{self.text()}".encode()).hexdigest()[:16]

    def to_dict(self) -> dict[str, Any]:
        """The same shape parse_card reads, so a stored card round-trips."""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "tells": list(self.tells),
            "asks_for": list(self.asks_for),
            "payment": sorted(self.payment),
            "sources": [
                {"publisher": s.publisher, "title": s.title, "url": s.url} for s in self.sources
            ],
        }


@dataclass(frozen=True, slots=True)
class Match:
    card: Card
    score: float


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise LibraryError(f"{field} must be 1-{limit} characters")
    if not value.isprintable():
        raise LibraryError(f"{field} contains control characters")
    return " ".join(value.split())


def _texts(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise LibraryError(f"{field} must list 1-8 entries")
    return tuple(_text(v, field, 160) for v in value)


def _source(value: Any) -> Source:
    if not isinstance(value, dict):
        raise LibraryError("each source must be an object")
    url = _text(value.get("url"), "source url", 300)
    if url_domain(url) not in TRUSTED_SOURCE_DOMAINS:
        raise LibraryError(f"source {url} is not on a trusted domain")
    return Source(
        publisher=_text(value.get("publisher"), "publisher", 100),
        title=_text(value.get("title"), "source title", 200),
        url=url,
    )


def parse_card(raw: Any) -> Card:
    """Validate one card. Used for the curated file and for discovered cards,
    so a card written by a model is held to the same rules as one we wrote."""
    if not isinstance(raw, dict):
        raise LibraryError("a card must be an object")
    card_id = raw.get("id")
    if not isinstance(card_id, str) or not _ID.fullmatch(card_id):
        raise LibraryError("card id must be lowercase letters, digits and dashes")
    payment = raw.get("payment")
    if (
        not isinstance(payment, list)
        or not payment
        or not all(isinstance(p, str) and p in PAYMENT_METHODS for p in payment)
    ):
        raise LibraryError(f"{card_id}: payment must list known methods")
    sources = raw.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 4:
        raise LibraryError(f"{card_id}: 1-4 sources are required")
    return Card(
        id=card_id,
        name=_text(raw.get("name"), "name", 60),
        description=_text(raw.get("description"), "description", 600),
        tells=_texts(raw.get("tells"), "tells"),
        asks_for=_texts(raw.get("asks_for"), "asks_for"),
        payment=frozenset(payment),
        sources=tuple(_source(s) for s in sources),
    )


def load_curated(path: Path = _CARDS_FILE) -> list[Card]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cards = [parse_card(raw) for raw in data.get("cards", [])]
    ids = [c.id for c in cards]
    if len(ids) != len(set(ids)):
        raise LibraryError("card ids must be unique")
    return cards


def encode_vector(vector: Sequence[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii")


def decode_vector(encoded: str) -> tuple[float, ...]:
    raw = base64.b64decode(encoded)
    return struct.unpack(f"<{len(raw) // 4}f", raw)


def _unit(vector: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        raise LibraryError("an embedding cannot be all zeros")
    return tuple(x / norm for x in vector)


class ScamLibrary:
    """Cards with their vectors, searched by cosine similarity."""

    def __init__(self, entries: Iterable[tuple[Card, Sequence[float]]]) -> None:
        self._entries = [(card, _unit(vector)) for card, vector in entries]
        ids = [card.id for card, _ in self._entries]
        if len(ids) != len(set(ids)):
            raise LibraryError("card ids must be unique")

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def cards(self) -> list[Card]:
        return [card for card, _ in self._entries]

    def entries(self) -> list[tuple[Card, tuple[float, ...]]]:
        return list(self._entries)

    def search(self, query: Sequence[float], *, k: int = TOP_K) -> list[Match]:
        """The k nearest cards, best first. Every card scores; filtering is the
        caller's job, because the thresholds are policy, not search."""
        q = _unit(query)
        scored = [
            Match(card, sum(a * b for a, b in zip(q, v, strict=True))) for card, v in self._entries
        ]
        scored.sort(key=lambda m: m.score, reverse=True)
        return scored[:k]


def load_library(
    extra: Iterable[tuple[Card, Sequence[float]]] = (),
    *,
    cards_path: Path = _CARDS_FILE,
    vectors_path: Path = _VECTORS_FILE,
) -> ScamLibrary:
    """The curated library with its precomputed vectors, plus any discovered cards.

    A curated card whose vector is missing or stale (its text changed since it
    was embedded) is an error: run backend/scripts/embed_scam_library.py.
    """
    vectors = json.loads(vectors_path.read_text(encoding="utf-8"))
    if vectors.get("model") != EMBEDDING_MODEL:
        raise LibraryError("scam_embeddings.json was made with a different model")
    stored: Mapping[str, Any] = vectors.get("cards", {})
    entries: list[tuple[Card, Sequence[float]]] = []
    for card in load_curated(cards_path):
        entry = stored.get(card.id)
        if not entry or entry.get("fingerprint") != card.fingerprint():
            raise LibraryError(f"{card.id}: vector missing or stale; re-run embed_scam_library.py")
        entries.append((card, decode_vector(entry["vector"])))
    return ScamLibrary([*entries, *extra])


class EmbeddingClient:
    """Embeds text on Nebius Token Factory, the same account and Zero Data
    Retention setting as the judgement calls."""

    def __init__(
        self,
        api_key: Callable[[], str],
        *,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        post: Callable[[str, bytes, dict[str, str], float], bytes] | None = None,
    ) -> None:
        self._api_key = api_key
        self._url = base_url.rstrip("/") + "/embeddings"
        self._timeout = timeout
        self._post = post or urllib_post

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        body = json.dumps({"model": EMBEDDING_MODEL, "input": list(texts)}).encode()
        headers = {
            "Authorization": f"Bearer {self._api_key()}",
            "Content-Type": "application/json",
        }
        data = json.loads(self._post(self._url, body, headers, self._timeout))["data"]
        ordered = sorted(data, key=lambda d: d["index"])
        return [list(map(float, d["embedding"])) for d in ordered]


class Embedder(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class ScamRetriever:
    """Embeds a call and returns the known scripts close enough to show the model."""

    def __init__(
        self,
        library: ScamLibrary,
        embedder: Embedder,
        *,
        k: int = TOP_K,
        min_score: float = REFERENCE_SCORE,
    ) -> None:
        self._library = library
        self._embedder = embedder
        self._k = k
        self._min_score = min_score

    def find(self, query: str) -> list[Match]:
        if not query.strip():
            return []
        [vector] = self._embedder.embed([query])
        return [m for m in self._library.search(vector, k=self._k) if m.score >= self._min_score]


def url_domain(url: str) -> str | None:
    match = re.fullmatch(r"https://([^/?#]+)(?:[/?#]\S*)?", url)
    return match.group(1).lower() if match else None


class DynamoScamCardStore:
    """Discovered cards (section 7b), with their vectors, in the main table.

    Everything lives under the single partition ``SCAMLIB``, which is what the
    refresh job's IAM policy is confined to. Pages already read are remembered
    for a year, so a weekly search does not pay to read them again.
    """

    PARTITION = "SCAMLIB"
    SEEN_DAYS = 365

    def __init__(self, table: Any) -> None:
        self._table = table

    @staticmethod
    def _seen_key(url: str) -> dict[str, str]:
        digest = hashlib.sha256(url.encode()).hexdigest()[:32]
        return {"pk": DynamoScamCardStore.PARTITION, "sk": f"SEEN#{digest}"}

    def load(self) -> list[tuple[Card, tuple[float, ...]]]:
        """Every stored card that still passes validation. A card that no
        longer does, because the rules tightened, is skipped, not trusted."""
        entries: list[tuple[Card, tuple[float, ...]]] = []
        query: dict[str, Any] = {
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :cards)",
            "ExpressionAttributeValues": {":pk": self.PARTITION, ":cards": "CARD#"},
        }
        while True:
            page = self._table.query(**query)
            for item in page.get("Items", []):
                try:
                    card = parse_card(json.loads(item["card"]))
                    entries.append((card, decode_vector(item["vector"])))
                except (LibraryError, KeyError, ValueError, TypeError):
                    continue
            if "LastEvaluatedKey" not in page:
                return entries
            query["ExclusiveStartKey"] = page["LastEvaluatedKey"]

    def add(self, card: Card, vector: Sequence[float], *, found_at: str, query: str) -> bool:
        """Store a new card. False if a card with that id already exists."""
        try:
            self._table.put_item(
                Item={
                    "pk": self.PARTITION,
                    "sk": f"CARD#{card.id}",
                    "card": json.dumps(card.to_dict()),
                    "vector": encode_vector(vector),
                    "model": EMBEDDING_MODEL,
                    "origin": "discovered",
                    "found_at": found_at,
                    "search_query": query,
                },
                ConditionExpression="attribute_not_exists(pk)",
            )
        except self._table.meta.client.exceptions.ConditionalCheckFailedException:
            return False
        return True

    def seen(self, url: str) -> bool:
        return "Item" in self._table.get_item(Key=self._seen_key(url))

    def mark_seen(self, url: str, now: int) -> None:
        self._table.put_item(
            Item={**self._seen_key(url), "ttl_epoch": now + self.SEEN_DAYS * 86_400}
        )
