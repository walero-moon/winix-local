"""Sensor entities selected from the shared Winix model catalog."""

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.const import EntityCategory, UnitOfTime

from .entity import WinixEntity
from ._core.model import load_builtin_catalog


async def async_setup_entry(hass, entry, async_add_entities):
    entities = [
        RawSensor(
            entry.runtime_data,
            "A21",
            "Filter usage",
            unit=UnitOfTime.HOURS,
            device_class=SensorDeviceClass.DURATION,
        ),
        RawSensor(
            entry.runtime_data,
            "rssi",
            "Wi-Fi signal",
            unit="dBm",
            device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        ),
    ]

    # The 5510 profile contains gas, brightness and RPM; it has no PM2.5 sensor.
    catalog = entry.runtime_data.catalog
    if catalog is None:
        catalog = await hass.async_add_executor_job(load_builtin_catalog)

    model_id = entry.runtime_data.config.get("model", "5510_US")

    for info in catalog.sensors_for(model_id):
        entities.append(
            RawSensor(
                entry.runtime_data,
                info.attribute,
                info.key.replace("_", " ").capitalize(),
                unit=info.unit,
                device_class=info.device_class,
                enum_values=info.enum_labels(),
            )
        )

    async_add_entities(entities)


class RawSensor(WinixEntity, SensorEntity):
    def __init__(
        self, coordinator, key, name, unit=None, device_class=None, enum_values=None
    ):
        super().__init__(coordinator, key, name)

        self.key = key
        self.enum_values = enum_values

        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class

        if enum_values:
            self._attr_options = list(enum_values.values())
            self._attr_device_class = SensorDeviceClass.ENUM

        if key in ("rssi", "A21"):
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        raw = self.coordinator.attributes.get(self.key)
        if raw is None:
            return None

        try:
            value = float(raw)
            return self.enum_values.get(int(value)) if self.enum_values else value
        except (ValueError, TypeError):
            return None
