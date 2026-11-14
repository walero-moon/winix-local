"""PlasmaWave is independent of fan presets."""

from homeassistant.components.switch import SwitchEntity

from .entity import WinixEntity
from ._core.const import PLASMA


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PlasmaSwitch(entry.runtime_data)])


class PlasmaSwitch(WinixEntity, SwitchEntity):
    def __init__(self, coordinator):
        super().__init__(coordinator, "plasma", "PlasmaWave")

    @property
    def is_on(self):
        value = self.coordinator.attributes.get(PLASMA.attribute)
        return None if value is None else value == "1"

    async def async_turn_on(self, **kwargs):
        await self.coordinator.send({PLASMA.attribute: PLASMA.encode("on")})

    async def async_turn_off(self, **kwargs):
        await self.coordinator.send({PLASMA.attribute: PLASMA.encode("off")})
