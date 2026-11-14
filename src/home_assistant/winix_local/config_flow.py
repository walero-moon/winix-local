"""Installation-bundle import; no Winix account credentials required."""

import voluptuous as vol
from homeassistant import config_entries

from ._core.installation import load_bundle
from ._core.model import load_builtin_catalog
from ._core.wire import mac_id


class WinixLocalFlow(config_entries.ConfigFlow, domain="winix_local"):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input:
            try:
                mac = mac_id(user_input["mac"])

                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()

                config = await self.hass.async_add_executor_job(
                    load_bundle, self.hass.config.path("winix_local", mac)
                )

                if config["mac"] != mac:
                    raise ValueError("Bundle identity mismatch")

                catalog = await self.hass.async_add_executor_job(load_builtin_catalog)
                model_info = catalog.get(config["model"])
                model_name = (
                    model_info.name if model_info is not None else config["model"]
                )
            except (OSError, ValueError, KeyError):
                errors["base"] = "invalid_bundle"
            else:
                return self.async_create_entry(
                    title=f"Winix {model_name} {mac[-6:]}", data={"mac": mac}
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("mac"): str}),
            errors=errors,
        )
