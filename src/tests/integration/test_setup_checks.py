"""Verify guided-setup readiness against disposable loopback services only."""

import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import socket
import tempfile
from types import SimpleNamespace
import unittest

from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

from winix_local.backend import RegistrationServer
from winix_local.pki import create_bundle
from winix_local.setup_checks import (
    MAX_CA_BYTES,
    EndpointCheckError,
    verify_endpoints,
)


def unused_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


class ObservedRegistrationServer(RegistrationServer):
    def __init__(self, directory, config, *, ca_body=None, ca_status=200):
        super().__init__(directory, config)
        self.ca_body = ca_body
        self.ca_status = ca_status
        self.requests = []

    async def get_ca(self, request):
        self.requests.append("ca")
        body = self.ca if self.ca_body is None else self.ca_body
        return web.Response(body=body, status=self.ca_status)

    async def get_token(self, request):
        self.requests.append("token")
        return await super().get_token(request)

    async def get_auth(self, request):
        self.requests.append("auth")
        return await super().get_auth(request)


def make_legacy_certificates(directory):
    """Model existing bundles that must remain usable without CA rotation."""
    ca_key = serialization.load_pem_private_key(
        (directory / "ca.key").read_bytes(), password=None
    )
    for name in ("ca.pem", "auth.pem"):
        certificate = x509.load_pem_x509_certificate((directory / name).read_bytes())
        builder = (
            x509.CertificateBuilder()
            .subject_name(certificate.subject)
            .issuer_name(certificate.issuer)
            .public_key(certificate.public_key())
            .serial_number(certificate.serial_number)
            .not_valid_before(certificate.not_valid_before_utc)
            .not_valid_after(certificate.not_valid_after_utc)
        )
        for extension in certificate.extensions:
            if isinstance(
                extension.value,
                (x509.SubjectKeyIdentifier, x509.AuthorityKeyIdentifier),
            ):
                continue
            builder = builder.add_extension(
                extension.value, critical=extension.critical
            )
        legacy = builder.sign(ca_key, hashes.SHA256())
        (directory / name).write_bytes(legacy.public_bytes(serialization.Encoding.PEM))


@asynccontextmanager
async def local_services(
    *, certificate_host="127.0.0.1", ca_body=None, ca_status=200, legacy=False
):
    broker_connected = asyncio.Event()

    async def accept_broker(reader, writer):
        broker_connected.set()
        writer.close()
        await writer.wait_closed()

    broker = await asyncio.start_server(accept_broker, "127.0.0.1", 0)
    broker_port = broker.sockets[0].getsockname()[1]

    try:
        with tempfile.TemporaryDirectory(prefix="winix-readiness-test-") as temporary:
            directory = Path(temporary)
            http_port = unused_port()
            https_port = unused_port()
            while https_port == http_port:
                https_port = unused_port()

            config = create_bundle(
                directory,
                "AABBCCDDEEFF",
                certificate_host,
                "127.0.0.1",
                model="5520_US",
                http_port=http_port,
                https_port=https_port,
                mqtt_port=broker_port,
            )
            public = json.loads((directory / "provision.json").read_text())
            if legacy:
                make_legacy_certificates(directory)
                ca = x509.load_pem_x509_certificate((directory / "ca.pem").read_bytes())
                public["ca_sha256"] = ca.fingerprint(hashes.SHA256()).hex()
            public["server_host"] = "127.0.0.1"
            registration = ObservedRegistrationServer(
                directory, config, ca_body=ca_body, ca_status=ca_status
            )
            registration.prepare()
            await registration.start("127.0.0.1")

            try:
                yield SimpleNamespace(
                    config=public,
                    registration=registration,
                    broker_connected=broker_connected,
                )
            finally:
                await registration.stop()
    finally:
        broker.close()
        await broker.wait_closed()


class EndpointReadinessTests(unittest.IsolatedAsyncioTestCase):
    async def verify(self, config):
        # verify_endpoints is synchronous; keep the fixture server's event loop
        # free to answer HTTP and TLS while verification runs in a worker.
        await asyncio.to_thread(verify_endpoints, config)

    async def test_pinned_ca_and_registration_tls_then_broker_tcp(self):
        async with local_services() as services:
            # Fingerprints are certificate DER hashes, not PEM-file hashes.
            # Accept the hex case chosen by the caller, retaining the same pin.
            services.config["ca_sha256"] = services.config["ca_sha256"].upper()
            await self.verify(services.config)
            await asyncio.wait_for(services.broker_connected.wait(), timeout=1)

            self.assertEqual(services.registration.requests, ["ca"])
            self.assertTrue(services.broker_connected.is_set())

    async def test_legacy_bundle_still_requires_its_pinned_ca_and_correct_hostname(
        self,
    ):
        async with local_services(legacy=True) as services:
            await self.verify(services.config)
            await asyncio.wait_for(services.broker_connected.wait(), timeout=1)
            self.assertEqual(services.registration.requests, ["ca"])

        async with local_services(legacy=True) as services:
            services.config["ca_sha256"] = "0" * 64
            with self.assertRaisesRegex(EndpointCheckError, "does not match"):
                await self.verify(services.config)
            self.assertFalse(services.broker_connected.is_set())

        async with local_services(
            legacy=True, certificate_host="different-host.test"
        ) as services:
            with self.assertRaisesRegex(EndpointCheckError, "TLS verification failed"):
                await self.verify(services.config)
            self.assertFalse(services.broker_connected.is_set())

    async def test_wrong_ca_fingerprint_stops_before_mqtt_probe(self):
        async with local_services() as services:
            services.config["ca_sha256"] = "0" * 64
            with self.assertRaisesRegex(EndpointCheckError, "does not match"):
                await self.verify(services.config)

            self.assertEqual(services.registration.requests, ["ca"])
            self.assertFalse(services.broker_connected.is_set())

    async def test_missing_pin_stops_before_contacting_services(self):
        async with local_services() as services:
            del services.config["ca_sha256"]
            with self.assertRaisesRegex(EndpointCheckError, "fingerprint"):
                await self.verify(services.config)

            self.assertEqual(services.registration.requests, [])
            self.assertFalse(services.broker_connected.is_set())

    async def test_unreachable_http_tls_and_mqtt_report_the_failed_endpoint(self):
        for field, message in (
            ("http_port", "HTTP listener is unreachable"),
            ("https_port", "TLS verification failed"),
            ("mqtt_port", "MQTT listener is unreachable"),
        ):
            with self.subTest(field=field):
                async with local_services() as services:
                    services.config[field] = unused_port()
                    with self.assertRaisesRegex(EndpointCheckError, message):
                        await self.verify(services.config)

                    expected_requests = [] if field == "http_port" else ["ca"]
                    self.assertEqual(services.registration.requests, expected_requests)
                    self.assertFalse(services.broker_connected.is_set())

    async def test_registration_certificate_must_match_the_selected_host(self):
        async with local_services(certificate_host="different-host.test") as services:
            with self.assertRaisesRegex(EndpointCheckError, "TLS verification failed"):
                await self.verify(services.config)

            self.assertEqual(services.registration.requests, ["ca"])
            self.assertFalse(services.broker_connected.is_set())

    async def test_ca_download_is_bounded_and_requires_successful_http(self):
        for options, message in (
            ({"ca_body": b"X" * (MAX_CA_BYTES + 1)}, "too large"),
            ({"ca_status": 404}, "did not serve"),
            ({"ca_body": b"not a certificate"}, "TLS verification failed"),
        ):
            with self.subTest(options=tuple(options)):
                async with local_services(**options) as services:
                    with self.assertRaisesRegex(EndpointCheckError, message):
                        await self.verify(services.config)

                    self.assertEqual(services.registration.requests, ["ca"])
                    self.assertFalse(services.broker_connected.is_set())


if __name__ == "__main__":
    unittest.main()
