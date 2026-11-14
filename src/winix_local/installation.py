"""On-disk installation contracts shared by HA and the standalone tools.

The records describe the existing JSON format. Loading preserves extra fields
so a newer bundle can still be read by an older installation.
"""

import json
from pathlib import Path
from typing import TypedDict, Union

from .wire import device_id, validate_model


class ProvisioningConfig(TypedDict):
    """Public connection settings; safe to copy to the setup laptop."""

    mac: str
    model: str
    server_host: str
    mqtt_host: str
    mqtt_port: int
    http_port: int
    https_port: int


class OptionalInstallationFields(TypedDict, total=False):
    offline_device_ip: str


class InstallationConfig(ProvisioningConfig, OptionalInstallationFields):
    """Private bundle.json settings; the token is a registration credential."""

    token: str


def load_bundle(directory: Union[str, Path]) -> InstallationConfig:
    """Read a private bundle and check its device identity without changing it."""
    config: InstallationConfig = json.loads(
        (Path(directory) / "bundle.json").read_text()
    )

    device_id(config["mac"], config["token"])
    validate_model(config["model"])
    return config
