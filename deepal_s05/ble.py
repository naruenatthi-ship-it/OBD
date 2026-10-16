"""Bluetooth LE adapters (for example vLinker MC+ "MC-IOS"), which have no
classic Bluetooth serial port. ble://AA:BB:CC:DD:EE:FF opens one through
bleak (BlueZ on Linux) and gives Elm327 the same read/write interface as a
serial port.

The ELM327 service is found by looking for a non-standard GATT service with
a notify characteristic (adapter -> us) and a write characteristic
(us -> adapter); vendors use different UUIDs (FFF0, FFE0, E7810A71...).
"""

import asyncio
import re
import threading

BLE_URL = re.compile(r"^ble://((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})$")

# Generic Access, Generic Attribute, Device Information, Battery
STANDARD_SERVICES = ("00001800-", "00001801-", "0000180a-", "0000180f-")
CHUNK = 20  # bytes per write; fits the smallest BLE MTU


def pick_characteristics(services):
    """(notify_char, write_char) of the first non-standard service that has
    both, preferring a single characteristic that does both."""
    for svc in services:
        if str(svc.uuid).lower().startswith(STANDARD_SERVICES):
            continue
        chars = list(svc.characteristics)
        notify = [c for c in chars
                  if {"notify", "indicate"} & set(c.properties)]
        write = [c for c in chars
                 if {"write", "write-without-response"} & set(c.properties)]
        both = [c for c in notify if c in write]
        if both:
            return both[0], both[0]
        if notify and write:
            return notify[0], write[0]
    raise OSError("ไม่เจอช่องรับส่งข้อมูล ELM327 ในอุปกรณ์ BLE นี้")


class BlePort:
    """The part of the pyserial interface Elm327 uses, over BLE GATT.
    `client` is a connected-on-open bleak BleakClient (or a stand-in)."""

    def __init__(self, client, timeout=0.2, connect_timeout=20.0):
        self.client = client
        self.timeout = timeout
        self.buf = bytearray()
        self.cond = threading.Condition()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever,
                                       daemon=True)
        self.thread.start()
        try:
            self._run(self._open(), connect_timeout)
        except BaseException:
            self.close()
            raise

    def _run(self, coro, timeout=10.0):
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        try:
            return future.result(timeout)
        except OSError:
            raise
        except Exception as e:  # bleak errors, timeouts
            raise OSError("BLE: %s" % (e or type(e).__name__)) from e

    async def _open(self):
        await self.client.connect()
        self.rx, self.tx = pick_characteristics(self.client.services)
        props = set(self.tx.properties)
        self.response = "write-without-response" not in props
        await self.client.start_notify(self.rx, self._on_data)

    def _on_data(self, _sender, data):
        with self.cond:
            self.buf.extend(data)
            self.cond.notify_all()

    def read(self, size=1):
        with self.cond:
            if not self.buf:
                self.cond.wait(self.timeout)
            out = bytes(self.buf[:size])
            del self.buf[:size]
            return out

    def write(self, data):
        if not getattr(self.client, "is_connected", True):
            raise OSError("BLE: หลุดการเชื่อมต่อกับกล่อง")
        for i in range(0, len(data), CHUNK):
            self._run(self.client.write_gatt_char(
                self.tx, bytes(data[i:i + CHUNK]), response=self.response))
        return len(data)

    def reset_input_buffer(self):
        with self.cond:
            self.buf.clear()

    def close(self):
        try:
            if self.loop.is_running():
                self._run(self.client.disconnect(), 5.0)
        except OSError:
            pass
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(2)


def open_ble(url, timeout=0.2):
    m = BLE_URL.match(url)
    if not m:
        raise ValueError("ใช้รูปแบบ ble://AA:BB:CC:DD:EE:FF")
    try:
        from bleak import BleakClient
    except ImportError:
        raise OSError("ต้องติดตั้ง bleak ก่อน: sudo apt-get install -y "
                      "python3-bleak (หรือ pip install bleak)")
    return BlePort(BleakClient(m.group(1), timeout=20.0), timeout)
