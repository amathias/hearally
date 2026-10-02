from __future__ import annotations

import unittest

from hearally.contact_channels import (
    ContactChannelState,
    ContactVerificationService,
    InMemoryVerificationRepository,
    TargetAlreadyClaimed,
    VerificationError,
    VerificationRateLimited,
)


class CapturingEmailSender:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send_verification(self, **message: object) -> str:
        self.messages.append(message)
        return "email-message-1"


class CapturingSmsSender:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    def send_verification(self, **message: object) -> str:
        self.messages.append(message)
        return "sms-message-1"


class ContactVerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 1_795_525_800
        self.repository = InMemoryVerificationRepository()
        self.email = CapturingEmailSender()
        self.sms = CapturingSmsSender()
        self.service = ContactVerificationService(
            self.repository,
            self.email,
            self.sms,
            pepper=b"p" * 32,
            clock=lambda: self.now,
        )

    def test_sms_stores_digest_not_code_and_confirms_channel(self) -> None:
        started = self.service.start_sms(
            contact_id="contact-1", phone_e164="+13125550199", locale="en"
        )
        code = self.sms.messages[0]["code"]
        stored = self.repository.challenges[started.challenge_id]

        self.assertNotEqual(stored.proof_digest, code)
        self.assertNotIn(code, repr(stored))
        self.assertEqual(started.masked_target, "***0199")
        self.assertEqual(
            self.service.confirm(challenge_id=started.challenge_id, proof=str(code)), "sms"
        )
        self.assertEqual(stored.target, "+13125550199")
        self.assertEqual(self.repository.challenges[started.challenge_id].proof_digest, "")

    def test_email_link_token_is_single_use(self) -> None:
        started = self.service.start_email(
            contact_id="contact-1", email="Person@Example.com", locale="fr"
        )
        token = str(self.email.messages[0]["token"])
        self.service.confirm(challenge_id=started.challenge_id, proof=token)
        with self.assertRaisesRegex(VerificationError, "not pending"):
            self.service.confirm(challenge_id=started.challenge_id, proof=token)

    def test_five_bad_attempts_lock_challenge(self) -> None:
        started = self.service.start_sms(
            contact_id="contact-1", phone_e164="+13125550199", locale="en"
        )
        for _ in range(5):
            with self.assertRaisesRegex(VerificationError, "invalid"):
                self.service.confirm(challenge_id=started.challenge_id, proof="000000")
        self.assertEqual(self.repository.challenges[started.challenge_id].status, "locked")

    def test_limits_three_sends_per_contact_or_target_per_day(self) -> None:
        for _ in range(3):
            self.service.start_sms(contact_id="contact-1", phone_e164="+13125550199", locale="en")
        with self.assertRaises(VerificationRateLimited):
            self.service.start_sms(contact_id="contact-1", phone_e164="+13125550200", locale="en")

    def test_same_phone_cannot_be_claimed_by_two_contacts(self) -> None:
        first = self.service.start_sms(
            contact_id="contact-1", phone_e164="+13125550199", locale="en"
        )
        self.service.confirm(
            challenge_id=first.challenge_id, proof=str(self.sms.messages[-1]["code"])
        )
        second = self.service.start_sms(
            contact_id="contact-2", phone_e164="+13125550199", locale="en"
        )
        with self.assertRaises(TargetAlreadyClaimed):
            self.service.confirm(
                challenge_id=second.challenge_id, proof=str(self.sms.messages[-1]["code"])
            )

    def test_verification_does_not_imply_consent(self) -> None:
        verified_only = ContactChannelState(phone_verified_at=self.now)
        self.assertFalse(verified_only.can_text("2026-09-22"))
        self.assertFalse(verified_only.can_call("2026-09-22"))

        opted_in = ContactChannelState(
            phone_verified_at=self.now,
            acknowledgement_version="2026-09-22",
            acknowledgement_at=self.now,
            sms_opt_in_at=self.now,
            voice_opt_in_at=self.now,
        )
        self.assertTrue(opted_in.can_text("2026-09-22"))
        self.assertTrue(opted_in.can_call("2026-09-22"))


if __name__ == "__main__":
    unittest.main()
