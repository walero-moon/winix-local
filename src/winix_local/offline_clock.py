"""Optional per-device DNS/NTP sink for WAN-blocked labs. Never forwards."""

import asyncio
import socket
import struct
import time

NTP_EPOCH = 2208988800


def dns_reply(data, address):
    if len(data) < 12 or len(data) > 512:
        raise ValueError("invalid DNS length")

    ident, flags, questions = struct.unpack_from("!HHH", data)
    if flags & 0xF800 or questions != 1:
        raise ValueError("only ordinary single-question DNS is supported")

    pos, labels = 12, []

    while True:
        if pos >= len(data):
            raise ValueError("truncated DNS name")

        length = data[pos]
        pos += 1
        if length == 0:
            break

        if length > 63 or pos + length > len(data):
            raise ValueError("invalid DNS label")

        labels.append(data[pos : pos + length].decode("ascii"))
        pos += length

    if pos + 4 > len(data):
        raise ValueError("truncated DNS question")

    kind, cls = struct.unpack_from("!HH", data, pos)

    # Ignore bounded optional additional records (e.g. EDNS); do not echo them.
    question = data[12 : pos + 4]
    answer = b""
    if kind == 1 and cls == 1:
        answer = (
            b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 30, 4) + socket.inet_aton(address)
        )

    header = struct.pack(
        "!HHHHHH", ident, 0x8400 | (flags & 0x100), 1, int(bool(answer)), 0, 0
    )

    return header + question + answer, ".".join(labels), kind


def ntp_reply(data, now):
    if len(data) < 48 or len(data) > 512 or data[0] & 7 != 3:
        raise ValueError("invalid NTP client request")

    version = (data[0] >> 3) & 7
    if version not in (3, 4):
        raise ValueError("unsupported NTP version")

    seconds = int(now) + NTP_EPOCH
    stamp = struct.pack("!II", seconds & 0xFFFFFFFF, int((now % 1) * 2**32))

    packet = bytearray(48)
    packet[:4] = bytes([(version << 3) | 4, 2, data[2], 236])
    packet[8:12] = struct.pack("!I", 66)
    packet[12:16] = b"LOCL"
    packet[16:24] = stamp
    packet[24:32] = data[40:48]
    packet[32:40] = stamp
    packet[40:48] = stamp

    return bytes(packet)


class ClockProtocol(asyncio.DatagramProtocol):
    def __init__(self, address, allowed, kind):
        self.address, self.allowed, self.kind = address, allowed, kind

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, peer):
        if peer[0] != self.allowed:
            return

        try:
            reply = (
                dns_reply(data, self.address)[0]
                if self.kind == "dns"
                else ntp_reply(data, time.time())
            )
            self.transport.sendto(reply, peer)
        except (ValueError, UnicodeError, OSError):
            return


class OfflineClock:
    def __init__(self, address, device_ip):
        self.address, self.device_ip = address, device_ip
        self.transports = []

    async def start(self):
        try:
            for port, kind in ((53, "dns"), (123, "ntp")):
                (
                    transport,
                    _,
                ) = await asyncio.get_running_loop().create_datagram_endpoint(
                    lambda: ClockProtocol(self.address, self.device_ip, kind),
                    local_addr=("0.0.0.0", port),
                )
                self.transports.append(transport)
        except BaseException:
            self.stop()
            raise

    def stop(self):
        for transport in self.transports:
            transport.close()

        self.transports.clear()
