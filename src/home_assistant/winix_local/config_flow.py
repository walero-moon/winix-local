"""Guided local enrollment; private key material never enters HA config entries."""

from ipaddress import ip_address
from pathlib import Path
import re
from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.network import NoURLAvailableError, get_url

from ._core.certificate_setup import (
    create_installation,
    validate_material,
    validate_settings,
)
from ._core.installation import load_bundle
from ._core.model import load_builtin_catalog
from ._core.wire import mac_id
from .downloads import download_links


class WinixLocalFlow(config_entries.ConfigFlow, domain="winix_local"):
    VERSION = 1

    def __init__(self):
        self._settings = {}
        self._material = {}
        self._mode = None
        self._retain_ca_key = False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return WinixOptionsFlow()

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(
            step_id="user", menu_options=["connection", "existing"]
        )

    def _suggest_registration_host(self):
        """Suggest HA's internal host without assuming the purifier can reach it."""
        try:
            internal_url = get_url(
                self.hass, allow_external=False, allow_cloud=False, allow_ip=True
            )
            host = urlsplit(internal_url).hostname
            if (
                host is None
                or host.lower() == "localhost"
                or len(host) > 253
                or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", host)
            ):
                return None
        except (NoURLAvailableError, ValueError):
            return None

        try:
            address = ip_address(host)
        except ValueError:
            return host

        if (
            address.version != 4
            or address.is_loopback
            or address.is_unspecified
            or address.is_multicast
        ):
            return None
        return host

    async def async_step_connection(self, user_input=None):
        errors = {}
        if user_input:
            try:
                settings = validate_settings(user_input)
                await self.async_set_unique_id(settings["mac"])
                self._abort_if_unique_id_configured()
            except (ValueError, KeyError, TypeError):
                errors["base"] = "invalid_settings"
            else:
                self._settings = settings
                return await self.async_step_certificate_source()

        suggested_values = dict(user_input or {})
        if "server_host" not in suggested_values:
            host = self._suggest_registration_host()
            if host is not None:
                suggested_values["server_host"] = host

        return self.async_show_form(
            step_id="connection",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required("mac"): str,
                        vol.Required("model"): str,
                        vol.Required("server_host"): str,
                        vol.Required("http_port", default=18080): vol.All(
                            vol.Coerce(int), vol.Range(min=1024, max=65535)
                        ),
                        vol.Required("https_port", default=18443): vol.All(
                            vol.Coerce(int), vol.Range(min=1024, max=65535)
                        ),
                        vol.Required("mqtt_host"): str,
                        vol.Required("mqtt_port", default=8883): vol.All(
                            vol.Coerce(int), vol.Range(min=1024, max=65535)
                        ),
                    }
                ),
                suggested_values,
            ),
            errors=errors,
        )

    async def async_step_certificate_source(self, user_input=None):
        if not self._settings:
            return await self.async_step_connection()

        return self.async_show_menu(
            step_id="certificate_source",
            menu_options=["generate", "issuer", "certificates"],
        )

    async def async_step_generate(self, user_input=None):
        return await self._certificate_step("generate", user_input)

    async def async_step_issuer(self, user_input=None):
        return await self._certificate_step("issuer", user_input)

    async def async_step_certificates(self, user_input=None):
        return await self._certificate_step("certificates", user_input)

    async def _certificate_step(self, mode, user_input):
        if not self._settings:
            return await self.async_step_connection()

        errors = {}
        if user_input is not None:
            material = {
                field.replace("_", "."): value
                for field, value in user_input.items()
                if field != "retain_ca_key" and value
            }
            retain = user_input.get("retain_ca_key", False)
            try:
                if mode != "generate":
                    await self.hass.async_add_executor_job(
                        validate_material, self._settings, material, mode
                    )
                if retain and mode == "certificates" and not material.get("ca.key"):
                    raise ValueError("Signing key was not supplied")
            except (ValueError, TypeError):
                errors["base"] = "invalid_certificates"
            else:
                self._material = material
                self._mode = mode
                self._retain_ca_key = retain
                return await self.async_step_confirm()

        fields = {}
        if mode != "generate":
            fields[vol.Required("ca_pem")] = selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            )
            marker = vol.Required if mode == "issuer" else vol.Optional
            fields[marker("ca_key")] = selector.TextSelector(
                selector.TextSelectorConfig(multiline=True)
            )
        if mode == "certificates":
            for name in ("auth", "broker", "device"):
                for suffix in ("pem", "key"):
                    fields[vol.Required(name + "_" + suffix)] = selector.TextSelector(
                        selector.TextSelectorConfig(multiline=True)
                    )
        fields[vol.Required("retain_ca_key", default=False)] = bool

        # Never prefill or return private PEM values after a validation error.
        return self.async_show_form(
            step_id=mode, data_schema=vol.Schema(fields), errors=errors
        )

    async def async_step_confirm(self, user_input=None):
        if not self._settings or self._mode is None:
            return await self.async_step_connection()
        errors = {}
        if user_input is not None:
            directory = Path(
                self.hass.config.path("winix_local", self._settings["mac"])
            )
            try:
                self._abort_if_unique_id_configured()
                config = await self.hass.async_add_executor_job(
                    create_installation,
                    directory,
                    self._settings,
                    self._mode,
                    self._material,
                    self._retain_ca_key,
                )
            except FileExistsError:
                errors["base"] = "bundle_exists"
            except (OSError, ValueError, TypeError):
                errors["base"] = "creation_failed"
            else:
                self._material.clear()
                return await self._create_entry(config)

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "model": self._settings["model"],
                "mac": self._settings["mac"],
                "mqtt_host": self._settings["mqtt_host"],
                "mqtt_port": str(self._settings["mqtt_port"]),
                "retained": "yes" if self._retain_ca_key else "no",
            },
        )

    async def async_step_existing(self, user_input=None):
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
            except (OSError, ValueError, KeyError):
                errors["base"] = "invalid_bundle"
            else:
                return await self._create_entry(config)

        return self.async_show_form(
            step_id="existing",
            data_schema=vol.Schema({vol.Required("mac"): str}),
            errors=errors,
        )

    async def _create_entry(self, config):
        catalog = await self.hass.async_add_executor_job(load_builtin_catalog)
        model_info = catalog.get(config["model"])
        model_name = model_info.name if model_info is not None else config["model"]
        return self.async_create_entry(
            title=f"Winix {model_name} {config['mac'][-6:]}",
            data={"mac": config["mac"]},
        )


class WinixOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data={})
        try:
            links = await download_links(self.hass, self.config_entry.data["mac"])
        except ValueError:
            return self.async_abort(reason="administrator_required")
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema({}), description_placeholders=links
        )
