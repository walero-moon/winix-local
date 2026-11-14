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
    async def test_import_entities_receive_and_nonretained_commands(self):
        for name in ("config_flow", "sensor", "switch", "fan", "_core.backend"):
            importlib.import_module("custom_components.winix_local." + name)
        from custom_components.winix_local.coordinator import WinixCoordinator
        from custom_components.winix_local.fan import WinixFan
        from custom_components.winix_local.switch import PlasmaSwitch
        from custom_components.winix_local.sensor import async_setup_entry

        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            coordinator = WinixCoordinator(
                hass,
                None,
                {
                    "mac": "AABBCCDDEEFF",
                    "token": "TESTTOKEN1",
                    "model": "5510_US",
                    "server_host": "127.0.0.1",
                    "mqtt_host": "127.0.0.1",
                    "mqtt_port": 8883,
                    "http_port": 18080,
                    "https_port": 18443,
                },
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
            ):
                self.assertFalse(coordinator.online)
                from homeassistant.exceptions import HomeAssistantError

                with self.assertRaises(HomeAssistantError):
                    await coordinator.send({"A04": "01"})
                data = {
                    "header": {"deviceId": coordinator.device_id},
                    "values": {
                        "modelId": "5510_US",
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
                msg = Mock(payload=json.dumps(data), retain=True)
                coordinator.receive(msg)
                self.assertFalse(coordinator.online)
                msg.retain = False
                coordinator.receive(msg)
                self.assertTrue(coordinator.online)
                fan = WinixFan(coordinator)
                self.assertEqual(fan.percentage, 50)
                await fan.async_set_percentage(25)
                sent = json.loads(publish.call_args.args[2])
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
                self.assertEqual(len(entities), 6)
                gaslevel = next(e for e in entities if e.key == "S07")
                self.assertEqual(gaslevel.native_value, "Poor")
                coordinator.last_telemetry_monotonic -= 901
                self.assertFalse(fan.available)
            await hass.async_stop()

    async def test_setup_failure_cleanup_and_error_classification(self):
        from types import SimpleNamespace

        from homeassistant.exceptions import ConfigEntryNotReady

        from custom_components.winix_local import async_setup_entry
        from custom_components.winix_local._core.pki import create_bundle

        with tempfile.TemporaryDirectory() as directory:
            hass = HomeAssistant(directory)
            root = __import__("pathlib").Path(directory) / "winix_local/AABBCCDDEEFF"
            create_bundle(root, "AABBCCDDEEFF", "127.0.0.1", "127.0.0.1")
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
            create_bundle(root, "AABBCCDDEEFF", "127.0.0.1", "127.0.0.1")
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
