"""Encode and decode local Winix setup messages.

DP0100 and response decryption verified against a stock 5510_US on 2026-11-13.
This module has no automatic device writes or cloud connections.
"""

import re
import socket
import struct

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# GEN2 frames carry a four-byte length followed by a fixed 16-byte header.
# The advertised request length counts plaintext, even when AES adds padding.
FRAME_HEADER_BYTES = 16
NONCE_BYTES = 4
AES_KEY_BYTES = 16
# Bound the advertised response size before reading its body.
MAX_RESPONSE_BYTES = 65536
MAX_URI_BYTES = 65535  # Each URI length occupies an unsigned 16-bit field.
MAX_SSID_BYTES = 32
MAX_PASSWORD_BYTES = 63


def header(command: int, response: bool = False) -> bytes:
    if not 1 <= command <= 4:
        raise ValueError("unexpected setup command")

    return b"GEN2" + bytes([int(response), command]) + bytes(10)


def handshake(nonce: bytes) -> bytes:
    if len(nonce) != NONCE_BYTES:
        raise ValueError("DP0100 nonce must be four bytes")

    # The advertised request length excludes the cleartext 0x01 security marker.
    return (
        struct.pack("<I", FRAME_HEADER_BYTES + len(nonce)) + header(1) + b"\x01" + nonce
    )


def secret_key(nonce: bytes, mac: str) -> bytes:
    mac = mac.replace(":", "").upper()
    if len(nonce) != NONCE_BYTES or not re.fullmatch(r"[0-9A-F]{12}", mac):
        raise ValueError("four-byte nonce and twelve-digit MAC required")

    mac_bytes = mac.encode("ascii")
    return bytes(
        [
            nonce[0],
            mac_bytes[0],
            mac_bytes[2],
            mac_bytes[4],
            nonce[1],
            mac_bytes[6],
            mac_bytes[8],
            mac_bytes[10],
            nonce[2],
            mac_bytes[1],
            mac_bytes[3],
            mac_bytes[5],
            nonce[3],
            mac_bytes[7],
            mac_bytes[9],
            mac_bytes[11],
        ]
    )


def crypt(data: bytes, key: bytes, decrypt: bool = False) -> bytes:
    """Match the stock protocol's AES-128-ECB and PKCS#7 padding in process."""
    if len(key) != AES_KEY_BYTES:
        raise ValueError("AES-128 key required")

    cipher = Cipher(algorithms.AES(key), modes.ECB())
    if decrypt:
        if not data or len(data) % AES_KEY_BYTES:
            raise ValueError("invalid encrypted setup payload")

        decryptor = cipher.decryptor()
        padded = decryptor.update(data) + decryptor.finalize()
        unpadder = padding.PKCS7(algorithms.AES.block_size).unpadder()
        try:
            return unpadder.update(padded) + unpadder.finalize()
        except ValueError as error:
            raise ValueError("invalid encrypted setup payload") from error

    padder = padding.PKCS7(algorithms.AES.block_size).padder()
    padded = padder.update(data) + padder.finalize()
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def server_body(
    ca_url: str,
    auth_url: str,
    broker_host: str,
    broker_port: int,
    region: int = 1,
    *,
    auth_ca_url: str = "",
) -> bytes:
    """URI01 CA download enabled local auth TLS on a stock 5510_US.

    Keep the empty default unless explicitly testing a separate auth CA.
    MQTT trust and backend registration remain separate acceptance tests.
    """
    if not 1 <= broker_port <= 65535 or not 0 <= region <= 255:
        raise ValueError("port or region out of range")

    uris = [ca_url, auth_ca_url, auth_url, broker_host]
    body = bytearray(struct.pack("<H", len(uris)))

    for uri in uris:
        data = uri.encode("utf-8")
        if len(data) > MAX_URI_BYTES or b"\x00" in data:
            raise ValueError("invalid URI")

        body.extend(struct.pack("<H", len(data)))
        body.extend(data)

    body.extend(struct.pack("<HB", broker_port, region))
    return bytes(body)


def encrypted_request(command: int, body: bytes, key: bytes) -> bytes:
    return (
        struct.pack("<I", FRAME_HEADER_BYTES + len(body))
        + header(command)
        + crypt(body, key)
    )


def scan_request() -> bytes:
    return struct.pack("<I", FRAME_HEADER_BYTES) + header(3)


def wifi_body(ssid: str, password: str, bssid: str) -> bytes:
    """Encode the DP0400 Wi-Fi settings without transmitting them."""
    ssid_bytes, password_bytes = ssid.encode("utf-8"), password.encode("utf-8")
    bssid = bssid.replace(":", "")
    if (
        not 1 <= len(ssid_bytes) <= MAX_SSID_BYTES
        or len(password_bytes) > MAX_PASSWORD_BYTES
    ):
        raise ValueError("SSID or passphrase length out of range")
    if not re.fullmatch(r"[0-9a-fA-F]{12}", bssid):
        raise ValueError("six-byte access point BSSID required")

    return (
        struct.pack("<H", len(ssid_bytes))
        + ssid_bytes
        + struct.pack("<H", len(password_bytes))
        + password_bytes
        + bytes.fromhex(bssid)
    )


def read_response(connection: socket.socket, limit: int = MAX_RESPONSE_BYTES) -> bytes:
    def read_exactly(count: int) -> bytes:
        data = bytearray()
        while len(data) < count:
            chunk = connection.recv(count - len(data))
            if not chunk:
                raise EOFError("purifier closed a partial response")
            data.extend(chunk)
        return bytes(data)

    prefix = read_exactly(4)
    (length,) = struct.unpack("<I", prefix)
    if not FRAME_HEADER_BYTES <= length <= limit:
        raise ValueError("unexpected response length")

    data = prefix + read_exactly(length)
    if data[4:8] != b"GEN2" or data[8] != 1:
        raise ValueError("unexpected response header")

    return data


def decrypt_response(packet: bytes, key: bytes, command: int) -> bytes:
    if packet[4:20] != header(command, response=True):
        raise ValueError("response command or reserved header differs")

    return crypt(packet[20:], key, decrypt=True)
