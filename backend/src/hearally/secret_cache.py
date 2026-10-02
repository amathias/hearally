"""Secrets Manager values, cached for a bounded time.

A Lambda container can live for hours. Caching a secret for the container's
whole life means rotating it does nothing until AWS happens to recycle the
container: an old HMAC secret keeps being accepted and the new one rejected.
Five minutes bounds that window while still costing only one Secrets Manager
call per container per five minutes.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

DEFAULT_TTL_SECONDS = 300


class CachedValue:
    """Calls ``fetch`` at most once per ``ttl_seconds``."""

    def __init__(
        self,
        fetch: Callable[[], Any],
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._clock = clock
        self._value: Any = None
        self._fetched_at: float | None = None

    def __call__(self) -> Any:
        now = self._clock()
        if self._fetched_at is None or now - self._fetched_at >= self._ttl:
            self._value = self._fetch()
            self._fetched_at = now
        return self._value


def parse_field(raw: str, field: str) -> str:
    """One string field of a JSON secret, or the raw value if it is not JSON.

    Plain strings are accepted so a value set by hand in the console still works.
    """
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(decoded, dict) or not isinstance(decoded.get(field), str):
        raise RuntimeError(f"Secret JSON must contain a string field named {field}")
    return str(decoded[field])


def current_field(client: Any, secret_id: str, field: str, **cache: Any) -> CachedValue:
    def fetch() -> str:
        return parse_field(client.get_secret_value(SecretId=secret_id)["SecretString"], field)

    return CachedValue(fetch, **cache)


def current_and_previous_field(
    client: Any, secret_id: str, field: str, **cache: Any
) -> CachedValue:
    """Both the current and previous versions, for secrets rotated in place.

    When Terraform rotates the edge token, CloudFront starts sending the new
    value at once while warm Lambdas still hold the old one. Accepting the
    previous version as well turns that into a clean handover instead of five
    minutes of 403s. There is no previous version until the first rotation.
    """

    def fetch() -> tuple[str, ...]:
        values = [parse_field(client.get_secret_value(SecretId=secret_id)["SecretString"], field)]
        try:
            previous = client.get_secret_value(SecretId=secret_id, VersionStage="AWSPREVIOUS")
        except Exception:
            return tuple(values)
        values.append(parse_field(previous["SecretString"], field))
        return tuple(values)

    return CachedValue(fetch, **cache)
