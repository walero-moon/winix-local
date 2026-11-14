"""Wire-layout checks independent of a connected purifier."""

import struct
import unittest
from unittest.mock import patch

from winix_local.setup_protocol import (
    crypt,
    decrypt_response,
    encrypted_request,
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

    def test_pkcs7_ciphertexts_match_the_previous_wire_implementation(self):
        # Frozen output from the previous codec, using synthetic data and the
        # public FIPS-197 example key. No executable is needed to run the test.
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        vectors = {
            0: "954f64f2e4e86e9eee82d20216684899",
            1: "4c4d10e1f5542fef3e2da31ff4b4471a",
            15: "b61e6af8da7260d2214369b951bf8963",
            16: ("0a940bb5416ef045f1c39458c653ea5a" "954f64f2e4e86e9eee82d20216684899"),
            17: ("0a940bb5416ef045f1c39458c653ea5a" "840766b749c09b4974288c10b9c20970"),
            32: (
                "0a940bb5416ef045f1c39458c653ea5a"
                "07feef74e1d5036e900eee118e949293"
                "954f64f2e4e86e9eee82d20216684899"
            ),
        }

        for length, expected in vectors.items():
            with self.subTest(length=length):
                plaintext = bytes(range(length))
                ciphertext = bytes.fromhex(expected)
                self.assertEqual(crypt(plaintext, key), ciphertext)
                self.assertEqual(crypt(ciphertext, key, decrypt=True), plaintext)

    def test_unicode_wifi_request_keeps_its_exact_encrypted_frame(self):
        body = wifi_body("Café Wi-Fi", "fixture-only", "10:20:30:40:50:60")
        key = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
        expected = bytes.fromhex(
            "3100000047454e32000400000000000000000000"
            "1b677716f4c4b0a8cdadc4e7da036bfb"
            "f9636a9e3e0eda7e588073b705ca61342"
            "ec95b5b97649f6a58ed8d8d07be35c7"
        )

        self.assertEqual(encrypted_request(4, body, key), expected)

    def test_cipher_runs_without_external_programs(self):
        key = bytes(range(16))
        plaintext = b"fixture setup settings"
        with patch("subprocess.run", side_effect=AssertionError("external process")):
            self.assertEqual(crypt(crypt(plaintext, key), key, decrypt=True), plaintext)

    def test_invalid_ciphertexts_and_keys_fail_without_echoing_data(self):
        key = bytes(range(16))
        # The FIPS ciphertext decrypts to a final byte of 0xff, which is not
        # valid PKCS#7 padding. Other cases exercise truncated block handling.
        invalid_ciphertexts = (
            b"",
            b"x",
            b"x" * 15,
            b"x" * 17,
            bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a"),
        )
        for ciphertext in invalid_ciphertexts:
            with self.subTest(length=len(ciphertext)):
                with self.assertRaisesRegex(
                    ValueError, "invalid encrypted setup payload"
                ) as failed:
                    crypt(ciphertext, key, decrypt=True)
                self.assertNotIn(key.hex(), str(failed.exception))
                if ciphertext:
                    self.assertNotIn(ciphertext.hex(), str(failed.exception))

        for invalid_key in (b"", bytes(15), bytes(17), bytes(24), bytes(32)):
            with self.subTest(key_length=len(invalid_key)):
                with self.assertRaisesRegex(ValueError, "AES-128 key required"):
                    crypt(b"fixture", invalid_key)

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
