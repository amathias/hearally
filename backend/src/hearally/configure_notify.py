from __future__ import annotations

import argparse
import json
from typing import Any

DISPLAY_NAME = "Hearally"


def _matching_configurations(client: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in client.describe_notify_configurations().get("NotifyConfigurations", [])
        if item.get("DisplayName") == DISPLAY_NAME
    ]


def configure(client: Any, *, apply: bool) -> dict[str, Any]:
    matches = _matching_configurations(client)
    if len(matches) > 1:
        raise RuntimeError("Multiple Hearally Notify configurations exist; resolve manually")
    if matches:
        item = matches[0]
        configuration_id = str(item.get("NotifyConfigurationId") or "")
        enabled_channels = set(item.get("EnabledChannels") or [])
        enabled_countries = set(item.get("EnabledCountries") or [])
        needs_update = not {"SMS"}.issubset(enabled_channels) or not {"US"}.issubset(
            enabled_countries
        )
        if apply and needs_update:
            client.update_notify_configuration(
                NotifyConfigurationId=configuration_id,
                EnabledChannels=sorted(enabled_channels | {"SMS"}),
                EnabledCountries=sorted(enabled_countries | {"US"}),
                DeletionProtectionEnabled=True,
            )
            item = _matching_configurations(client)[0]
        return {
            "action": "updated" if apply and needs_update else "none",
            "configuration_id": configuration_id,
            "status": item.get("Status"),
            "tier": item.get("Tier"),
            "sms_enabled": "SMS" in set(item.get("EnabledChannels") or []),
            "us_enabled": "US" in set(item.get("EnabledCountries") or []),
        }
    if not apply:
        return {
            "action": "create_required",
            "display_name": DISPLAY_NAME,
            "tier": "BASIC",
            "channels": ["SMS"],
            "countries": ["US"],
        }
    created = client.create_notify_configuration(
        DisplayName=DISPLAY_NAME,
        UseCase="CODE_VERIFICATION",
        EnabledChannels=["SMS"],
        EnabledCountries=["US"],
        DeletionProtectionEnabled=True,
        Tags=[
            {"Key": "Application", "Value": "Hearally"},
            {"Key": "Environment", "Value": "Production"},
            {"Key": "ManagedBy", "Value": "hearally-configure-notify"},
        ],
    )
    return {
        "action": "created",
        "configuration_id": created.get("NotifyConfigurationId"),
        "status": created.get("Status"),
        "tier": created.get("Tier"),
        "sms_enabled": "SMS" in set(created.get("EnabledChannels") or []),
        "us_enabled": "US" in set(created.get("EnabledCountries") or []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect or create Hearally's AWS managed SMS verification configuration."
    )
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create or update the configuration. Without this flag the command is read-only.",
    )
    args = parser.parse_args()

    import boto3

    client = boto3.client("pinpoint-sms-voice-v2", region_name=args.region)
    print(json.dumps(configure(client, apply=args.apply), separators=(",", ":")))


if __name__ == "__main__":
    main()
