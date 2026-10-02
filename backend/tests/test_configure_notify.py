from __future__ import annotations

import unittest

from hearally.configure_notify import configure


class NotifyClient:
    def __init__(self, configurations: list[dict] | None = None) -> None:
        self.configurations = configurations or []
        self.created: dict | None = None

    def describe_notify_configurations(self) -> dict:
        return {"NotifyConfigurations": self.configurations}

    def create_notify_configuration(self, **kwargs: object) -> dict:
        self.created = kwargs
        return {
            "NotifyConfigurationId": "nc-created123",
            "Status": "ACTIVE",
            "Tier": "BASIC",
            "EnabledChannels": kwargs["EnabledChannels"],
            "EnabledCountries": kwargs["EnabledCountries"],
        }


class NotifyConfigurationTests(unittest.TestCase):
    def test_is_read_only_without_apply(self) -> None:
        client = NotifyClient()
        result = configure(client, apply=False)
        self.assertEqual(result["action"], "create_required")
        self.assertIsNone(client.created)

    def test_creates_sms_only_protected_configuration(self) -> None:
        client = NotifyClient()
        result = configure(client, apply=True)
        self.assertEqual(result["action"], "created")
        assert client.created is not None
        self.assertEqual(client.created["EnabledChannels"], ["SMS"])
        self.assertEqual(client.created["EnabledCountries"], ["US"])
        self.assertTrue(client.created["DeletionProtectionEnabled"])
        self.assertEqual(client.created["UseCase"], "CODE_VERIFICATION")


if __name__ == "__main__":
    unittest.main()
