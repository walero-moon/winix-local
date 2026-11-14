"""Opt-in broker acceptance against a disposable broker's loopback namespace.

WINIX_MQTT_TEST_ROOT must contain bundle/ and broker/ generated test fixtures.
Run only inside the isolated test broker network namespace, never production.
"""

import json
import os
from pathlib import Path
import queue
import socket
import ssl
import threading
import unittest
from unittest.mock import patch

try:
    import paho.mqtt.client as mqtt
except ImportError:
    mqtt = None


@unittest.skipUnless(
    os.environ.get("WINIX_MQTT_TEST_ROOT") and mqtt is not None,
    "requires an explicit disposable broker fixture and paho-mqtt",
)
class BrokerRuntimeTests(unittest.TestCase):
    def test_password_mtls_telemetry_commands_and_device_acl(self):
        root = Path(os.environ["WINIX_MQTT_TEST_ROOT"])
        bundle = json.loads((root / "bundle/bundle.json").read_text())
        credentials = json.loads((root / "broker/ha-credentials.json").read_text())
        original_resolve = socket.getaddrinfo

        # Keep certificate hostname verification while routing the generated
        # test broker identity to this disposable namespace's loopback address.
        def resolve(host, port, *args, **kwargs):
            address = "127.0.0.1" if host == bundle["mqtt_host"] else host
            return original_resolve(address, port, *args, **kwargs)

        topic_suffix = f"Air01/{bundle['model']}/{bundle['mac']}_{bundle['token']}"
        event_topic = "Winix/Event/" + topic_suffix
        control_topic = "Winix/Control/" + topic_suffix
        received_events = queue.Queue()
        received_controls = queue.Queue()
        connection_errors = queue.Queue()
        backend_ready = threading.Event()
        device_ready = threading.Event()

        backend = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2, client_id="winix-backend-test"
        )
        backend.username_pw_set(**credentials)
        device = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=bundle["mac"] + "_" + bundle["token"],
        )
        device.tls_set(
            ca_certs=str(root / "bundle/ca.pem"),
            certfile=str(root / "bundle/device.pem"),
            keyfile=str(root / "bundle/device.key"),
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )

        def on_backend_connect(client, userdata, flags, reason, properties):
            if reason.is_failure:
                connection_errors.put("Backend authentication failed")
                backend_ready.set()
                return

            client.subscribe("Winix/Event/#", qos=1)

        def on_device_connect(client, userdata, flags, reason, properties):
            if reason.is_failure:
                connection_errors.put("Device certificate rejected")
                device_ready.set()
                return

            client.subscribe(control_topic, qos=1)

        backend.on_connect = on_backend_connect
        backend.on_subscribe = lambda *args: backend_ready.set()
        backend.on_message = lambda client, userdata, message: received_events.put(
            (message.topic, message.payload)
        )
        device.on_connect = on_device_connect
        device.on_subscribe = lambda *args: device_ready.set()
        device.on_message = lambda client, userdata, message: received_controls.put(
            message.payload
        )

        with patch.object(socket, "getaddrinfo", side_effect=resolve):
            try:
                backend.connect("127.0.0.1", 1883)
                backend.loop_start()
                self.assertTrue(backend_ready.wait(5), "Backend subscription timed out")
                self.assertTrue(
                    connection_errors.empty(), list(connection_errors.queue)
                )

                device.connect(bundle["mqtt_host"], bundle["mqtt_port"])
                device.loop_start()
                self.assertTrue(device_ready.wait(5), "Device subscription timed out")
                self.assertTrue(
                    connection_errors.empty(), list(connection_errors.queue)
                )

                device.publish(
                    event_topic, b"verified telemetry", qos=1
                ).wait_for_publish(5)
                self.assertEqual(
                    received_events.get(timeout=5), (event_topic, b"verified telemetry")
                )
                backend.publish(
                    control_topic, b"verified command", qos=0
                ).wait_for_publish(5)
                self.assertEqual(received_controls.get(timeout=5), b"verified command")

                device.publish(control_topic, b"forbidden", qos=1).wait_for_publish(5)
                with self.assertRaises(queue.Empty):
                    received_controls.get(timeout=1)

                # Mosquitto may accept a subscription filter; ACLs still must
                # filter deliveries outside the certificate's device identity.
                device.publish(
                    f"Winix/Event/Air01/{bundle['model']}/OTHER_DEVICE",
                    b"forbidden",
                    qos=1,
                ).wait_for_publish(5)
                with self.assertRaises(queue.Empty):
                    received_events.get(timeout=1)
            finally:
                device.disconnect()
                device.loop_stop()
                backend.disconnect()
                backend.loop_stop()


if __name__ == "__main__":
    unittest.main()
