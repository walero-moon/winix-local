"""Guided purifier setup. HA and MQTT installation remain operator-managed."""

import argparse
import getpass
from pathlib import Path
from typing import Optional, Tuple

from .discovery import DiscoveryError, identify_device
from .installation import ProvisioningConfig
from .networks import scan_networks
from .provision import apply_configuration, load_provisioning_config, validate_hostname
from .setup_checks import EndpointCheckError, verify_endpoints
from .setup_files import prepare_setup
from .setup_protocol import wifi_body
from .wire import mac_id


class SetupCancelled(ValueError):
    """The operator stopped before sending configuration messages."""


def _ask(
    prompt: str, default: Optional[str] = None, *, preserve_whitespace: bool = False
) -> str:
    suffix = f" [{default}]" if default is not None else ""
    try:
        answer = input(f"{prompt}{suffix}: ")
        if not preserve_whitespace:
            answer = answer.strip()
    except (EOFError, KeyboardInterrupt) as error:
        raise SetupCancelled("Setup cancelled") from error

    return answer if answer else (default or "")


def _confirm(prompt: str) -> bool:
    return _ask(prompt, "y/N").lower() in ("y", "yes")


def _hostname(prompt: str, default: Optional[str] = None) -> str:
    while True:
        answer = _ask(prompt, default)
        try:
            return validate_hostname(answer)
        except ValueError as error:
            print(str(error))


def _port(prompt: str, default: int) -> int:
    while True:
        answer = _ask(prompt, str(default))
        try:
            port = int(answer)
            if 1024 <= port <= 65535:
                return port
        except ValueError:
            pass

        print("Enter a listener port from 1024 to 65535.")


def _join_hotspot() -> None:
    print(
        "Put the purifier in pairing mode and connect this laptop to its WINIX SMART Wi-Fi."
    )
    _ask("Press Enter when connected")


def _identify(
    args, expected_model: Optional[str] = None, known_mac: Optional[str] = None
):
    candidate = args.mac or known_mac
    while True:
        try:
            return identify_device(
                args.setup_host,
                args.setup_port,
                mac=candidate,
                expected_model=expected_model,
            )
        except DiscoveryError as error:
            print(str(error))
            entered = _ask("Full purifier MAC, or q to stop")
            if entered.lower() == "q":
                raise SetupCancelled("Setup cancelled")
            try:
                candidate = mac_id(entered)
            except ValueError as error:
                print(str(error))


def _check_endpoints(config: ProvisioningConfig) -> None:
    while True:
        try:
            verify_endpoints(config)
            print("Registration CA/TLS verified; MQTT TCP listener is reachable.")
            return
        except EndpointCheckError as error:
            print(str(error))
            if not _confirm("Fix the local services and retry?"):
                raise SetupCancelled("Setup stopped before configuring the purifier")


def _choose_network(args, mac: str, model: str) -> Tuple[str, str]:
    try:
        networks = scan_networks(
            args.setup_host, args.setup_port, mac, expected_model=model
        )
    except (OSError, EOFError, ValueError) as error:
        print(
            f"Wi-Fi scan unavailable ({type(error).__name__}); enter the target manually."
        )
        networks = ()

    visible = sorted(
        (network for network in networks if network.ssid),
        key=lambda network: network.rssi,
        reverse=True,
    )
    if visible:
        for index, network in enumerate(visible, start=1):
            print(
                f"{index}. {network.ssid!r}  {network.rssi} dBm  "
                f"channel {network.channel}  {network.bssid}"
            )

        while True:
            choice = _ask("Target network number, or m for manual/hidden Wi-Fi", "1")
            if choice.lower() == "m":
                break
            try:
                selected = visible[int(choice) - 1]
                if int(choice) > 0:
                    return selected.ssid, selected.bssid
            except (ValueError, IndexError):
                pass
            print("Select a listed network number or m.")

    while True:
        ssid = _ask("Target Wi-Fi name (SSID)", preserve_whitespace=True)
        bssid = _ask("Target access-point BSSID")
        try:
            wifi_body(ssid, "", bssid)
            return ssid, bssid
        except ValueError as error:
            print(str(error))


def _credentials(ssid: str, bssid: str) -> bytes:
    while True:
        try:
            password = getpass.getpass(
                "Target Wi-Fi password (blank for open network): "
            )
        except (EOFError, KeyboardInterrupt) as error:
            raise SetupCancelled("Setup cancelled") from error

        try:
            return wifi_body(ssid, password, bssid)
        except ValueError as error:
            print(str(error))


def _new_installation(args) -> ProvisioningConfig:
    server_host = _hostname("Local registration server (HA LAN IP or hostname)")
    mqtt_host = _hostname("MQTT server LAN IP or hostname", server_host)
    http_port = _port("CA download HTTP port", 18080)
    https_port = _port("Registration HTTPS port", 18443)
    mqtt_port = _port("Device MQTT TLS port", 8883)
    while http_port == https_port:
        print("HTTP and HTTPS listeners need different ports.")
        https_port = _port("Registration HTTPS port", 18443)
    while mqtt_port == 1883:
        print("Device TLS needs a separate port from the backend listener on 1883.")
        mqtt_port = _port("Device MQTT TLS port", 8883)

    default_directory = Path.home() / ".local/share/winix-local/setup"
    directory = (
        args.directory
        or Path(_ask("Private setup directory", str(default_directory))).expanduser()
    )
    _join_hotspot()
    mac, model = _identify(args)

    prepared = prepare_setup(
        directory,
        mac=mac,
        model=model,
        server_host=server_host,
        mqtt_host=mqtt_host,
        mqtt_port=mqtt_port,
        http_port=http_port,
        https_port=https_port,
    )
    print(
        f"{'Reused' if prepared.reused else 'Prepared'} installation for {model}, MAC {mac}."
    )
    print(f"HA private bundle: {prepared.bundle_directory}")
    print(f"Broker runtime files: {prepared.broker_config_directory}")
    print(f"HA broker credentials (keep private): {prepared.ha_credentials_path}")
    print(f"Resume config: {prepared.bundle_directory / 'provision.json'}")

    if args.prepare_only:
        return load_provisioning_config(prepared.bundle_directory / "provision.json")

    print(
        "Reconnect this laptop to its normal network. Install the bundle and broker "
        "files manually, configure HA, and start the local services. "
        "This command does not install or start HA/MQTT."
    )
    _ask("Press Enter when the local services are ready")
    return load_provisioning_config(prepared.bundle_directory / "provision.json")


def run_setup(args: argparse.Namespace) -> None:
    """Collect settings and use the verified provisioning path for device writes."""
    config_path = args.config
    if config_path is None and args.directory is None and not args.prepare_only:
        entered = _ask(
            "Existing provision.json path (blank to prepare new certificates)"
        )
        if entered:
            config_path = Path(entered)

    if config_path:
        config = load_provisioning_config(config_path.expanduser())
        if args.prepare_only:
            raise ValueError("--prepare-only is for creating a new installation")
    else:
        config = _new_installation(args)
        if args.prepare_only:
            print("Files prepared; purifier settings were not changed.")
            return

    print("Use your normal LAN connection while checking the local services.")
    _check_endpoints(config)
    _join_hotspot()
    mac, model = _identify(
        args, expected_model=config["model"], known_mac=config["mac"]
    )
    if mac != mac_id(config["mac"]):
        raise ValueError("Purifier MAC does not match this installation")

    ssid, bssid = _choose_network(args, mac, model)
    credentials = _credentials(ssid, bssid)
    print(f"Device: {model} / {mac}")
    print(f"Target Wi-Fi: {ssid!r} / {bssid}")
    print(f"Registration: https://{config['server_host']}:{config['https_port']}/auth")
    print(f"MQTT: {config['mqtt_host']}:{config['mqtt_port']}")
    if not _confirm("Send these server and Wi-Fi settings?"):
        print("Cancelled; purifier settings were not changed.")
        return

    try:
        apply_configuration(config, credentials, args.setup_host, args.setup_port)
    except (EOFError, ValueError) as error:
        # Keep payload and key material out of normal CLI errors, including
        # interrupted exchanges and malformed encrypted responses.
        raise ValueError(
            "The setup reply was interrupted or could not be decoded. "
            "Some settings may have been sent; verify the purifier before retrying."
        ) from error
