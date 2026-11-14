"""Private guided-setup artifacts and safe reruns; no service or device calls."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from winix_local.setup_files import (
    BROKER_RUNTIME_FILES,
    PreparedSetup,
    SetupFilesError,
    prepare_setup,
)

SETTINGS = {
    "mac": "AA:BB:CC:DD:EE:FF",
    "model": "5520_US",
    "server_host": "192.168.1.20",
    "mqtt_host": "broker.example",
}


def snapshot(directory):
    return {
        path.relative_to(directory)
        .as_posix(): hashlib.sha256(path.read_bytes())
        .hexdigest()
        for path in directory.rglob("*")
        if path.is_file()
    }


class SetupFilesTests(unittest.TestCase):
    def test_prepare_and_resume_preserve_ca_token_and_broker_password(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "installation"
            prepared = prepare_setup(directory, **SETTINGS)
            before = snapshot(directory)

            self.assertIsInstance(prepared, PreparedSetup)
            self.assertFalse(prepared.reused)
            self.assertEqual(prepared.installation_config["model"], "5520_US")
            self.assertEqual(prepared.installation_config["mac"], "AABBCCDDEEFF")
            self.assertEqual(
                prepared.ha_credentials_path.parent, prepared.broker_export_directory
            )

            resumed = prepare_setup(directory, **SETTINGS)
            self.assertTrue(resumed.reused)
            self.assertEqual(snapshot(directory), before)
            self.assertEqual(resumed.installation_config, prepared.installation_config)

    def test_broker_runtime_directory_has_no_ca_signing_key_or_ha_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_setup(Path(temporary) / "installation", **SETTINGS)
            self.assertEqual(
                {path.name for path in prepared.broker_config_directory.iterdir()},
                set(BROKER_RUNTIME_FILES),
            )
            self.assertTrue((prepared.bundle_directory / "ca.key").is_file())
            self.assertFalse((prepared.broker_config_directory / "ca.key").exists())
            self.assertFalse(
                (prepared.broker_config_directory / "ha-credentials.json").exists()
            )

            for path in prepared.root.rglob("*"):
                self.assertEqual(
                    path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600
                )
            self.assertEqual(prepared.root.stat().st_mode & 0o777, 0o700)

            state = json.loads((prepared.root / "setup.json").read_text())
            self.assertNotIn("token", state["settings"])
            self.assertNotIn("password", state["settings"])

    def test_changed_settings_or_files_never_rotate_existing_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "installation"
            prepare_setup(directory, **SETTINGS)
            before = snapshot(directory)

            changed = dict(SETTINGS, model="5510_US")
            with self.assertRaisesRegex(SetupFilesError, "settings differ"):
                prepare_setup(directory, **changed)
            self.assertEqual(snapshot(directory), before)

            password_file = directory / "broker-export/passwords"
            password_file.write_text("changed by user\n")
            before = snapshot(directory)
            with self.assertRaisesRegex(SetupFilesError, "artifact changed"):
                prepare_setup(directory, **SETTINGS)
            self.assertEqual(snapshot(directory), before)

    def test_incomplete_existing_directory_is_not_adopted_or_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "installation"
            directory.mkdir()
            marker = directory / "existing-private-key"
            marker.write_text("leave this alone")

            with self.assertRaisesRegex(SetupFilesError, "not a completed"):
                prepare_setup(directory, **SETTINGS)
            self.assertEqual({path.name for path in directory.iterdir()}, {marker.name})
            self.assertEqual(marker.read_text(), "leave this alone")

    def test_custom_ports_are_shared_by_bundle_export_and_compose(self):
        with tempfile.TemporaryDirectory() as temporary:
            prepared = prepare_setup(
                Path(temporary) / "installation",
                **SETTINGS,
                mqtt_port=18883,
                http_port=18081,
                https_port=18444,
            )
            config = prepared.installation_config
            self.assertEqual(
                (config["mqtt_port"], config["http_port"], config["https_port"]),
                (18883, 18081, 18444),
            )
            self.assertIn(
                "listener 18883\n",
                (prepared.broker_config_directory / "mosquitto.conf").read_text(),
            )
            compose = prepared.compose_path.read_text()
            self.assertIn(":18883:18883", compose)
            self.assertIn('user: "1883:1883"', compose)
            self.assertIn("MQTT_CONFIG_DIR:?", compose)
            self.assertIn("/mosquitto/config:ro", compose)

    def test_private_artifacts_are_rejected_inside_git_or_through_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repository"
            repository.mkdir()
            (repository / ".git").write_text("gitdir: elsewhere\n")
            with self.assertRaisesRegex(SetupFilesError, "outside Git"):
                prepare_setup(repository / "private-installation", **SETTINGS)
            self.assertFalse((repository / "private-installation").exists())

            destination = root / "real-directory"
            destination.mkdir()
            link = root / "link"
            link.symlink_to(destination, target_is_directory=True)
            with self.assertRaisesRegex(SetupFilesError, "symlink"):
                prepare_setup(link, **SETTINGS)
            self.assertEqual(list(destination.iterdir()), [])

    def test_invalid_input_does_not_create_artifacts(self):
        for overrides in (
            {"model": "5520/#"},
            {"mqtt_port": 0},
            {"mqtt_port": 1883},
            {"http_port": 18443},
            {"server_host": "http://bad"},
        ):
            with (
                self.subTest(overrides=overrides),
                tempfile.TemporaryDirectory() as temporary,
            ):
                directory = Path(temporary) / "installation"
                with self.assertRaises(ValueError):
                    prepare_setup(directory, **dict(SETTINGS, **overrides))
                self.assertFalse(directory.exists())


if __name__ == "__main__":
    unittest.main()
