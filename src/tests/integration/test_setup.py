"""Guided setup through its prompts and public seams; no real device calls."""

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from winix_local import cli, setup
from winix_local.discovery import DiscoveryError
from winix_local.networks import WifiNetwork
from winix_local.setup_checks import EndpointCheckError
from winix_local.setup_files import prepare_setup
from winix_local.setup_protocol import wifi_body

if __package__:
    from .test_provisioning import PurifierFixture
else:
    from test_provisioning import PurifierFixture


MAC = "AABBCCDDEEFF"
MODEL = "5520_US"
PASSWORD = "fixture-wireless-$secret&"
NETWORK = WifiNetwork("Fixture-IoT", "10:20:30:40:50:60", -45, 6, 3)


def arguments(**changes):
    values = {
        "config": None,
        "directory": None,
        "mac": None,
        "setup_host": "192.168.43.1",
        "setup_port": 47000,
        "prepare_only": False,
    }
    values.update(changes)
    return argparse.Namespace(**values)


def prepared(directory):
    return prepare_setup(
        directory,
        mac=MAC,
        model=MODEL,
        server_host="192.168.1.20",
        mqtt_host="192.168.1.21",
    )


def snapshots(directory):
    return {
        path.relative_to(directory)
        .as_posix(): hashlib.sha256(path.read_bytes())
        .hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


class GuidedSetupTests(unittest.TestCase):
    def test_existing_public_config_guides_scan_and_provision_without_saving_wifi_secret(
        self,
    ):
        with tempfile.TemporaryDirectory() as temporary:
            installation = prepared(Path(temporary) / "private")
            config_path = installation.bundle_directory / "provision.json"
            config = json.loads(config_path.read_text())
            before = snapshots(installation.root)
            output = io.StringIO()
            args = arguments(config=config_path)

            with (
                patch("builtins.input", side_effect=["", "1", "y"]),
                patch("winix_local.setup.getpass.getpass", return_value=PASSWORD),
                patch("winix_local.setup.verify_endpoints") as endpoints,
                patch(
                    "winix_local.setup.identify_device", return_value=(MAC, MODEL)
                ) as identify,
                patch("winix_local.setup.scan_networks", return_value=(NETWORK,)),
                patch("winix_local.setup.apply_configuration") as apply,
                redirect_stdout(output),
            ):
                setup.run_setup(args)

            endpoints.assert_called_once_with(config)
            identify.assert_called_once()
            self.assertEqual(identify.call_args.kwargs.get("mac"), MAC)
            self.assertEqual(identify.call_args.kwargs.get("expected_model"), MODEL)
            apply.assert_called_once()
            call_values = (*apply.call_args.args, *apply.call_args.kwargs.values())
            self.assertIn(config, call_values)
            self.assertIn(wifi_body(NETWORK.ssid, PASSWORD, NETWORK.bssid), call_values)
            self.assertNotIn(PASSWORD, output.getvalue())
            self.assertEqual(snapshots(installation.root), before)
            self.assertFalse(
                any(
                    PASSWORD.encode() in path.read_bytes()
                    for path in installation.root.rglob("*")
                    if path.is_file()
                )
            )

    def test_zero_option_setup_accepts_existing_config_without_preparing_new_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            installation = prepared(Path(temporary) / "private")
            config_path = installation.bundle_directory / "provision.json"
            config = json.loads(config_path.read_text())
            before = snapshots(installation.root)
            args = arguments()

            with (
                patch("builtins.input", side_effect=[str(config_path), "", "1", "y"]),
                patch("winix_local.setup.prepare_setup") as prepare,
                patch("winix_local.setup.verify_endpoints") as endpoints,
                patch("winix_local.setup.identify_device", return_value=(MAC, MODEL)),
                patch("winix_local.setup.scan_networks", return_value=(NETWORK,)),
                patch("winix_local.setup.getpass.getpass", return_value=PASSWORD),
                patch("winix_local.setup.apply_configuration") as apply,
                redirect_stdout(io.StringIO()),
            ):
                setup.run_setup(args)

            prepare.assert_not_called()
            endpoints.assert_called_once_with(config)
            apply.assert_called_once()
            self.assertEqual(snapshots(installation.root), before)

    def test_guided_setup_sends_real_tcp_configuration_and_preserves_ssid_spaces(self):
        with tempfile.TemporaryDirectory() as temporary:
            installation = prepared(Path(temporary) / "private")
            config_path = installation.bundle_directory / "provision.json"
            before = snapshots(installation.root)
            output = io.StringIO()
            ssid = " Fixture-IoT "

            with PurifierFixture(MODEL) as fixture:
                args = arguments(
                    config=config_path,
                    setup_host="127.0.0.1",
                    setup_port=fixture.port,
                )
                with (
                    patch("builtins.input", side_effect=["", ssid, NETWORK.bssid, "y"]),
                    patch("winix_local.setup.verify_endpoints"),
                    patch(
                        "winix_local.setup.identify_device", return_value=(MAC, MODEL)
                    ),
                    patch("winix_local.setup.scan_networks", return_value=()),
                    patch("winix_local.setup.getpass.getpass", return_value=PASSWORD),
                    redirect_stdout(output),
                ):
                    setup.run_setup(args)

            self.assertEqual([command for command, _ in fixture.commands], [2, 4])
            server_settings = fixture.commands[0][1]
            self.assertIn(b"http://192.168.1.20:18080/ca.pem", server_settings)
            self.assertIn(b"https://192.168.1.20:18443/auth", server_settings)
            self.assertIn(b"192.168.1.21", server_settings)
            self.assertEqual(
                fixture.commands[1][1], wifi_body(ssid, PASSWORD, NETWORK.bssid)
            )
            self.assertNotIn(PASSWORD, output.getvalue())
            self.assertEqual(snapshots(installation.root), before)

    def test_prepare_only_discovers_mac_and_reuses_certificates_and_password(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private"
            args = arguments(directory=directory, prepare_only=True)
            answers = ["192.168.1.20", "192.168.1.21", "", "", "", ""]

            with (
                patch("builtins.input", side_effect=answers * 2),
                patch(
                    "winix_local.setup.identify_device", return_value=(MAC, MODEL)
                ) as identify,
                patch("winix_local.setup.verify_endpoints") as endpoints,
                patch("winix_local.setup.scan_networks") as scan,
                patch("winix_local.setup.apply_configuration") as apply,
                patch("winix_local.setup.getpass.getpass") as password,
                redirect_stdout(io.StringIO()),
            ):
                setup.run_setup(args)
                before = snapshots(directory)
                setup.run_setup(args)

            self.assertEqual(identify.call_count, 2)
            for call in identify.call_args_list:
                self.assertIsNone(call.kwargs.get("mac"))
            self.assertEqual(snapshots(directory), before)
            self.assertTrue((directory / "bundle/ca.key").is_file())
            self.assertTrue((directory / "broker-config/device.acl").is_file())
            endpoints.assert_not_called()
            scan.assert_not_called()
            apply.assert_not_called()
            password.assert_not_called()

    def test_manual_mac_is_requested_only_after_automatic_identification_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "private"
            args = arguments(directory=directory, prepare_only=True)
            answers = [
                "192.168.1.20",
                "192.168.1.21",
                "",
                "",
                "",
                "",
                "aa:bb:cc:dd:ee:ff",
            ]

            with (
                patch("builtins.input", side_effect=answers),
                patch(
                    "winix_local.setup.identify_device",
                    side_effect=[
                        DiscoveryError("fixture neighbor unavailable"),
                        (MAC, MODEL),
                    ],
                ) as identify,
                patch("winix_local.setup.apply_configuration") as apply,
                redirect_stdout(io.StringIO()),
            ):
                setup.run_setup(args)

            self.assertEqual(identify.call_count, 2)
            self.assertIsNone(identify.call_args_list[0].kwargs["mac"])
            self.assertEqual(identify.call_args_list[1].kwargs["mac"], MAC)
            config = json.loads((directory / "bundle/provision.json").read_text())
            self.assertEqual(config["mac"], MAC)
            apply.assert_not_called()

    def test_manual_network_and_declined_confirmation_send_no_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            installation = prepared(Path(temporary) / "private")
            before = snapshots(installation.root)
            output = io.StringIO()
            args = arguments(config=installation.bundle_directory / "provision.json")

            with (
                patch(
                    "builtins.input", side_effect=["", "Hidden-IoT", NETWORK.bssid, "n"]
                ),
                patch("winix_local.setup.verify_endpoints"),
                patch("winix_local.setup.identify_device", return_value=(MAC, MODEL)),
                patch("winix_local.setup.scan_networks", return_value=()),
                patch("winix_local.setup.getpass.getpass", return_value=PASSWORD),
                patch("winix_local.setup.apply_configuration") as apply,
                redirect_stdout(output),
            ):
                setup.run_setup(args)

            apply.assert_not_called()
            self.assertIn("Cancelled", output.getvalue())
            self.assertNotIn(PASSWORD, output.getvalue())
            self.assertEqual(snapshots(installation.root), before)

    def test_unready_services_abort_before_device_identification_or_writes(self):
        with tempfile.TemporaryDirectory() as temporary:
            installation = prepared(Path(temporary) / "private")
            before = snapshots(installation.root)
            args = arguments(config=installation.bundle_directory / "provision.json")

            with (
                patch("builtins.input", return_value="n"),
                patch(
                    "winix_local.setup.verify_endpoints",
                    side_effect=EndpointCheckError("fixture services are unavailable"),
                ),
                patch("winix_local.setup.identify_device") as identify,
                patch("winix_local.setup.apply_configuration") as apply,
                patch("winix_local.setup.getpass.getpass") as password,
                redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(setup.SetupCancelled):
                    setup.run_setup(args)

            identify.assert_not_called()
            apply.assert_not_called()
            password.assert_not_called()
            self.assertEqual(snapshots(installation.root), before)

    def test_keyboard_cancellation_preserves_existing_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            installation = prepared(Path(temporary) / "private")
            before = snapshots(installation.root)
            args = arguments(config=installation.bundle_directory / "provision.json")

            with (
                patch("builtins.input", side_effect=KeyboardInterrupt),
                patch("winix_local.setup.verify_endpoints"),
                patch("winix_local.setup.identify_device") as identify,
                patch("winix_local.setup.apply_configuration") as apply,
                redirect_stdout(io.StringIO()),
            ):
                with self.assertRaises(setup.SetupCancelled):
                    setup.run_setup(args)

            identify.assert_not_called()
            apply.assert_not_called()
            self.assertEqual(snapshots(installation.root), before)

    def test_handoff_errors_are_concise_and_do_not_expose_protocol_keys(self):
        protocol_key = "fixture-sensitive-aes-key"
        failures = (
            EOFError("partial reply"),
            subprocess.CalledProcessError(1, ["openssl", "-K", protocol_key]),
        )
        with tempfile.TemporaryDirectory() as temporary:
            installation = prepared(Path(temporary) / "private")
            args = arguments(config=installation.bundle_directory / "provision.json")
            before = snapshots(installation.root)

            for failure in failures:
                output = io.StringIO()
                with (
                    self.subTest(failure=type(failure).__name__),
                    patch("builtins.input", side_effect=["", "1", "y"]),
                    patch("winix_local.setup.verify_endpoints"),
                    patch(
                        "winix_local.setup.identify_device", return_value=(MAC, MODEL)
                    ),
                    patch("winix_local.setup.scan_networks", return_value=(NETWORK,)),
                    patch("winix_local.setup.getpass.getpass", return_value=PASSWORD),
                    patch("winix_local.setup.apply_configuration", side_effect=failure),
                    patch(
                        "winix_local.cli.provision_main",
                        side_effect=lambda: setup.run_setup(args),
                    ),
                    redirect_stdout(output),
                    redirect_stderr(output),
                ):
                    with self.assertRaises(SystemExit) as stopped:
                        cli.main()

                self.assertEqual(stopped.exception.code, 1)
                self.assertIn("Some settings may have been sent", output.getvalue())
                self.assertNotIn("Traceback", output.getvalue())
                self.assertNotIn(protocol_key, output.getvalue())
                self.assertNotIn(PASSWORD, output.getvalue())
                self.assertEqual(snapshots(installation.root), before)

    def test_setup_command_exposes_guided_options(self):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "winix_local", "setup", "--help"],
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--config", result.stdout)
        self.assertIn("--prepare-only", result.stdout)
        self.assertIn("--mac", result.stdout)


if __name__ == "__main__":
    unittest.main()
