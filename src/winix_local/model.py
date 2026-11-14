"""Immutable, transport-independent device catalog loaded from validated data.

Catalog readers receive immutable model/sensor records. A replacement data file
can extend model coverage without editing the parser or HA entity implementation.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple

from .const import DeviceFamily


class CatalogError(ValueError):
    """A catalog document contains invalid or inconsistent definitions."""


def _text(value: Any, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{location}: expected nonempty text")

    return value


def _mapping(value: Any, location: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise CatalogError(f"{location}: expected an object")

    return value


def _hours(value: Any, location: str) -> int:
    if type(value) is not int or value <= 0:
        raise CatalogError(f"{location}: expected positive integer hours")

    return value


@dataclass(frozen=True)
class SensorSpec:
    key: str
    attribute: str
    device_class: Optional[str] = None
    unit: Optional[str] = None
    levels: Tuple[Tuple[int, str], ...] = ()

    def enum_labels(self) -> Optional[Mapping[int, str]]:
        return MappingProxyType(dict(self.levels)) if self.levels else None


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    name: str
    family: DeviceFamily
    filter_hours: int
    sensors: Tuple[SensorSpec, ...]


class ModelCatalog:
    """Resolve models case-insensitively, with explicit unknown-model fallbacks."""

    __slots__ = ("_default_filter_hours", "_models")

    def __init__(self, document: Mapping[str, Any]) -> None:
        document = _mapping(document, "catalog")

        if (
            type(document.get("schema_version")) is not int
            or document["schema_version"] != 1
        ):
            raise CatalogError("catalog.schema_version: only version 1 is supported")

        self._default_filter_hours = _hours(
            document.get("default_filter_hours"), "default_filter_hours"
        )

        sensors = self._read_sensors(document.get("sensors"))
        profiles = self._read_profiles(document.get("profiles"), sensors)

        models = {}

        for raw_id, raw in _mapping(document.get("models"), "models").items():
            model_id = _text(raw_id, "model identifier").casefold()
            if model_id in models:
                raise CatalogError(
                    f"models.{raw_id}: case-insensitive identifier collision"
                )

            raw = _mapping(raw, f"models.{raw_id}")
            profile = _text(raw.get("profile"), f"models.{raw_id}.profile")
            if profile not in profiles:
                raise CatalogError(f"models.{raw_id}: unknown profile {profile!r}")

            try:
                family = DeviceFamily(raw.get("family"))
            except (TypeError, ValueError) as error:
                raise CatalogError(f"models.{raw_id}: unknown device family") from error

            models[model_id] = ModelSpec(
                model_id=model_id,
                name=_text(raw.get("name"), f"models.{raw_id}.name"),
                family=family,
                filter_hours=_hours(
                    raw.get("filter_hours", self.default_filter_hours),
                    f"models.{raw_id}.filter_hours",
                ),
                sensors=profiles[profile],
            )

        self._models = MappingProxyType(models)

    @staticmethod
    def _read_sensors(raw_sensors: Any) -> Mapping[str, SensorSpec]:
        result = {}

        for key, raw in _mapping(raw_sensors, "sensors").items():
            _text(key, "sensor key")
            raw = _mapping(raw, f"sensors.{key}")

            attribute = _text(raw.get("attribute"), f"sensors.{key}.attribute")
            if not re.fullmatch(r"[A-Z][0-9]{2}", attribute):
                raise CatalogError(f"sensors.{key}: invalid wire attribute")

            levels = []

            for code, label in _mapping(
                raw.get("levels", {}), f"sensors.{key}.levels"
            ).items():
                if not isinstance(code, str) or not re.fullmatch(r"[0-9]+", code):
                    raise CatalogError(f"sensors.{key}: invalid level identifier")

                levels.append((int(code), _text(label, f"sensors.{key}.levels.{code}")))

            if len({code for code, _ in levels}) != len(levels):
                raise CatalogError(f"sensors.{key}: duplicate numeric level")

            device_class, unit = raw.get("device_class"), raw.get("unit")
            for field, value in (("device_class", device_class), ("unit", unit)):
                if value is not None:
                    _text(value, f"sensors.{key}.{field}")

            if levels and (unit is not None or device_class is not None):
                raise CatalogError(
                    f"sensors.{key}: enumerations cannot also have numeric units/classes"
                )

            result[key] = SensorSpec(
                key=key,
                attribute=attribute,
                device_class=device_class,
                unit=unit,
                levels=tuple(levels),
            )

        return result

    @staticmethod
    def _read_profiles(
        raw_profiles: Any, sensors: Mapping[str, SensorSpec]
    ) -> Mapping[str, Tuple[SensorSpec, ...]]:
        result = {}

        for key, references in _mapping(raw_profiles, "profiles").items():
            _text(key, "profile key")
            if not isinstance(references, list):
                raise CatalogError(f"profiles.{key}: expected a list of sensor keys")

            profile = []
            seen_attributes = set()

            for reference in references:
                _text(reference, f"profiles.{key} sensor reference")
                if reference not in sensors:
                    raise CatalogError(f"profiles.{key}: unknown sensor {reference!r}")

                spec = sensors[reference]
                if spec.attribute in seen_attributes:
                    raise CatalogError(
                        f"profiles.{key}: repeated wire attribute {spec.attribute}"
                    )

                seen_attributes.add(spec.attribute)
                profile.append(spec)

            result[key] = tuple(profile)

        return result

    @property
    def default_filter_hours(self) -> int:
        return self._default_filter_hours

    @property
    def model_ids(self) -> Tuple[str, ...]:
        return tuple(sorted(self._models))

    def get(self, model_id: str) -> Optional[ModelSpec]:
        return self._models.get(model_id.casefold())

    def sensors_for(self, model_id: str) -> Tuple[SensorSpec, ...]:
        model = self.get(model_id)

        return model.sensors if model else ()

    def filter_hours_for(self, model_id: str) -> int:
        model = self.get(model_id)

        return model.filter_hours if model else self.default_filter_hours

    @classmethod
    def from_path(cls, path: Path) -> ModelCatalog:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise CatalogError(f"{path.name}: invalid JSON") from error

        return cls(document)


@lru_cache(maxsize=1)
def load_builtin_catalog() -> ModelCatalog:
    """Call in an executor in HA; the immutable parsed catalog can be shared."""
    return ModelCatalog.from_path(Path(__file__).with_name("catalog.json"))
