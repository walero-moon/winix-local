"""Exercise registration through real loopback HTTPS; no purifier or cloud."""

from pathlib import Path
import json
import socket
import ssl
import tempfile
import unittest

from aiohttp import ClientSession, ClientConnectorCertificateError

from winix_local import backend, pki


class BackendTests(unittest.IsolatedAsyncioTestCase):
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
                http_port=ports[0],
                https_port=ports[1],
            )
            self.assertEqual(len(config["token"]), 10)
            self.assertNotIn(
                "token", json.loads((Path(root) / "provision.json").read_text())
            )
            with self.assertRaises(ValueError):
                pki.create_bundle(root, "AABBCCDDEEFF", "a", "b")
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
