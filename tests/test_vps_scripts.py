"""pi/vps-cron.sh must never lose the VPS owner's other cron jobs."""

import os
import subprocess

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "pi", "vps-cron.sh")
FAKE = """#!/bin/sh
if [ "$1" = "-l" ]; then
    [ "${FAIL:-0}" = 1 ] && { echo "crontab: permission denied" >&2; exit 1; }
    [ -f "$CRONFILE" ] || { echo "no crontab for user" >&2; exit 1; }
    cat "$CRONFILE"
elif [ "$1" = "-" ]; then
    cat > "$CRONFILE"
fi
"""
BOT = "*/5 * * * * /home/u/bot/run.sh\n0 3 * * * /home/u/bot/clean.sh\n"


@pytest.fixture
def run(tmp_path):
    (tmp_path / "bin").mkdir()
    fake = tmp_path / "bin" / "crontab"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    cron = tmp_path / "cron"

    def go(fail=False):
        env = dict(os.environ, HOME=str(tmp_path / "home"),
                   PATH="%s:%s" % (tmp_path / "bin", os.environ["PATH"]),
                   CRONFILE=str(cron), FAIL="1" if fail else "0")
        return subprocess.run(["sh", SCRIPT, "8080"], env=env,
                              capture_output=True, text=True).returncode
    return go, cron


def test_keeps_other_jobs_and_adds_one_line(run):
    go, cron = run
    cron.write_text(BOT)
    assert go() == 0 and go() == 0          # twice: still one line
    lines = cron.read_text().splitlines()
    assert lines[:2] == BOT.splitlines()
    assert sum("vps-serve.sh" in l for l in lines) == 1


def test_unreadable_crontab_is_left_alone(run):
    go, cron = run
    cron.write_text(BOT)
    assert go(fail=True) == 1
    assert cron.read_text() == BOT


def test_no_crontab_yet(run):
    go, cron = run
    assert go() == 0
    assert "vps-serve.sh 8080" in cron.read_text()
