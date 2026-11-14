"""Winix fan controls using the shared speed definitions."""

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.util.percentage import (
    ordered_list_item_to_percentage,
    percentage_to_ordered_list_item,
)

from .entity import WinixEntity
from ._core.const import FAN_SPEED, MODE, POWER


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([WinixFan(entry.runtime_data)])


class WinixFan(WinixEntity, FanEntity):
    _attr_supported_features = (
        FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
        | FanEntityFeature.SET_SPEED
        | FanEntityFeature.PRESET_MODE
    )
    _attr_preset_modes = list(MODE.names)
    _attr_speed_count = len(FAN_SPEED.names)

    def __init__(self, coordinator):
        super().__init__(coordinator, "fan", None)

    @property
    def is_on(self):
        value = self.coordinator.attributes.get(POWER.attribute)
        return None if value is None else value == "1"

    @property
    def percentage(self):
        if self.is_on is False:
            return 0

        code = self.coordinator.attributes.get(FAN_SPEED.attribute)
        name = FAN_SPEED.decode(code)
        return ordered_list_item_to_percentage(FAN_SPEED.names, name) if name else None

    @property
    def preset_mode(self):
        return MODE.decode(self.coordinator.attributes.get(MODE.attribute))

    async def async_turn_on(self, percentage=None, preset_mode=None, **kwargs):
        if percentage is not None:
            await self.async_set_percentage(percentage)
        elif preset_mode:
            await self.async_set_preset_mode(preset_mode)
        else:
            await self.coordinator.send({POWER.attribute: POWER.encode("on")})

    async def async_turn_off(self, **kwargs):
        await self.coordinator.send({POWER.attribute: POWER.encode("off")})

    async def async_set_percentage(self, percentage):
        if percentage == 0:
            await self.async_turn_off()
            return

        name = percentage_to_ordered_list_item(FAN_SPEED.names, percentage)
        await self.coordinator.send(
            {
                POWER.attribute: POWER.encode("on"),
                MODE.attribute: MODE.encode("manual"),
                FAN_SPEED.attribute: FAN_SPEED.encode(name),
            }
        )

    async def async_set_preset_mode(self, preset_mode):
        await self.coordinator.send(
            {
                POWER.attribute: POWER.encode("on"),
                MODE.attribute: MODE.encode(preset_mode),
            }
        )
