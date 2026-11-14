"""Exercise model-aware provisioning against a stock-protocol TCP fixture."""

import json
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

from winix_local import pki
from winix_local.prepare_broker import export_broker_files
from winix_local.setup_protocol import crypt, header, secret_key

FIXTURE_MAC = "AABBCCDDEEFF"
FIXTURE_PASSWORD = "dummy-test-password"
MODEL_CANDIDATES = ("5510_US", "5520_US", "FUTURE_MODEL_US")


def read_exactly(connection, count):
    data = bytearray()
    while len(data) < count:
        chunk = connection.recv(count - len(data))
        if not chunk:
            if not data:
                return None
            raise EOFError("Client closed a partial request")
        data.extend(chunk)

    return bytes(data)


class PurifierFixture:
    """Record setup commands after reporting a configurable device model."""

    def __init__(self, model):
        self.model = model
        self.commands = []
        self.errors = []
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(5)
        self.port = self.listener.getsockname()[1]
        self.worker = threading.Thread(target=self.receive, daemon=True)

    def __enter__(self):
        self.worker.start()
        return self

    def __exit__(self, error_type, error, traceback):
        self.worker.join(5)
        self.listener.close()
        if error_type is None:
            if self.worker.is_alive():
                raise AssertionError("Provisioning fixture did not finish")
            if self.errors:
                raise self.errors[0]

    def receive(self):
        try:
            connection, _ = self.listener.accept()
            with connection:
                connection.settimeout(5)
                if read_exactly(connection, 4) != struct.pack("<I", 20):
                    raise ValueError("Unexpected handshake length")
                if read_exactly(connection, 16) != header(1):
                    raise ValueError("Unexpected handshake header")
                if read_exactly(connection, 1) != b"\x01":
                    raise ValueError("Missing security marker")

                nonce = read_exactly(connection, 4)
                key = secret_key(nonce, FIXTURE_MAC)
                self.reply(
                    connection, key, 1, self.model.encode("ascii") + b"\x001.0.3"
                )

                while True:
                    prefix = read_exactly(connection, 4)
                    if prefix is None:
                        return

                    (length,) = struct.unpack("<I", prefix)
                    request_header = read_exactly(connection, 16)
                    command = request_header[5]
                    if request_header != header(command) or command not in (2, 4):
                        raise ValueError("Unexpected setup command")

                    ciphertext_size = ((length - 16) // 16 + 1) * 16
                    body = crypt(
                        read_exactly(connection, ciphertext_size), key, decrypt=True
                    )
                    self.commands.append((command, body))
                    self.reply(connection, key, command, b"\x00")
        except BaseException as error:
            self.errors.append(error)
        finally:
            self.listener.close()

    @staticmethod
    def reply(connection, key, command, body):
        encrypted = crypt(body, key)
        connection.sendall(
            struct.pack("<I", 16 + len(encrypted)) + header(command, True) + encrypted
        )


def run_cli(arguments, password=""):
    return subprocess.run(
        [sys.executable, "-B", "-m", "winix_local.provision", *arguments],
        input=password + "\n",
        text=True,
        capture_output=True,
        timeout=15,
    )


def write_public_config(path, model):
    config = {
        "mac": FIXTURE_MAC,
        "model": model,
        "server_host": "10.0.0.20",
        "mqtt_host": "10.0.0.21",
        "mqtt_port": 8883,
        "http_port": 18080,
        "https_port": 18443,
    }
    path.write_text(json.dumps(config))


def provision_arguments(config_path, fixture):
    return [
        "provision",
        "--config",
        str(config_path),
        "--ssid",
        "FixtureNetwork",
        "--bssid",
        "AA:BB:CC:11:22:33",
        "--setup-host",
        "127.0.0.1",
        "--setup-port",
        str(fixture.port),
    ]


def initialization_arguments(output_path):
    return [
        "init",
        "--mac",
        FIXTURE_MAC,
        "--server-host",
        "127.0.0.1",
        "--mqtt-host",
        "127.0.0.1",
        "--out",
        str(output_path),
    ]


class ProvisionTests(unittest.TestCase):
    def test_models_share_complete_setup_without_logging_wifi_password(self):
        for model in MODEL_CANDIDATES:
            with self.subTest(model=model), tempfile.TemporaryDirectory() as directory:
                config_path = Path(directory) / "public.json"
                write_public_config(config_path, model)

                with PurifierFixture(model) as fixture:
                    result = run_cli(
                        provision_arguments(config_path, fixture), FIXTURE_PASSWORD
                    )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(model, result.stdout)
                self.assertNotIn(FIXTURE_PASSWORD, result.stdout + result.stderr)
                self.assertEqual([command for command, _ in fixture.commands], [2, 4])
                self.assertIn(b"https://10.0.0.20:18443/auth", fixture.commands[0][1])
                self.assertIn(b"http://10.0.0.20:18080/ca.pem", fixture.commands[0][1])
                self.assertIn(FIXTURE_PASSWORD.encode(), fixture.commands[1][1])
                self.assertIn(b"FixtureNetwork", fixture.commands[1][1])

    def test_exact_model_mismatch_stops_before_any_configuration(self):
        for reported_model in ("5520_US", "5510_US_FAKE"):
            with (
                self.subTest(reported_model=reported_model),
                tempfile.TemporaryDirectory() as directory,
            ):
                config_path = Path(directory) / "public.json"
                write_public_config(config_path, "5510_US")

                with PurifierFixture(reported_model) as fixture:
                    result = run_cli(
                        provision_arguments(config_path, fixture), FIXTURE_PASSWORD
                    )

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(fixture.commands, [])
                self.assertNotIn(FIXTURE_PASSWORD, result.stdout + result.stderr)

    def test_check_only_identifies_models_without_configuring_servers_or_wifi(self):
        for model in ("5510_US", "5520_US"):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as directory:
                config_path = Path(directory) / "public.json"
                write_public_config(config_path, model)

                with PurifierFixture(model) as fixture:
                    result = run_cli(
                        provision_arguments(config_path, fixture) + ["--check-only"]
                    )

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(model, result.stdout)
                self.assertEqual(fixture.commands, [])

    def test_explicit_model_initialization_records_both_bundle_formats(self):
        for model in MODEL_CANDIDATES:
            with self.subTest(model=model), tempfile.TemporaryDirectory() as directory:
                output = Path(directory) / "installation"
                result = run_cli(
                    initialization_arguments(output)
                    + [
                        "--model",
                        model,
                        "--setup-host",
                        "127.0.0.1",
                        "--setup-port",
                        "1",
                    ]
                )

                self.assertEqual(result.returncode, 0, result.stderr)
                for filename in ("bundle.json", "provision.json"):
                    self.assertEqual(
                        json.loads((output / filename).read_text())["model"], model
                    )

    def test_initialization_discovers_model_without_configuring_device(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "installation"
            with PurifierFixture("5520_US") as fixture:
                result = run_cli(
                    initialization_arguments(output)
                    + ["--setup-host", "127.0.0.1", "--setup-port", str(fixture.port)]
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(fixture.commands, [])
            for filename in ("bundle.json", "provision.json"):
                self.assertEqual(
                    json.loads((output / filename).read_text())["model"], "5520_US"
                )

    def test_broker_export_scopes_acl_to_configured_model_and_device(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle_directory = Path(directory) / "installation"
            output_directory = Path(directory) / "broker"
            config = pki.create_bundle(
                bundle_directory,
                FIXTURE_MAC,
                "127.0.0.1",
                "127.0.0.1",
                model="5520_US",
            )
            export_broker_files(bundle_directory, output_directory)

            suffix = f"Air01/5520_US/{FIXTURE_MAC}_{config['token']}"
            self.assertEqual(
                (output_directory / "device.acl").read_text().splitlines(),
                [
                    "user " + FIXTURE_MAC,
                    "topic read Winix/Control/" + suffix,
                    "topic write Winix/Conn/" + suffix,
                    "topic write Winix/Event/" + suffix,
                ],
            )
            self.assertNotIn("5510_US", (output_directory / "device.acl").read_text())


if __name__ == "__main__":
    unittest.main()
