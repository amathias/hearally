"""Keeping the scam library current with Tavily, and storing what it finds
(docs/ARCHITECTURE.md section 5)."""

from __future__ import annotations

import json
import unittest
from typing import Any

from test_scam_library import FakeEmbedder, card

from hearally.scam_discovery import (
    DUPLICATE_SCORE,
    QUERIES,
    Page,
    TavilyClient,
    card_from_extraction,
    clean_title,
    discover,
    extraction_request,
)
from hearally.scam_library import (
    TRUSTED_PUBLISHERS,
    DynamoScamCardStore,
    ScamLibrary,
    decode_vector,
)

FTC_URL = "https://consumer.ftc.gov/consumer-alerts/2026/09/fake-toll-calls"
PAGE = Page(url=FTC_URL, title="Scammers call about unpaid tolls", text="...", query=QUERIES[0])


def extraction(**overrides: Any) -> str:
    data: dict[str, Any] = {
        "page_kind": "one_scam",
        "channel": "phone",
        "new_scam": True,
        "name": "unpaid toll scam",
        "description": "A caller says you owe road tolls and will be fined unless you pay now.",
        "tells": ["says you owe tolls", "threatens a fine today"],
        "asks_for": ["payment of the tolls"],
        "payment": ["card_details", "payment_app"],
    }
    data.update(overrides)
    return json.dumps(data)


class TavilyTests(unittest.TestCase):
    def test_searches_only_trusted_publishers_and_drops_anything_else(self) -> None:
        sent: dict[str, Any] = {}

        def post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
            sent.update(url=url, body=json.loads(body), headers=headers)
            return json.dumps(
                {
                    "results": [
                        {"url": FTC_URL, "title": "Toll calls", "raw_content": "x" * 20_000},
                        {"url": "https://scam-blog.example.com/tolls", "title": "Blog"},
                        {"url": "http://consumer.ftc.gov/insecure", "title": "Plain http"},
                    ]
                }
            ).encode()

        pages = TavilyClient(lambda: "tvly-key", post=post).search(QUERIES[0])
        self.assertEqual([p.url for p in pages], [FTC_URL])
        self.assertLessEqual(len(pages[0].text), 12_000)
        self.assertEqual(sent["body"]["include_domains"], sorted(TRUSTED_PUBLISHERS))
        self.assertEqual(sent["body"]["query"], QUERIES[0])
        self.assertEqual(sent["headers"]["Authorization"], "Bearer tvly-key")

    def test_the_queries_are_fixed_public_phrases(self) -> None:
        """Nothing from anyone's calls can reach Tavily: the queries are constants."""
        for query in QUERIES:
            self.assertNotIn("{", query)
            self.assertLess(len(query), 80)


class ExtractionTests(unittest.TestCase):
    def test_a_new_scam_becomes_a_card_with_the_pages_source(self) -> None:
        new = card_from_extraction(extraction(), PAGE, set())
        assert new is not None
        self.assertEqual(new.id, "unpaid-toll-scam")
        [source] = new.sources
        self.assertEqual(source.url, FTC_URL)
        self.assertEqual(source.publisher, "Federal Trade Commission")

    def test_the_publisher_is_never_taken_from_the_model(self) -> None:
        new = card_from_extraction(extraction(publisher="Totally Legit Agency"), PAGE, set())
        assert new is not None
        self.assertEqual(new.sources[0].publisher, "Federal Trade Commission")

    def test_not_a_new_scam_or_a_bad_card_is_nothing(self) -> None:
        for raw in (
            extraction(new_scam=False),
            extraction(name="unpaid tolls"),  # must end in "scam"
            extraction(payment=["magic_beans"]),
            extraction(tells=[]),
            extraction(description="x" * 700),
            "not json",
        ):
            with self.subTest(raw=raw[:40]):
                self.assertIsNone(card_from_extraction(raw, PAGE, set()))

    def test_only_one_phone_or_doorstep_scam_per_page_is_accepted(self) -> None:
        """Enforced in code: on 2026-09-25 Nemotron turned a 'common types of fraud'
        overview into a phone 'blackmail scam' card."""
        for kind, channel in (
            ("overview", "phone"),
            ("news", "phone"),
            ("one_scam", "online_only"),
        ):
            with self.subTest(kind=kind, channel=channel):
                raw = extraction(page_kind=kind, channel=channel)
                self.assertIsNone(card_from_extraction(raw, PAGE, set()))
        doorstep = card_from_extraction(extraction(channel="doorstep"), PAGE, set())
        self.assertIsNotNone(doorstep)

    def test_the_site_name_is_trimmed_from_a_page_title(self) -> None:
        self.assertEqual(
            clean_title("Consumer Alert: Scam Calls | Federal Communications Commission"),
            "Consumer Alert: Scam Calls",
        )
        self.assertEqual(
            clean_title("New Scam: Fake Recall Notices"), "New Scam: Fake Recall Notices"
        )
        self.assertEqual(clean_title("Well-known tricks — AARP"), "Well-known tricks")

    def test_a_page_off_the_trusted_list_can_never_become_a_card(self) -> None:
        stray = Page("https://scam-blog.example.com/x", "t", "x", QUERIES[0])
        self.assertIsNone(card_from_extraction(extraction(), stray, set()))

    def test_an_id_already_taken_gets_a_suffix(self) -> None:
        new = card_from_extraction(extraction(), PAGE, {"unpaid-toll-scam"})
        assert new is not None
        self.assertEqual(new.id, "unpaid-toll-scam-2")

    def test_the_request_lists_known_scams_and_marks_the_page_untrusted(self) -> None:
        request = extraction_request(PAGE, [card("grandparent")])
        self.assertIn("- grandparent scam:", request["messages"][1]["content"])
        self.assertIn("untrusted", request["messages"][0]["content"])
        self.assertTrue(request["response_format"]["json_schema"]["strict"])


class FakeTavily:
    def __init__(self, pages: list[Page]) -> None:
        self.pages = pages
        self.queries: list[str] = []

    def search(self, query: str) -> list[Page]:
        self.queries.append(query)
        return self.pages if query == QUERIES[0] else []


class FakeNemotron:
    def __init__(self, raw: str) -> None:
        self.raw = raw
        self.calls = 0

    def complete(self, request: dict[str, Any]) -> str:
        self.calls += 1
        return self.raw


class MemoryStore:
    def __init__(self, seen: set[str] | None = None) -> None:
        self.seen_urls = set(seen or ())
        self.cards: dict[str, Any] = {}

    def seen(self, url: str) -> bool:
        return url in self.seen_urls

    def mark_seen(self, url: str, now: int) -> None:
        self.seen_urls.add(url)

    def add(self, card: Any, vector: Any, *, found_at: str, query: str) -> bool:
        if card.id in self.cards:
            return False
        self.cards[card.id] = (card, vector, found_at, query)
        return True


def library() -> ScamLibrary:
    return ScamLibrary([(card("grandparent"), [1.0, 0.0])])


class DiscoverTests(unittest.TestCase):
    def run_discover(
        self, pages: list[Page], raw: str, vector: list[float], store: MemoryStore
    ) -> Any:
        return discover(
            tavily=FakeTavily(pages),  # type: ignore[arg-type]
            nemotron=FakeNemotron(raw),
            embedder=FakeEmbedder(vector),
            library=library(),
            store=store,
            clock=lambda: 1_795_525_800,
        )

    def test_a_new_scam_is_added_and_its_page_remembered(self) -> None:
        store = MemoryStore()
        report = self.run_discover([PAGE], extraction(), [0.0, 1.0], store)
        self.assertEqual(report.added, ["unpaid-toll-scam"])
        self.assertIn(FTC_URL, store.seen_urls)
        self.assertEqual(report.searched, len(QUERIES))

    def test_a_page_read_before_is_not_paid_for_again(self) -> None:
        nemotron = FakeNemotron(extraction())
        report = discover(
            tavily=FakeTavily([PAGE]),  # type: ignore[arg-type]
            nemotron=nemotron,
            embedder=FakeEmbedder([0.0, 1.0]),
            library=library(),
            store=MemoryStore(seen={FTC_URL}),
        )
        self.assertEqual((report.already_seen, nemotron.calls), (1, 0))

    def test_a_card_too_close_to_one_held_is_a_duplicate(self) -> None:
        """The same scam in other words: the code decides by embedding, not the model."""
        store = MemoryStore()
        report = self.run_discover([PAGE], extraction(), [1.0, 0.05], store)
        self.assertGreaterEqual(1.0 / (1.0 + 0.05**2) ** 0.5, DUPLICATE_SCORE)
        self.assertEqual((report.duplicates, report.added), (1, []))
        self.assertIn(FTC_URL, store.seen_urls, "still remembered, so not re-read")

    def test_the_same_new_scam_on_two_pages_is_added_once(self) -> None:
        second = Page(FTC_URL + "-again", "Tolls again", "...", QUERIES[0])
        store = MemoryStore()
        report = self.run_discover([PAGE, second], extraction(), [0.0, 1.0], store)
        self.assertEqual(report.added, ["unpaid-toll-scam"])
        self.assertEqual(report.duplicates, 1)

    def test_a_page_that_is_not_a_new_scam_is_remembered_and_skipped(self) -> None:
        store = MemoryStore()
        report = self.run_discover([PAGE], extraction(new_scam=False), [0.0, 1.0], store)
        self.assertEqual((report.not_a_new_scam, report.added), (1, []))
        self.assertIn(FTC_URL, store.seen_urls)


class ConditionalCheckFailedException(Exception):
    pass


class FakeTable:
    def __init__(self, page_size: int = 1) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.page_size = page_size

        class Client:
            class exceptions:
                ConditionalCheckFailedException = ConditionalCheckFailedException

        class Meta:
            client = Client()

        self.meta = Meta()

    def put_item(self, Item: dict[str, Any], ConditionExpression: str | None = None) -> None:
        key = (Item["pk"], Item["sk"])
        if ConditionExpression and key in self.items:
            raise ConditionalCheckFailedException()
        self.items[key] = dict(Item)

    def get_item(self, Key: dict[str, str]) -> dict[str, Any]:
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": item} if item else {}

    def query(self, **kwargs: Any) -> dict[str, Any]:
        values = kwargs["ExpressionAttributeValues"]
        rows = sorted(
            (
                v
                for (pk, sk), v in self.items.items()
                if pk == values[":pk"] and sk.startswith(values[":cards"])
            ),
            key=lambda v: v["sk"],
        )
        start = kwargs.get("ExclusiveStartKey", {}).get("sk")
        if start:
            rows = [r for r in rows if r["sk"] > start]
        page = rows[: self.page_size]
        response: dict[str, Any] = {"Items": page}
        if len(rows) > self.page_size:
            response["LastEvaluatedKey"] = {"pk": page[-1]["pk"], "sk": page[-1]["sk"]}
        return response


class StoreTests(unittest.TestCase):
    def test_cards_round_trip_through_the_table_across_pages(self) -> None:
        table = FakeTable(page_size=1)
        store = DynamoScamCardStore(table)
        self.assertTrue(store.add(card("toll-a"), [0.5, 0.5], found_at="t", query="q"))
        self.assertTrue(store.add(card("toll-b"), [1.0, 0.0], found_at="t", query="q"))
        self.assertFalse(store.add(card("toll-a"), [0.0, 1.0], found_at="t", query="q"))
        loaded = store.load()
        self.assertEqual([c.id for c, _ in loaded], ["toll-a", "toll-b"])
        self.assertEqual(list(loaded[0][1]), [0.5, 0.5])

    def test_a_stored_card_that_no_longer_validates_is_skipped(self) -> None:
        table = FakeTable(page_size=10)
        store = DynamoScamCardStore(table)
        store.add(card("good-one"), [1.0], found_at="t", query="q")
        bad = card("bad-one").to_dict()
        bad["sources"][0]["url"] = "https://no-longer-trusted.example.com/x"
        table.items[("SCAMLIB", "CARD#bad-one")] = {
            "pk": "SCAMLIB",
            "sk": "CARD#bad-one",
            "card": json.dumps(bad),
            "vector": "",
        }
        self.assertEqual([c.id for c, _ in store.load()], ["good-one"])

    def test_pages_read_are_remembered_for_a_year(self) -> None:
        table = FakeTable()
        store = DynamoScamCardStore(table)
        self.assertFalse(store.seen(FTC_URL))
        store.mark_seen(FTC_URL, 1_000)
        self.assertTrue(store.seen(FTC_URL))
        [item] = [v for (pk, sk), v in table.items.items() if sk.startswith("SEEN#")]
        self.assertEqual(item["ttl_epoch"], 1_000 + 365 * 86_400)
        self.assertNotIn("ftc", item["sk"], "the key is a digest, not the URL")

    def test_the_vector_encoding_is_shared_with_the_curated_file(self) -> None:
        table = FakeTable()
        DynamoScamCardStore(table).add(card("toll-a"), [0.25, -2.0], found_at="t", query="q")
        stored = table.items[("SCAMLIB", "CARD#toll-a")]["vector"]
        self.assertEqual(list(decode_vector(stored)), [0.25, -2.0])


if __name__ == "__main__":
    unittest.main()


class NoSecretValue(Exception):
    pass


class RefreshHandlerTests(unittest.TestCase):
    def test_without_a_tavily_key_it_skips_and_touches_nothing(self) -> None:
        from hearally.handlers.scam_refresh import run

        class Secrets:
            def get_secret_value(self, SecretId: str) -> dict[str, str]:
                raise NoSecretValue(SecretId)

        table = FakeTable()
        with self.assertLogs("hearally.handlers.scam_refresh", "WARNING"):
            result = run(Secrets(), table, tavily_secret="t", nebius_secret="n")
        self.assertEqual(result, {"skipped": "no_tavily_key"})
        self.assertEqual(table.items, {})
