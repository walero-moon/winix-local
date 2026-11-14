"""Wire validation and source-scoped clock replies; no external traffic."""

import json
import socket
import struct
import time
import unittest
from unittest.mock import Mock

from winix_local import offline_clock as clock
from winix_local import wire


class WireTests(unittest.TestCase):
    def test_shared_controls_and_exact_model_topics(self):
        identity = wire.device_id("AA:BB:CC:DD:EE:FF", "TESTTOKEN1")

        for model in ("5510_US", "5520_US", "FutureModel_01"):
            with self.subTest(model=model):
                command = wire.control(identity, {"A04": "01"}, model)
                self.assertEqual(command["values"]["apiNo"], "A211")
                self.assertEqual(command["values"]["modelId"], model)
                self.assertEqual(
                    wire.topic("Control", identity, model),
                    f"Winix/Control/Air01/{model}/{identity}",
                )

                for attributes in ({"P01": "0"}, {"A04": "08"}, {"A07": "2"}, {}):
                    with self.assertRaises(ValueError):
                        wire.control(identity, attributes, model)

        with self.assertRaises(ValueError):
            wire.device_id("bad", "TESTTOKEN1")

    def test_telemetry_is_scoped_to_exact_device_and_model(self):
        identity = wire.device_id("AABBCCDDEEFF", "TESTTOKEN1")

        for model in ("5510_US", "5520_US", "FutureModel_01"):
            with self.subTest(model=model):
                data = {
                    "header": {"deviceId": identity},
                    "values": {
                        "modelId": model,
                        "deviceGroup": "Air01",
                        "apiNo": "A210",
                        "attributes": {"A04": "02"},
                    },
                }
                payload = json.dumps(data)
                self.assertEqual(
                    wire.telemetry(payload, identity, model), {"A04": "02"}
                )

                for wrong_identity, wrong_model in (
                    (identity + "other", model),
                    (identity, model + "_other"),
                    (identity, model.swapcase()),
                ):
                    with self.assertRaises(ValueError):
                        wire.telemetry(payload, wrong_identity, wrong_model)

                data["values"]["attributes"] = {"A04": {}}
                with self.assertRaises(ValueError):
                    wire.telemetry(json.dumps(data), identity, model)

    def test_model_identifiers_cannot_escape_their_topic_segment(self):
        identity = wire.device_id("AABBCCDDEEFF", "TESTTOKEN1")

        for model in (
            "",
            "5520/US",
            "5520+US",
            "5520#",
            "5520 US",
            "5520\nUS",
            "5520\x00US",
            "\u00e9",
            "A" * 65,
            None,
        ):
            with self.subTest(model=model):
                with self.assertRaises(ValueError):
                    wire.validate_model(model)
                with self.assertRaises(ValueError):
                    wire.topic("Control", identity, model)
                with self.assertRaises(ValueError):
                    wire.control(identity, {"A04": "01"}, model)
                with self.assertRaises(ValueError):
                    wire.telemetry("{}", identity, model)

        self.assertEqual(wire.validate_model("A" * 64), "A" * 64)
        self.assertEqual(wire.validate_model("5520_US"), "5520_US")

    def test_identity_parser_reads_model_before_nul_delimited_firmware(self):
        self.assertEqual(wire.parse_device_model(b"5520_US\x001.0.3"), "5520_US")
        self.assertEqual(wire.parse_device_model(b"FutureModel_01"), "FutureModel_01")

        for identity in (
            b"",
            b"\x001.0.3",
            b"5520_US1.0.3",
            b"5520_US\n",
            b"5520/US\x001.0.3",
            b"\xff\x001.0.3",
        ):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                wire.parse_device_model(identity)


class ClockTests(unittest.TestCase):
    def test_device_only_time_and_dns(self):
        for kind in ("dns", "ntp"):
            protocol = clock.ClockProtocol("10.0.0.20", "10.0.0.42", kind)
            transport = Mock()
            protocol.connection_made(transport)
            if kind == "ntp":
                packet = bytes([0x23]) + bytes(39) + b"testtime"
            else:
                packet = (
                    struct.pack("!HHHHHH", 1, 0x100, 1, 0, 0, 0)
                    + b"\x04pool\x03ntp\x03org\x00"
                    + struct.pack("!HH", 1, 1)
                )
            protocol.datagram_received(packet, ("10.0.0.43", 1234))
            transport.sendto.assert_not_called()
            protocol.datagram_received(packet, ("10.0.0.42", 1234))
            reply = transport.sendto.call_args.args[0]
            if kind == "ntp":
                self.assertEqual(reply[24:32], b"testtime")
                stamp = struct.unpack("!I", reply[40:44])[0] - 2208988800
                self.assertLess(abs(time.time() - stamp), 2)
            else:
                self.assertEqual(reply[-4:], socket.inet_aton("10.0.0.20"))


if __name__ == "__main__":
    unittest.main()
