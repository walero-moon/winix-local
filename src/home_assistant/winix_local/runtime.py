"""Local Winix registration, MQTT transport, and Home Assistant entities."""

from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from ._core.backend import RegistrationServer
from .coordinator import WinixCoordinator
from ._core.installation import load_bundle
from ._core.offline_clock import OfflineClock
from ._core.model import load_builtin_catalog

PLATFORMS = [Platform.FAN, Platform.SENSOR, Platform.SWITCH]


async def async_setup_entry(hass, entry):
    directory = hass.config.path("winix_local", entry.data["mac"])
    coordinator = None

    try:
        config = await hass.async_add_executor_job(load_bundle, directory)
        catalog = await hass.async_add_executor_job(load_builtin_catalog)

        coordinator = WinixCoordinator(hass, entry, config, catalog=catalog)

        server = await hass.async_add_executor_job(
            RegistrationServer, directory, config
        )
        coordinator.server = server
        await hass.async_add_executor_job(server.prepare)

        if config.get("offline_device_ip"):
            coordinator.clock = OfflineClock(
                config["server_host"], config["offline_device_ip"]
            )
            await coordinator.clock.start()

        await server.start()
        await coordinator.start()
        coordinator.async_set_updated_data({})

        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except (OSError, ValueError, KeyError, HomeAssistantError) as error:
        if coordinator:
            await coordinator.stop()

        raise ConfigEntryNotReady(
            "Local setup failed; check the catalog, MQTT and listener ports"
        ) from error
    except BaseException:
        # Always release listeners, including on cancellation; coding errors keep
        # their original traceback instead of becoming a retryable setup error.
        if coordinator:
            await coordinator.stop()

        raise

    return True


async def async_unload_entry(hass, entry):
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
        return True
    return False
