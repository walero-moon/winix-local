"""Validated shared air-purifier messages, scoped to the device model."""

import json
import re
from datetime import datetime, timezone
from typing import Mapping, Union

AIR_PURIFIER_GROUP = "Air01"
# Preserve the incoming payload length limit for both text and byte messages.
MAX_TELEMETRY_LENGTH = 65536
ALLOWED = {
    "A02": {"0", "1"},
    "A03": {"01", "02"},
    "A04": {"01", "02", "03", "05"},
    "A07": {"0", "1"},
}


def validate_model(value: str) -> str:
    """Keep the device's exact model identifier within one MQTT topic segment."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value
    ):
        raise ValueError("Expected an ASCII model identifier without topic delimiters")

    return value


def parse_device_model(identity: bytes) -> str:
    """Read the NUL-delimited model in a DP0100 identity response."""
    try:
        model = identity.split(b"\x00", 1)[0].decode("ascii")
    except UnicodeDecodeError as error:
        raise ValueError("Device returned a non-ASCII model identifier") from error

    return validate_model(model)


def mac_id(value: str) -> str:
    value = value.replace(":", "").replace("-", "").upper()
    if not re.fullmatch(r"[0-9A-F]{12}", value):
        raise ValueError("Expected a twelve-digit MAC address")
    return value


def device_id(mac: str, token: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9]{10}", token):
        raise ValueError("Expected ten alphanumeric token characters")
    return mac_id(mac) + "_" + token


def topic(kind: str, identity: str, model: str) -> str:
    if kind not in ("Control", "Conn", "Event"):
        raise ValueError("Unknown topic kind")

    model = validate_model(model)
    mac, token = identity.split("_")
    device_id(mac, token)

    return f"Winix/{kind}/{AIR_PURIFIER_GROUP}/{model}/{identity}"


def control(identity: str, attributes: Mapping[str, str], model: str) -> dict:
    model = validate_model(model)

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
            "deviceGroup": AIR_PURIFIER_GROUP,
            "modelId": model,
            "attributes": dict(attributes),
        },
    }


def telemetry(payload: Union[str, bytes], identity: str, model: str) -> dict:
    model = validate_model(model)

    if len(payload) > MAX_TELEMETRY_LENGTH:
        raise ValueError("Telemetry too large")

    data = json.loads(payload)
    if data["header"]["deviceId"] != identity:
        raise ValueError("Unexpected device identity")

    values = data["values"]
    if values["deviceGroup"] != AIR_PURIFIER_GROUP or values["modelId"] != model:
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
