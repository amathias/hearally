"""Proof that a request arrived through CloudFront.

WAF cannot attach to an HTTP API directly, so the protections live on a
CloudFront distribution and the API Gateway hostname stays reachable. Without
this check, anyone who discovered the execute-api hostname could skip the rate
limiting and the managed rule groups entirely.

CloudFront adds a shared secret header to every origin request. Anything
missing it never came through the edge.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Mapping, Sequence

EDGE_HEADER = "x-hearally-edge"


class EdgeVerificationError(PermissionError):
    """Raised when a request did not arrive through the edge."""


class EdgeVerifier:
    """Checks the shared secret CloudFront attaches to origin requests.

    The token is resolved lazily so a Lambda that never serves a request does
    not pay for a Secrets Manager call, and compared in constant time so a
    timing signal cannot leak it a byte at a time.
    """

    def __init__(
        self,
        token_provider: Callable[[], str | Sequence[str]],
        *,
        required: bool = True,
    ) -> None:
        # The provider may return several tokens: the current and previous
        # versions during a rotation. Any one of them is accepted.
        self._token_provider = token_provider
        self._required = required

    def verify(self, headers: Mapping[str, str]) -> None:
        if not self._required:
            return

        supplied = ""
        for key, value in headers.items():
            if key.lower() == EDGE_HEADER:
                supplied = (value or "").strip()
                break

        if not supplied:
            raise EdgeVerificationError("Request did not arrive through the edge")

        provided = self._token_provider()
        candidates = [provided] if isinstance(provided, str) else list(provided)
        expected = [token.strip() for token in candidates if token and token.strip()]
        if not expected:
            # A misconfigured secret must fail closed. Treating an empty
            # expected value as "allow anything" would silently disable the
            # check the moment the secret was cleared.
            raise EdgeVerificationError("Edge token is not configured")

        # Compare against every candidate rather than stopping at the first
        # match, so the time taken does not reveal which version matched.
        matched = False
        for token in expected:
            matched |= hmac.compare_digest(supplied, token)
        if not matched:
            raise EdgeVerificationError("Edge token did not match")
