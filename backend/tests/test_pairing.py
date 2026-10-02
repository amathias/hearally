"""Pairing a computer with an account: codes, tokens, revocation, and the
DynamoDB adapter's conditions."""

from __future__ import annotations

import json
import unittest
from typing import Any

from hearally.edge import EdgeVerifier
from hearally.handlers.pairing import (
    handle_acknowledge,
    handle_create,
    handle_redeem,
    handle_revoke,
)
from hearally.models import ValidationError
from hearally.pairing import (
    PAIRING_TTL_SECONDS,
    AcknowledgementRequiredError,
    DeviceAuthenticationError,
    DynamoPairingRepository,
    InMemoryPairingRepository,
    PairingService,
    PairingUnavailableError,
    digest,
    parse_token,
)

VERSION = "2026-09-24"
ACCOUNT = "3f1c2a90-1111-4222-8333-944455556666"
OTHER = "9e8d7c6b-0000-4000-8000-000000000000"
EDGE = "edge-token"


class Clock:
    def __init__(self) -> None:
        self.now = 1_795_525_800

    def __call__(self) -> float:
        return self.now


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class PairingServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.repository = InMemoryPairingRepository()
        self.service = PairingService(
            self.repository, acknowledgement_version=VERSION, clock=self.clock
        )

    def pair(self, account: str = ACCOUNT) -> dict[str, str]:
        code = self.service.create_code(account, VERSION)["code"]
        return self.service.redeem(code, "Kitchen laptop")

    def test_the_acknowledgement_is_required_before_the_first_code(self) -> None:
        with self.assertRaises(AcknowledgementRequiredError):
            self.service.create_code(ACCOUNT)
        with self.assertRaises(AcknowledgementRequiredError):
            self.service.create_code(ACCOUNT, "2025-01-01")
        self.assertTrue(self.service.needs_acknowledgement(ACCOUNT))

    def test_the_acknowledgement_is_recorded_once_and_not_asked_again(self) -> None:
        self.service.create_code(ACCOUNT, VERSION)
        self.assertFalse(self.service.needs_acknowledgement(ACCOUNT))
        self.service.create_code(ACCOUNT)  # no version needed now
        consent = self.repository.items[(f"USER#{ACCOUNT}", f"CONSENT#{VERSION}")]
        self.assertEqual(consent["method"], "dashboard")

    def test_codes_are_long_random_and_stored_only_as_digests(self) -> None:
        issued = self.service.create_code(ACCOUNT, VERSION)
        self.assertEqual(len(issued["code"]), 32)
        self.assertNotEqual(issued["code"], self.service.create_code(ACCOUNT)["code"])
        self.assertIn(digest(issued["code"]), self.repository.codes)
        self.assertNotIn(issued["code"], json.dumps(self.repository.codes))

    def test_a_code_pairs_one_computer_and_only_once(self) -> None:
        code = self.service.create_code(ACCOUNT, VERSION)["code"]
        paired = self.service.redeem(code, "Kitchen laptop")
        account, device_id, _ = parse_token(paired["token"])
        self.assertEqual((account, device_id), (ACCOUNT, paired["device_id"]))
        with self.assertRaises(PairingUnavailableError):
            self.service.redeem(code, "Another laptop")

    def test_a_code_expires_after_ten_minutes(self) -> None:
        code = self.service.create_code(ACCOUNT, VERSION)["code"]
        self.clock.now += PAIRING_TTL_SECONDS
        with self.assertRaises(PairingUnavailableError):
            self.service.redeem(code, "Kitchen laptop")

    def test_a_malformed_code_or_name_is_a_validation_error(self) -> None:
        code = self.service.create_code(ACCOUNT, VERSION)["code"]
        for bad_code, name in (("short", "x"), (12, "x"), (code, ""), (code, "x" * 61), (code, 5)):
            with self.subTest(code=bad_code, name=name), self.assertRaises(ValidationError):
                self.service.redeem(bad_code, name)

    def test_the_token_authenticates_its_computer(self) -> None:
        paired = self.pair()
        computer = self.service.authenticate(bearer(paired["token"]))
        self.assertEqual((computer.account_id, computer.device_id), (ACCOUNT, paired["device_id"]))

    def test_the_stored_computer_holds_a_digest_never_the_secret(self) -> None:
        paired = self.pair()
        secret = paired["token"].split(".")[3]
        record = self.repository.computer(ACCOUNT, paired["device_id"])
        assert record is not None
        self.assertEqual(record["credential_digest"], digest(secret))
        self.assertNotIn(secret, json.dumps(record))
        self.assertEqual(record["name"], "Kitchen laptop")

    def test_anything_but_a_live_token_is_refused(self) -> None:
        paired = self.pair()
        account, device_id, secret = parse_token(paired["token"])
        for headers in (
            {},
            {"Authorization": paired["token"]},  # no scheme
            {"Authorization": "Basic abc"},
            bearer("hd2." + paired["token"][4:]),
            bearer(f"hd1.{account}.{device_id}.{'A' * 43}"),  # wrong secret
            bearer(f"hd1.{account}.{'0' * 32}.{secret}"),  # unknown computer
            bearer(f"hd1.{OTHER}.{device_id}.{secret}"),  # moved to another account
        ):
            with self.subTest(headers=headers), self.assertRaises(DeviceAuthenticationError):
                self.service.authenticate(headers)

    def test_revoking_stops_the_computer_and_forgets_its_digest(self) -> None:
        paired = self.pair()
        self.assertTrue(self.service.revoke(ACCOUNT, paired["device_id"]))
        with self.assertRaises(DeviceAuthenticationError):
            self.service.authenticate(bearer(paired["token"]))
        record = self.repository.computer(ACCOUNT, paired["device_id"])
        assert record is not None
        self.assertNotIn("credential_digest", record)

    def test_an_account_cannot_revoke_another_accounts_computer(self) -> None:
        paired = self.pair()
        self.assertFalse(self.service.revoke(OTHER, paired["device_id"]))
        self.service.authenticate(bearer(paired["token"]))  # still works
        self.assertFalse(self.service.revoke(ACCOUNT, "../../etc"))


def signed_in(
    sub: str = ACCOUNT, body: dict[str, Any] | None = None, **extra: Any
) -> dict[str, Any]:
    request: dict[str, Any] = {
        "headers": {"x-hearally-edge": EDGE},
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": sub}}}},
        **extra,
    }
    if body is not None:
        request["body"] = json.dumps(body)
    return request


class PairingHandlerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.edge = EdgeVerifier(lambda: EDGE)
        self.service = PairingService(
            InMemoryPairingRepository(), acknowledgement_version=VERSION, clock=Clock()
        )

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        return handle_create(request, self.edge, self.service, VERSION)

    def redeem(self, body: Any) -> dict[str, Any]:
        return handle_redeem(
            {"headers": {"x-hearally-edge": EDGE}, "body": json.dumps(body)},
            self.edge,
            self.service,
        )

    def test_create_asks_for_the_acknowledgement_first(self) -> None:
        response = self.create(signed_in())
        self.assertEqual(response["statusCode"], 409)
        self.assertEqual(
            json.loads(response["body"]), {"error": "acknowledgement_required", "version": VERSION}
        )

    def test_create_returns_a_code_that_is_never_cached(self) -> None:
        response = self.create(signed_in(body={"acknowledgement_version": VERSION}))
        self.assertEqual(response["statusCode"], 201)
        self.assertEqual(len(json.loads(response["body"])["code"]), 32)
        self.assertEqual(response["headers"]["cache-control"], "private, no-store")

    def test_create_refuses_unknown_fields_and_missing_identity(self) -> None:
        self.assertEqual(self.create(signed_in(body={"account": OTHER}))["statusCode"], 400)
        self.assertEqual(self.create(signed_in(sub=""))["statusCode"], 401)
        no_edge = signed_in()
        no_edge["headers"] = {}
        self.assertEqual(self.create(no_edge)["statusCode"], 403)

    def test_redeem_returns_a_token_once(self) -> None:
        code = json.loads(
            self.create(signed_in(body={"acknowledgement_version": VERSION}))["body"]
        )["code"]
        first = self.redeem({"code": code, "computer_name": "Kitchen laptop"})
        self.assertEqual(first["statusCode"], 201)
        self.assertTrue(json.loads(first["body"])["token"].startswith("hd1."))
        again = self.redeem({"code": code, "computer_name": "Kitchen laptop"})
        self.assertEqual(again["statusCode"], 404)
        self.assertEqual(json.loads(again["body"]), {"error": "pairing_unavailable"})

    def test_redeem_refuses_a_bad_request_without_detail_about_codes(self) -> None:
        self.assertEqual(self.redeem({"code": "x" * 32})["statusCode"], 400)
        self.assertEqual(self.redeem(["not", "an", "object"])["statusCode"], 400)
        unknown = self.redeem({"code": "x" * 32, "computer_name": "Laptop"})
        self.assertEqual(unknown["statusCode"], 404)

    def test_revoke_is_scoped_to_the_signed_in_account(self) -> None:
        code = self.service.create_code(ACCOUNT, VERSION)["code"]
        device_id = self.service.redeem(code, "Laptop")["device_id"]
        path = {"pathParameters": {"device_id": device_id}}
        self.assertEqual(
            handle_revoke(signed_in(OTHER, **path), self.edge, self.service)["statusCode"], 404
        )
        self.assertEqual(
            handle_revoke(signed_in(ACCOUNT, **path), self.edge, self.service)["statusCode"], 200
        )


# ---------- the DynamoDB adapter ----------


class ConditionalCheckFailedException(Exception):
    pass


class TransactionCanceledException(Exception):
    def __init__(self, reasons: list[dict[str, str]]) -> None:
        super().__init__("cancelled")
        self.response = {"CancellationReasons": reasons}


class FakeTable:
    """Enough of DynamoDB to honour the conditions the pairing adapter uses."""

    name = "hearally-prod"

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.reads: list[bool] = []
        outer = self

        class Client:
            class exceptions:
                ConditionalCheckFailedException = ConditionalCheckFailedException
                TransactionCanceledException = TransactionCanceledException

            @staticmethod
            def transact_write_items(TransactItems: list[dict[str, Any]]) -> None:
                outer.transact(TransactItems)

        class Meta:
            client = Client()

        self.meta = Meta()

    def put_item(self, Item: dict[str, Any], ConditionExpression: str) -> None:
        assert ConditionExpression == "attribute_not_exists(pk)"
        key = (Item["pk"], Item["sk"])
        if key in self.items:
            raise ConditionalCheckFailedException()
        self.items[key] = dict(Item)

    def get_item(self, Key: dict[str, str], ConsistentRead: bool = False) -> dict[str, Any]:
        self.reads.append(ConsistentRead)
        item = self.items.get((Key["pk"], Key["sk"]))
        return {"Item": dict(item)} if item else {}

    def update_item(self, Key: dict[str, str], UpdateExpression: str, **kwargs: Any) -> None:
        key = (Key["pk"], Key["sk"])
        if kwargs.get("ConditionExpression") == "attribute_exists(pk)" and key not in self.items:
            raise ConditionalCheckFailedException()
        item = self.items.setdefault(key, {"pk": key[0], "sk": key[1]})
        names = kwargs.get("ExpressionAttributeNames", {})
        values = kwargs.get("ExpressionAttributeValues", {})
        if "REMOVE credential_digest" in UpdateExpression:
            item.pop("credential_digest", None)
        if ":revoked" in values:
            item["status"] = values[":revoked"]
        if ":now" in values and "#seen" not in names:
            item["last_seen_at"] = values[":now"]
        if "#count" in names:
            item.update(
                {"id": values[":id"], "model": values[":model"], "last_seen_at": values[":now"]}
            )
            item["recordings"] = item.get("recordings", 0) + values[":one"]

    def transact(self, operations: list[dict[str, Any]]) -> None:
        delete = operations[0]["Delete"]
        put = operations[1]["Put"]
        key = (delete["Key"]["pk"], delete["Key"]["sk"])
        now = delete["ExpressionAttributeValues"][":now"]
        existing = self.items.get(key)
        if existing is None or existing["expires_epoch"] <= now:
            raise TransactionCanceledException(
                [{"Code": "ConditionalCheckFailed"}, {"Code": "None"}]
            )
        del self.items[key]
        self.items[(put["Item"]["pk"], put["Item"]["sk"])] = dict(put["Item"])


class DynamoPairingRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.table = FakeTable()
        self.clock = Clock()
        self.service = PairingService(
            DynamoPairingRepository(self.table), acknowledgement_version=VERSION, clock=self.clock
        )

    def test_codes_live_under_their_digest_with_a_ttl(self) -> None:
        code = self.service.create_code(ACCOUNT, VERSION)["code"]
        item = self.table.items[(f"PAIR#{digest(code)}", "CODE")]
        self.assertEqual(item["ttl_epoch"], self.clock.now + PAIRING_TTL_SECONDS)
        self.assertEqual(item["account_id"], ACCOUNT)

    def test_redeeming_consumes_the_code_and_stores_the_computer_under_the_account(self) -> None:
        code = self.service.create_code(ACCOUNT, VERSION)["code"]
        paired = self.service.redeem(code, "Laptop")
        self.assertNotIn((f"PAIR#{digest(code)}", "CODE"), self.table.items)
        self.assertIn((f"USER#{ACCOUNT}", f"COMPUTER#{paired['device_id']}"), self.table.items)
        with self.assertRaises(PairingUnavailableError):
            self.service.redeem(code, "Laptop")

    def test_throttling_during_redeem_is_not_reported_as_an_expired_code(self) -> None:
        code = self.service.create_code(ACCOUNT, VERSION)["code"]

        def throttled(operations: list[dict[str, Any]]) -> None:
            raise TransactionCanceledException([{"Code": "ThrottlingError"}])

        self.table.transact = throttled  # type: ignore[method-assign]
        with self.assertRaises(TransactionCanceledException):
            self.service.redeem(code, "Laptop")

    def test_authentication_reads_consistently_so_revocation_is_immediate(self) -> None:
        code = self.service.create_code(ACCOUNT, VERSION)["code"]
        paired = self.service.redeem(code, "Laptop")
        self.table.reads.clear()
        self.service.authenticate(bearer(paired["token"]))
        self.assertEqual(self.table.reads, [True])
        self.assertTrue(self.service.revoke(ACCOUNT, paired["device_id"]))
        with self.assertRaises(DeviceAuthenticationError):
            self.service.authenticate(bearer(paired["token"]))
        self.assertFalse(self.service.revoke(ACCOUNT, "f" * 32))

    def test_the_acknowledgement_is_recorded_once(self) -> None:
        self.service.create_code(ACCOUNT, VERSION)
        first = dict(self.table.items[(f"USER#{ACCOUNT}", f"CONSENT#{VERSION}")])
        self.clock.now += 100
        DynamoPairingRepository(self.table).record_acknowledgement(
            ACCOUNT, VERSION, int(self.clock.now)
        )
        self.assertEqual(self.table.items[(f"USER#{ACCOUNT}", f"CONSENT#{VERSION}")], first)

    def test_activity_updates_the_computer_and_counts_the_plauds_recordings(self) -> None:
        code = self.service.create_code(ACCOUNT, VERSION)["code"]
        paired = self.service.redeem(code, "Laptop")
        computer = self.service.authenticate(bearer(paired["token"]))
        self.service.record_activity(computer, "note-pro", "note_pro")
        self.service.record_activity(computer, "note-pro", "note_pro")
        self.service.record_activity(computer, None, None)
        recorder = self.table.items[(f"USER#{ACCOUNT}", "DEVICE#note-pro")]
        self.assertEqual(recorder["recordings"], 2)
        self.assertIn(
            "last_seen_at", self.table.items[(f"USER#{ACCOUNT}", f"COMPUTER#{paired['device_id']}")]
        )

    def test_a_removed_computer_is_not_recreated_by_late_activity(self) -> None:
        repository = DynamoPairingRepository(self.table)
        repository.seen(ACCOUNT, "a" * 32, 1)
        self.assertNotIn((f"USER#{ACCOUNT}", f"COMPUTER#{'a' * 32}"), self.table.items)


if __name__ == "__main__":
    unittest.main()


class AcknowledgeHandlerTests(unittest.TestCase):
    """Accepting the acknowledgement without pairing a computer: someone who
    connects only through Plaud sign-in must be able to agree too."""

    def setUp(self) -> None:
        self.edge = EdgeVerifier(lambda: EDGE)
        self.repository = InMemoryPairingRepository()
        self.service = PairingService(
            self.repository, acknowledgement_version=VERSION, clock=Clock()
        )

    def call(self, request: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        response = handle_acknowledge(request, self.edge, self.service, VERSION)
        return response["statusCode"], json.loads(response["body"])

    def test_the_current_version_is_recorded_once(self) -> None:
        status, body = self.call(signed_in(body={"acknowledgement_version": VERSION}))
        self.assertEqual((status, body["new"]), (200, True))
        self.assertFalse(self.service.needs_acknowledgement(ACCOUNT))
        _, body = self.call(signed_in(body={"acknowledgement_version": VERSION}))
        self.assertFalse(body["new"])

    def test_an_older_version_must_be_read_again(self) -> None:
        status, body = self.call(signed_in(body={"acknowledgement_version": "2026-01-01"}))
        self.assertEqual(
            (status, body), (409, {"error": "acknowledgement_changed", "version": VERSION})
        )
        self.assertTrue(self.service.needs_acknowledgement(ACCOUNT))

    def test_needs_a_signed_in_account_and_exactly_the_version(self) -> None:
        request = signed_in(body={"acknowledgement_version": VERSION})
        request["requestContext"] = {}
        self.assertEqual(self.call(request)[0], 401)
        self.assertEqual(
            self.call(signed_in(body={"acknowledgement_version": VERSION, "x": 1}))[0], 400
        )
