"""Minimal ELM327 client for reading UDS DIDs (service 0x22) over CAN.

Only read requests are exposed: the client never sends write, routine,
reset or DTC-clear services to the vehicle.
"""

import re
import time

import serial

PROMPT = b">"

ERROR_REPLIES = (
    "NO DATA", "CAN ERROR", "BUS ERROR", "BUS INIT", "UNABLE TO CONNECT",
    "STOPPED", "BUFFER FULL", "FB ERROR", "DATA ERROR", "ACT ALERT",
    "LV RESET", "ERR",
)

NRC_NAMES = {
    0x10: "generalReject",
    0x11: "serviceNotSupported",
    0x12: "subFunctionNotSupported",
    0x13: "incorrectMessageLength",
    0x22: "conditionsNotCorrect",
    0x31: "requestOutOfRange",
    0x33: "securityAccessDenied",
    0x7F: "serviceNotSupportedInActiveSession",
}


class ElmError(Exception):
    pass


class NoData(ElmError):
    pass


class NegativeResponse(ElmError):
    def __init__(self, service, nrc):
        self.service = service
        self.nrc = nrc
        super().__init__("NRC 0x%02X %s" % (nrc, NRC_NAMES.get(nrc, "")))


def open_port(port, baudrate=38400, timeout=1.0):
    """`port` is a serial device (/dev/rfcomm0, /dev/ttyUSB0, COM5) or a
    pyserial URL such as socket://192.168.0.10:35000 for WiFi adapters."""
    if "://" in port:
        return serial.serial_for_url(port, timeout=timeout)
    return serial.Serial(port, baudrate=baudrate, timeout=timeout)


def parse_can_frames(text):
    """Parse ELM327 output with headers on (ATH1, 11-bit CAN) into a list of
    (header, frame_bytes). Works with spaces on or off."""
    frames = []
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip().replace(" ", "").upper()
        if not line or not re.fullmatch(r"[0-9A-F]+", line) or len(line) < 5:
            continue
        header, payload = line[:3], line[3:]
        if len(payload) % 2:
            continue
        frames.append((header, bytes.fromhex(payload)))
    return frames


def assemble_isotp(frames):
    """Join ISO-TP frames (from one ECU) into UDS response messages."""
    messages = []
    buf, expected = None, 0
    for frame in frames:
        if not frame:
            continue
        kind = frame[0] >> 4
        if kind == 0:  # single frame
            length = frame[0] & 0x0F
            messages.append(bytes(frame[1:1 + length]))
            buf = None
        elif kind == 1:  # first frame
            expected = ((frame[0] & 0x0F) << 8) | frame[1]
            buf = bytearray(frame[2:])
        elif kind == 2 and buf is not None:  # consecutive frame
            buf.extend(frame[1:])
            if len(buf) >= expected:
                messages.append(bytes(buf[:expected]))
                buf = None
    if buf is not None:
        raise ElmError("incomplete multi-frame response")
    return messages


def response_header(request_header):
    """Physical response id of an 11-bit request id (request + 8)."""
    return "%03X" % (int(request_header, 16) + 8)


class Elm327:
    def __init__(self, port, baudrate=38400, timeout=2.0, log=None):
        self.port_name = port
        self.ser = open_port(port, baudrate, timeout=0.2)
        self.timeout = timeout
        self.log = log  # optional callable(direction, text)
        self.header = None

    def close(self):
        self.ser.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def command(self, cmd, timeout=None):
        """Send one line and return the reply text up to the '>' prompt."""
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r").encode("ascii"))
        if self.log:
            self.log(">>", cmd)
        deadline = time.monotonic() + (timeout or self.timeout)
        data = b""
        while time.monotonic() < deadline:
            chunk = self.ser.read(256)
            if chunk:
                data += chunk
                if PROMPT in chunk:
                    break
        else:
            raise ElmError("timeout waiting for reply to %r" % cmd)
        text = data.split(PROMPT)[0].decode("ascii", "replace")
        text = text.replace("\x00", "")
        # Drop the echoed command if echo is still on
        lines = [ln.strip() for ln in text.replace("\r", "\n").split("\n")]
        lines = [ln for ln in lines if ln and ln.upper() != cmd.upper()]
        reply = "\n".join(lines)
        if self.log:
            self.log("<<", reply)
        return reply

    def initialize(self):
        """Reset the adapter and set it up for 11-bit 500 kbps CAN."""
        self.command("ATZ", timeout=5)
        for cmd in ("ATE0", "ATL0", "ATS1", "ATH1", "ATSP6", "ATCAF1"):
            reply = self.command(cmd)
            if "OK" not in reply.upper():
                raise ElmError("%s failed: %r" % (cmd, reply))
        self.header = None
        return self.adapter_info()

    def adapter_info(self):
        info = {"version": self.command("ATI")}
        try:
            info["voltage"] = self.command("ATRV")
        except ElmError:
            info["voltage"] = ""
        return info

    def set_header(self, header):
        if header == self.header:
            return
        reply = self.command("ATSH" + header)
        if "OK" not in reply.upper():
            raise ElmError("ATSH%s failed: %r" % (header, reply))
        self.header = header

    def read_did(self, header, did):
        """UDS ReadDataByIdentifier (0x22). Returns the data bytes after the
        echoed DID."""
        self.set_header(header)
        reply = self.command("22%04X" % did)
        upper = reply.upper()
        if upper.strip() == "?":
            raise ElmError("adapter rejected request")
        for err in ERROR_REPLIES:
            if err in upper:
                raise NoData(err)
        rx = response_header(header)
        frames = [f for h, f in parse_can_frames(reply) if h == rx]
        if not frames:
            raise NoData("no frames from %s in %r" % (rx, reply))
        for msg in assemble_isotp(frames):
            if len(msg) >= 3 and msg[0] == 0x7F and msg[1] == 0x22:
                if msg[2] == 0x78:  # response pending, real answer follows
                    continue
                raise NegativeResponse(0x22, msg[2])
            if len(msg) >= 3 and msg[0] == 0x62 and \
                    (msg[1] << 8 | msg[2]) == did:
                return msg[3:]
        raise NoData("unexpected reply %r" % reply)
