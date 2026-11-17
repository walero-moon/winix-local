"""Local registration HTTP/TLS listeners. No cloud calls or firmware routes."""

import hmac
import ssl
from pathlib import Path
from typing import Optional, Union

from aiohttp import web

from .installation import InstallationConfig


class RegistrationServer:
    def __init__(self, directory: Union[str, Path], config: InstallationConfig):
        self.directory = Path(directory)
        self.config = config
        self.runner: Optional[web.AppRunner] = None

    def prepare(self):
        certificate_directory = self.directory
        self.ca = (certificate_directory / "ca.pem").read_bytes()
        self.device_cert = (certificate_directory / "device.pem").read_text()
        self.device_key = (certificate_directory / "device.key").read_text()

        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2

        # Stock Winix TLS clients offer static RSA suites that newer Python
        # defaults omit. Add their SHA-256 suites without enabling SHA-1 suites
        # or lowering OpenSSL's security level. Keep the modern defaults too.
        default_ciphers = [
            cipher["name"]
            for cipher in self.context.get_ciphers()
            if cipher["protocol"] != "TLSv1.3"
        ]
        self.context.set_ciphers(
            ":".join([*default_ciphers, "AES256-SHA256", "AES128-SHA256"])
        )

        self.context.load_cert_chain(
            certificate_directory / "auth.pem", certificate_directory / "auth.key"
        )

    async def start(self, bind="0.0.0.0"):
        app = web.Application(client_max_size=1024)
        app.router.add_get("/ca.pem", self.get_ca)
        app.router.add_get("/auth/register/token/{mac}", self.get_token)
        app.router.add_get("/auth/register/auth/{mac}/{token}", self.get_auth)

        self.runner = web.AppRunner(app, access_log=None)

        try:
            await self.runner.setup()
            await web.TCPSite(self.runner, bind, self.config["http_port"]).start()
            await web.TCPSite(
                self.runner, bind, self.config["https_port"], ssl_context=self.context
            ).start()
        except BaseException:
            await self.stop()
            raise

    async def stop(self):
        if self.runner:
            await self.runner.cleanup()
            self.runner = None

    async def get_ca(self, request):
        return web.Response(body=self.ca, content_type="application/x-pem-file")

    def matches(self, request):
        # CA is public; registration credentials are HTTPS only, exact MAC/token.
        return request.secure and request.match_info["mac"] == self.config["mac"]

    async def get_token(self, request):
        if not self.matches(request):
            raise web.HTTPNotFound()

        return web.json_response(
            {
                "headers": {
                    "resultCode": "S100",
                    "resultMessage": "token is generated",
                },
                "body": {"token": self.config["token"]},
                "statusCode": 200,
            }
        )

    async def get_auth(self, request):
        if not self.matches(request) or not hmac.compare_digest(
            request.match_info["token"], self.config["token"]
        ):
            raise web.HTTPNotFound()

        return web.json_response(
            {
                "headers": {
                    "resultCode": "S100",
                    "resultMessage": "device Info generated",
                },
                "body": {"privateKey": self.device_key, "deviceCert": self.device_cert},
                "statusCode": 200,
            }
        )
