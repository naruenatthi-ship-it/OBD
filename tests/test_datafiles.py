import datetime
import os
import time

from deepal_s05 import cli, dashboard, datafiles


def touch(folder, name, days_old=0):
    path = folder / name
    path.write_text("x", encoding="utf-8")
    t = time.time() - days_old * 86400
    os.utime(path, (t, t))


def test_recorded_time_from_name():
    f = datafiles.recorded_time
    assert f("scan_7A4_742_20261016-1421.csv", 0) == \
        datetime.datetime(2026, 10, 16, 14, 21)
    assert f("charge_20261016_1300.csv", 0) == \
        datetime.datetime(2026, 10, 16, 13, 0)
    assert f("drive_20261016.csv", 0) == datetime.datetime(2026, 10, 16)
    assert f("aux12v_202610.csv", 0) == datetime.datetime(2026, 10, 1)
    assert f("checkup_history.csv", 0) == datetime.datetime.fromtimestamp(0)


def test_entries_sorted_by_recording_not_by_last_write(tmp_path):
    touch(tmp_path, "drive_20261001.csv")            # written just now
    touch(tmp_path, "scan_7A4_20261016-1421.csv", 1)
    touch(tmp_path, "notes.txt")
    names = [e["name"] for e in datafiles.entries(str(tmp_path))]
    assert names == ["scan_7A4_20261016-1421.csv", "drive_20261001.csv"]


def test_cleanup_plan_keeps_recordings_and_history(tmp_path):
    now = datetime.datetime(2026, 12, 31)
    for name in ("bg_20261016-1351.log", "bg_20261230-0900.log",
                 "rec_drive.log", "topology.html", "drive_20260101.csv",
                 "rec_drive_20260101-1214.csv", "scan_7A4_20260101-1000.csv",
                 "charge_20260101_1300.csv", "aux12v_202601.csv",
                 "checkup_history.csv", "dtc_history.json",
                 "check_20260101-1000.json", "check_20260201-1000.json",
                 "check_20260301-1000.json", "check_20260401-1000.json",
                 "check_20261201-1000.json"):
        touch(tmp_path, name, days_old=30)
    plan = {n for n, _, _ in datafiles.cleanup_plan(str(tmp_path), now)}
    assert plan == {"bg_20261016-1351.log", "rec_drive.log",
                    "topology.html", "check_20260201-1000.json",
                    "check_20260101-1000.json"}


def test_clean_command_and_files_page(tmp_path, monkeypatch, capsys):
    touch(tmp_path, "bg_20200101-0000.log")
    touch(tmp_path, "drive_20200101.csv")
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    assert cli.main(["clean", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "bg_20200101-0000.log").exists()
    page = dashboard.files_page(str(tmp_path))
    assert "01/01/2020" in page and "drive_20200101.csv" in page
    assert cli.main(["clean", "--dir", str(tmp_path), "--yes"]) == 0
    assert not (tmp_path / "bg_20200101-0000.log").exists()
    assert (tmp_path / "drive_20200101.csv").exists()
