"""Switch tests: marks from the dashboard matched with DID bytes."""

import datetime
import json
import threading
import urllib.request

from deepal_s05 import dashboard, marks

T0 = datetime.datetime(2026, 11, 14, 20, 0)


def write_rec(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        f.write("time,did_700_F2F3,did_700_F1C1\n")
        for t, a, b in rows:
            f.write("%s,%s,%s\n" % (t.isoformat(), a, b))


def test_marks_finds_the_byte_that_follows_the_switch(tmp_path):
    # 700 F2F3 byte 1 goes 00 -> 01 a few seconds after each "AC on" and
    # back after each "AC off"; F1C1 counts up all the time (noise)
    rows = []
    ac = 0
    for k in range(240):                      # 40 min, every 10 s
        t = T0 + datetime.timedelta(seconds=10 * k)
        if k in (30, 90, 150):
            marks.add(str(tmp_path), "แอร์ เปิด", t.timestamp())
        if k in (60, 120, 180):
            marks.add(str(tmp_path), "แอร์ ปิด", t.timestamp())
        if k in (31, 91, 151):
            ac = 1
        if k in (61, 121, 181):
            ac = 0
        rows.append((t, "51%02X" % ac, "%04X" % k))
    write_rec(tmp_path / "rec_test_20261114-2000.csv", rows)
    by_event, ms = marks.analyse(str(tmp_path))
    assert len(ms) == 6
    best = by_event["แอร์ เปิด"][0]
    assert best[1:5] == ("did_700_F2F3", 1, 3, 3)
    assert by_event["แอร์ ปิด"][0][1:3] == ("did_700_F2F3", 1)
    text = marks.report(str(tmp_path))
    assert "700 F2F3 ไบต์ 1" in text and "F1C1" not in text


def test_mark_page_posts_a_mark(tmp_path):
    class Poller:
        def json(self):
            return "{}"
    server = dashboard.make_server(Poller(), port=0, data_dir=str(tmp_path),
                                   local=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    try:
        page = urllib.request.urlopen(base + "/mark").read().decode("utf-8")
        assert "แอร์ เปิด" in page
        req = urllib.request.Request(
            base + "/mark", data=json.dumps({"event": "ไฟหน้า เปิด"}).encode(),
            headers={"Content-Type": "application/json",
                     "X-Requested-With": "deepal"})
        reply = json.loads(urllib.request.urlopen(req).read())
        assert reply["time"]
        assert marks.read_marks(str(tmp_path))[0][1] == "ไฟหน้า เปิด"
        result = urllib.request.urlopen(base + "/mark.txt").read()
        assert "ไฟหน้า เปิด" in result.decode("utf-8")
    finally:
        server.shutdown()


def test_autopilot_reads_the_watch_list_fast_during_a_test(tmp_path):
    from deepal_s05 import autopilot
    t = 1_800_000_000.0
    pilot = autopilot.Autopilot(lambda: None, [], [], str(tmp_path),
                                clock=lambda: t, watch=[], set_clock=None)
    assert pilot.watch_pace(t) is None                # no test yet
    marks.add(str(tmp_path), "แอร์ เปิด")
    import os
    now = os.path.getmtime(tmp_path / marks.MARKS_FILE)
    assert pilot.watch_pace(now + 60) == marks.FOCUS_PER_ROUND
    assert pilot.watch_pace(now + marks.FOCUS_S + 1) is None
    assert marks.step_wait(str(tmp_path)) >= 30


def test_guided_test_swaps_the_watch_list_and_puts_it_back(tmp_path):
    from deepal_s05 import autopilot
    d = str(tmp_path)
    (tmp_path / "survey_results.csv").write_text(
        "time,header,did,length,raw\n"
        "2026-10-16T23:00:00,700,FE05,24,00\n"
        "2026-10-16T23:00:01,780,FD0C,2,0000\n")    # FD0C is known
    (tmp_path / "watch_live.csv").write_text("header,did\n7A7,FD18\n")
    assert marks.prepare(d, ("700", "780")) == 1
    assert autopilot.load_watch(d) == [("700", "FE05")]
    marks.prepare(d, ("700",))                       # backup kept once
    marks.restore(d)
    assert autopilot.load_watch(d) == [("7A7", "FD18")]
    assert marks.step_wait(d) >= 30


def test_states_find_the_byte_that_holds_the_mode(tmp_path):
    # 700 FE05 byte 0: EA while in Sport, EB in Eco; read once a minute
    d = str(tmp_path)
    rows, mode = [], "โหมด Eco"
    for k in range(40):
        t = T0 + datetime.timedelta(seconds=60 * k)
        if k % 8 == 0:
            mode = "โหมด Sport" if mode == "โหมด Eco" else "โหมด Eco"
            marks.add(d, mode, t.timestamp())
        v = 0xEA if mode == "โหมด Sport" else 0xEB
        rows.append((t, "%02X%02X" % (v, k % 7), "00"))
    with open(tmp_path / "drive_20261114.csv", "w") as f:
        f.write("time,did_700_FE05,did_700_F1C1\n")
        for t, a, b in rows:
            f.write("%s,%s,%s\n" % (t.isoformat(), a, b))
    found = marks.analyse_states(d)["โหมดขับ"]
    assert found[0][:2] == ("did_700_FE05", 0)
    assert "700 FE05 ไบต์ 0" in marks.report(d)


def test_autopilot_picks_up_a_new_watch_list(tmp_path):
    from deepal_s05 import autopilot
    import os
    (tmp_path / "watch_live.csv").write_text("header,did\n700,F2F3\n")
    pilot = autopilot.Autopilot(lambda: None, [], [], str(tmp_path),
                                set_clock=None)
    assert pilot.watch.keys == [("700", "F2F3")]
    (tmp_path / "watch_live.csv").write_text("header,did\n780,FD1A\n")
    st = os.stat(tmp_path / "watch_live.csv")
    os.utime(tmp_path / "watch_live.csv", (st.st_atime, st.st_mtime + 5))
    pilot.reload_watch()
    assert pilot.watch.keys == [("780", "FD1A")]
