"""Catalog compatibility and extension tests; runs without Home Assistant."""

from dataclasses import FrozenInstanceError
import hashlib
import json
import unittest

from winix_local import const as controls
from winix_local import model, wire


def example():
    return {
        "schema_version": 1,
        "default_filter_hours": 6480,
        "sensors": {"gas": {"attribute": "S08"}},
        "profiles": {"gas": ["gas"], "empty": []},
        "models": {
            "example": {"name": "Example", "family": "air_purifier", "profile": "gas"}
        },
    }


class CatalogTests(unittest.TestCase):
    def test_full_catalog_preserves_previous_metadata(self):
        catalog = model.load_builtin_catalog()
        snapshot = {}
        for key in catalog.model_ids:
            item = catalog.get(key.upper())
            snapshot[key] = {
                "name": item.name,
                "filter_hours": item.filter_hours,
                "sensors": [
                    {
                        "key": s.key,
                        "attribute": s.attribute,
                        "device_class": s.device_class,
                        "unit": s.unit,
                        "levels": {str(k): v for k, v in s.levels},
                    }
                    for s in item.sensors
                ],
            }
        encoded = json.dumps(
            snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        self.assertEqual(len(snapshot), 59)
        self.assertEqual(
            hashlib.sha256(encoded).hexdigest(),
            "8187d5a44d0113d5be9e4d322973e9ac0a0812bf090f68e66a1190be12121bed",
        )
        self.assertEqual(catalog.sensors_for("unknown"), ())
        self.assertEqual(catalog.filter_hours_for("unknown"), 6480)
        self.assertEqual(
            catalog.get("AC101").family, controls.DeviceFamily.AIR_CONDITIONER
        )
        self.assertEqual(
            catalog.get("x24_kr").family, controls.DeviceFamily.DEHUMIDIFIER
        )

    def test_custom_model_and_profile_need_no_parser_changes(self):
        data = example()
        data["models"]["another"] = {
            "name": "Another",
            "profile": "empty",
            "family": "dehumidifier",
            "filter_hours": 123,
        }
        catalog = model.ModelCatalog(data)
        self.assertEqual(catalog.sensors_for("EXAMPLE")[0].attribute, "S08")
        self.assertEqual(catalog.filter_hours_for("ANOTHER"), 123)
        self.assertEqual(catalog.sensors_for("another"), ())
        # Changes to the caller's document cannot corrupt a parsed catalog.
        data["sensors"]["gas"]["attribute"] = "S99"
        self.assertEqual(catalog.sensors_for("example")[0].attribute, "S08")
        with self.assertRaises(FrozenInstanceError):
            catalog.get("example").name = "changed"
        with self.assertRaises(AttributeError):
            catalog.default_filter_hours = 1

    def test_bad_schema_references_and_duplicate_attributes_are_rejected(self):
        mutations = [
            lambda d: d.update(schema_version=2),
            lambda d: d.update(default_filter_hours=True),
            lambda d: d["models"]["example"].update(profile="missing"),
            lambda d: d["profiles"].update(gas=["missing"]),
            lambda d: d["profiles"].update(gas=["gas", "gas"]),
            lambda d: d["sensors"]["gas"].update(attribute="invalid"),
            lambda d: d["models"].update(EXAMPLE=d["models"]["example"]),
            lambda d: d["models"]["example"].update(family="unknown"),
        ]
        for mutate in mutations:
            data = example()
            mutate(data)
            with self.assertRaises(model.CatalogError):
                model.ModelCatalog(data)

    def test_enum_units_and_duplicate_numeric_levels_are_rejected(self):
        data = example()
        data["sensors"]["gas"]["levels"] = {"01": "Good", "1": "Other"}
        with self.assertRaises(model.CatalogError):
            model.ModelCatalog(data)
        data["sensors"]["gas"].update(levels={"1": "Good"}, unit="ppm")
        with self.assertRaises(model.CatalogError):
            model.ModelCatalog(data)
        data["sensors"]["gas"].pop("unit")
        labels = model.ModelCatalog(data).sensors_for("example")[0].enum_labels()
        self.assertEqual(labels[1], "Good")
        with self.assertRaises(TypeError):
            labels[1] = "changed"

    def test_ui_controls_stay_within_the_validated_wire_commands(self):
        for choice in (
            controls.POWER,
            controls.MODE,
            controls.PLASMA,
            controls.FAN_SPEED,
        ):
            self.assertLessEqual(
                {code for _, code in choice.choices}, wire.ALLOWED[choice.attribute]
            )

    def test_control_encoding_and_numeric_constraints(self):
        for name, code in controls.FAN_SPEED.choices:
            self.assertEqual(controls.FAN_SPEED.encode(name), code)
            self.assertEqual(controls.FAN_SPEED.decode(code), name)
        with self.assertRaises(ValueError):
            controls.FAN_SPEED.encode("unsupported")
        self.assertIsNone(controls.FAN_SPEED.decode("99"))
        with self.assertRaises(ValueError):
            controls.ControlChoices("A04", (("a", "1"), ("b", "1")))
        supplied = [["off", "0"], ["on", "1"]]
        choice = controls.ControlChoices("A02", supplied)
        supplied[0][1] = "changed"
        self.assertEqual(choice.encode("off"), "0")
        self.assertTrue(controls.HUMIDITY_RANGE.accepts(35))
        self.assertTrue(controls.HUMIDITY_RANGE.accepts(70))
        for value in (34, 36, 71, 35.0, True):
            self.assertFalse(controls.HUMIDITY_RANGE.accepts(value))


if __name__ == "__main__":
    unittest.main()
