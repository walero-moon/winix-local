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
    r"(?<![0-9A-Fa-f:-])"
    r"(?:(?:[0-9A-Fa-f]{1,2}:){5}[0-9A-Fa-f]{1,2}|"
    r"(?:[0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2})"
    r"(?![0-9A-Fa-f:-])"
)
_WINDOWS_INTERFACE_PATTERN = re.compile(
    r"(?<![0-9.])([0-9]+(?:\.[0-9]+){3})\s+---\s+0x[0-9A-Fa-f]+\s*$"
)


class DiscoveryError(ValueError):
    """Automatic identification failed; a manual MAC may still be usable."""


def _normalize_neighbor_mac(value: str) -> str:
    """Handle macOS short octets and Windows dashes; require usable unicast MACs."""
    octets = value.replace("-", ":").split(":")
    if len(octets) != 6 or any(
        re.fullmatch(r"[0-9A-Fa-f]{1,2}", octet) is None for octet in octets
    ):
        raise DiscoveryError("The setup peer returned an invalid neighbor MAC")

    normalized = mac_id("".join(octet.zfill(2) for octet in octets))
    if normalized in ("000000000000", "FFFFFFFFFFFF") or int(normalized[:2], 16) & 1:
        raise DiscoveryError("The setup peer returned a non-unicast neighbor MAC")

    return normalized


def _line_peer(line: str) -> Optional[ipaddress.IPv4Address]:
    """Recognize the numeric peer column without depending on localized headings."""
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


def _parse_peer_mac(
    output: str,
    peer: ipaddress.IPv4Address,
    local_ip: Optional[ipaddress.IPv4Address] = None,
) -> Optional[str]:
    candidates = set()
    current_interface = None

    for line in output.splitlines():
        # Windows emits one table per interface. The punctuation and numeric
        # fields stay useful even when "Interface" and column labels translate.
        interface = _WINDOWS_INTERFACE_PATTERN.search(line)
        if interface is not None:
            try:
                current_interface = ipaddress.IPv4Address(interface.group(1))
            except ipaddress.AddressValueError:
                current_interface = None
            continue

        if local_ip is not None and current_interface not in (None, local_ip):
            continue
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


def _neighbor_commands(peer: str, local_ip: Optional[str] = None) -> list[list[str]]:
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

    if sys.platform == "win32":
        arp_command = shutil.which("arp.exe")
        if arp_command is None:
            return []

        command = [arp_command, "-a", peer]
        if local_ip is not None:
            command.extend(["-N", local_ip])
        return [command]

    raise DiscoveryError("Automatic MAC lookup supports Windows, macOS and Linux")


def discover_peer_mac(ip: str, local_ip: Optional[str] = None) -> str:
    """Read one exact IPv4 peer from the OS neighbor table; never scan a subnet.

    Open the setup connection first so the kernel has resolved its neighbor. The
    returned value is a candidate and must pass the device handshake before use.
    Windows can additionally restrict its query to the connection's interface.
    """
    try:
        peer = ipaddress.IPv4Address(ip)
        local = ipaddress.IPv4Address(local_ip) if local_ip is not None else None
        if local is not None and (local.is_unspecified or local.is_multicast):
            raise ipaddress.AddressValueError("Invalid local interface address")
    except ipaddress.AddressValueError as error:
        raise DiscoveryError(
            "Automatic MAC lookup requires IPv4 setup and interface addresses"
        ) from error

    commands = _neighbor_commands(str(peer), str(local) if local is not None else None)
    if not commands:
        raise DiscoveryError("No supported neighbor-table command is installed")

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="ascii",
                errors="replace",
                timeout=NEIGHBOR_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue

        if result.returncode != 0:
            continue

        candidate = _parse_peer_mac(result.stdout, peer, local)
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
                peer_ip = connection.getpeername()[0]
                if sys.platform == "win32":
                    candidate = discover_peer_mac(
                        peer_ip, local_ip=connection.getsockname()[0]
                    )
                else:
                    candidate = discover_peer_mac(peer_ip)

            nonce = secrets.token_bytes(4)
            key = secret_key(nonce, candidate)
            model = verify_device(connection, key, nonce, expected_model)
    except DiscoveryError:
        raise
    except (OSError, EOFError, ValueError) as error:
        raise DiscoveryError(
            "Could not verify the purifier's setup identity; check the setup Wi-Fi "
            "connection and full device MAC"
        ) from error

    return candidate, model
