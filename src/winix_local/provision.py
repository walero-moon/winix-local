#!/usr/bin/env python3
"""Prepare a private installation bundle, then configure stock 5510 Wi-Fi.

No Winix cloud requests. No automatic Wi-Fi switching or router changes.
"""

import argparse
import getpass
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys

from .installation import ProvisioningConfig
from .setup_protocol import (
    handshake,
    secret_key,
    encrypted_request,
    read_response,
    decrypt_response,
    server_body,
    wifi_body,
)
from .wire import mac_id

SETUP_TIMEOUT_SECONDS = 10
HANDSHAKE_COMMAND = 1
SERVER_SETTINGS_COMMAND = 2
WIFI_SETTINGS_COMMAND = 4


def validate_hostname(value: str) -> str:
    """Accept a LAN host without a scheme, port or config-file delimiters."""
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", value):
        raise ValueError("Use a LAN IPv4 address or DNS hostname (no scheme or port)")

    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    init_parser = commands.add_parser(
        "init", help="Generate private CA, credentials and broker configuration once"
    )
    init_parser.add_argument("--mac", required=True)
    init_parser.add_argument("--server-host", required=True)
    init_parser.add_argument("--mqtt-host", required=True)
    init_parser.add_argument("--mqtt-port", type=int, default=8883)
    init_parser.add_argument("--http-port", type=int, default=18080)
    init_parser.add_argument("--https-port", type=int, default=18443)
    init_parser.add_argument("--out", type=Path, required=True)
    init_parser.add_argument(
        "--offline-device-ip",
        help="Optional: enable device-only DNS/NTP listeners in HA; requires UDP 53/123 access",
    )

    provision_parser = commands.add_parser(
        "provision",
        help="Send setup over purifier hotspot; password prompted, never logged",
    )
    provision_parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="Public provision.json exported by init",
    )
    provision_parser.add_argument("--ssid", required=True)
    provision_parser.add_argument(
        "--bssid", required=True, help="Target 2.4 GHz access point BSSID"
    )
    provision_parser.add_argument("--setup-host", default="192.168.43.1")
    provision_parser.add_argument("--setup-port", type=int, default=47000)
    provision_parser.add_argument(
        "--check-only",
        action="store_true",
        help="Identify device without changing its settings",
    )

    return parser


def initialize_installation(args: argparse.Namespace) -> None:
    """Validate all inputs before generating any private installation files."""
    mac = mac_id(args.mac)
    validate_hostname(args.server_host)
    validate_hostname(args.mqtt_host)

    for port in (args.mqtt_port, args.http_port, args.https_port):
        if not 1024 <= port <= 65535:
            raise ValueError("Use unprivileged listener ports 1024..65535")

    if args.http_port == args.https_port:
        raise ValueError("HTTP and HTTPS ports must differ")

    if args.offline_device_ip:
        ipaddress.IPv4Address(args.server_host)
        ipaddress.IPv4Address(args.offline_device_ip)

    # Provisioning alone uses stdlib/OpenSSL; only bundle creation needs PKI deps.
    from .pki import create_bundle

    create_bundle(
        args.out,
        mac,
        args.server_host,
        args.mqtt_host,
        mqtt_port=args.mqtt_port,
        http_port=args.http_port,
        https_port=args.https_port,
    )

    if args.offline_device_ip:
        bundle_path = args.out / "bundle.json"
        config = json.loads(bundle_path.read_text())
        config["offline_device_ip"] = args.offline_device_ip
        bundle_path.write_text(json.dumps(config, indent=2) + "\n")

    print(
        "Private bundle created. Back it up securely; do not regenerate it on upgrades."
    )
    print(
        "Copy bundle to HA /config/winix_local/"
        + mac
        + "/; provision.json alone may be copied to the setup laptop."
    )


def load_provisioning_config(path: Path) -> ProvisioningConfig:
    config: ProvisioningConfig = json.loads(path.read_text())
    mac_id(config["mac"])
    validate_hostname(config["server_host"])
    validate_hostname(config["mqtt_host"])

    if config.get("model") != "5510_US":
        raise ValueError("Only 5510_US is validated")

    return config


def verify_device(connection: socket.socket, key: bytes, nonce: bytes) -> None:
    connection.sendall(handshake(nonce))
    device_identity = decrypt_response(
        read_response(connection), key, HANDSHAKE_COMMAND
    )

    if not device_identity.startswith(b"5510_US"):
        raise ValueError("Unexpected model; nothing was configured")

    print("5510_US identity verified.")


def configure_servers(
    connection: socket.socket, key: bytes, config: ProvisioningConfig
) -> None:
    """DP0200 must succeed before Wi-Fi credentials change the connection."""
    download_base_url = f"http://{config['server_host']}:{config['http_port']}"
    auth_url = f"https://{config['server_host']}:{config['https_port']}/auth"
    ca_url = download_base_url + "/ca.pem"

    body = server_body(
        ca_url,
        auth_url,
        config["mqtt_host"],
        config["mqtt_port"],
        auth_ca_url=ca_url,
    )
    connection.sendall(encrypted_request(SERVER_SETTINGS_COMMAND, body, key))
    reply = decrypt_response(read_response(connection), key, SERVER_SETTINGS_COMMAND)

    if reply != b"\x00":
        raise ValueError("Server settings rejected; Wi-Fi not sent")


def configure_wifi(connection: socket.socket, key: bytes, credentials: bytes) -> None:
    """DP0400 may close the socket as the purifier joins its target Wi-Fi."""
    connection.sendall(encrypted_request(WIFI_SETTINGS_COMMAND, credentials, key))

    try:
        reply = decrypt_response(read_response(connection), key, WIFI_SETTINGS_COMMAND)
        if not reply or reply[0] != 0:
            raise ValueError("Wi-Fi configuration rejected")
    except (EOFError, OSError):
        print(
            "Setup connection closed during Wi-Fi transition; this is not proof of success."
        )


def provision_device(args: argparse.Namespace) -> None:
    config = load_provisioning_config(args.config)
    mac = mac_id(config["mac"])

    # Validate credential structure before changing any server settings.
    credentials = b""
    if not args.check_only:
        password = getpass.getpass("Target Wi-Fi password: ")
        credentials = wifi_body(args.ssid, password, args.bssid)

    nonce = secrets.token_bytes(4)
    key = secret_key(nonce, mac)

    with socket.create_connection(
        (args.setup_host, args.setup_port), timeout=SETUP_TIMEOUT_SECONDS
    ) as connection:
        verify_device(connection, key, nonce)
        if args.check_only:
            return

        configure_servers(connection, key, config)
        configure_wifi(connection, key, credentials)

        print(
            "Setup messages sent. Rejoin your normal network and confirm live telemetry in HA."
        )


def main(argv=None) -> None:
    os.umask(0o077)
    args = build_parser().parse_args(argv)

    if args.command == "init":
        initialize_installation(args)
    else:
        provision_device(args)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        print("Setup failed: " + str(error), file=sys.stderr)
        sys.exit(1)
