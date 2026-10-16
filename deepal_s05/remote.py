"""Viewing the car from anywhere: the Pi copies its data folder to a
server (pi/obd `vps sync`), and the server shows the same pages from
those files with `serve`, behind a password. Nothing on the server
talks to the car; the "live" page shows the newest recorded row.
"""

import csv
import datetime
import glob
import json
import os
import re

ARRAY_KEY = re.compile(r"^(cells|temps)_(\d+)$")


class LatestFile:
    """Stands in for the dashboard poller: /data is the last row of the
    newest drive_*.csv, marked with its age."""

    def __init__(self, data_dir):
        self.data_dir = data_dir

    def latest_row(self):
        paths = glob.glob(os.path.join(self.data_dir, "drive_*.csv"))
        if not paths:
            return None
        path = max(paths, key=os.path.getmtime)
        last = None
        with open(path, newline="", encoding="utf-8") as f:
            for last in csv.DictReader(f):
                pass
        return last

    def json(self):
        row = self.latest_row()
        data = {"time": None, "values": {}, "arrays": {}, "errors": {},
                "alerts": [], "error": None, "files": True, "info": {},
                "labels": {}, "custom": [],
                "status": "ยังไม่มีข้อมูลจาก Pi"}
        if row:
            arrays = {}
            for k, v in row.items():
                m = ARRAY_KEY.match(k or "")
                try:
                    num = float(v)
                except (TypeError, ValueError):
                    continue
                if m:
                    arrays.setdefault(m.group(1), {})[int(m.group(2))] = num
                elif k not in ("elapsed_s",):
                    data["values"][k] = num
            data["arrays"] = {k: [v[i] for i in sorted(v)]
                              for k, v in arrays.items()}
            try:
                t = datetime.datetime.fromisoformat(row["time"])
                data["time"] = t.timestamp()
                data["status"] = "ข้อมูลล่าสุดที่ Pi ส่งมา (%s)" % \
                    t.strftime("%d/%m %H:%M")
            except (KeyError, ValueError):
                pass
        return json.dumps(data, ensure_ascii=False)
