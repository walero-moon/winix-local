"""Stable MAC identities, independent of replaceable registration tokens."""

from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo


class WinixEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key, name):
        super().__init__(coordinator)
        mac = coordinator.config["mac"]
        self._attr_unique_id = f"{mac}_{key}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={("winix_local", mac)},
            name=f"Winix {coordinator.model_name} {mac[-6:]}",
            manufacturer="Winix",
            model=coordinator.model_name,
        )

    @property
    def available(self):
        return self.coordinator.online
