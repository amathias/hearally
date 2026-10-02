from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol


class VerificationError(ValueError):
    """A channel challenge cannot be created or confirmed."""


class VerificationRateLimited(VerificationError):
    pass


class TargetAlreadyClaimed(VerificationError):
    pass


class EmailVerificationSender(Protocol):
    def send_verification(
        self,
        *,
        email: str,
        token: str,
        challenge_id: str,
        expires_at: int,
        locale: str,
    ) -> str: ...


class SmsVerificationSender(Protocol):
    def send_verification(
        self,
        *,
        phone_e164: str,
        code: str,
        challenge_id: str,
        expires_at: int,
        locale: str,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class VerificationChallenge:
    challenge_id: str
    contact_id: str
    channel: str
    target: str
    target_fingerprint: str
    proof_digest: str
    status: str
    attempts: int
    created_at: int
    expires_at: int
    provider_message_id: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationStart:
    challenge_id: str
    channel: str
    masked_target: str
    expires_at: int


class VerificationRepository(Protocol):
    def count_recent(
        self,
        *,
        contact_id: str,
        channel: str,
        target_fingerprint: str,
        since: int,
    ) -> int: ...

    def save(self, challenge: VerificationChallenge) -> None: ...

    def get(self, challenge_id: str) -> VerificationChallenge | None: ...

    def update(self, challenge: VerificationChallenge) -> None: ...

    def claim_verified_target(
        self, *, contact_id: str, channel: str, target: str, verified_at: int
    ) -> bool: ...


class InMemoryVerificationRepository:
    def __init__(self) -> None:
        self.challenges: dict[str, VerificationChallenge] = {}
        self.verified_targets: dict[tuple[str, str], tuple[str, int]] = {}
        self.target_owners: dict[tuple[str, str], str] = {}

    def count_recent(
        self,
        *,
        contact_id: str,
        channel: str,
        target_fingerprint: str,
        since: int,
    ) -> int:
        return sum(
            challenge.created_at >= since
            and challenge.channel == channel
            and (
                challenge.contact_id == contact_id
                or challenge.target_fingerprint == target_fingerprint
            )
            for challenge in self.challenges.values()
        )

    def save(self, challenge: VerificationChallenge) -> None:
        if challenge.challenge_id in self.challenges:
            raise VerificationError("Challenge identifier collision")
        self.challenges[challenge.challenge_id] = challenge

    def get(self, challenge_id: str) -> VerificationChallenge | None:
        return self.challenges.get(challenge_id)

    def update(self, challenge: VerificationChallenge) -> None:
        self.challenges[challenge.challenge_id] = challenge

    def claim_verified_target(
        self, *, contact_id: str, channel: str, target: str, verified_at: int
    ) -> bool:
        owner_key = (channel, target)
        owner = self.target_owners.get(owner_key)
        if owner is not None and owner != contact_id:
            return False
        self.target_owners[owner_key] = contact_id
        self.verified_targets[(contact_id, channel)] = (target, verified_at)
        return True


class ContactVerificationService:
    def __init__(
        self,
        repository: VerificationRepository,
        email_sender: EmailVerificationSender,
        sms_sender: SmsVerificationSender,
        *,
        pepper: bytes,
        clock: Callable[[], float] = time.time,
        daily_limit: int = 3,
        max_attempts: int = 5,
    ) -> None:
        if len(pepper) < 32:
            raise ValueError("Verification pepper must contain at least 32 bytes")
        self._repository = repository
        self._email_sender = email_sender
        self._sms_sender = sms_sender
        self._pepper = pepper
        self._clock = clock
        self._daily_limit = daily_limit
        self._max_attempts = max_attempts

    def start_email(self, *, contact_id: str, email: str, locale: str) -> VerificationStart:
        target = _normalize_email(email)
        proof = secrets.token_urlsafe(32)
        return self._start(
            contact_id=contact_id,
            channel="email",
            target=target,
            proof=proof,
            locale=locale,
            lifetime_seconds=86_400,
            sender=lambda challenge_id, expires_at: self._email_sender.send_verification(
                email=target,
                token=proof,
                challenge_id=challenge_id,
                expires_at=expires_at,
                locale=locale,
            ),
        )

    def start_sms(self, *, contact_id: str, phone_e164: str, locale: str) -> VerificationStart:
        target = _normalize_phone(phone_e164)
        proof = f"{secrets.randbelow(1_000_000):06d}"
        return self._start(
            contact_id=contact_id,
            channel="sms",
            target=target,
            proof=proof,
            locale=locale,
            lifetime_seconds=600,
            sender=lambda challenge_id, expires_at: self._sms_sender.send_verification(
                phone_e164=target,
                code=proof,
                challenge_id=challenge_id,
                expires_at=expires_at,
                locale=locale,
            ),
        )

    def _start(
        self,
        *,
        contact_id: str,
        channel: str,
        target: str,
        proof: str,
        locale: str,
        lifetime_seconds: int,
        sender: Callable[[str, int], str],
    ) -> VerificationStart:
        now = int(self._clock())
        target_fingerprint = self._target_fingerprint(channel, target)
        recent = self._repository.count_recent(
            contact_id=contact_id,
            channel=channel,
            target_fingerprint=target_fingerprint,
            since=now - 86_400,
        )
        if recent >= self._daily_limit:
            raise VerificationRateLimited("Verification send limit reached")

        challenge_id = "verify_" + secrets.token_urlsafe(18)
        expires_at = now + lifetime_seconds
        challenge = VerificationChallenge(
            challenge_id=challenge_id,
            contact_id=contact_id,
            channel=channel,
            target=target,
            target_fingerprint=target_fingerprint,
            proof_digest=self._proof_digest(challenge_id, proof),
            status="pending",
            attempts=0,
            created_at=now,
            expires_at=expires_at,
        )
        self._repository.save(challenge)
        try:
            provider_message_id = sender(challenge_id, expires_at)
        except Exception:
            self._repository.update(replace(challenge, status="delivery_failed", proof_digest=""))
            raise
        self._repository.update(replace(challenge, provider_message_id=provider_message_id))
        return VerificationStart(
            challenge_id=challenge_id,
            channel=channel,
            masked_target=_mask_target(channel, target),
            expires_at=expires_at,
        )

    def confirm(self, *, challenge_id: str, proof: str) -> str:
        now = int(self._clock())
        challenge = self._repository.get(challenge_id)
        if challenge is None or challenge.status != "pending":
            raise VerificationError("Challenge is not pending")
        if challenge.expires_at <= now:
            self._repository.update(replace(challenge, status="expired", proof_digest=""))
            raise VerificationError("Challenge has expired")
        if challenge.attempts >= self._max_attempts:
            raise VerificationError("Challenge attempt limit reached")

        candidate = self._proof_digest(challenge.challenge_id, proof)
        if not hmac.compare_digest(candidate, challenge.proof_digest):
            attempts = challenge.attempts + 1
            status = "locked" if attempts >= self._max_attempts else "pending"
            self._repository.update(replace(challenge, attempts=attempts, status=status))
            raise VerificationError("Verification proof is invalid")

        if not self._repository.claim_verified_target(
            contact_id=challenge.contact_id,
            channel=challenge.channel,
            target=challenge.target,
            verified_at=now,
        ):
            self._repository.update(replace(challenge, status="conflict", proof_digest=""))
            raise TargetAlreadyClaimed("Channel is already verified by another contact")

        self._repository.update(replace(challenge, status="verified", proof_digest=""))
        return challenge.channel

    def _proof_digest(self, challenge_id: str, proof: str) -> str:
        return hmac.new(
            self._pepper,
            f"proof:{challenge_id}:{proof}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _target_fingerprint(self, channel: str, target: str) -> str:
        return hmac.new(
            self._pepper,
            f"target:{channel}:{target}".encode(),
            hashlib.sha256,
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class ContactChannelState:
    email_verified_at: int | None = None
    phone_verified_at: int | None = None
    acknowledgement_version: str | None = None
    acknowledgement_at: int | None = None
    email_opt_in_at: int | None = None
    sms_opt_in_at: int | None = None
    voice_opt_in_at: int | None = None

    def can_email(self, current_acknowledgement: str) -> bool:
        return self._acknowledged(current_acknowledgement) and all(
            (self.email_verified_at, self.email_opt_in_at)
        )

    def can_text(self, current_acknowledgement: str) -> bool:
        return self._acknowledged(current_acknowledgement) and all(
            (self.phone_verified_at, self.sms_opt_in_at)
        )

    def can_call(self, current_acknowledgement: str) -> bool:
        return self._acknowledged(current_acknowledgement) and all(
            (self.phone_verified_at, self.voice_opt_in_at)
        )

    def _acknowledged(self, current: str) -> bool:
        return bool(self.acknowledgement_at and self.acknowledgement_version == current)


def _normalize_email(value: str) -> str:
    candidate = value.strip().casefold()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", candidate) or len(candidate) > 254:
        raise VerificationError("Email address is invalid")
    return candidate


def _normalize_phone(value: str) -> str:
    candidate = value.strip()
    if not re.fullmatch(r"\+[1-9]\d{7,14}", candidate):
        raise VerificationError("Phone number must use E.164 format")
    return candidate


def _mask_target(channel: str, target: str) -> str:
    if channel == "email":
        local, domain = target.split("@", 1)
        return f"{local[:1]}***@{domain}"
    return "***" + target[-4:]
