"""Validated 5510 messages. No HA dependency; shared by provisioning/tests."""

import json
import re
from datetime import datetime, timezone
from typing import Mapping, Union

MODEL = "5510_US"
# Preserve the incoming payload length limit for both text and byte messages.
MAX_TELEMETRY_LENGTH = 65536
ALLOWED = {
    "A02": {"0", "1"},
    "A03": {"01", "02"},
    "A04": {"01", "02", "03", "05"},
    "A07": {"0", "1"},
}


def mac_id(value: str) -> str:
    value = value.replace(":", "").replace("-", "").upper()
    if not re.fullmatch(r"[0-9A-F]{12}", value):
        raise ValueError("Expected a twelve-digit MAC address")
    return value


def device_id(mac: str, token: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9]{10}", token):
        raise ValueError("Expected ten alphanumeric token characters")
    return mac_id(mac) + "_" + token


def topic(kind: str, identity: str) -> str:
    if kind not in ("Control", "Conn", "Event"):
        raise ValueError("Unknown topic kind")

    mac, token = identity.split("_")
    device_id(mac, token)

    return f"Winix/{kind}/Air01/{MODEL}/{identity}"


def control(identity: str, attributes: Mapping[str, str]) -> dict:
    if not attributes or any(
        key not in ALLOWED or value not in ALLOWED[key]
        for key, value in attributes.items()
    ):
        raise ValueError("Unsupported control attribute/value")

    device_id(*identity.split("_"))

    return {
        "header": {
            "deviceId": identity,
            "sndDate": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")[:17],
        },
        "values": {
            "apiNo": "A211",
            "apiGroup": "001",
            "deviceGroup": "Air01",
            "modelId": MODEL,
            "attributes": dict(attributes),
        },
    }


def telemetry(payload: Union[str, bytes], identity: str) -> dict:
    if len(payload) > MAX_TELEMETRY_LENGTH:
        raise ValueError("Telemetry too large")

    data = json.loads(payload)
    if data["header"]["deviceId"] != identity:
        raise ValueError("Unexpected device identity")

    values = data["values"]
    if values["deviceGroup"] != "Air01" or values["modelId"] != MODEL:
        raise ValueError("Unexpected model")
    if values["apiNo"] not in ("A100", "A210", "A220", "A230", "A240"):
        raise ValueError("Unsupported event")

    attributes = values.get("attributes", {})
    if not isinstance(attributes, dict) or any(
        not isinstance(value, (str, int, float)) for value in attributes.values()
    ):
        raise ValueError("Invalid attributes")

    result = {key: str(value) for key, value in attributes.items()}
    if "rssi" in values:
        result["rssi"] = str(values["rssi"])

    return result
