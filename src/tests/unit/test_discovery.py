"""Exact-peer MAC discovery and handshake gating without network access."""

import struct
import subprocess
import unittest
from unittest.mock import Mock, patch

from winix_local.discovery import DiscoveryError, discover_peer_mac, identify_device
from winix_local.setup_protocol import crypt, header, secret_key

PEER = "192.168.43.1"
MAC = "AABB0CDDEE10"


def command_result(output, returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=output, stderr="")


class NeighborDiscoveryTests(unittest.TestCase):
    def test_macos_exact_peer_and_short_octets(self):
        output = (
            "? (192.168.43.10) at aa:bb:cc:dd:ee:ff on en0 ifscope [ethernet]\n"
            "? (192.168.43.1) at aa:bb:c:dd:ee:10 on en0 ifscope [ethernet]\n"
        )
        with (
            patch("winix_local.discovery.sys.platform", "darwin"),
            patch(
                "winix_local.discovery.subprocess.run",
                return_value=command_result(output),
            ) as run,
        ):
            self.assertEqual(discover_peer_mac(PEER), MAC)

        self.assertEqual(run.call_args.args[0], ["/usr/sbin/arp", "-n", PEER])
        self.assertEqual(run.call_args.kwargs["timeout"], 5)
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_linux_ip_neighbor_requires_exact_peer(self):
        output = (
            "192.168.43.10 dev wlan0 lladdr aa:bb:cc:dd:ee:ff REACHABLE\n"
            "192.168.43.1 dev wlan0 lladdr aa:bb:0c:dd:ee:10 STALE\n"
        )
        with (
            patch("winix_local.discovery.sys.platform", "linux"),
            patch(
                "winix_local.discovery.shutil.which",
                side_effect=lambda name: "/sbin/" + name,
            ),
            patch(
                "winix_local.discovery.subprocess.run",
                return_value=command_result(output),
            ) as run,
        ):
            self.assertEqual(discover_peer_mac(PEER), MAC)

        self.assertEqual(
            run.call_args.args[0], ["/sbin/ip", "-4", "neigh", "show", "to", PEER]
        )
        run.assert_called_once()

    def test_linux_falls_back_to_arp_for_empty_missing_or_failed_ip(self):
        arp = command_result(
            "Address HWtype HWaddress Flags Mask Iface\n"
            "192.168.43.1 ether aa:bb:0c:dd:ee:10 C wlan0\n"
        )
        for ip_result in (
            command_result(""),
            command_result("failed", returncode=1),
            FileNotFoundError(),
            subprocess.TimeoutExpired("ip", 5),
        ):
            with (
                self.subTest(ip_result=type(ip_result).__name__),
                patch("winix_local.discovery.sys.platform", "linux"),
                patch(
                    "winix_local.discovery.shutil.which",
                    side_effect=lambda name: "/sbin/" + name,
                ),
                patch(
                    "winix_local.discovery.subprocess.run", side_effect=[ip_result, arp]
                ) as run,
            ):
                self.assertEqual(discover_peer_mac(PEER), MAC)
                self.assertEqual(run.call_args.args[0], ["/sbin/arp", "-n", PEER])

    def test_incomplete_other_peer_and_invalid_addresses_do_not_produce_mac(self):
        for output in (
            "? (192.168.43.1) at (incomplete) on en0 ifscope [ethernet]\n",
            "192.168.43.1 dev wlan0 INCOMPLETE\n",
            "192.168.43.1 dev wlan0 lladdr aa:bb:0c:dd:ee:10 FAILED\n",
            "? (192.168.43.10) at aa:bb:0c:dd:ee:10 on en0\n",
            "? (192.168.43.1) at ff:ff:ff:ff:ff:ff on en0\n",
            "? (192.168.43.1) at 00:00:00:00:00:00 on en0\n",
            "? (192.168.43.1) at 01:00:5e:00:00:01 on en0\n",
        ):
            with (
                self.subTest(output=output),
                patch("winix_local.discovery.sys.platform", "darwin"),
                patch(
                    "winix_local.discovery.subprocess.run",
                    return_value=command_result(output),
                ),
                self.assertRaises(DiscoveryError),
            ):
                discover_peer_mac(PEER)

    def test_conflicting_neighbor_macs_are_not_guessed(self):
        output = (
            "? (192.168.43.1) at aa:bb:0c:dd:ee:10 on en0\n"
            "? (192.168.43.1) at aa:bb:cc:dd:ee:ff on en1\n"
        )
        with (
            patch("winix_local.discovery.sys.platform", "darwin"),
            patch(
                "winix_local.discovery.subprocess.run",
                return_value=command_result(output),
            ),
            self.assertRaisesRegex(DiscoveryError, "Multiple"),
        ):
            discover_peer_mac(PEER)

    def test_bad_peer_unsupported_system_or_missing_commands_need_manual_mac(self):
        with patch("winix_local.discovery.subprocess.run") as run:
            for peer in ("purifier.local", "192.168.43.1;echo bad", "::1"):
                with self.subTest(peer=peer), self.assertRaises(DiscoveryError):
                    discover_peer_mac(peer)
            run.assert_not_called()

        with (
            patch("winix_local.discovery.sys.platform", "win32"),
            self.assertRaises(DiscoveryError),
        ):
            discover_peer_mac(PEER)

        with (
            patch("winix_local.discovery.sys.platform", "linux"),
            patch("winix_local.discovery.shutil.which", return_value=None),
            self.assertRaises(DiscoveryError),
        ):
            discover_peer_mac(PEER)


class IdentityDiscoveryTests(unittest.TestCase):
    def connection(self):
        connection = Mock()
        connection.getpeername.return_value = (PEER, 47000)
        manager = Mock()
        manager.__enter__ = Mock(return_value=connection)
        manager.__exit__ = Mock(return_value=False)
        return manager, connection

    def test_connection_populates_neighbor_before_candidate_is_verified(self):
        manager, connection = self.connection()
        order = []

        def connect(*args, **kwargs):
            order.append("connect")
            return manager

        def discover(peer):
            order.append("neighbor")
            self.assertEqual(peer, PEER)
            return MAC

        def verify(peer_connection, key, nonce, expected_model):
            order.append("handshake")
            self.assertIs(peer_connection, connection)
            self.assertEqual(key, secret_key(b"1234", MAC))
            self.assertEqual(nonce, b"1234")
            self.assertEqual(expected_model, "5520_US")
            return "5520_US"

        with (
            patch(
                "winix_local.discovery.socket.create_connection", side_effect=connect
            ) as create,
            patch("winix_local.discovery.discover_peer_mac", side_effect=discover),
            patch("winix_local.discovery.secrets.token_bytes", return_value=b"1234"),
            patch("winix_local.provision.verify_device", side_effect=verify),
        ):
            self.assertEqual(
                identify_device(PEER, 47000, expected_model="5520_US"), (MAC, "5520_US")
            )

        self.assertEqual(order, ["connect", "neighbor", "handshake"])
        create.assert_called_once_with((PEER, 47000), timeout=10)
        manager.__exit__.assert_called_once()
        connection.sendall.assert_not_called()

    def test_real_codec_verifies_discovered_candidate_without_configuration(self):
        class HandshakePeer:
            def __init__(self):
                self.reply = bytearray()
                self.requests = []

            def __enter__(self):
                return self

            def __exit__(self, *arguments):
                return False

            def getpeername(self):
                return PEER, 47000

            def sendall(self, request):
                self.requests.append(request)
                if request[4:20] != header(1):
                    raise AssertionError("Discovery sent a configuration request")

                nonce = request[-4:]
                encrypted = crypt(b"5520_US\x001.0.3", secret_key(nonce, MAC))
                self.reply.extend(
                    struct.pack("<I", 16 + len(encrypted))
                    + header(1, response=True)
                    + encrypted
                )

            def recv(self, count):
                part = bytes(self.reply[:count])
                del self.reply[:count]
                return part

        for candidate in (MAC, "AABB0CDDEE11"):
            with self.subTest(candidate=candidate):
                peer = HandshakePeer()
                with (
                    patch(
                        "winix_local.discovery.socket.create_connection",
                        return_value=peer,
                    ),
                    patch(
                        "winix_local.discovery.discover_peer_mac",
                        return_value=candidate,
                    ),
                ):
                    if candidate == MAC:
                        self.assertEqual(
                            identify_device(expected_model="5520_US"), (MAC, "5520_US")
                        )
                    else:
                        with self.assertRaises(DiscoveryError):
                            identify_device(expected_model="5520_US")

                self.assertEqual(len(peer.requests), 1)
                self.assertEqual(peer.requests[0][4:20], header(1))

    def test_explicit_mac_skips_neighbor_lookup(self):
        manager, connection = self.connection()
        with (
            patch(
                "winix_local.discovery.socket.create_connection", return_value=manager
            ),
            patch("winix_local.discovery.discover_peer_mac") as discover,
            patch(
                "winix_local.provision.verify_device", return_value="5520_US"
            ) as verify,
        ):
            self.assertEqual(
                identify_device(PEER, 47000, mac="aa:bb:0c:dd:ee:10"), (MAC, "5520_US")
            )

        discover.assert_not_called()
        self.assertIs(verify.call_args.args[0], connection)

    def test_wrong_candidate_or_unexpected_model_is_not_accepted(self):
        for failure in (
            ValueError("wrong model"),
            EOFError("partial reply"),
            subprocess.CalledProcessError(1, "openssl"),
        ):
            with self.subTest(failure=type(failure).__name__):
                manager, _ = self.connection()
                with (
                    patch(
                        "winix_local.discovery.socket.create_connection",
                        return_value=manager,
                    ),
                    patch("winix_local.discovery.discover_peer_mac", return_value=MAC),
                    patch("winix_local.provision.verify_device", side_effect=failure),
                    self.assertRaisesRegex(DiscoveryError, "Could not verify"),
                ):
                    identify_device(PEER, 47000)

                manager.__exit__.assert_called_once()

    def test_invalid_inputs_and_neighbor_failure_do_not_start_handshake(self):
        with patch("winix_local.discovery.socket.create_connection") as create:
            for options in (
                {"mac": "bad"},
                {"expected_model": "5520/#"},
                {"setup_port": 0},
            ):
                with self.subTest(options=options), self.assertRaises(DiscoveryError):
                    identify_device(**options)
            create.assert_not_called()

        manager, _ = self.connection()
        with (
            patch(
                "winix_local.discovery.socket.create_connection", return_value=manager
            ),
            patch(
                "winix_local.discovery.discover_peer_mac",
                side_effect=DiscoveryError("no neighbor"),
            ),
            patch("winix_local.provision.verify_device") as verify,
            self.assertRaisesRegex(DiscoveryError, "no neighbor"),
        ):
            identify_device()
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
