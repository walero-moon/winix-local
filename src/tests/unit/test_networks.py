"""Synthetic DP0301 scan fixtures; no real access-point data or network calls."""

from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
import io
import struct
import unittest
from unittest.mock import patch

from winix_local.networks import WifiNetwork, decode_scan, scan_networks
from winix_local.setup_protocol import (
    crypt,
    handshake,
    header,
    scan_request,
    secret_key,
)

FIXTURE_MAC = "AABBCCDDEEFF"
FIXTURE_NONCE = b"1234"


def network_record(
    index=0,
    ssid=b"Example-IoT",
    bssid=bytes.fromhex("021122334455"),
    rssi=-47,
    channel=6,
    security=4,
):
    return (
        struct.pack("<HH", index, len(ssid))
        + ssid
        + bssid
        + struct.pack("<hHB", rssi, channel, security)
    )


def scan_body(*records, return_code=0):
    return struct.pack("<H", len(records)) + b"".join(records) + bytes([return_code])


def response(command, body):
    key = secret_key(FIXTURE_NONCE, FIXTURE_MAC)
    ciphertext = crypt(body, key)
    return (
        struct.pack("<I", 16 + len(ciphertext))
        + header(command, response=True)
        + ciphertext
    )


class FixtureSocket:
    def __init__(self, responses):
        self.responses = bytearray(responses)
        self.sent = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def sendall(self, packet):
        self.sent.append(packet)

    def recv(self, count):
        # Real TCP may split any frame across reads.
        size = min(count, 3)
        chunk = bytes(self.responses[:size])
        del self.responses[:size]
        return chunk


class ScanDecoderTests(unittest.TestCase):
    def test_multiple_access_points_hidden_ssid_and_utf8_byte_lengths(self):
        body = scan_body(
            network_record(),
            network_record(
                index=1,
                ssid="Caf\u00e9".encode(),
                bssid=bytes.fromhex("0266778899AA"),
                rssi=-62,
                channel=11,
                security=7,
            ),
            network_record(
                index=2,
                ssid=b"",
                bssid=bytes.fromhex("02BBCCDDEEFF"),
                rssi=-80,
                channel=1,
                security=5,
            ),
        )
        networks = decode_scan(body)
        self.assertEqual(
            networks,
            (
                WifiNetwork("Example-IoT", "02:11:22:33:44:55", -47, 6, 4),
                WifiNetwork("Caf\u00e9", "02:66:77:88:99:AA", -62, 11, 7),
                WifiNetwork("", "02:BB:CC:DD:EE:FF", -80, 1, 5),
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            networks[0].ssid = "changed"

    def test_successful_empty_scan_and_duplicate_ssids_are_preserved(self):
        self.assertEqual(decode_scan(scan_body()), ())
        networks = decode_scan(
            scan_body(
                network_record(),
                network_record(index=1, bssid=bytes.fromhex("0266778899AA")),
            )
        )
        self.assertEqual(
            [network.ssid for network in networks], ["Example-IoT", "Example-IoT"]
        )
        self.assertNotEqual(networks[0].bssid, networks[1].bssid)

    def test_malformed_scan_cannot_exceed_fields_or_ignore_trailing_data(self):
        valid = scan_body(network_record())
        cases = (
            b"",
            bytes(2),
            bytes(65537),
            struct.pack("<H", 65535) + b"\x00",
            valid[:-2] + b"\x00",
            valid + b"\x00",
            scan_body(network_record(ssid=b"A" * 33)),
        )
        for body in cases:
            with self.subTest(size=len(body)), self.assertRaises(ValueError):
                decode_scan(body)

    def test_scan_failure_and_invalid_utf8_have_explicit_errors(self):
        with self.assertRaisesRegex(ValueError, "rejected"):
            decode_scan(scan_body(return_code=1))
        with self.assertRaisesRegex(ValueError, "non-UTF-8"):
            decode_scan(scan_body(network_record(ssid=b"\xff")))


class ScanRequestTests(unittest.TestCase):
    def test_single_connection_sends_only_handshake_and_scan(self):
        fixture = FixtureSocket(
            response(1, b"5520_US\x001.0.3") + response(3, scan_body(network_record()))
        )
        with (
            patch(
                "winix_local.networks.socket.create_connection", return_value=fixture
            ) as connect,
            patch(
                "winix_local.networks.secrets.token_bytes", return_value=FIXTURE_NONCE
            ),
            redirect_stdout(io.StringIO()),
        ):
            networks = scan_networks(
                "127.0.0.1", 47000, FIXTURE_MAC, expected_model="5520_US"
            )

        connect.assert_called_once_with(("127.0.0.1", 47000), timeout=10)
        self.assertEqual(fixture.sent, [handshake(FIXTURE_NONCE), scan_request()])
        self.assertEqual(networks[0].ssid, "Example-IoT")
        self.assertTrue(fixture.closed)
        self.assertEqual(fixture.responses, b"")

    def test_model_mismatch_stops_before_scan_or_configuration(self):
        fixture = FixtureSocket(response(1, b"5520_US\x001.0.3"))
        with (
            patch(
                "winix_local.networks.socket.create_connection", return_value=fixture
            ),
            patch(
                "winix_local.networks.secrets.token_bytes", return_value=FIXTURE_NONCE
            ),
            self.assertRaisesRegex(ValueError, "does not match"),
        ):
            scan_networks("127.0.0.1", 47000, FIXTURE_MAC, expected_model="5510_US")

        self.assertEqual(fixture.sent, [handshake(FIXTURE_NONCE)])
        self.assertTrue(fixture.closed)


if __name__ == "__main__":
    unittest.main()
