import pytest

from deepal_s05.elm327 import Elm327


class FakeSerial:
    """Serial stand-in: answers commands from a script and streams frames
    after ATMA until any character arrives."""

    def __init__(self, stream):
        self.written = []
        self.stream = list(stream)
        self.pending = b""
        self.monitoring = False

    def reset_input_buffer(self):
        self.pending = b""

    def write(self, data):
        self.written.append(data)
        cmd = data.decode().strip()
        if cmd == "ATMA":
            self.monitoring = True
        elif self.monitoring:
            self.monitoring = False
            self.pending = b"\r>"
        else:
            self.pending = b"OK\r\r>"

    def read(self, size):
        if self.monitoring and self.stream:
            return self.stream.pop(0)
        data, self.pending = self.pending, b""
        return data

    def close(self):
        pass


def test_monitor_collects_lines_and_stops():
    stream = [b"7A1 03 22 F2 2F 00 00 00 00\r7A9 04 62",
              b" F2 2F 48 00 00 00\r", b"BUFFER FULL\r"]
    ser = FakeSerial(stream)
    elm = Elm327("fake", ser=ser)
    lines = []
    overflows = elm.monitor(lines.append, duration=0.2)
    assert lines == ["7A1 03 22 F2 2F 00 00 00 00",
                     "7A9 04 62 F2 2F 48 00 00 00"]
    assert overflows == 1
    assert ser.written[-1] == b"\r"  # monitoring was stopped


def test_setup_monitor_commands():
    ser = FakeSerial([])
    elm = Elm327("fake", ser=ser)
    assert elm.setup_monitor("7A1", "7F7") == []
    sent = [w.decode().strip() for w in ser.written]
    assert sent == ["ATCRA", "ATCAF0", "ATCF7A1", "ATCM7F7", "ATCSM1"]


@pytest.mark.parametrize("payload", ["2EF22F01", "3101FF00", "14FFFFFF",
                                     "1101", "2701", "1002"])
def test_unsafe_requests_are_refused(payload):
    elm = Elm327("fake", ser=FakeSerial([]))
    with pytest.raises(ValueError):
        elm.request("7A1", payload)
