"""Prepare private setup artifacts without installing or starting services.

A completed directory is reused only when its settings and generated files still
match. Preparation never rotates an existing CA, device token or broker password.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from typing import Mapping, Union

from .installation import InstallationConfig, ProvisioningConfig, load_bundle
from .pki import create_bundle, private_write
from .prepare_broker import export_broker_files
from .private_storage import restrict_private
from .provision import validate_hostname
from .wire import mac_id, validate_model

STATE_VERSION = 1
STATE_FILENAME = "setup.json"
BROKER_RUNTIME_FILES = (
    "ca.pem",
    "broker.pem",
    "broker.key",
    "passwords",
    "device.acl",
    "backend.acl",
    "mosquitto.conf",
)
BUNDLE_FILES = (
    "ca.key",
    "ca.pem",
    "auth.key",
    "auth.pem",
    "broker.key",
    "broker.pem",
    "device.key",
    "device.pem",
    "bundle.json",
    "provision.json",
)
MOSQUITTO_IMAGE = (
    "eclipse-mosquitto:2.0.22@sha256:"
    "81a6193ae693942a3231cd3ac52875b02b7e3a19da671b2fb4b55cc874c5b279"
)


class SetupFilesError(ValueError):
    """An existing directory cannot be reused safely for these setup settings."""


@dataclass(frozen=True)
class PreparedSetup:
    root: Path
    bundle_directory: Path
    broker_export_directory: Path
    broker_config_directory: Path
    compose_path: Path
    ha_credentials_path: Path
    installation_config: InstallationConfig
    reused: bool = False


def _prepared(root: Path, reused: bool = False) -> PreparedSetup:
    return PreparedSetup(
        root=root,
        bundle_directory=root / "bundle",
        broker_export_directory=root / "broker-export",
        broker_config_directory=root / "broker-config",
        compose_path=root / "compose.yaml",
        ha_credentials_path=root / "broker-export/ha-credentials.json",
        installation_config=load_bundle(root / "bundle"),
        reused=reused,
    )


def _validate_directory(directory: Path) -> Path:
    directory = directory.expanduser().absolute()
    if directory.is_symlink():
        raise SetupFilesError("Setup directory must not be a symlink")

    directory = directory.resolve()
    if any((parent / ".git").exists() for parent in (directory, *directory.parents)):
        raise SetupFilesError("Keep private setup files outside Git repositories")
    if directory.exists() and not directory.is_dir():
        raise SetupFilesError("Setup path must be a directory")

    return directory


def _owned_files() -> tuple[str, ...]:
    return (
        *("bundle/" + name for name in BUNDLE_FILES),
        *("broker-export/" + name for name in BROKER_RUNTIME_FILES),
        "broker-export/ha-credentials.json",
        *("broker-config/" + name for name in BROKER_RUNTIME_FILES),
        "compose.yaml",
    )


def _file_digest(root: Path, relative: str) -> str:
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise SetupFilesError(f"Setup artifacts must not be symlinks: {relative}")

    if not path.is_file():
        raise SetupFilesError(f"Setup is incomplete; missing {relative}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _reuse_existing(root: Path, settings: Mapping[str, object]) -> PreparedSetup:
    state_path = root / STATE_FILENAME
    if state_path.is_symlink() or not state_path.is_file():
        raise SetupFilesError(
            "Setup directory is not a completed guided setup; choose an empty directory"
        )

    try:
        state = json.loads(state_path.read_text())
    except (OSError, ValueError) as error:
        raise SetupFilesError("Cannot read saved setup state") from error

    if not isinstance(state, dict) or state.get("version") != STATE_VERSION:
        raise SetupFilesError("Unsupported saved setup state")
    if state.get("settings") != settings:
        raise SetupFilesError(
            "Setup settings differ from this directory; reuse its original settings "
            "or choose a new directory"
        )

    checksums = state.get("checksums")
    if not isinstance(checksums, dict) or set(checksums) != set(_owned_files()):
        raise SetupFilesError("Saved setup is incomplete")
    for relative, expected in checksums.items():
        if _file_digest(root, relative) != expected:
            raise SetupFilesError(
                f"Setup artifact changed; refusing to replace {relative}"
            )

    config_files = {path.name for path in (root / "broker-config").iterdir()}
    if config_files != set(BROKER_RUNTIME_FILES):
        raise SetupFilesError("Broker config contains unexpected files")

    return _prepared(root, reused=True)


def _compose(mqtt_port: int) -> str:
    # Installation owns host permissions. Generated files stay private to their
    # creator; this example requires a separately installed UID/GID 1883 copy.
    return f"""# Set MQTT_CONFIG_DIR to an installed config copy readable by UID/GID 1883.
name: winix-mqtt
services:
  mosquitto:
    image: {MOSQUITTO_IMAGE}
    user: "1883:1883"
    restart: unless-stopped
    read_only: true
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    ports:
      - "${{MQTT_BIND_IP:?Set the broker server LAN bind address}}:1883:1883"
      - "${{MQTT_BIND_IP:?Set the broker server LAN bind address}}:{mqtt_port}:{mqtt_port}"
    volumes:
      - "${{MQTT_CONFIG_DIR:?Set the installed broker config directory}}:/mosquitto/config:ro"
      - mqtt-data:/mosquitto/data
    tmpfs:
      - /tmp:rw,noexec,nosuid,nodev,size=8m
volumes:
  mqtt-data:
"""


def prepare_setup(
    directory: Union[str, Path],
    *,
    mac: str,
    model: str,
    server_host: str,
    mqtt_host: str,
    mqtt_port: int = 8883,
    http_port: int = 18080,
    https_port: int = 18443,
) -> PreparedSetup:
    """Generate once or reuse unchanged artifacts; perform no deployment work."""
    settings: ProvisioningConfig = {
        "mac": mac_id(mac),
        "model": validate_model(model),
        "server_host": validate_hostname(server_host),
        "mqtt_host": validate_hostname(mqtt_host),
        "mqtt_port": mqtt_port,
        "http_port": http_port,
        "https_port": https_port,
    }
    for port in (mqtt_port, http_port, https_port):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise SetupFilesError("Use unprivileged listener ports 1024..65535")
    if mqtt_port == 1883:
        raise SetupFilesError("Device TLS port must differ from backend port 1883")
    if http_port == https_port:
        raise SetupFilesError("HTTP and HTTPS ports must differ")

    root = _validate_directory(Path(directory))
    if root.exists() and any(root.iterdir()):
        return _reuse_existing(root, settings)

    root.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{root.name}-", dir=root.parent))
    try:
        restrict_private(staging, directory=True)
        create_bundle(staging / "bundle", **settings)
        export_broker_files(staging / "bundle", staging / "broker-export")

        config_directory = staging / "broker-config"
        config_directory.mkdir(mode=0o700)
        restrict_private(config_directory, directory=True)
        for name in BROKER_RUNTIME_FILES:
            private_write(
                config_directory / name, (staging / "broker-export" / name).read_bytes()
            )
        private_write(staging / "compose.yaml", _compose(mqtt_port).encode())

        state = {
            "version": STATE_VERSION,
            "settings": settings,
            "checksums": {name: _file_digest(staging, name) for name in _owned_files()},
        }
        private_write(
            staging / STATE_FILENAME, (json.dumps(state, indent=2) + "\n").encode()
        )
        # Windows cannot replace an existing directory, even an empty one.
        # rmdir refuses a directory that acquired contents while preparing.
        if root.exists():
            root.rmdir()
        staging.replace(root)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    return _prepared(root)
