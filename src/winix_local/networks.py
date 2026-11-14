"""Read nearby Wi-Fi networks through the purifier's setup scan command.

The DP0301 layout was checked against the app parser and two captured 5510
responses. Scanning sends DP0100 and DP0300 only; it changes no device settings.
"""

from dataclasses import dataclass
import secrets
import socket
import struct
from typing import Optional

from .setup_protocol import (
    MAX_RESPONSE_BYTES,
    MAX_SSID_BYTES,
    NONCE_BYTES,
    decrypt_response,
    read_response,
    scan_request,
    secret_key,
)
from .wire import mac_id

SCAN_COMMAND = 3
SCAN_COUNT_BYTES = 2
SCAN_RETURN_CODE_BYTES = 1
NETWORK_FIXED_BYTES = 15


@dataclass(frozen=True)
class WifiNetwork:
    """One access point; security is the raw device code, without guessed labels."""

    ssid: str
    bssid: str
    rssi: int
    channel: int
    security: int


def decode_scan(body: bytes) -> tuple[WifiNetwork, ...]:
    """Decode a complete successful DP0301 plaintext body with bounded fields."""
    minimum_length = SCAN_COUNT_BYTES + SCAN_RETURN_CODE_BYTES
    if not minimum_length <= len(body) <= MAX_RESPONSE_BYTES:
        raise ValueError("Invalid Wi-Fi scan response length")

    payload_end = len(body) - SCAN_RETURN_CODE_BYTES
    if body[payload_end] != 0:
        raise ValueError("Purifier rejected the Wi-Fi scan request")

    (count,) = struct.unpack_from("<H", body)
    if count > (payload_end - SCAN_COUNT_BYTES) // NETWORK_FIXED_BYTES:
        raise ValueError("Wi-Fi scan record count exceeds the response length")

    networks = []
    offset = SCAN_COUNT_BYTES

    for _ in range(count):
        if offset + 4 > payload_end:
            raise ValueError("Truncated Wi-Fi scan record header")

        _record_index, ssid_length = struct.unpack_from("<HH", body, offset)
        if ssid_length > MAX_SSID_BYTES:
            raise ValueError("Wi-Fi scan SSID exceeds 32 bytes")

        record_end = offset + NETWORK_FIXED_BYTES + ssid_length
        if record_end > payload_end:
            raise ValueError("Truncated Wi-Fi scan record")

        ssid_start = offset + 4
        bssid_start = ssid_start + ssid_length
        try:
            ssid = body[ssid_start:bssid_start].decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError(
                "Wi-Fi scan contains a non-UTF-8 SSID; enter network details manually"
            ) from error

        bssid = ":".join(
            f"{octet:02X}" for octet in body[bssid_start : bssid_start + 6]
        )
        rssi, channel, security = struct.unpack_from("<hHB", body, bssid_start + 6)
        networks.append(
            WifiNetwork(
                ssid=ssid,
                bssid=bssid,
                rssi=rssi,
                channel=channel,
                security=security,
            )
        )
        offset = record_end

    if offset != payload_end:
        raise ValueError("Unexpected trailing bytes in Wi-Fi scan response")

    return tuple(networks)


def scan_networks(
    setup_host: str,
    setup_port: int,
    mac: str,
    expected_model: Optional[str] = None,
) -> tuple[WifiNetwork, ...]:
    """Verify one setup peer and ask it for a Wi-Fi scan, without configuring it."""
    from .provision import SETUP_TIMEOUT_SECONDS, verify_device

    mac = mac_id(mac)
    nonce = secrets.token_bytes(NONCE_BYTES)
    key = secret_key(nonce, mac)

    with socket.create_connection(
        (setup_host, setup_port), timeout=SETUP_TIMEOUT_SECONDS
    ) as connection:
        verify_device(connection, key, nonce, expected_model=expected_model)
        connection.sendall(scan_request())
        plaintext = decrypt_response(read_response(connection), key, SCAN_COMMAND)

    return decode_scan(plaintext)
