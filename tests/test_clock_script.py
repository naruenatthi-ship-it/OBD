"""pi/deepal-clock.sh: the clock never starts earlier than the last
known time after a power cut, and is never moved back."""

import os
import subprocess

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "pi", "deepal-clock.sh")
FAKE_DATE = """#!/bin/sh
if [ "$1" = "+%s" ]; then cat "$NOW"; else echo "$2" > "$SET"; fi
"""


def run(tmp_path, action, now, saved=None):
    (tmp_path / "bin").mkdir(exist_ok=True)
    fake = tmp_path / "bin" / "date"
    fake.write_text(FAKE_DATE)
    fake.chmod(0o755)
    (tmp_path / "now").write_text(str(now))
    clock = tmp_path / ".clock"
    if saved is not None:
        clock.write_text(saved)
    setf = tmp_path / "set"
    if setf.exists():
        setf.unlink()
    env = dict(os.environ, PATH="%s:%s" % (tmp_path / "bin", os.environ["PATH"]),
               NOW=str(tmp_path / "now"), SET=str(setf),
               CLOCK_FILE=str(clock))
    rc = subprocess.run(["sh", SCRIPT, action], env=env).returncode
    return rc, (setf.read_text().strip() if setf.exists() else None), clock


def test_restore_moves_a_clock_that_is_behind_forward(tmp_path):
    assert run(tmp_path, "restore", 1000, "2000")[:2] == (0, "@2000")


def test_restore_never_moves_the_clock_back(tmp_path):
    assert run(tmp_path, "restore", 3000, "2000")[:2] == (0, None)
    assert run(tmp_path, "restore", 3000, "junk")[:2] == (0, None)
    (tmp_path / ".clock").unlink()
    assert run(tmp_path, "restore", 3000)[:2] == (0, None)


def test_save_keeps_the_time(tmp_path):
    rc, _, clock = run(tmp_path, "save", 4242)
    assert rc == 0 and clock.read_text().strip() == "4242"
