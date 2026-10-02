from __future__ import annotations

import html
from typing import Any
from urllib.parse import urlencode


class SesEmailVerificationSender:
    def __init__(self, client: Any, *, from_email: str, confirmation_base_url: str) -> None:
        if not confirmation_base_url.startswith("https://"):
            raise ValueError("Confirmation URL must use HTTPS")
        self._client = client
        self._from_email = from_email
        self._confirmation_base_url = confirmation_base_url.rstrip("/")

    def send_verification(
        self,
        *,
        email: str,
        token: str,
        challenge_id: str,
        expires_at: int,
        locale: str,
    ) -> str:
        query = urlencode({"challenge": challenge_id, "token": token})
        link = f"{self._confirmation_base_url}/verify-email?{query}"
        response = self._client.send_email(
            FromEmailAddress=self._from_email,
            Destination={"ToAddresses": [email]},
            Content={
                "Simple": {
                    "Subject": {"Data": "Confirm your Hearally email", "Charset": "UTF-8"},
                    "Body": {
                        "Text": {
                            "Data": (
                                "Confirm this email for Hearally: "
                                f"{link}\n\nHearally is not an emergency service."
                            ),
                            "Charset": "UTF-8",
                        },
                        "Html": {
                            "Data": (
                                "<p>Confirm this email for Hearally:</p>"
                                "<p><a href="
                                f'"{html.escape(link, quote=True)}"'
                                ">Confirm email</a></p>"
                                "<p>Hearally is not an emergency service.</p>"
                            ),
                            "Charset": "UTF-8",
                        },
                    },
                }
            },
            EmailTags=[{"Name": "purpose", "Value": "contact-verification"}],
        )
        return str(response["MessageId"])


class AwsNotifySmsSender:
    def __init__(
        self,
        client: Any,
        *,
        configuration_id: str,
        template_id: str,
    ) -> None:
        self._client = client
        self._configuration_id = configuration_id
        self._template_id = template_id

    def send_verification(
        self,
        *,
        phone_e164: str,
        code: str,
        challenge_id: str,
        expires_at: int,
        locale: str,
    ) -> str:
        response = self._client.send_notify_text_message(
            ConfigurationId=self._configuration_id,
            DestinationPhoneNumber=phone_e164,
            MessageTemplate={
                "TemplateId": self._template_id,
                "TemplateVariables": {"code": code},
            },
            TimeToLive=600,
            Context={"challenge_id": challenge_id, "locale": locale},
        )
        message_id = response.get("MessageId")
        if not isinstance(message_id, str) or not message_id:
            raise RuntimeError("AWS SMS response did not contain a message identifier")
        return message_id
