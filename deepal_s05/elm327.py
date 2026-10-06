"""Minimal ELM327 client for reading UDS DIDs (service 0x22) over CAN.

Only read-type requests are exposed: ReadDataByIdentifier (0x22),
ReadDTCInformation by status mask (0x19 02), switching
between the default and extended diagnostic session (0x10 01/03) and
TesterPresent (0x3E). The client never sends write, routine, security
access, reset or DTC-clear services to the vehicle.
"""

import re
import socket
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


class SocketPort:
    """The part of the pyserial interface Elm327 uses, over a connected
    stream socket (Bluetooth RFCOMM)."""

    def __init__(self, sock, timeout=0.2):
        self.sock = sock
        self.timeout = timeout
        sock.settimeout(timeout)

    def read(self, size=1):
        try:
            return self.sock.recv(size)
        except socket.timeout:
            return b""

    def write(self, data):
        self.sock.sendall(data)
        return len(data)

    def reset_input_buffer(self):
        self.sock.setblocking(False)
        try:
            while self.sock.recv(4096):
                pass
        except OSError:  # nothing waiting (BlockingIOError is an OSError)
            pass
        finally:
            self.sock.settimeout(self.timeout)

    def close(self):
        self.sock.close()


RFCOMM_URL = re.compile(r"^rfcomm://((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})"
                        r"(?::(\d+))?$")


def open_rfcomm(url, timeout=0.2):
    """rfcomm://AA:BB:CC:DD:EE:FF[:channel] connects straight to a paired
    Bluetooth adapter (Linux, Windows) without binding /dev/rfcomm0."""
    m = RFCOMM_URL.match(url)
    if not m:
        raise ValueError("ใช้รูปแบบ rfcomm://AA:BB:CC:DD:EE:FF หรือ "
                         "rfcomm://AA:BB:CC:DD:EE:FF:1")
    sock = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM,
                         socket.BTPROTO_RFCOMM)
    sock.settimeout(15)
    try:
        sock.connect((m.group(1), int(m.group(2) or 1)))
    except OSError:
        sock.close()
        raise
    return SocketPort(sock, timeout)


def open_port(port, baudrate=38400, timeout=1.0):
    """`port` is a serial device (/dev/rfcomm0, /dev/ttyUSB0, COM5), a
    pyserial URL such as socket://192.168.0.10:35000 for WiFi adapters, or
    rfcomm://<MAC> for a paired Bluetooth adapter."""
    if port.startswith("rfcomm://"):
        return open_rfcomm(port, timeout)
    if "://" in port:
        return serial.serial_for_url(port, timeout=timeout)
    return serial.Serial(port, baudrate=baudrate, timeout=timeout)


def parse_can_frames(text, header_len=3):
    """Parse ELM327 output with headers on (ATH1) into a list of
    (header, frame_bytes). `header_len` is 3 hex digits for 11-bit CAN and
    8 for 29-bit CAN. Works with spaces on or off."""
    frames = []
    for line in text.replace("\r", "\n").split("\n"):
        line = line.strip().replace(" ", "").upper()
        if not line or not re.fullmatch(r"[0-9A-F]+", line) or \
                len(line) < header_len + 2:
            continue
        header, payload = line[:header_len], line[header_len:]
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
    """Response id for a request id.

    11-bit: request + 8 (7A1 -> 7A9). 29-bit physical (18DA<target><tester>):
    target and tester swap (18DA01F1 -> 18DAF101). 29-bit functional
    (18DB....) has no single responder: None."""
    if len(request_header) == 8:
        if request_header[2:4].upper() == "DA":
            return (request_header[:4] + request_header[6:8] +
                    request_header[4:6]).upper()
        return None
    return "%03X" % (int(request_header, 16) + 8)


def is_29bit(header):
    return len(header) == 8


# Requests the client is allowed to put on the bus (hex, no spaces)
ALLOWED_REQUESTS = re.compile(
    r"^(22[0-9A-F]{4}|1902[0-9A-F]{2}|1001|1003|3E00)$")


class Elm327:
    def __init__(self, port, baudrate=38400, timeout=2.0, log=None,
                 ser=None, protocol="6"):
        """protocol: ELM327 ATSP number, "6" = CAN 11-bit 500 kbps (Deepal),
        "7" = CAN 29-bit 500 kbps (Honda)."""
        self.port_name = port
        self.ser = ser or open_port(port, baudrate, timeout=0.2)
        self.timeout = timeout
        self.log = log  # optional callable(direction, text)
        self.protocol = str(protocol)
        self.header_len = 8 if self.protocol in ("7", "9") else 3
        self.header = None
        self.cra_supported = True

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
        """Reset the adapter and set it up for the CAN protocol given at
        construction (11-bit or 29-bit, 500 kbps)."""
        self.command("ATZ", timeout=5)
        for cmd in ("ATE0", "ATL0", "ATS1", "ATH1", "ATSP" + self.protocol,
                    "ATCAF1"):
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

    def battery_voltage(self):
        """12 V battery voltage measured by the adapter at the OBD port."""
        reply = self.command("ATRV")
        m = re.search(r"(\d+(?:\.\d+)?)\s*V", reply.upper())
        if not m:
            raise ElmError("ATRV: %r" % reply)
        return float(m.group(1))

    def _ok(self, cmd):
        reply = self.command(cmd)
        if "OK" not in reply.upper():
            raise ElmError("%s failed: %r" % (cmd, reply))

    def set_header(self, header):
        """Address requests to `header` and only accept its response id.
        For 29-bit ids the priority byte goes through ATCP and the rest
        through ATSH; functional 29-bit requests accept any 18DAF1xx."""
        header = header.upper()
        if header == self.header:
            return
        if is_29bit(header):
            self._ok("ATCP" + header[:2])
            self._ok("ATSH" + header[2:])
        else:
            self._ok("ATSH" + header)
        rx = response_header(header)
        if self.cra_supported:
            if rx is not None:
                reply = self.command("ATCRA" + rx)
                if "OK" not in reply.upper():
                    self.cra_supported = False
            else:
                try:
                    self._ok("ATCRA")  # clear a single-id filter first
                    self._ok("ATCF" + header[:2] + "DA" + header[6:8] + "00")
                    self._ok("ATCM1FFFFF00")
                except ElmError:
                    self.cra_supported = False
        self.header = header

    def request(self, header, payload):
        """Send one allowed UDS request and return the response messages
        (bytes, starting with the response SID) from the ECU."""
        if not ALLOWED_REQUESTS.match(payload):
            raise ValueError("request %s is not allowed" % payload)
        self.set_header(header)
        reply = self.command(payload)
        upper = reply.upper()
        if upper.strip() == "?":
            raise ElmError("adapter rejected request")
        for err in ERROR_REPLIES:
            if err in upper:
                raise NoData(err)
        rx = response_header(header.upper())
        by_sender = {}
        for h, f in parse_can_frames(reply, self.header_len):
            if h == rx or (rx is None and
                           h.startswith(header[:2] + "DA" + header[6:8])):
                by_sender.setdefault(h, []).append(f)
        if not by_sender:
            raise NoData("no frames from %s in %r" % (rx, reply))
        messages = []
        for frames in by_sender.values():
            messages += assemble_isotp(frames)
        return messages, reply

    def _positive(self, header, payload):
        sid = int(payload[:2], 16)
        messages, reply = self.request(header, payload)
        for msg in messages:
            if len(msg) >= 3 and msg[0] == 0x7F and msg[1] == sid:
                if msg[2] == 0x78:  # response pending, real answer follows
                    continue
                raise NegativeResponse(sid, msg[2])
            if msg and msg[0] == sid + 0x40:
                return msg
        raise NoData("unexpected reply %r" % reply)

    def read_did(self, header, did):
        """UDS ReadDataByIdentifier (0x22). Returns the data bytes after the
        echoed DID."""
        msg = self._positive(header, "22%04X" % did)
        if len(msg) < 3 or (msg[1] << 8 | msg[2]) != did:
            raise NoData("reply for another DID: %s" % msg.hex().upper())
        return msg[3:]

    def read_dtcs(self, header, mask=0xFF):
        """ReadDTCInformation, reportDTCByStatusMask. Returns the raw
        positive response (59 02 ...)."""
        return self._positive(header, "1902%02X" % mask)

    def start_session(self, header, extended=True):
        """DiagnosticSessionControl: extended (0x03) or default (0x01)."""
        return self._positive(header, "1003" if extended else "1001")

    def tester_present(self, header):
        return self._positive(header, "3E00")

    def setup_monitor(self, can_filter="700", can_mask="700"):
        """Prepare a passive capture: raw frames (PCI bytes visible), no
        acknowledgements on the bus, only ids matching filter/mask."""
        warnings = []
        for cmd in ("ATCRA", "ATCAF0", "ATCF" + can_filter,
                    "ATCM" + can_mask):
            reply = self.command(cmd)
            if "OK" not in reply.upper():
                raise ElmError("%s failed: %r" % (cmd, reply))
        if "OK" not in self.command("ATCSM1").upper():
            warnings.append("กล่องไม่รองรับ ATCSM1 (silent monitoring)")
        self.header = None
        return warnings

    def monitor(self, on_line, duration=None):
        """Run ATMA and call on_line(text) for every received line until
        `duration` seconds pass or Ctrl+C. Restarts after BUFFER FULL.
        Returns the number of buffer overflows."""
        overflows = 0
        start = time.monotonic()
        self.ser.reset_input_buffer()
        self.ser.write(b"ATMA\r")
        buf = b""
        try:
            while duration is None or time.monotonic() - start < duration:
                chunk = self.ser.read(512)
                if not chunk:
                    continue
                buf += chunk
                *lines, buf = buf.replace(b"\n", b"\r").split(b"\r")
                for raw in lines:
                    line = raw.decode("ascii", "replace").strip()
                    if not line or line.upper() == "ATMA":
                        continue
                    if "BUFFER FULL" in line.upper():
                        overflows += 1
                        continue
                    on_line(line)
                if PROMPT in buf:  # adapter stopped monitoring by itself
                    buf = b""
                    self.ser.write(b"ATMA\r")
        finally:
            self.ser.write(b"\r")  # any character stops ATMA
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if PROMPT in self.ser.read(256):
                    break
        return overflows
