from __future__ import annotations

import unittest

from hearally.edge import EdgeVerificationError, EdgeVerifier


class EdgeVerifierTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls = 0

    def _provider(self, token: str = "correct-horse-battery-staple"):
        def provide() -> str:
            self.calls += 1
            return token

        return provide

    def test_accepts_matching_token(self) -> None:
        verifier = EdgeVerifier(self._provider())
        verifier.verify({"x-hearally-edge": "correct-horse-battery-staple"})

    def test_header_name_is_case_insensitive(self) -> None:
        verifier = EdgeVerifier(self._provider())
        verifier.verify({"X-Hearally-Edge": "correct-horse-battery-staple"})

    def test_surrounding_whitespace_is_ignored(self) -> None:
        verifier = EdgeVerifier(self._provider())
        verifier.verify({"x-hearally-edge": "  correct-horse-battery-staple  "})

    def test_rejects_missing_header(self) -> None:
        verifier = EdgeVerifier(self._provider())
        with self.assertRaises(EdgeVerificationError):
            verifier.verify({})

    def test_rejects_empty_header(self) -> None:
        verifier = EdgeVerifier(self._provider())
        with self.assertRaises(EdgeVerificationError):
            verifier.verify({"x-hearally-edge": "   "})

    def test_rejects_wrong_token(self) -> None:
        verifier = EdgeVerifier(self._provider())
        with self.assertRaises(EdgeVerificationError):
            verifier.verify({"x-hearally-edge": "not-the-token"})

    def test_rejects_prefix_of_the_token(self) -> None:
        """A prefix must not pass: comparison is whole-value, not startswith."""
        verifier = EdgeVerifier(self._provider())
        with self.assertRaises(EdgeVerificationError):
            verifier.verify({"x-hearally-edge": "correct-horse"})

    def test_fails_closed_when_the_secret_is_empty(self) -> None:
        """An unset secret must reject, never allow everything through."""
        verifier = EdgeVerifier(self._provider(""))
        with self.assertRaises(EdgeVerificationError):
            verifier.verify({"x-hearally-edge": "anything"})

    def test_does_not_resolve_the_secret_when_the_header_is_absent(self) -> None:
        """No Secrets Manager call for a request that cannot possibly pass."""
        verifier = EdgeVerifier(self._provider())
        with self.assertRaises(EdgeVerificationError):
            verifier.verify({})
        self.assertEqual(self.calls, 0)

    def test_can_be_disabled_for_local_development(self) -> None:
        verifier = EdgeVerifier(self._provider(), required=False)
        verifier.verify({})
        self.assertEqual(self.calls, 0)


class ConfigHandlerEdgeTests(unittest.TestCase):
    """The verifier is only worth anything if a handler actually calls it."""

    def setUp(self) -> None:
        from hearally.config import Settings

        self.settings = Settings(
            environment="production",
            service_version="9.9.9",
            table_name="table",
            processing_queue_url="https://queue",
        )

    def test_returns_config_when_the_edge_token_matches(self) -> None:
        import json

        from hearally.handlers.config import handle

        response = handle(
            self.settings,
            headers={"x-hearally-edge": "token"},
            edge=EdgeVerifier(lambda: "token"),
        )
        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(json.loads(response["body"])["version"], "9.9.9")

    def test_returns_403_without_the_edge_token(self) -> None:
        from hearally.handlers.config import handle

        response = handle(self.settings, headers={}, edge=EdgeVerifier(lambda: "token"))
        self.assertEqual(response["statusCode"], 403)

    def test_403_body_does_not_name_the_header(self) -> None:
        """Naming the header would tell an attacker what to forge."""
        from hearally.handlers.config import handle

        response = handle(self.settings, headers={}, edge=EdgeVerifier(lambda: "token"))
        self.assertNotIn("edge", response["body"].lower())

    def test_works_without_a_verifier_at_all(self) -> None:
        from hearally.handlers.config import handle

        response = handle(self.settings)
        self.assertEqual(response["statusCode"], 200)


if __name__ == "__main__":
    unittest.main()
