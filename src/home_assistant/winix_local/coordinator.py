"""Push telemetry through HA's configured MQTT client; never cloud poll."""

from __future__ import annotations

import json
import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING, Callable, Optional

from homeassistant.components import mqtt
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from ._core.installation import InstallationConfig
from ._core.wire import device_id, topic, telemetry, control

if TYPE_CHECKING:
    from ._core.backend import RegistrationServer
    from ._core.model import ModelCatalog
    from ._core.offline_clock import OfflineClock

_LOGGER = logging.getLogger(__name__)

# Keep the existing 15-minute grace period for quiet device connections.
TELEMETRY_TIMEOUT_SECONDS = 15 * 60
AVAILABILITY_CHECK_INTERVAL = timedelta(seconds=30)


class WinixCoordinator(DataUpdateCoordinator):
    def __init__(
        self,
        hass,
        entry,
        config: InstallationConfig,
        *,
        catalog: Optional[ModelCatalog] = None,
    ):
        super().__init__(
            hass,
            _LOGGER,
            name="Winix Local",
            config_entry=entry,
            update_interval=AVAILABILITY_CHECK_INTERVAL,
        )

        self.config = config
        self.catalog = catalog
        self.device_id = device_id(config["mac"], config["token"])

        self.attributes: dict[str, str] = {}
        self.last_telemetry_monotonic: float = 0
        self.subscription_unsubscribers: list[Callable[[], None]] = []

        self.server: Optional[RegistrationServer] = None
        self.clock: Optional[OfflineClock] = None

    @property
    def online(self) -> bool:
        if not mqtt.is_connected(self.hass) or not self.last_telemetry_monotonic:
            return False

        telemetry_age_seconds = time.monotonic() - self.last_telemetry_monotonic
        return telemetry_age_seconds < TELEMETRY_TIMEOUT_SECONDS

    async def _async_update_data(self):
        # Trigger availability checks without fabricating a fresh device report.
        return dict(self.attributes)

    async def start(self):
        if not await mqtt.async_wait_for_mqtt_client(self.hass):
            raise HomeAssistantError(
                "Configure the Home Assistant MQTT integration first"
            )

        try:
            for kind in ("Conn", "Event"):
                self.subscription_unsubscribers.append(
                    await mqtt.async_subscribe(
                        self.hass, topic(kind, self.device_id), self.receive, qos=1
                    )
                )
        except BaseException:
            self.stop_subscriptions()
            raise

    @callback
    def receive(self, message):
        # Retained old data must never make a disconnected purifier look online.
        if message.retain:
            return

        try:
            attributes = telemetry(message.payload, self.device_id)
        except (ValueError, KeyError, TypeError, AttributeError):
            _LOGGER.warning("Rejected malformed or unrelated Winix telemetry")
            return

        self.attributes.update(attributes)
        self.last_telemetry_monotonic = time.monotonic()
        self.async_set_updated_data(dict(self.attributes))

    async def send(self, attributes):
        if not self.online:
            raise HomeAssistantError("Purifier is unavailable; command was not queued")

        payload = control(self.device_id, attributes)

        # QoS 0, non-retained, avoids replaying old power/fan commands on reconnect.
        await mqtt.async_publish(
            self.hass,
            topic("Control", self.device_id),
            json.dumps(payload, separators=(",", ":")),
            qos=0,
            retain=False,
        )

    def stop_subscriptions(self):
        for unsubscribe in self.subscription_unsubscribers:
            unsubscribe()

        self.subscription_unsubscribers.clear()

    async def stop(self):
        self.stop_subscriptions()

        if self.clock:
            self.clock.stop()

        if self.server:
            await self.server.stop()
