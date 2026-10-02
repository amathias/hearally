from __future__ import annotations


class ValidationError(ValueError):
    """Raised when a request does not match its contract.

    Messages describe the request's shape, never its content, because handlers
    return them to the caller.
    """
