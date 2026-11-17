"""Exercise registration through real loopback HTTPS; no purifier or cloud."""

from pathlib import Path
import asyncio
import json
import socket
import ssl
import tempfile
import unittest

from aiohttp import ClientSession, ClientConnectorCertificateError

from winix_local import backend, pki


class BackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_accepts_purifier_rsa_sha256_cipher_suites(self):
        # The stock purifier offers 0x003d, 0x0035, 0x003c and 0x002f.
        # Python's current server defaults exclude all four. Only the SHA-256
        # suites need enabling, so the SHA-1 suites remain unnecessary.
        with tempfile.TemporaryDirectory() as root:
            ports = []
            for _ in range(2):
                with socket.socket() as listener:
                    listener.bind(("127.0.0.1", 0))
                    ports.append(listener.getsockname()[1])

            config = pki.create_bundle(
                root,
                "AABBCCDDEEFF",
                "127.0.0.1",
                "127.0.0.1",
                model="5520_US",
                http_port=ports[0],
                https_port=ports[1],
            )
            server = backend.RegistrationServer(root, config)
            server.prepare()
            defaults = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            self.assertEqual(server.context.minimum_version, ssl.TLSVersion.TLSv1_2)
            if hasattr(defaults, "security_level"):
                self.assertEqual(server.context.security_level, defaults.security_level)

            await server.start("127.0.0.1")
            try:
                for cipher in ("AES256-SHA256", "AES128-SHA256"):
                    with self.subTest(cipher=cipher):
                        client = ssl.create_default_context(
                            cafile=str(Path(root) / "ca.pem")
                        )
                        client.minimum_version = ssl.TLSVersion.TLSv1_2
                        client.maximum_version = ssl.TLSVersion.TLSv1_2
                        client.set_ciphers(cipher)

                        reader, writer = await asyncio.wait_for(
                            asyncio.open_connection("127.0.0.1", ports[1], ssl=client),
                            timeout=3,
                        )
                        try:
                            negotiated = writer.get_extra_info("ssl_object")
                            self.assertEqual(negotiated.version(), "TLSv1.2")
                            self.assertEqual(negotiated.cipher()[0], cipher)
                            writer.write(
                                b"GET /auth/register/token/AABBCCDDEEFF HTTP/1.1\r\n"
                                b"Host: 127.0.0.1\r\nConnection: close\r\n\r\n"
                            )
                            await writer.drain()
                            response = await asyncio.wait_for(reader.read(), timeout=3)
                            headers, body = response.split(b"\r\n\r\n", 1)
                            self.assertTrue(headers.startswith(b"HTTP/1.1 200"))
                            self.assertEqual(
                                json.loads(body)["body"]["token"], config["token"]
                            )
                        finally:
                            writer.close()
                            await writer.wait_closed()
            finally:
                await server.stop()

    async def test_tls_bootstrap_exact_routes_and_restart(self):
        with tempfile.TemporaryDirectory() as root:
            ports = []
            for _ in range(2):
                s = socket.socket()
                s.bind(("127.0.0.1", 0))
                ports.append(s.getsockname()[1])
                s.close()
            config = pki.create_bundle(
                root,
                "AABBCCDDEEFF",
                "127.0.0.1",
                "127.0.0.1",
                model="5520_US",
                http_port=ports[0],
                https_port=ports[1],
            )
            self.assertEqual(config["model"], "5520_US")
            self.assertEqual(len(config["token"]), 10)
            self.assertNotIn(
                "token", json.loads((Path(root) / "provision.json").read_text())
            )
            with self.assertRaises(ValueError):
                pki.create_bundle(root, "AABBCCDDEEFF", "a", "b", model="5520_US")
            server = backend.RegistrationServer(root, config)
            server.prepare()
            context = ssl.create_default_context(cafile=str(Path(root) / "ca.pem"))
            for _ in range(2):
                await server.start("127.0.0.1")
                try:
                    async with ClientSession() as session:
                        url = f"http://127.0.0.1:{ports[0]}"
                        async with session.get(url + "/ca.pem") as r:
                            self.assertIn("BEGIN CERTIFICATE", await r.text())
                        async with session.get(
                            url + "/auth/register/token/AABBCCDDEEFF"
                        ) as r:
                            self.assertEqual(r.status, 404)
                        url = f"https://127.0.0.1:{ports[1]}"
                        async with session.get(
                            url + "/auth/register/token/AABBCCDDEEFF", ssl=context
                        ) as r:
                            self.assertEqual(
                                (await r.json())["body"]["token"], config["token"]
                            )
                        async with session.get(
                            url + "/auth/register/auth/AABBCCDDEEFF/" + config["token"],
                            ssl=context,
                        ) as r:
                            self.assertIn("deviceCert", (await r.json())["body"])
                        async with session.get(
                            url + "/auth/register/auth/AABBCCDDEEFF/wrong", ssl=context
                        ) as r:
                            self.assertEqual(r.status, 404)
                        async with session.get(url + "/firmware", ssl=context) as r:
                            self.assertEqual(r.status, 404)
                        with self.assertRaises(ClientConnectorCertificateError):
                            await session.get(url + "/ca.pem")
                finally:
                    await server.stop()


if __name__ == "__main__":
    unittest.main()
