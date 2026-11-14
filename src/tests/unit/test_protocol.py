"""Wire-layout checks independent of a connected purifier."""

import struct
import unittest

from winix_local.setup_protocol import (
    crypt,
    decrypt_response,
    handshake,
    secret_key,
    server_body,
    wifi_body,
)


class ProtocolTests(unittest.TestCase):
    def test_handshake_matches_recovered_wire_layout(self):
        self.assertEqual(
            handshake(b"\x00\x01\x02\x03").hex(),
            "1400000047454e320001000000000000000000000100010203",
        )

    def test_aes_matches_standard_aes128_ecb_vector(self):
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        plain = bytes.fromhex("00112233445566778899aabbccddeeff")
        cipher = crypt(plain, key)
        self.assertEqual(cipher[:16].hex(), "69c4e0d86a7b0430d8cdb78070b4c55a")
        self.assertEqual(crypt(cipher, key, decrypt=True), plain)

    def test_server_body_has_four_length_prefixed_uris_and_port_and_region(self):
        for auth_ca in ("", "http://lab/auth-ca.pem"):
            with self.subTest(auth_ca=auth_ca):
                body = server_body(
                    "http://lab/ca",
                    "https://lab/auth",
                    "lab",
                    18883,
                    auth_ca_url=auth_ca,
                )
                self.assertEqual(struct.unpack_from("<H", body)[0], 4)
                pos, uris = 2, []
                for _ in range(4):
                    (length,) = struct.unpack_from("<H", body, pos)
                    pos += 2
                    uris.append(body[pos : pos + length].decode())
                    pos += length
                self.assertEqual(
                    uris, ["http://lab/ca", auth_ca, "https://lab/auth", "lab"]
                )
                self.assertEqual(body[pos:], b"\xc3\x49\x01")

    def test_wifi_lengths_use_utf8_and_include_binary_bssid(self):
        self.assertEqual(
            wifi_body("lab", "test-only", "01:23:45:67:89:ab"),
            b"\x03\x00lab\x09\x00test-only\x01\x23\x45\x67\x89\xab",
        )

    def test_invalid_identity_or_wrong_response_command_is_rejected(self):
        with self.assertRaises(ValueError):
            secret_key(b"123", "AABBCCDDEEFF")
        with self.assertRaises(ValueError):
            secret_key(b"1234", "not-a-mac")
        with self.assertRaises(ValueError):
            decrypt_response(bytes(32), bytes(16), 2)


if __name__ == "__main__":
    unittest.main()
