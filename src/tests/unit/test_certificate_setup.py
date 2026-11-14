"""Certificate enrollment contracts exercised without HA or a broker."""

import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from winix_local.certificate_setup import (
    CERTIFICATE_FILES,
    create_installation,
    export_artifact,
    validate_material,
    validate_settings,
)
from winix_local.pki import create_bundle

SETTINGS = dict(
    mac="AABBCCDDEEFF",
    model="5520_US",
    server_host="registration.test",
    mqtt_host="broker.test",
    mqtt_port=8883,
    http_port=18080,
    https_port=18443,
)


class CertificateSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = tempfile.TemporaryDirectory()
        cls.source_path = Path(cls.source.name)
        create_bundle(cls.source_path, **SETTINGS)
        cls.material = {
            name: (cls.source_path / name).read_text()
            for name in (*CERTIFICATE_FILES, "ca.key")
        }

    @classmethod
    def tearDownClass(cls):
        cls.source.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.destination = Path(self.temporary.name) / "installation"

    def test_generated_ca_key_is_optional_and_public_export_has_no_secrets(self):
        config = create_installation(self.destination, SETTINGS, "generate", {}, False)
        self.assertFalse((self.destination / "ca.key").exists())
        body, content_type, filename = export_artifact(self.destination, "provision")
        self.assertEqual(
            (content_type, filename), ("application/json", "provision.json")
        )
        public = json.loads(body)
        self.assertEqual(public["model"], "5520_US")
        self.assertEqual(len(public["ca_sha256"]), 64)
        self.assertNotIn("token", public)
        self.assertNotIn(config["token"].encode(), body)
        self.assertNotIn(b"PRIVATE KEY", body)

    def test_generated_key_retention_is_explicit(self):
        create_installation(self.destination, SETTINGS, "generate", {}, True)
        self.assertTrue((self.destination / "ca.key").is_file())

    def test_imported_issuer_is_reused_and_not_saved_without_consent(self):
        for retain in (False, True):
            with self.subTest(retain=retain):
                destination = self.destination / str(retain)
                create_installation(
                    destination, SETTINGS, "issuer", self.material, retain
                )
                self.assertEqual(
                    (destination / "ca.pem").read_text(), self.material["ca.pem"]
                )
                self.assertEqual((destination / "ca.key").exists(), retain)
                ca = x509.load_pem_x509_certificate(
                    (destination / "ca.pem").read_bytes()
                )
                for name in ("auth", "broker", "device"):
                    leaf = x509.load_pem_x509_certificate(
                        (destination / (name + ".pem")).read_bytes()
                    )
                    leaf.verify_directly_issued_by(ca)
                    self.assertLessEqual(
                        leaf.not_valid_after_utc, ca.not_valid_after_utc
                    )

    def test_preissued_certificates_preserve_bytes_without_a_signing_key(self):
        material = {
            name: value for name, value in self.material.items() if name != "ca.key"
        }
        create_installation(self.destination, SETTINGS, "certificates", material, False)
        self.assertFalse((self.destination / "ca.key").exists())
        for name in CERTIFICATE_FILES:
            self.assertEqual((self.destination / name).read_text(), material[name])

    def test_preissued_retention_requires_supplied_matching_signer(self):
        material = dict(self.material)
        del material["ca.key"]
        with self.assertRaises(ValueError):
            create_installation(
                self.destination, SETTINGS, "certificates", material, True
            )
        self.assertFalse(self.destination.exists())
        material["ca.key"] = material["device.key"]
        with self.assertRaises(ValueError):
            create_installation(
                self.destination, SETTINGS, "certificates", material, True
            )
        self.assertFalse(self.destination.exists())
        create_installation(
            self.destination, SETTINGS, "certificates", self.material, True
        )
        self.assertEqual(
            (self.destination / "ca.key").read_text(), self.material["ca.key"]
        )

    def test_wrong_keys_hosts_identity_and_purposes_are_rejected_without_writes(self):
        cases = []
        wrong_key = dict(self.material, **{"auth.key": self.material["device.key"]})
        cases.append((SETTINGS, wrong_key))
        cases.append((dict(SETTINGS, mac="112233445566"), self.material))
        cases.append((dict(SETTINGS, mqtt_host="wrong.test"), self.material))
        cases.append((dict(SETTINGS, server_host="wrong.test"), self.material))
        swapped = dict(
            self.material,
            **{
                "device.pem": self.material["broker.pem"],
                "device.key": self.material["broker.key"],
            },
        )
        cases.append((SETTINGS, swapped))
        for settings, material in cases:
            with self.subTest(settings=settings):
                with self.assertRaisesRegex(
                    ValueError, "Certificate material"
                ) as error:
                    create_installation(
                        self.destination, settings, "certificates", material, False
                    )
                self.assertNotIn("BEGIN", str(error.exception))
                self.assertFalse(self.destination.exists())

    def test_bad_issuer_signatures_expiry_and_ca_constraints_are_rejected(self):
        original = x509.load_pem_x509_certificate(self.material["device.pem"].encode())
        ca = x509.load_pem_x509_certificate(self.material["ca.pem"].encode())
        ca_key = serialization.load_pem_private_key(
            self.material["ca.key"].encode(), None
        )
        device_key = serialization.load_pem_private_key(
            self.material["device.key"].encode(), None
        )
        from datetime import datetime, timezone

        for purpose, expired, is_ca, signer in (
            (ExtendedKeyUsageOID.CLIENT_AUTH, False, False, device_key),
            (ExtendedKeyUsageOID.CLIENT_AUTH, True, False, ca_key),
            (ExtendedKeyUsageOID.CLIENT_AUTH, False, True, ca_key),
            (ExtendedKeyUsageOID.SERVER_AUTH, False, False, ca_key),
        ):
            builder = (
                x509.CertificateBuilder()
                .subject_name(original.subject)
                .issuer_name(ca.subject)
                .public_key(device_key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime(2020, 1, 1, tzinfo=timezone.utc))
                .not_valid_after(
                    datetime(2021 if expired else 2035, 1, 1, tzinfo=timezone.utc)
                )
                .add_extension(x509.BasicConstraints(ca=is_ca, path_length=None), True)
                .add_extension(x509.ExtendedKeyUsage([purpose]), False)
            )
            material = dict(self.material)
            material["device.pem"] = (
                builder.sign(signer, hashes.SHA256())
                .public_bytes(serialization.Encoding.PEM)
                .decode()
            )
            with self.assertRaisesRegex(ValueError, "Certificate material"):
                validate_material(SETTINGS, material, "certificates")

    def test_malformed_material_never_leaks_parser_contents(self):
        for bad in (
            "private-fixture-secret",
            "☃",
            "X" * 32769,
            self.material["ca.pem"] * 2,
        ):
            material = dict(self.material, **{"ca.pem": bad})
            with self.assertRaises(ValueError) as error:
                validate_material(SETTINGS, material, "issuer")
            self.assertNotIn(bad, str(error.exception))

    def test_extra_pem_blocks_cannot_smuggle_keys_into_exports(self):
        for field in ("ca.pem", "broker.pem", "broker.key"):
            material = dict(self.material)
            material[field] += self.material["ca.key"]
            with self.subTest(field=field), self.assertRaises(ValueError):
                create_installation(
                    self.destination, SETTINGS, "certificates", material, False
                )
            self.assertFalse(self.destination.exists())

    def test_existing_bundle_is_never_overwritten(self):
        self.destination.mkdir()
        sentinel = self.destination / "keep"
        sentinel.write_text("existing data")
        with self.assertRaises(FileExistsError):
            create_installation(self.destination, SETTINGS, "generate", {}, False)
        self.assertEqual(sentinel.read_text(), "existing data")

    def test_broker_export_is_neutral_and_does_not_include_ca_or_device_keys(self):
        config = create_installation(
            self.destination, SETTINGS, "certificates", self.material, True
        )
        body, content_type, _ = export_artifact(self.destination, "broker")
        self.assertEqual(content_type, "application/zip")
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertEqual(
                set(archive.namelist()),
                {
                    "ca.pem",
                    "broker.pem",
                    "broker.key",
                    "requirements.json",
                    "README.txt",
                },
            )
            requirements = json.loads(archive.read("requirements.json"))
            self.assertTrue(requirements["require_client_certificate"])
            self.assertEqual(
                requirements["client_certificate_common_name"], SETTINGS["mac"]
            )
            self.assertEqual(
                requirements["device"]["subscribe"],
                [f"Winix/Control/Air01/5520_US/{config['mac']}_{config['token']}"],
            )
            self.assertEqual(
                requirements["home_assistant"]["publish"],
                requirements["device"]["subscribe"],
            )
            self.assertNotIn(self.material["ca.key"].encode(), body)
            self.assertNotIn(self.material["device.key"].encode(), body)
        with self.assertRaises(ValueError):
            export_artifact(self.destination, "../ca.key")

    def test_invalid_settings_are_rejected(self):
        for field, value in (
            ("mac", "../bad"),
            ("model", "../bad"),
            ("server_host", "https://ha"),
            ("mqtt_port", True),
            ("http_port", 80),
            ("https_port", 18080),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_settings(dict(SETTINGS, **{field: value}))
