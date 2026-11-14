"""Exercise the built HACS ZIP inside an isolated HA environment.

Set WINIX_HACS_ZIP to a built archive, or build dist/winix_local.zip first.
The source adapter is never added to the import path. No production HA starts.
"""

import asyncio
import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import zipfile
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

if importlib.util.find_spec("homeassistant") is None:
    HomeAssistant = None
else:
    from homeassistant.core import HomeAssistant

PROJECT_ROOT = Path(__file__).resolve().parents[3]
_archive_directory = None
_previous_custom_components = {}


def setUpModule():
    """Install exactly the release artifact into a temporary import root."""
    global _archive_directory, _previous_custom_components
    if HomeAssistant is None:
        return

    archive_path = Path(
        os.environ.get("WINIX_HACS_ZIP", PROJECT_ROOT / "dist/winix_local.zip")
    )
    if not archive_path.is_file():
        raise RuntimeError("Build the HACS ZIP before running the HA runtime tests")

    _archive_directory = tempfile.TemporaryDirectory(prefix="winix-hacs-test-")
    import_root = Path(_archive_directory.name)
    integration_root = import_root / "custom_components/winix_local"
    integration_root.mkdir(parents=True)

    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            if member.filename.startswith("/") or ".." in Path(member.filename).parts:
                raise ValueError("Unsafe archive member")
        archive.extractall(integration_root)

    _previous_custom_components = {
        name: module
        for name, module in sys.modules.items()
        if name == "custom_components" or name.startswith("custom_components.")
    }
    for name in _previous_custom_components:
        del sys.modules[name]

    sys.path.insert(0, str(import_root))
    importlib.invalidate_caches()

    integration = importlib.import_module("custom_components.winix_local")
    if not Path(integration.__file__).resolve().is_relative_to(integration_root):
        raise AssertionError("HA test imported source instead of the HACS artifact")


def tearDownModule():
    if _archive_directory is None:
        return

    for name in list(sys.modules):
        if name == "custom_components" or name.startswith("custom_components."):
            del sys.modules[name]
    sys.modules.update(_previous_custom_components)
    sys.path.remove(_archive_directory.name)
    importlib.invalidate_caches()
    _archive_directory.cleanup()


@unittest.skipIf(HomeAssistant is None, "requires the Home Assistant runtime")
class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_models_share_entities_subscriptions_and_nonretained_commands(self):
        for model, display_name, sensor_count in (
            ("5510_US", "5510", 6),
            ("5520_US", "5520", 6),
            ("FUTURE_MODEL", "FUTURE_MODEL", 2),
        ):
            with self.subTest(model=model):
                await self.check_model_runtime(model, display_name, sensor_count)

    async def check_model_runtime(self, model, display_name, sensor_count):
        for name in ("config_flow", "sensor", "switch", "fan", "_core.backend"):
            importlib.import_module("custom_components.winix_local." + name)
        from custom_components.winix_local.coordinator import WinixCoordinator
        from custom_components.winix_local.fan import WinixFan
        from custom_components.winix_local.switch import PlasmaSwitch
        from custom_components.winix_local.sensor import async_setup_entry
        from custom_components.winix_local._core.model import load_builtin_catalog
        from homeassistant.exceptions import HomeAssistantError

        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            coordinator = WinixCoordinator(
                hass,
                None,
                {
                    "mac": "AABBCCDDEEFF",
                    "token": "TESTTOKEN1",
                    "model": model,
                    "server_host": "127.0.0.1",
                    "mqtt_host": "127.0.0.1",
                    "mqtt_port": 8883,
                    "http_port": 18080,
                    "https_port": 18443,
                },
                catalog=load_builtin_catalog(),
            )
            coordinator.async_set_updated_data = Mock()

            with (
                patch(
                    "custom_components.winix_local.coordinator.mqtt.is_connected",
                    return_value=True,
                ),
                patch(
                    "custom_components.winix_local.coordinator.mqtt.async_publish",
                    new_callable=AsyncMock,
                ) as publish,
                patch(
                    "custom_components.winix_local.coordinator.mqtt.async_wait_for_mqtt_client",
                    new_callable=AsyncMock,
                    return_value=True,
                ),
                patch(
                    "custom_components.winix_local.coordinator.mqtt.async_subscribe",
                    new_callable=AsyncMock,
                    side_effect=lambda *args, **kwargs: Mock(),
                ) as subscribe,
            ):
                await coordinator.start()
                self.assertEqual(
                    [call.args[1] for call in subscribe.call_args_list],
                    [
                        f"Winix/Conn/Air01/{model}/{coordinator.device_id}",
                        f"Winix/Event/Air01/{model}/{coordinator.device_id}",
                    ],
                )
                self.assertFalse(coordinator.online)
                with self.assertRaises(HomeAssistantError):
                    await coordinator.send({"A04": "01"})

                data = {
                    "header": {"deviceId": coordinator.device_id},
                    "values": {
                        "modelId": model,
                        "deviceGroup": "Air01",
                        "apiNo": "A210",
                        "attributes": {
                            "A02": "1",
                            "A03": "02",
                            "A04": "02",
                            "A07": "0",
                            "S07": "03",
                            "S08": "57",
                            "S14": "47",
                            "A21": "38",
                        },
                    },
                }
                message = Mock(payload=json.dumps(data), retain=True)
                coordinator.receive(message)
                self.assertFalse(coordinator.online)

                message.retain = False
                coordinator.receive(message)
                self.assertTrue(coordinator.online)
                last_seen = coordinator.last_telemetry_monotonic
                updates = coordinator.async_set_updated_data.call_count

                # Another model cannot update this device or extend availability,
                # even when its MAC/token identity and attribute names match.
                data["values"]["modelId"] = "OTHER_MODEL"
                data["values"]["attributes"]["A04"] = "03"
                coordinator.receive(Mock(payload=json.dumps(data), retain=False))
                self.assertEqual(coordinator.last_telemetry_monotonic, last_seen)
                self.assertEqual(coordinator.async_set_updated_data.call_count, updates)
                self.assertEqual(coordinator.attributes["A04"], "02")

                fan = WinixFan(coordinator)
                self.assertEqual(fan.unique_id, "AABBCCDDEEFF_fan")
                self.assertEqual(fan.device_info["model"], display_name)
                self.assertEqual(
                    fan.device_info["name"], f"Winix {display_name} DDEEFF"
                )
                self.assertEqual(fan.percentage, 50)

                await fan.async_set_percentage(25)
                self.assertEqual(
                    publish.call_args.args[1],
                    f"Winix/Control/Air01/{model}/{coordinator.device_id}",
                )
                sent = json.loads(publish.call_args.args[2])
                self.assertEqual(sent["values"]["modelId"], model)
                self.assertEqual(
                    sent["values"]["attributes"], {"A02": "1", "A03": "02", "A04": "01"}
                )
                self.assertFalse(publish.call_args.kwargs["retain"])
                self.assertEqual(fan.percentage, 50)  # no optimistic state
                self.assertFalse(PlasmaSwitch(coordinator).is_on)

                add = Mock()
                entry = Mock(runtime_data=coordinator)
                await async_setup_entry(hass, entry, add)
                entities = add.call_args.args[0]
                self.assertEqual(len(entities), sensor_count)
                if sensor_count > 2:
                    gaslevel = next(
                        entity for entity in entities if entity.key == "S07"
                    )
                    self.assertEqual(gaslevel.native_value, "Poor")

                coordinator.last_telemetry_monotonic -= 901
                self.assertFalse(fan.available)
                await coordinator.stop()

            await hass.async_stop()

    async def test_config_flow_uses_catalog_names_and_unknown_model_fallback(self):
        from custom_components.winix_local.config_flow import WinixLocalFlow
        from custom_components.winix_local._core.pki import create_bundle

        for model, display_name in (
            ("5510_US", "5510"),
            ("5520_US", "5520"),
            ("FUTURE_MODEL", "FUTURE_MODEL"),
        ):
            with self.subTest(model=model), tempfile.TemporaryDirectory() as directory:
                hass = HomeAssistant(directory)
                bundle_directory = Path(directory) / "winix_local/AABBCCDDEEFF"
                create_bundle(
                    bundle_directory,
                    "AABBCCDDEEFF",
                    "127.0.0.1",
                    "127.0.0.1",
                    model=model,
                )
                flow = WinixLocalFlow()
                flow.hass = hass
                flow.async_set_unique_id = AsyncMock()
                flow._abort_if_unique_id_configured = Mock()
                flow.async_create_entry = Mock(return_value={"type": "create_entry"})

                result = await flow.async_step_user({"mac": "AA:BB:CC:DD:EE:FF"})
                self.assertEqual(result["type"], "create_entry")
                self.assertEqual(
                    flow.async_create_entry.call_args.kwargs,
                    {
                        "title": f"Winix {display_name} DDEEFF",
                        "data": {"mac": "AABBCCDDEEFF"},
                    },
                )
                flow.async_set_unique_id.assert_awaited_once_with("AABBCCDDEEFF")
                await hass.async_stop()

    async def test_config_flow_still_rejects_bundle_mac_mismatch(self):
        from custom_components.winix_local.config_flow import WinixLocalFlow
        from custom_components.winix_local._core.pki import create_bundle

        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            bundle_directory = Path(directory) / "winix_local/AABBCCDDEEFF"
            create_bundle(
                bundle_directory,
                "112233445566",
                "127.0.0.1",
                "127.0.0.1",
                model="5520_US",
            )
            flow = WinixLocalFlow()
            flow.hass = hass
            flow.async_set_unique_id = AsyncMock()
            flow._abort_if_unique_id_configured = Mock()
            flow.async_create_entry = Mock()
            flow.async_show_form = Mock(return_value={"type": "form"})

            result = await flow.async_step_user({"mac": "AABBCCDDEEFF"})
            self.assertEqual(result["type"], "form")
            self.assertEqual(
                flow.async_show_form.call_args.kwargs["errors"],
                {"base": "invalid_bundle"},
            )
            flow.async_create_entry.assert_not_called()
            await hass.async_stop()

    async def test_setup_failure_cleanup_and_error_classification(self):
        from types import SimpleNamespace

        from homeassistant.exceptions import ConfigEntryNotReady

        from custom_components.winix_local import async_setup_entry
        from custom_components.winix_local._core.pki import create_bundle

        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            root = __import__("pathlib").Path(directory) / "winix_local/AABBCCDDEEFF"
            create_bundle(
                root, "AABBCCDDEEFF", "127.0.0.1", "127.0.0.1", model="5510_US"
            )
            entry = SimpleNamespace(data={"mac": "AABBCCDDEEFF"})

            # Expected environment failures request a retry. Unexpected bugs and
            # cancellation retain their identity; all paths release resources.
            for failure in (
                OSError("port busy"),
                RuntimeError("bug"),
                asyncio.CancelledError(),
            ):
                with (
                    self.subTest(failure=type(failure).__name__),
                    patch(
                        "custom_components.winix_local.runtime.RegistrationServer"
                    ) as server_class,
                    patch(
                        "custom_components.winix_local.runtime.WinixCoordinator"
                    ) as coordinator_class,
                ):
                    server = server_class.return_value
                    server.prepare = Mock()
                    server.start = AsyncMock(side_effect=failure)
                    coordinator = coordinator_class.return_value
                    coordinator.stop = AsyncMock()

                    expected = (
                        ConfigEntryNotReady
                        if isinstance(failure, OSError)
                        else type(failure)
                    )
                    with self.assertRaises(expected) as raised:
                        await async_setup_entry(hass, entry)

                    coordinator.stop.assert_awaited_once()
                    if isinstance(failure, OSError):
                        self.assertIs(raised.exception.__cause__, failure)
                    else:
                        self.assertIs(raised.exception, failure)

            await hass.async_stop()

    async def test_integration_listener_setup_and_unload(self):
        from custom_components.winix_local import async_setup_entry, async_unload_entry
        from custom_components.winix_local._core.pki import create_bundle
        from types import SimpleNamespace
        import socket

        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            root = __import__("pathlib").Path(directory) / "winix_local/AABBCCDDEEFF"
            create_bundle(
                root, "AABBCCDDEEFF", "127.0.0.1", "127.0.0.1", model="5510_US"
            )
            entry = SimpleNamespace(
                data={"mac": "AABBCCDDEEFF"}, async_on_unload=Mock()
            )
            hass.config_entries = Mock()
            hass.config_entries.async_forward_entry_setups = AsyncMock()
            hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
            with (
                patch(
                    "custom_components.winix_local.coordinator.mqtt.async_wait_for_mqtt_client",
                    new_callable=AsyncMock,
                    return_value=True,
                ),
                patch(
                    "custom_components.winix_local.coordinator.mqtt.async_subscribe",
                    new_callable=AsyncMock,
                    side_effect=lambda *a, **k: Mock(),
                ),
            ):
                self.assertTrue(await async_setup_entry(hass, entry))
                for port in (18080, 18443):
                    with socket.create_connection(("127.0.0.1", port), timeout=2):
                        pass
                self.assertTrue(await async_unload_entry(hass, entry))
                for port in (18080, 18443):
                    with self.assertRaises(OSError):
                        socket.create_connection(("127.0.0.1", port), timeout=2)
            await hass.async_stop()


if __name__ == "__main__":
    unittest.main()
