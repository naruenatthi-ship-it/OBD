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
    finally:
        server.shutdown()
