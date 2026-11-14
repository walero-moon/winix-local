"""Admin-only, short-lived certificate downloads through HA's HTTP authentication."""

from datetime import timedelta
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.http.const import KEY_HASS_USER, KEY_HASS_REFRESH_TOKEN_ID
from homeassistant.components import websocket_api
from homeassistant.helpers.http import current_request

from ._core.certificate_setup import export_artifact
from ._core.wire import mac_id


class WinixDownloadView(HomeAssistantView):
    url = "/api/winix_local/{mac}/export/{kind}"
    name = "api:winix_local:export"
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, mac, kind):
        user = request.get(KEY_HASS_USER)
        if user is None or not user.is_admin:
            raise web.HTTPForbidden()

        if kind not in ("broker", "provision"):
            raise web.HTTPNotFound()
        try:
            normalized = mac_id(mac)
            if normalized != mac:
                raise ValueError("Noncanonical identity")
            if not any(
                entry.data.get("mac") == mac
                for entry in self.hass.config_entries.async_entries("winix_local")
            ):
                raise web.HTTPNotFound()
            directory = Path(self.hass.config.path("winix_local", mac))
            body, content_type, filename = await self.hass.async_add_executor_job(
                export_artifact, directory, kind
            )
        except (OSError, ValueError, KeyError):
            raise web.HTTPNotFound() from None

        return web.Response(
            body=body,
            content_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
            },
        )


def register_downloads(hass):
    if not hass.data.setdefault("winix_local", {}).get("downloads_registered"):
        hass.http.register_view(WinixDownloadView(hass))
        hass.data["winix_local"]["downloads_registered"] = True


async def download_links(hass, mac):
    """Bind signed paths to the current administrator login, for five minutes."""
    request = current_request.get()
    connection = websocket_api.current_connection.get()
    if request is not None:
        user = request.get(KEY_HASS_USER)
        refresh_token_id = request.get(KEY_HASS_REFRESH_TOKEN_ID)
    elif connection is not None:
        user = connection.user
        refresh_token_id = connection.refresh_token_id
    else:
        raise ValueError("Administrator login required")

    refresh_token = (
        hass.auth.async_get_refresh_token(refresh_token_id)
        if refresh_token_id
        else None
    )
    if (
        user is None
        or not user.is_admin
        or refresh_token is None
        or refresh_token.user.id != user.id
    ):
        raise ValueError("Administrator login required")

    register_downloads(hass)
    return {
        kind: async_sign_path(
            hass,
            f"/api/winix_local/{mac}/export/{kind}",
            timedelta(minutes=5),
            refresh_token_id=refresh_token_id,
        )
        for kind in ("broker", "provision")
    }
