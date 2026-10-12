"""The AWS senders, checked against AWS's own API definitions (botocore's Stubber
rejects any parameter the real API would)."""

from __future__ import annotations

import unittest

try:
    import boto3
    from botocore.stub import Stubber
except ImportError:  # pragma: no cover - boto3 is a runtime dependency
    boto3 = None

from hearally.providers.aws_channels import AwsNotifySmsSender


@unittest.skipIf(boto3 is None, "boto3 is not installed")
class NotifySmsTests(unittest.TestCase):
    def test_the_request_matches_the_real_api(self) -> None:
        client = boto3.client("pinpoint-sms-voice-v2", region_name="us-east-1")
        sender = AwsNotifySmsSender(
            client,
            configuration_id="notify-abc123",
            template_id="notify-code-verification-english-001",
        )
        with Stubber(client) as stub:
            stub.add_response(
                "send_notify_text_message",
                {"MessageId": "m-1"},
                {
                    "NotifyConfigurationId": "notify-abc123",
                    "DestinationPhoneNumber": "+15552010123",
                    "TemplateId": "notify-code-verification-english-001",
                    "TemplateVariables": {"code": "123456"},
                    "TimeToLive": 600,
                    "Context": {"challenge_id": "c1", "locale": "en-US"},
                },
            )
            message_id = sender.send_verification(
                phone_e164="+15552010123",
                code="123456",
                challenge_id="c1",
                expires_at=0,
                locale="en-US",
            )
        self.assertEqual(message_id, "m-1")


if __name__ == "__main__":
    unittest.main()
