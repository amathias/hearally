"""Heads-up calls: what the voice says, the CALL-E request, and the ally's number."""

from __future__ import annotations

import io
import json
import unittest
from datetime import UTC, datetime
from typing import Any

from test_allies import AFTERNOON, NIGHT, SUB, AllyTestCase, item

from hearally.allies import Deliverer, plan_deliveries
from hearally.ally_phone import DAILY_CODES, MAX_ATTEMPTS, AllyPhoneService, normalize_phone
from hearally.calls import MONTHLY_CALLS, CalleCaller, CallError, CallMessage, task
from hearally.models import ValidationError

MESSAGE = CallMessage(ally_name="Dana", holder="Aaron", summary="A caller asked for a card number.")


class TaskTests(unittest.TestCase):
    def test_a_notice_a_brief_summary_and_where_to_look(self) -> None:
        text = task(MESSAGE)
        for needed in (
            "Hi Dana, this is Hearally with a notice about Aaron.",
            "A caller asked for a card number.",
            "check your email from Hearally",
            "not an emergency service",
        ):
            with self.subTest(needed=needed):
                self.assertIn(needed, text)

    def test_it_never_advises_or_asks_for_anything(self) -> None:
        text = task(MESSAGE).lower()
        self.assertIn("do not ask for any information", text)
        self.assertIn("do not say that a scam or any danger is happening", text)
        for banned in ("urgent", "protect", "safety", "guarantee"):
            self.assertNotIn(banned, text)


class Response(io.BytesIO):
    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: Any) -> None:
        return None


class CalleCallerTests(unittest.TestCase):
    def test_one_post_with_the_key_and_an_idempotency_key(self) -> None:
        seen: list[Any] = []

        def opener(request: Any, timeout: float) -> Response:
            seen.append(request)
            return Response(json.dumps({"id": "call_abc123", "status": "queued"}).encode())

        call_id = CalleCaller(lambda: "test-key", opener=opener).call(
            "+15552010123", MESSAGE, idempotency_key="k1"
        )
        self.assertEqual(call_id, "call_abc123")
        [request] = seen
        self.assertEqual(request.full_url, "https://api.heycall-e.com/v1/calls")
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(request.get_header("Idempotency-key"), "k1")
        body = json.loads(request.data)
        self.assertEqual(body["recipients"][0]["phones"], ["+15552010123"])

    def test_refuses_without_a_key_or_a_valid_number(self) -> None:
        with self.assertRaises(CallError):
            CalleCaller(lambda: "").call("+15552010123", MESSAGE, idempotency_key="k")
        with self.assertRaises(CallError):
            CalleCaller(lambda: "k").call("555-0123", MESSAGE, idempotency_key="k")

    def test_a_rejected_call_is_an_error(self) -> None:
        def opener(request: Any, timeout: float) -> Response:
            return Response(json.dumps({"id": "call_x", "status": "failed"}).encode())

        with self.assertRaises(CallError):
            CalleCaller(lambda: "k", opener=opener).call(
                "+15552010123", MESSAGE, idempotency_key="k"
            )


class Sms:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_verification(self, **kwargs: Any) -> str:
        self.sent.append(kwargs)
        return "m1"


class Caller:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, CallMessage]] = []
        self.fail = fail

    def call(self, phone: str, message: CallMessage, *, idempotency_key: str) -> str:
        if self.fail:
            raise CallError("unavailable")
        self.calls.append((phone, message))
        return f"call_{len(self.calls)}"


class PhoneTestCase(AllyTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.sms = Sms()
        self.phone = AllyPhoneService(self.links, self.repo, self.sms, lambda: "p" * 40, self.clock)

    def verified(self) -> tuple[str, dict[str, Any]]:
        ally = self.confirmed()
        token = self.mailer.token()
        self.phone.start(token, "(555) 201-0123")
        self.phone.confirm(token, self.sms.sent[-1]["code"])
        return token, self.repo.ally(SUB, ally["id"]) or {}


class PhoneTests(PhoneTestCase):
    def test_numbers_are_us_and_normalised(self) -> None:
        self.assertEqual(normalize_phone("555 201 0123"), "+15552010123")
        self.assertEqual(normalize_phone("1-555-201-0123"), "+15552010123")
        for bad in ("+447700900123", "123", "", None):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                normalize_phone(bad)

    def test_a_code_by_text_proves_the_number_and_is_never_stored(self) -> None:
        token, ally = self.verified()
        code = self.sms.sent[-1]["code"]
        self.assertEqual((ally["phone"], ally["phone_last4"]), ("+15552010123", "0123"))
        self.assertNotIn(code, repr(self.repo.records))
        view = self.links.view(token)
        self.assertEqual(
            view["phone"], {"last4": "0123", "verified": True, "calls": False, "pending": False}
        )

    def test_wrong_codes_and_their_limit(self) -> None:
        self.confirmed()
        token = self.mailer.token()
        self.phone.start(token, "5552010123")
        for _ in range(MAX_ATTEMPTS):
            with self.assertRaises(ValidationError):
                self.phone.confirm(
                    token, "000000" if self.sms.sent[-1]["code"] != "000000" else "111111"
                )
        with self.assertRaises(ValidationError):
            self.phone.confirm(token, self.sms.sent[-1]["code"])  # locked now

    def test_codes_expire_and_are_limited_per_day(self) -> None:
        self.confirmed()
        token = self.mailer.token()
        self.phone.start(token, "5552010123")
        self.clock.now += 601
        with self.assertRaises(ValidationError):
            self.phone.confirm(token, self.sms.sent[-1]["code"])
        for _ in range(DAILY_CODES - 1):
            self.phone.start(token, "5552010123")
        with self.assertRaises(ValidationError):
            self.phone.start(token, "5552010123")

    def test_calls_need_a_proved_number_and_are_recorded(self) -> None:
        self.confirmed()
        token = self.mailer.token()
        with self.assertRaises(ValidationError):
            self.phone.set_calls(token, True)
        self.phone.start(token, "5552010123")
        self.phone.confirm(token, self.sms.sent[-1]["code"])
        self.phone.set_calls(token, True)
        decisions = [
            v["decision"]
            for (_, sk), v in self.repo.records.items()
            if sk.startswith("ALLYCONSENT#")
        ]
        self.assertIn("calls_on", decisions)

    def test_only_a_confirmed_ally_can_add_a_number(self) -> None:
        self.add()
        with self.assertRaises(ValidationError):
            self.phone.start(self.mailer.token(), "5552010123")


class CallDeliveryTests(PhoneTestCase):
    def calling(self, level: str = "alerts_only") -> str:
        token, ally = self.verified()
        self.repo.update_ally(SUB, ally["id"], {"level": level})
        self.phone.set_calls(token, True)
        return token

    def heads_up(self, n: int = 1, at: float = AFTERNOON) -> None:
        entry = item("priority", n)
        self.repo.put(SUB, entry["sk"], entry)
        plan_deliveries(self.repo, SUB, [entry], at)

    def outbox_kinds(self) -> list[str]:
        return sorted(v["kind"] for (pk, _), v in self.repo.records.items() if pk == "OUTBOX")

    def test_a_heads_up_brings_a_call_as_well_as_the_email(self) -> None:
        self.calling()
        self.heads_up()
        self.assertEqual(self.outbox_kinds(), ["call", "heads_up"])
        caller = Caller()
        Deliverer(self.repo, self.mailer, self.clock, caller=caller).run()
        [(phone, message)] = caller.calls
        self.assertEqual(
            (phone, message.ally_name, message.holder), ("+15552010123", "Dana", "Aaron")
        )
        sent = [
            v
            for (_, sk), v in self.repo.records.items()
            if sk.startswith("SENT#") and v["channel"] == "call"
        ]
        self.assertEqual([s["status"] for s in sent], ["placed"])

    def test_no_call_in_quiet_hours_or_without_calls_on(self) -> None:
        self.calling()
        self.heads_up(at=NIGHT)
        self.assertEqual(self.outbox_kinds(), ["heads_up"])

    def test_at_most_five_calls_a_month_per_account(self) -> None:
        self.calling()
        caller = Caller()
        deliverer = Deliverer(self.repo, self.mailer, self.clock, caller=caller)
        for n in range(1, MONTHLY_CALLS + 2):
            self.heads_up(n)
            deliverer.run()
        self.assertEqual(len(caller.calls), MONTHLY_CALLS)
        statuses = [
            v["status"]
            for (_, sk), v in self.repo.records.items()
            if sk.startswith("SENT#") and v["channel"] == "call"
        ]
        self.assertEqual(statuses.count("monthly_limit_reached"), 1)

    def test_a_failed_call_does_not_use_the_months_budget(self) -> None:
        self.calling()
        self.heads_up()
        Deliverer(self.repo, self.mailer, self.clock, caller=Caller(fail=True)).run()
        month = datetime.fromtimestamp(AFTERNOON, UTC).strftime("%Y-%m")
        self.assertEqual(self.repo.records[(f"USER#{SUB}", f"CALLBUDGET#{month}")]["used"], 0)


if __name__ == "__main__":
    unittest.main()
