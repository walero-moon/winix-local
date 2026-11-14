"""Certificate identifiers and existing bundle preservation; no network calls."""

import ipaddress
from pathlib import Path
import tempfile
import unittest

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from winix_local.pki import (
    CERTIFICATE_NOT_AFTER,
    CERTIFICATE_NOT_BEFORE,
    create_bundle,
)


def load_certificate(directory, name):
    return x509.load_pem_x509_certificate((directory / f"{name}.pem").read_bytes())


class CertificateTests(unittest.TestCase):
    def test_generated_chain_has_consistent_subject_and_authority_identifiers(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            create_bundle(
                directory,
                "AA:BB:CC:DD:EE:FF",
                "127.0.0.1",
                "broker.test",
                model="5520_US",
            )
            ca = load_certificate(directory, "ca")
            ca_subject_id = ca.extensions.get_extension_for_class(
                x509.SubjectKeyIdentifier
            )
            ca_authority_id = ca.extensions.get_extension_for_class(
                x509.AuthorityKeyIdentifier
            )

            self.assertEqual(ca.subject, ca.issuer)
            self.assertEqual(
                ca_subject_id.value,
                x509.SubjectKeyIdentifier.from_public_key(ca.public_key()),
            )
            self.assertEqual(
                ca_authority_id.value.key_identifier, ca_subject_id.value.digest
            )
            self.assertFalse(ca_subject_id.critical)
            self.assertFalse(ca_authority_id.critical)

            constraints = ca.extensions.get_extension_for_class(x509.BasicConstraints)
            self.assertTrue(constraints.critical)
            self.assertEqual(
                constraints.value, x509.BasicConstraints(ca=True, path_length=0)
            )
            usage = ca.extensions.get_extension_for_class(x509.KeyUsage)
            self.assertTrue(usage.critical)
            self.assertTrue(usage.value.key_cert_sign)
            self.assertTrue(usage.value.crl_sign)
            self.assertFalse(usage.value.digital_signature)
            self.assertFalse(usage.value.key_encipherment)

            for name, expected_usage in (
                ("auth", ExtendedKeyUsageOID.SERVER_AUTH),
                ("broker", ExtendedKeyUsageOID.SERVER_AUTH),
                ("device", ExtendedKeyUsageOID.CLIENT_AUTH),
            ):
                with self.subTest(certificate=name):
                    certificate = load_certificate(directory, name)
                    subject_id = certificate.extensions.get_extension_for_class(
                        x509.SubjectKeyIdentifier
                    )
                    authority_id = certificate.extensions.get_extension_for_class(
                        x509.AuthorityKeyIdentifier
                    )
                    self.assertEqual(certificate.issuer, ca.subject)
                    self.assertEqual(
                        subject_id.value,
                        x509.SubjectKeyIdentifier.from_public_key(
                            certificate.public_key()
                        ),
                    )
                    self.assertEqual(
                        authority_id.value.key_identifier, ca_subject_id.value.digest
                    )
                    self.assertFalse(subject_id.critical)
                    self.assertFalse(authority_id.critical)
                    self.assertEqual(
                        list(
                            certificate.extensions.get_extension_for_class(
                                x509.ExtendedKeyUsage
                            ).value
                        ),
                        [expected_usage],
                    )
                    self.assertEqual(
                        certificate.not_valid_before_utc, CERTIFICATE_NOT_BEFORE
                    )
                    self.assertEqual(
                        certificate.not_valid_after_utc, CERTIFICATE_NOT_AFTER
                    )

            auth_san = (
                load_certificate(directory, "auth")
                .extensions.get_extension_for_class(x509.SubjectAlternativeName)
                .value
            )
            self.assertEqual(
                auth_san.get_values_for_type(x509.IPAddress),
                [ipaddress.ip_address("127.0.0.1")],
            )
            broker_san = (
                load_certificate(directory, "broker")
                .extensions.get_extension_for_class(x509.SubjectAlternativeName)
                .value
            )
            self.assertEqual(
                broker_san.get_values_for_type(x509.DNSName), ["broker.test"]
            )
            device = load_certificate(directory, "device")
            self.assertEqual(
                device.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
                "AABBCCDDEEFF",
            )
            with self.assertRaises(x509.ExtensionNotFound):
                device.extensions.get_extension_for_class(x509.SubjectAlternativeName)

    def test_creating_a_bundle_again_preserves_existing_keys_and_certificates(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            arguments = (directory, "AABBCCDDEEFF", "127.0.0.1", "broker.test")
            create_bundle(*arguments, model="5520_US")
            before = {path.name: path.read_bytes() for path in directory.iterdir()}

            with self.assertRaisesRegex(ValueError, "must be empty"):
                create_bundle(*arguments, model="5520_US")

            self.assertEqual(
                {path.name: path.read_bytes() for path in directory.iterdir()}, before
            )


if __name__ == "__main__":
    unittest.main()
