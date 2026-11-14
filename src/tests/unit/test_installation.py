"""Model-aware installation records and broker ACLs; no external connections."""

import json
import os
from pathlib import Path
import tempfile
import unittest

from winix_local.installation import load_bundle
from winix_local.pki import create_bundle
from winix_local.prepare_broker import device_acl, export_broker_files


class InstallationTests(unittest.TestCase):
    def test_private_public_and_broker_exports_preserve_device_model(self):
        for model in ("5510_US", "5520_US", "FutureModel_01"):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle_directory = root / "bundle"
                config = create_bundle(
                    bundle_directory,
                    "AA:BB:CC:DD:EE:FF",
                    "127.0.0.1",
                    "broker.test",
                    model=model,
                )
                self.assertEqual(config["model"], model)
                self.assertEqual(load_bundle(bundle_directory), config)

                public = json.loads((bundle_directory / "provision.json").read_text())
                self.assertEqual(public["model"], model)
                self.assertNotIn("token", public)
                if os.name != "nt":
                    self.assertEqual(bundle_directory.stat().st_mode & 0o777, 0o700)
                    for path in bundle_directory.iterdir():
                        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

                export_directory = root / "broker"
                export_broker_files(bundle_directory, export_directory)
                identity = f"AABBCCDDEEFF_{config['token']}"
                self.assertEqual(
                    (export_directory / "device.acl").read_text(),
                    "user AABBCCDDEEFF\n"
                    f"topic read Winix/Control/Air01/{model}/{identity}\n"
                    f"topic write Winix/Conn/Air01/{model}/{identity}\n"
                    f"topic write Winix/Event/Air01/{model}/{identity}\n",
                )
                self.assertFalse((export_directory / "ca.key").exists())
                if os.name != "nt":
                    for path in export_directory.iterdir():
                        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_invalid_model_fails_before_creating_bundle_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "bundle"
            with self.assertRaises(ValueError):
                create_bundle(
                    directory,
                    "AABBCCDDEEFF",
                    "127.0.0.1",
                    "broker.test",
                    model="5520/US",
                )
            self.assertFalse(directory.exists())

    def test_existing_bundle_requires_model_without_overwriting_extra_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            config = {
                "mac": "AABBCCDDEEFF",
                "token": "TESTTOKEN1",
                "model": "5520_US",
                "future_field": {"preserved": True},
            }
            bundle_path = directory / "bundle.json"
            bundle_path.write_text(json.dumps(config))
            original = bundle_path.read_bytes()

            self.assertEqual(load_bundle(directory), config)
            self.assertEqual(bundle_path.read_bytes(), original)

            del config["model"]
            bundle_path.write_text(json.dumps(config))
            with self.assertRaises(KeyError):
                load_bundle(directory)

    def test_invalid_bundle_cannot_create_broker_export(self):
        for field, invalid_value in (("model", "5520/#"), ("token", "bad")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle = root / "bundle"
                bundle.mkdir()
                config = {
                    "mac": "AABBCCDDEEFF",
                    "token": "TESTTOKEN1",
                    "model": "5520_US",
                }
                config[field] = invalid_value
                (bundle / "bundle.json").write_text(json.dumps(config))

                with self.assertRaises(ValueError):
                    export_broker_files(bundle, root / "broker")
                self.assertFalse((root / "broker").exists())

    def test_acl_normalizes_mac_without_widening_device_access(self):
        config = {
            "mac": "aa:bb:cc:dd:ee:ff",
            "token": "TESTTOKEN1",
            "model": "5520_US",
        }
        acl = device_acl(config)
        self.assertTrue(acl.startswith("user AABBCCDDEEFF\n"))
        self.assertNotIn("+", acl)
        self.assertNotIn("#", acl)
        self.assertEqual(len(acl.splitlines()), 4)
        self.assertNotIn("5510_US", acl)


if __name__ == "__main__":
    unittest.main()
