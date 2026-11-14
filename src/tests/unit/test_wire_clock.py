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
    def test_controls_and_identity(self):
        identity = wire.device_id("AA:BB:CC:DD:EE:FF", "TESTTOKEN1")
        self.assertEqual(
            wire.control(identity, {"A04": "01"})["values"]["apiNo"], "A211"
        )
        for attrs in ({"P01": "0"}, {"A04": "08"}, {"A07": "2"}, {}):
            with self.assertRaises(ValueError):
                wire.control(identity, attrs)
        with self.assertRaises(ValueError):
            wire.device_id("bad", "TESTTOKEN1")

    def test_actual_telemetry_and_wrong_device(self):
        identity = wire.device_id("AABBCCDDEEFF", "TESTTOKEN1")
        data = {
            "header": {"deviceId": identity},
            "values": {
                "modelId": "5510_US",
                "deviceGroup": "Air01",
                "apiNo": "A210",
                "attributes": {"A04": "02"},
            },
        }
        self.assertEqual(wire.telemetry(json.dumps(data), identity), {"A04": "02"})
        with self.assertRaises(ValueError):
            wire.telemetry(json.dumps(data), identity + "other")
        data["values"]["attributes"] = {"A04": {}}
        with self.assertRaises(ValueError):
            wire.telemetry(json.dumps(data), identity)


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
