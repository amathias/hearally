"""Shared plumbing for Nebius Token Factory: chat completions and embeddings.

Token Factory is hosted inference. The account must have Zero Data Retention
enabled before anything carrying a transcript is sent (AGENTS.md).
"""

from __future__ import annotations

BASE_URL = "https://api.tokenfactory.nebius.com/v1"


def urllib_post(url: str, body: bytes, headers: dict[str, str], timeout: float) -> bytes:
    from urllib.request import Request, urlopen

    with urlopen(Request(url, data=body, headers=headers, method="POST"), timeout=timeout) as r:
        return bytes(r.read())
