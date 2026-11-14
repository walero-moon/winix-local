"""Shared device vocabulary and validated control choices; no HA dependency."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
from typing import Optional, Tuple

DOMAIN = "winix_local"
NAME = "Winix Local"


class DeviceFamily(str, Enum):
    AIR_PURIFIER = "air_purifier"
    DEHUMIDIFIER = "dehumidifier"
    AIR_CONDITIONER = "air_conditioner"


@dataclass(frozen=True)
class ControlChoices:
    """One wire attribute with an ordered, reversible set of named values."""

    attribute: str
    choices: Tuple[Tuple[str, str], ...]

    def __post_init__(self) -> None:
        try:
            choices = tuple((name, code) for name, code in self.choices)
        except (TypeError, ValueError) as error:
            raise ValueError("Control choices must be name/code pairs") from error

        object.__setattr__(self, "choices", choices)

        if not isinstance(self.attribute, str) or not re.fullmatch(
            r"[A-Z][0-9]{2}", self.attribute
        ):
            raise ValueError("Control attributes must have a letter and two digits")

        if not self.choices or any(
            not isinstance(name, str)
            or not name
            or not isinstance(code, str)
            or not code
            for name, code in self.choices
        ):
            raise ValueError("Controls need nonempty names and wire codes")

        if len(set(self.names)) != len(self.choices):
            raise ValueError("Control names must be unique")

        if len({code for _, code in self.choices}) != len(self.choices):
            raise ValueError("Wire codes must be unique")

    @property
    def names(self) -> Tuple[str, ...]:
        return tuple(name for name, _ in self.choices)

    def encode(self, name: str) -> str:
        for candidate, code in self.choices:
            if candidate == name:
                return code

        raise ValueError(f"Unsupported {self.attribute} choice: {name!r}")

    def decode(self, code: str) -> Optional[str]:
        return next(
            (name for name, candidate in self.choices if candidate == code), None
        )


@dataclass(frozen=True)
class NumberRange:
    """A stepped range, reusable for humidity and other numeric controls."""

    minimum: int
    maximum: int
    step: int = 1

    def __post_init__(self) -> None:
        values = (self.minimum, self.maximum, self.step)

        if (
            any(type(value) is not int for value in values)
            or self.minimum > self.maximum
            or self.step <= 0
        ):
            raise ValueError("Invalid numeric range")

    def accepts(self, value: int) -> bool:
        return (
            type(value) is int
            and self.minimum <= value <= self.maximum
            and (value - self.minimum) % self.step == 0
        )


POWER = ControlChoices("A02", (("off", "0"), ("on", "1")))
MODE = ControlChoices("A03", (("auto", "01"), ("manual", "02")))
PLASMA = ControlChoices("A07", POWER.choices)
FAN_SPEED = ControlChoices(
    "A04", (("low", "01"), ("medium", "02"), ("high", "03"), ("turbo", "05"))
)

# Additional catalog vocabulary does not enable unverified transport commands.
EXTENDED_AIRFLOW = ControlChoices(
    FAN_SPEED.attribute, FAN_SPEED.choices + (("sleep", "06"), ("super", "08"))
)
DEHUMIDIFIER_SPEEDS = ("low", "high", "turbo")
DEHUMIDIFIER_MODES = ("auto", "manual", "clothes", "shoes", "quiet", "continuous")
HUMIDITY_RANGE = NumberRange(35, 70, 5)
