"""End-to-end test against ELM327-emulator with the deepal_s05 scenario."""

import os
import socket
import sys
import time

import pytest

pytest.importorskip("elm")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "emulator"))

from run_emulator import start  # noqa: E402

from deepal_s05 import pids  # noqa: E402
from deepal_s05.cli import main, read_signals  # noqa: E402
from deepal_s05.elm327 import Elm327, NegativeResponse  # noqa: E402


def free_port():
    with socket.socket() as s:
        s.bind(("localhost", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def url():
    port = free_port()
    with start(port):
        deadline = time.monotonic() + 10
        while True:
            try:
                socket.create_connection(("localhost", port), 0.5).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)
        yield "socket://localhost:%d" % port


def test_read_all_signals(url):
    with Elm327(url) as elm:
        elm.initialize()
        results = read_signals(elm, pids.SIGNALS, 56.1)
    assert all(err is None for _, _, err in results.values())
    assert results["pack_voltage"][1] == pytest.approx(358.6)
    assert results["soh"][1] == pytest.approx(98.7)


def test_negative_response(url):
    with Elm327(url) as elm:
        elm.initialize()
        with pytest.raises(NegativeResponse) as e:
            elm.read_did("7A1", 0xF201)
    assert e.value.nrc == 0x31


def test_cli_check(url, tmp_path, capsys):
    out = tmp_path / "check.json"
    assert main(["--port", url, "check", "--save", str(out)]) == 0
    assert "14 จาก 14" in capsys.readouterr().out
    assert out.exists()
