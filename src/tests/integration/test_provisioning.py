#!/usr/bin/env python3
"""Exercise the provisioning CLI against a stock-protocol TCP fixture."""
import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

from winix_local.setup_protocol import crypt, header, secret_key


class ProvisionTests(unittest.TestCase):
    def test_complete_setup_and_password_not_logged(self):
        captured = []
        errors = []
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen()

        def exact(conn, n):
            b = b""
            while len(b) < n:
                chunk = conn.recv(n - len(b))
                if not chunk:
                    raise EOFError()
                b += chunk
            return b

        def device():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(5)
                    length = struct.unpack("<I", exact(conn, 4))[0]
                    self.assertEqual(length, 20)
                    self.assertEqual(exact(conn, 16), header(1))
                    self.assertEqual(exact(conn, 1), b"\x01")
                    nonce = exact(conn, 4)
                    key = secret_key(nonce, "AABBCCDDEEFF")

                    def reply(command, body):
                        encrypted = crypt(body, key)
                        conn.sendall(
                            struct.pack("<I", 16 + len(encrypted))
                            + header(command, True)
                            + encrypted
                        )

                    reply(1, b"5510_US\x001.0.3")
                    for command in (2, 4):
                        length = struct.unpack("<I", exact(conn, 4))[0]
                        self.assertEqual(exact(conn, 16), header(command))
                        body = crypt(
                            exact(conn, ((length - 16) // 16 + 1) * 16),
                            key,
                            decrypt=True,
                        )
                        captured.append(body)
                        reply(command, b"\x00")
            except BaseException as error:
                errors.append(error)
            finally:
                listener.close()

        worker = threading.Thread(target=device, daemon=True)
        worker.start()
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "public.json"
            config.write_text(
                json.dumps(
                    dict(
                        mac="AABBCCDDEEFF",
                        model="5510_US",
                        server_host="10.0.0.20",
                        mqtt_host="10.0.0.21",
                        mqtt_port=8883,
                        http_port=18080,
                        https_port=18443,
                    )
                )
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "winix_local.provision",
                    "provision",
                    "--config",
                    str(config),
                    "--ssid",
                    "FixtureNetwork",
                    "--bssid",
                    "AA:BB:CC:11:22:33",
                    "--setup-host",
                    "127.0.0.1",
                    "--setup-port",
                    str(listener.getsockname()[1]),
                ],
                input="dummy-test-password\n",
                text=True,
                capture_output=True,
                timeout=10,
            )
            worker.join(3)
            if errors:
                raise errors[0]
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn("dummy-test-password", result.stdout + result.stderr)
            self.assertIn(b"https://10.0.0.20:18443/auth", captured[0])
            self.assertIn(b"http://10.0.0.20:18080/ca.pem", captured[0])
            self.assertIn(b"dummy-test-password", captured[1])
            self.assertIn(b"FixtureNetwork", captured[1])


if __name__ == "__main__":
    unittest.main()
