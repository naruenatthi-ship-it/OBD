"""Bluetooth LE transport, with a stand-in for bleak's BleakClient."""

import pytest

from deepal_s05 import ble
from deepal_s05.elm327 import Elm327


class Char:
    def __init__(self, uuid, *props):
        self.uuid, self.properties = uuid, list(props)


class Service:
    def __init__(self, uuid, *chars):
        self.uuid, self.characteristics = uuid, list(chars)


RX = Char("0000fff1-0000-1000-8000-00805f9b34fb", "notify")
TX = Char("0000fff2-0000-1000-8000-00805f9b34fb", "write-without-response")
SERVICES = [
    Service("00001800-0000-1000-8000-00805f9b34fb",
            Char("00002a00-0000-1000-8000-00805f9b34fb", "read", "write")),
    Service("0000180f-0000-1000-8000-00805f9b34fb",
            Char("00002a19-0000-1000-8000-00805f9b34fb", "read", "notify")),
    Service("0000fff0-0000-1000-8000-00805f9b34fb", RX, TX),
]


class FakeClient:
    """Answers ELM327 commands like an adapter, split over two
    notifications to check that the port reassembles them."""

    def __init__(self, services=SERVICES):
        self.services = services
        self.is_connected = False
        self.line = b""
        self.writes = []

    async def connect(self):
        self.is_connected = True

    async def start_notify(self, char, callback):
        assert char is RX
        self.callback = callback

    async def write_gatt_char(self, char, data, response):
        assert char is TX and response is False
        self.writes.append(data)
        self.line += data
        if self.line.endswith(b"\r"):
            cmd, self.line = self.line.strip().decode(), b""
            reply = b"ELM327 v2.2" if cmd == "ATZ" else b"OK"
            self.callback(char, bytearray(reply[:3]))
            self.callback(char, bytearray(reply[3:] + b"\r\r>"))

    async def disconnect(self):
        self.is_connected = False


def test_pick_characteristics():
    assert ble.pick_characteristics(SERVICES) == (RX, TX)
    both = Char("bef8d6c9-9c21-4c9e-b632-bd58c1009f9f", "read", "write",
                "notify")
    vgate = Service("e7810a71-73ae-499d-8c15-faa9aef0c3f2", both)
    assert ble.pick_characteristics(SERVICES[:2] + [vgate]) == (both, both)
    with pytest.raises(OSError):
        ble.pick_characteristics(SERVICES[:2])


def test_elm327_over_ble():
    client = FakeClient()
    port = ble.BlePort(client, timeout=0.05)
    elm = Elm327("ble://C0:88:BE:81:C4:41", ser=port)
    try:
        assert elm.command("ATZ") == "ELM327 v2.2"
        assert elm.command("ATE0") == "OK"
        # long commands go out in pieces of at most 20 bytes
        elm.command("AT" + "X" * 30)
        assert max(len(w) for w in client.writes) <= ble.CHUNK
    finally:
        elm.close()
    assert not client.is_connected


def test_write_after_disconnect_raises():
    client = FakeClient()
    port = ble.BlePort(client, timeout=0.05)
    client.is_connected = False
    with pytest.raises(OSError):
        port.write(b"ATZ\r")
    port.close()


def test_bad_url():
    with pytest.raises(ValueError):
        ble.open_ble("ble://nope")
