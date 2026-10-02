from __future__ import annotations

import os
from dataclasses import dataclass

from hearally.audio import SCHEMA_VERSION as INGEST_SCHEMA_VERSION
from hearally.languages import SUPPORTED_LANGUAGE_TAGS

# The version of the signup acknowledgement (ARCHITECTURE.md section 9). Changing the
# wording means changing this, and everyone re-accepting before they upload.
# 2026-09-24 added line 7: record only what you have the legal right to record.
# 2026-10-01 added line 8: I am 18 or older. Cognito's sign-up page links the
# terms and privacy policy; the age confirmation is asked here, in our app.
ACKNOWLEDGEMENT_VERSION = "2026-10-01"


class ConfigurationError(RuntimeError):
    """Raised when required service configuration is invalid."""


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    service_version: str
    table_name: str
    processing_queue_url: str
    ingest_ttl_seconds: int = 604_800

    @classmethod
    def from_environment(cls) -> Settings:
        required = ("HEARALLY_TABLE_NAME", "PROCESSING_QUEUE_URL")
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ConfigurationError(
                "Hearally is not configured: missing " + ", ".join(sorted(missing))
            )

        return cls(
            environment=os.getenv("HEARALLY_ENV", "production"),
            service_version=os.getenv("HEARALLY_VERSION", "0.1.0"),
            table_name=os.environ["HEARALLY_TABLE_NAME"],
            processing_queue_url=os.environ["PROCESSING_QUEUE_URL"],
            ingest_ttl_seconds=_positive_int("INGEST_TTL_SECONDS", 604_800, 2_592_000),
        )

    def public_info(self) -> dict[str, object]:
        return {
            "service": "Hearally",
            "version": self.service_version,
            "environment": self.environment,
            "ingest_schema_version": INGEST_SCHEMA_VERSION,
            "acknowledgement_version": ACKNOWLEDGEMENT_VERSION,
            "default_language": "en",
            "supported_languages": list(SUPPORTED_LANGUAGE_TAGS),
        }


def _positive_int(name: str, default: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < 1 or value > maximum:
        raise ConfigurationError(f"{name} must be between 1 and {maximum}")
    return value
