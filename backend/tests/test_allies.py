"""Allies: invitations, the ally link, planning and sending."""

from __future__ import annotations

import json
import re
import unittest
from datetime import UTC, datetime
from typing import Any

from hearally.allies import (
    NOT_EMERGENCY,
    AllyLinkService,
    AllyNotFoundError,
    AllyService,
    Deliverer,
    InMemoryAllyRepository,
    LinkInvalidError,
    plan_deliveries,
)
from hearally.edge import EdgeVerifier
from hearally.handlers.allies import handle_link, handle_manage
from hearally.models import ValidationError

SUB = "3f1c2a90-1111-4222-8333-944455556666"
EDGE = "edge-token"
# Wednesday 30 September 2026, 15:00 in Chicago (20:00 UTC): waking hours.
AFTERNOON = datetime(2026, 9, 30, 20, 0, tzinfo=UTC).timestamp()
# The same day, 23:30 in Chicago: quiet hours.
NIGHT = datetime(2026, 10, 1, 4, 30, tzinfo=UTC).timestamp()


class Mailer:
    def __init__(self) -> None:
        self.sent: list[dict[str, str]] = []

    def send(self, *, to: str, subject: str, text: str, html_body: str, purpose: str) -> str:
        self.sent.append(
            {"to": to, "subject": subject, "text": text, "html": html_body, "purpose": purpose}
        )
        return f"m{len(self.sent)}"

    def token(self, n: int = -1) -> str:
        found = re.search(r"/a/#t=([A-Za-z0-9_-]+)", self.sent[n]["text"])
        assert found
        return found.group(1)


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def item(grade: str, n: int = 1) -> dict[str, Any]:
    item_id = f"{n:016x}"
    return {
        "sk": f"ITEM#2026-09-30T19:39:00+00:00#{item_id}",
        "id": item_id,
        "spoken_at": "2026-09-30T19:39:00+00:00",
        "category": "appointment",
        "grade": grade,
        "summary": f"Summary {n}",
    }


class AllyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = InMemoryAllyRepository()
        self.mailer = Mailer()
        self.clock = Clock(AFTERNOON)
        self.allies = AllyService(self.repo, self.mailer, self.clock)
        self.links = AllyLinkService(self.repo, self.clock)
        self.allies.set_display_name(SUB, "Aaron")

    def add(
        self, name: str = "Dana", email: str = "dana@example.com", **extra: Any
    ) -> dict[str, Any]:
        return self.allies.add(
            SUB, {"name": name, "email": email, "timezone": "America/Chicago", **extra}
        )

    def confirmed(self, **extra: Any) -> dict[str, Any]:
        ally = self.add(**extra)
        self.links.confirm(self.mailer.token())
        return self.repo.ally(SUB, ally["id"]) or {}


class AccountHolderTests(AllyTestCase):
    def test_adding_an_ally_sends_an_invitation_with_a_private_link(self) -> None:
        ally = self.add()
        self.assertEqual((ally["status"], ally["first"]), ("invited", True))
        [mail] = self.mailer.sent
        self.assertEqual(mail["subject"], "Aaron would like you as an ally on Hearally")
        self.assertIn(NOT_EMERGENCY, mail["text"])
        # Only the digest of the link is stored.
        token = self.mailer.token()
        self.assertNotIn(token, repr(self.repo.records))

    def test_allies_need_to_know_who_is_asking(self) -> None:
        self.repo.records.clear()
        with self.assertRaises(ValidationError):
            self.add()

    def test_one_first_ally_at_a_time(self) -> None:
        dana, sam = self.add(), self.add("Sam", "sam@example.com")
        self.assertFalse(sam["first"])
        self.allies.update(SUB, sam["id"], {"first": True})
        self.assertFalse((self.repo.ally(SUB, dana["id"]) or {})["first"])

    def test_limits_and_duplicates(self) -> None:
        self.add()
        with self.assertRaises(ValidationError):
            self.add("Dana again", "DANA@example.com")
        for n in range(4):
            self.add(f"A{n}", f"a{n}@example.com")
        with self.assertRaises(ValidationError):
            self.add("Six", "six@example.com")

    def test_removing_an_ally_revokes_their_link_at_once(self) -> None:
        ally = self.add()
        token = self.mailer.token()
        self.allies.remove(SUB, ally["id"])
        with self.assertRaises(LinkInvalidError):
            self.links.view(token)


class AllyLinkTests(AllyTestCase):
    def test_nothing_is_shown_before_the_ally_confirms(self) -> None:
        self.add()
        self.repo.put(SUB, item("daily")["sk"], item("daily"))
        view = self.links.view(self.mailer.token())
        self.assertEqual((view["holder"], view["status"], view["items"]), ("Aaron", "invited", []))

    def test_confirming_is_recorded_and_shows_what_they_may_hear(self) -> None:
        self.clock.now = datetime(2026, 9, 30, 19, 0, tzinfo=UTC).timestamp()
        ally = self.confirmed(level="alerts_only")
        self.assertTrue(
            any(sk.startswith(f"ALLYCONSENT#{ally['id']}#") for _, sk in self.repo.records)
        )
        for grade, n in (("priority", 1), ("daily", 2)):
            entry = item(grade, n)
            self.repo.put(SUB, entry["sk"], entry)
        view = self.links.view(self.mailer.token())
        self.assertEqual([i["grade"] for i in view["items"]], ["priority"])

    def test_wrong_tokens_all_look_the_same(self) -> None:
        for token in (None, "short", "x" * 43):
            with self.subTest(token=token), self.assertRaises(LinkInvalidError):
                self.links.view(token)

    def shown(self, entry: dict[str, Any]) -> str:
        """A confirmed ally who can see this item; returns their token."""
        self.clock.now = datetime(2026, 9, 30, 19, 0, tzinfo=UTC).timestamp()
        self.confirmed()
        self.repo.put(SUB, entry["sk"], entry)
        return self.mailer.token()

    def learned(self) -> list[dict[str, Any]]:
        return [v for (_, sk), v in self.repo.records.items() if sk.startswith("SUPPRESSION#")]

    def test_this_was_nothing_needs_a_known_reason_and_a_shown_item(self) -> None:
        token = self.shown(item("daily"))
        self.links.dismiss(token, "0" * 15 + "1", "tv_or_radio")
        with self.assertRaises(ValidationError):
            self.links.dismiss(token, "0" * 15 + "1", "he hates it")
        with self.assertRaises(ValidationError):
            self.links.dismiss(token, "0" * 15 + "9", "tv_or_radio")  # not on their page

    def test_this_was_nothing_on_a_heads_up_is_learned(self) -> None:
        entry = dict(item("priority"), category="scam_pressure", signals=["urgency"])
        token = self.shown(entry)
        self.links.dismiss(token, entry["id"], "known_caller")
        [pattern] = self.learned()
        self.assertEqual(
            (pattern["category"], pattern["signals"], pattern["reason"], pattern["ally_name"]),
            ("scam_pressure", ["urgency"], "known_caller", "Dana"),
        )

    def test_a_daily_list_item_teaches_nothing(self) -> None:
        token = self.shown(item("daily"))
        self.links.dismiss(token, "0" * 15 + "1", "was_me")
        self.assertEqual(self.learned(), [])

    def test_the_account_holder_can_remove_what_was_learned(self) -> None:
        entry = dict(item("call_today"), signals=[])
        self.links.dismiss(self.shown(entry), entry["id"], "handled_it")
        [pattern] = self.learned()
        self.allies.forget(SUB, pattern["id"])
        self.assertEqual(self.learned(), [])
        with self.assertRaises(AllyNotFoundError):
            self.allies.forget(SUB, pattern["id"])
        with self.assertRaises(ValidationError):
            self.allies.forget(SUB, "not-an-id")


class PlanningTests(AllyTestCase):
    def queued(self) -> list[dict[str, Any]]:
        return sorted(
            (v for (pk, _), v in self.repo.records.items() if pk == "OUTBOX"), key=lambda e: e["sk"]
        )

    def test_a_heads_up_goes_at_once_to_every_ally_who_hears_them(self) -> None:
        self.confirmed()
        self.confirmed(name="Sam", email="sam@example.com", level="alerts_only")
        self.add("Pat", "pat@example.com")  # invited, never confirmed
        self.assertEqual(plan_deliveries(self.repo, SUB, [item("priority")], NIGHT), 2)
        self.assertTrue(
            all(e["kind"] == "heads_up" and e["due_epoch"] == int(NIGHT) for e in self.queued())
        )

    def test_worth_a_call_today_goes_to_the_first_ally_and_waits_for_morning(self) -> None:
        self.confirmed()
        self.confirmed(name="Sam", email="sam@example.com")
        plan_deliveries(self.repo, SUB, [item("call_today")], NIGHT)
        first, other = self.queued()
        self.assertEqual(first["kind"], "call_today")
        morning = datetime(2026, 10, 1, 13, 0, tzinfo=UTC).timestamp()  # 08:00 in Chicago
        self.assertEqual(first["due_epoch"], int(morning))
        self.assertEqual(other["kind"], "digest")

    def test_everything_else_waits_for_the_daily_list(self) -> None:
        self.confirmed()
        plan_deliveries(self.repo, SUB, [item("daily")], AFTERNOON)
        [entry] = self.queued()
        evening = datetime(2026, 9, 30, 23, 0, tzinfo=UTC).timestamp()  # 18:00 in Chicago
        self.assertEqual((entry["kind"], entry["due_epoch"]), ("digest", int(evening)))

    def test_allies_who_hear_nothing_get_nothing(self) -> None:
        self.confirmed(level="off")
        self.assertEqual(plan_deliveries(self.repo, SUB, [item("priority")], AFTERNOON), 0)


class DeliveryTests(AllyTestCase):
    def test_due_messages_are_sent_once_and_logged(self) -> None:
        ally = self.confirmed()
        for n in (1, 2):
            entry = item("daily", n)
            self.repo.put(SUB, entry["sk"], entry)
        plan_deliveries(self.repo, SUB, [item("daily", 1), item("daily", 2)], AFTERNOON)
        deliverer = Deliverer(self.repo, self.mailer, self.clock)
        self.assertEqual(deliverer.run()["sent"], 0)  # not due yet
        self.clock.now = AFTERNOON + 4 * 3600
        before = len(self.mailer.sent)
        self.assertEqual(deliverer.run(), {"sent": 1, "skipped": 0})
        mail = self.mailer.sent[before]
        self.assertEqual(
            (mail["to"], mail["subject"]), ("dana@example.com", "Hearally: today's list")
        )
        self.assertIn("Summary 1", mail["text"])
        self.assertIn("Summary 2", mail["text"])
        self.assertIn(NOT_EMERGENCY, mail["text"])
        self.assertIn("nothing", mail["text"])
        [sent] = [v for (_, sk), v in self.repo.records.items() if sk.startswith("SENT#")]
        self.assertEqual((sent["ally_name"], sent["kind"]), (ally["name"], "digest"))
        self.assertEqual(deliverer.run()["sent"], 0)  # never twice

    def test_a_heads_up_with_a_new_element_says_what_was_new(self) -> None:
        self.confirmed()
        entry = dict(item("priority"), new_signals=["secrecy"])
        self.repo.put(SUB, entry["sk"], entry)
        plan_deliveries(self.repo, SUB, [entry], AFTERNOON)
        Deliverer(self.repo, self.mailer, self.clock).run()
        self.assertIn("also asking to keep it secret", self.mailer.sent[-1]["text"])

    def test_an_ally_who_stopped_meanwhile_gets_nothing(self) -> None:
        self.confirmed()
        entry = item("priority")
        self.repo.put(SUB, entry["sk"], entry)
        plan_deliveries(self.repo, SUB, [entry], AFTERNOON)
        self.links.stop(self.mailer.token())
        before = len(self.mailer.sent)
        self.assertEqual(
            Deliverer(self.repo, self.mailer, self.clock).run(), {"sent": 0, "skipped": 1}
        )
        self.assertEqual(len(self.mailer.sent), before)


def request(action: str, body: dict[str, Any], sub: str | None = SUB) -> dict[str, Any]:
    req: dict[str, Any] = {
        "headers": {"x-hearally-edge": EDGE},
        "pathParameters": {"action": action},
        "body": json.dumps(body),
    }
    if sub:
        req["requestContext"] = {"authorizer": {"jwt": {"claims": {"sub": sub}}}}
    return req


class HandlerTests(AllyTestCase):
    def test_the_account_holder_manages_allies(self) -> None:
        edge = EdgeVerifier(lambda: EDGE)
        response = handle_manage(
            request("add", {"name": "Dana", "email": "dana@example.com"}), edge, self.allies
        )
        self.assertEqual(response["statusCode"], 201)
        ally = json.loads(response["body"])["ally"]
        self.assertNotIn("link_digest", ally)
        response = handle_manage(request("remove", {"ally_id": ally["id"]}), edge, self.allies)
        self.assertEqual(response["statusCode"], 200)
        response = handle_manage(request("forget", {"pattern_id": "a" * 16}), edge, self.allies)
        self.assertEqual(response["statusCode"], 404)
        self.assertEqual(
            handle_manage(request("add", {}, sub=None), edge, self.allies)["statusCode"], 401
        )

    def test_a_bad_link_is_a_plain_404(self) -> None:
        edge = EdgeVerifier(lambda: EDGE)
        response = handle_link(request("view", {"token": "x" * 43}, sub=None), edge, self.links)
        self.assertEqual(
            (response["statusCode"], json.loads(response["body"])),
            (404, {"error": "link_not_valid"}),
        )


if __name__ == "__main__":
    unittest.main()
