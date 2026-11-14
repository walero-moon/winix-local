#!/usr/bin/env python3
"""Export broker-only files from private bundle. Never copies the CA signing key."""

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil

from .installation import InstallationConfig, load_bundle
from .private_storage import restrict_private
from .wire import device_id, mac_id, topic, validate_model

# Preserve Mosquitto's established $7$ password-file encoding. This is its
# compatibility parameter, rather than a general-purpose password KDF policy.
MOSQUITTO_PBKDF2_ITERATIONS = 101
PASSWORD_SALT_BYTES = 12
PASSWORD_DIGEST_BYTES = 64


def encode_broker_password(password: str, salt: bytes) -> str:
    """Return the existing Mosquitto $7$ PBKDF2-SHA512 record."""
    digest = hashlib.pbkdf2_hmac(
        "sha512",
        password.encode(),
        salt,
        MOSQUITTO_PBKDF2_ITERATIONS,
        dklen=PASSWORD_DIGEST_BYTES,
    )

    encoded_salt = base64.b64encode(salt).decode()
    encoded_digest = base64.b64encode(digest).decode()
    return f"$7${MOSQUITTO_PBKDF2_ITERATIONS}${encoded_salt}${encoded_digest}"


def device_acl(config: InstallationConfig) -> str:
    mac = mac_id(config["mac"])
    identity = device_id(mac, config["token"])
    model = validate_model(config["model"])

    return (
        f"user {mac}\n"
        f"topic read {topic('Control', identity, model)}\n"
        f"topic write {topic('Conn', identity, model)}\n"
        f"topic write {topic('Event', identity, model)}\n"
    )


def broker_configuration(mqtt_port: int) -> str:
    return f"""persistence true
persistence_location /mosquitto/data/
log_dest stdout
per_listener_settings true
listener 1883
allow_anonymous false
password_file /mosquitto/config/passwords
acl_file /mosquitto/config/backend.acl
listener {mqtt_port}
allow_anonymous false
require_certificate true
use_identity_as_username true
cafile /mosquitto/config/ca.pem
certfile /mosquitto/config/broker.pem
keyfile /mosquitto/config/broker.key
acl_file /mosquitto/config/device.acl
tls_version tlsv1.2
"""


def export_broker_files(bundle_directory: Path, output_directory: Path) -> None:
    config = load_bundle(bundle_directory)
    acl = device_acl(config)

    output_directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    restrict_private(output_directory, directory=True)
    for name in ("ca.pem", "broker.pem", "broker.key"):
        shutil.copyfile(bundle_directory / name, output_directory / name)

    # Password is generated here, never passed in argv or sent to a server.
    password = secrets.token_urlsafe(32)
    encoded_password = encode_broker_password(
        password, secrets.token_bytes(PASSWORD_SALT_BYTES)
    )
    (output_directory / "passwords").write_text(f"homeassistant:{encoded_password}\n")
    (output_directory / "ha-credentials.json").write_text(
        json.dumps({"username": "homeassistant", "password": password}) + "\n"
    )

    (output_directory / "device.acl").write_text(acl)
    (output_directory / "backend.acl").write_text(
        "user homeassistant\ntopic read Winix/Conn/#\ntopic read Winix/Event/#\n"
        "topic write Winix/Control/#\ntopic readwrite homeassistant/#\n"
    )
    (output_directory / "mosquitto.conf").write_text(
        broker_configuration(config["mqtt_port"])
    )

    for exported_file in output_directory.iterdir():
        restrict_private(exported_file, directory=False)


def main(argv=None) -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    export_broker_files(args.bundle, args.out)
    print(
        "Broker export ready. Install broker files as UID/GID 1883; keep ha-credentials.json private and out of broker mount."
    )


if __name__ == "__main__":
    main()
