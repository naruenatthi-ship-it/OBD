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


OBD = os.path.join(os.path.dirname(__file__), "..", "pi", "obd")


@pytest.fixture
def auto(tmp_path):
    """Runs `obd vps auto` with fake nmcli/rsync/ssh/sleep."""
    bin_ = tmp_path / "obin"
    bin_.mkdir()
    for name, body in {
            "nmcli": 'echo "$WIFI:wlan0"',
            "rsync": 'echo rsync >> "$CALLS"; exit "${RSYNC_RC:-0}"',
            "ssh": 'echo ssh >> "$CALLS"',
            "sleep": "exit 0"}.items():
        (bin_ / name).write_text("#!/bin/sh\n" + body + "\n")
        (bin_ / name).chmod(0o755)
    data = tmp_path / "data"
    data.mkdir()

    def go(wifi, hotspot="on", rsync_rc=0, host="u@vps"):
        conf = tmp_path / "conf"
        conf.write_text("VPS_HOST=%s\nVPS_HOTSPOT=%s\n" % (host, hotspot))
        calls = tmp_path / "calls"
        calls.write_text("")
        env = dict(os.environ, OBD_CONF=str(conf), OBD_DATA=str(data),
                   PATH="%s:%s" % (bin_, os.environ["PATH"]), WIFI=wifi,
                   CALLS=str(calls), RSYNC_RC=str(rsync_rc))
        rc = subprocess.run(["bash", OBD, "vps", "auto"], env=env,
                            capture_output=True, text=True).returncode
        state = data / ".vps_state"
        return (rc, state.read_text().split()[0] if state.exists() else None,
                calls.read_text().split())
    return go


def test_vps_auto_sends_on_any_wifi_by_default(auto):
    rc, state, calls = auto("hotspot")
    assert rc == 0 and state == "ok" and calls.count("rsync") == 2


def test_vps_auto_can_skip_the_phone_hotspot(auto):
    assert auto("hotspot", hotspot="off") == (0, "skip", [])
    rc, state, calls = auto("home", hotspot="off")
    assert rc == 0 and state == "ok"


def test_vps_auto_retries_then_reports_failure(auto):
    rc, state, calls = auto("home", rsync_rc=1)
    assert rc == 1 and state == "fail" and calls.count("rsync") == 3


def test_vps_auto_quiet_without_vps(auto):
    assert auto("home", host="") == (0, None, [])
