from __future__ import annotations

import json
import unittest

from hearally.edge import EdgeVerificationError, EdgeVerifier
from hearally.secret_cache import (
    CachedValue,
    current_and_previous_field,
    current_field,
    parse_field,
)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeSecrets:
    """Secrets Manager with a current and, optionally, a previous version."""

    def __init__(self, current: str, previous: str | None = None) -> None:
        self.current = current
        self.previous = previous
        self.calls = 0

    def get_secret_value(self, SecretId: str, VersionStage: str = "AWSCURRENT") -> dict[str, str]:
        self.calls += 1
        if VersionStage == "AWSPREVIOUS":
            if self.previous is None:
                raise RuntimeError("ResourceNotFoundException")
            return {"SecretString": self.previous}
        return {"SecretString": self.current}


class CachedValueTests(unittest.TestCase):
    def test_fetches_once_within_the_window(self) -> None:
        calls = []
        clock = Clock()
        cached = CachedValue(lambda: calls.append(1) or "v", ttl_seconds=300, clock=clock)
        cached()
        clock.now = 299
        cached()
        self.assertEqual(len(calls), 1)

    def test_refetches_after_the_window(self) -> None:
        """Review F05: a rotated secret must take effect without a redeploy."""
        secrets = FakeSecrets(json.dumps({"secret": "old"}))
        clock = Clock()
        provider = current_field(secrets, "id", "secret", ttl_seconds=300, clock=clock)

        self.assertEqual(provider(), "old")
        secrets.current = json.dumps({"secret": "new"})
        self.assertEqual(provider(), "old", "still cached inside the window")

        clock.now = 300
        self.assertEqual(provider(), "new")


class ParseFieldTests(unittest.TestCase):
    def test_reads_a_json_field(self) -> None:
        self.assertEqual(parse_field('{"token":"abc"}', "token"), "abc")

    def test_accepts_a_plain_string(self) -> None:
        self.assertEqual(parse_field("abc", "token"), "abc")

    def test_refuses_json_without_the_field(self) -> None:
        with self.assertRaises(RuntimeError):
            parse_field('{"other":"abc"}', "token")


class EdgeRotationTests(unittest.TestCase):
    def test_accepts_both_versions_during_a_rotation(self) -> None:
        secrets = FakeSecrets(json.dumps({"token": "new"}), json.dumps({"token": "old"}))
        verifier = EdgeVerifier(current_and_previous_field(secrets, "id", "token"))
        verifier.verify({"x-hearally-edge": "new"})
        verifier.verify({"x-hearally-edge": "old"})

    def test_still_rejects_anything_else(self) -> None:
        secrets = FakeSecrets(json.dumps({"token": "new"}), json.dumps({"token": "old"}))
        verifier = EdgeVerifier(current_and_previous_field(secrets, "id", "token"))
        with self.assertRaises(EdgeVerificationError):
            verifier.verify({"x-hearally-edge": "neither"})

    def test_works_before_any_rotation_has_happened(self) -> None:
        """There is no AWSPREVIOUS until the first rotation."""
        secrets = FakeSecrets(json.dumps({"token": "only"}))
        verifier = EdgeVerifier(current_and_previous_field(secrets, "id", "token"))
        verifier.verify({"x-hearally-edge": "only"})

    def test_an_empty_candidate_list_still_fails_closed(self) -> None:
        verifier = EdgeVerifier(lambda: ("", "  "))
        with self.assertRaises(EdgeVerificationError):
            verifier.verify({"x-hearally-edge": "anything"})


if __name__ == "__main__":
    unittest.main()
