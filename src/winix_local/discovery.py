"""Read a setup peer's MAC candidate and verify it with the setup handshake.

Neighbor tables describe the directly connected peer, not the purifier's protocol
identity. The identification function returns a discovered MAC only after it
decrypts a valid DP0100 response. No server settings or Wi-Fi credentials are sent here.
"""

import ipaddress
import re
import secrets
import shutil
import socket
import subprocess
import sys
from typing import Optional, Tuple

from .setup_protocol import secret_key
from .wire import mac_id, validate_model

NEIGHBOR_COMMAND_TIMEOUT_SECONDS = 5
_MAC_PATTERN = re.compile(
    r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,2}:){5}[0-9A-Fa-f]{1,2}(?![0-9A-Fa-f:])"
)


class DiscoveryError(ValueError):
    """Automatic identification failed; a manual MAC may still be usable."""


def _normalize_neighbor_mac(value: str) -> str:
    """Handle macOS's short octets and reject unusable link-layer addresses."""
    octets = value.split(":")
    if len(octets) != 6 or any(
        re.fullmatch(r"[0-9A-Fa-f]{1,2}", octet) is None for octet in octets
    ):
        raise DiscoveryError("The setup peer returned an invalid neighbor MAC")

    normalized = mac_id("".join(octet.zfill(2) for octet in octets))
    if normalized in ("000000000000", "FFFFFFFFFFFF") or int(normalized[:2], 16) & 1:
        raise DiscoveryError("The setup peer returned a non-unicast neighbor MAC")

    return normalized


def _line_peer(line: str) -> Optional[ipaddress.IPv4Address]:
    """Recognize macOS/BSD arp and Linux ip-neigh/arp address columns."""
    parenthesized = re.search(r"\(([^()\s]+)\)\s+at\s+", line)
    if parenthesized is not None:
        value = parenthesized.group(1)
    else:
        columns = line.split()
        if not columns:
            return None
        value = columns[0]

    try:
        return ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError:
        return None


def _parse_peer_mac(output: str, peer: ipaddress.IPv4Address) -> Optional[str]:
    candidates = set()

    for line in output.splitlines():
        if _line_peer(line) != peer:
            continue

        states = set(line.upper().split())
        if states & {"FAILED", "INCOMPLETE", "(INCOMPLETE)"}:
            continue

        for match in _MAC_PATTERN.finditer(line):
            candidates.add(_normalize_neighbor_mac(match.group(0)))

    if len(candidates) > 1:
        raise DiscoveryError("Multiple neighbor MACs were reported for the setup peer")

    return next(iter(candidates), None)


def _neighbor_commands(peer: str) -> list[list[str]]:
    if sys.platform == "darwin":
        return [["/usr/sbin/arp", "-n", peer]]

    if sys.platform.startswith("linux"):
        commands = []
        ip_command = shutil.which("ip")
        arp_command = shutil.which("arp")
        if ip_command is not None:
            commands.append([ip_command, "-4", "neigh", "show", "to", peer])
        if arp_command is not None:
            commands.append([arp_command, "-n", peer])
        return commands

    raise DiscoveryError("Automatic MAC lookup supports macOS and Linux only")


def discover_peer_mac(ip: str) -> str:
    """Read one exact IPv4 peer from the OS neighbor table; never scan a subnet.

    Open the setup connection first so the kernel has resolved its neighbor. The
    returned value is a candidate and must pass the device handshake before use.
    """
    try:
        peer = ipaddress.IPv4Address(ip)
    except ipaddress.AddressValueError as error:
        raise DiscoveryError(
            "Automatic MAC lookup requires an IPv4 setup peer"
        ) from error

    commands = _neighbor_commands(str(peer))
    if not commands:
        raise DiscoveryError("No supported neighbor-table command is installed")

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=NEIGHBOR_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        if result.returncode != 0:
            continue

        candidate = _parse_peer_mac(result.stdout, peer)
        if candidate is not None:
            return candidate

    raise DiscoveryError(
        f"No usable MAC was found for setup peer {peer}; join the purifier's setup Wi-Fi "
        "or enter its full MAC manually"
    )


def identify_device(
    setup_host: str = "192.168.43.1",
    setup_port: int = 47000,
    mac: Optional[str] = None,
    expected_model: Optional[str] = None,
) -> Tuple[str, str]:
    """Return a verified MAC/model using one read-only setup connection.

    An explicit MAC skips neighbor discovery. This supports manual fallback and
    environments whose operating system cannot expose the directly connected MAC.
    """
    from .provision import SETUP_TIMEOUT_SECONDS, verify_device

    if not 1 <= setup_port <= 65535:
        raise DiscoveryError("Setup port must be between 1 and 65535")

    try:
        candidate = mac_id(mac) if mac is not None else None
        if expected_model is not None:
            validate_model(expected_model)
    except ValueError as error:
        raise DiscoveryError(str(error)) from error

    try:
        with socket.create_connection(
            (setup_host, setup_port), timeout=SETUP_TIMEOUT_SECONDS
        ) as connection:
            if candidate is None:
                candidate = discover_peer_mac(connection.getpeername()[0])

            nonce = secrets.token_bytes(4)
            key = secret_key(nonce, candidate)
            model = verify_device(connection, key, nonce, expected_model)
    except DiscoveryError:
        raise
    except (OSError, EOFError, ValueError, subprocess.SubprocessError) as error:
        raise DiscoveryError(
            "Could not verify the purifier's setup identity; check the setup Wi-Fi "
            "connection and full device MAC"
        ) from error

    return candidate, model
