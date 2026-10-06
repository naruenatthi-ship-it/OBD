from deepal_s05 import analysis


def cells_bytes(mv_values):
    return b"".join(v.to_bytes(2, "big") for v in mv_values)


def test_find_cell_run():
    data = b"\x01" + cells_bytes([3330, 3331, 3329] * 4) + b"\xff\xff"
    run = analysis.find_cell_run(data)
    assert run["count"] == 12
    assert run["offset"] == 1
    assert run["min_v"] == 3.329 and run["max_v"] == 3.331


def test_find_cell_run_rejects_short_or_spread():
    assert analysis.find_cell_run(cells_bytes([3330] * 5)) is None
    assert analysis.find_cell_run(
        cells_bytes([2600, 4200] * 6)) is None


def test_parse_sniff_line():
    assert analysis.parse_sniff_line("7A1 03 22 F2 2F 00 00 00 00") == (
        "7A1", bytes.fromhex("0322F22F00000000"))
    assert analysis.parse_sniff_line("BUFFER FULL") is None


def frames_from(lines):
    out = []
    for i, line in enumerate(lines):
        can_id, data = analysis.parse_sniff_line(line)
        out.append((float(i), can_id, data))
    return out


def sniffed_session():
    """A scanner reading SOC and a 20-byte cell block from the BMS."""
    cells = cells_bytes([3330, 3331, 3329, 3332, 3330, 3328, 3331, 3330,
                         3329, 3330])
    payload = bytes.fromhex("62F2A0") + cells  # 23 bytes
    lines = [
        "7A1 02 10 03 00 00 00 00 00",
        "7A9 06 50 03 00 32 01 F4 00",
        "7A1 03 22 F2 2F 00 00 00 00",
        "7A9 04 62 F2 2F 48 00 00 00",
        "7A1 03 22 F2 A0 00 00 00 00",
        "7A9 10 %02X %s" % (len(payload), payload[:6].hex(" ").upper()),
        "7A1 30 00 00 00 00 00 00 00",
        "7A9 21 %s" % payload[6:13].hex(" ").upper(),
        "7A9 22 %s" % payload[13:20].hex(" ").upper(),
        "7A9 23 %s 00 00 00 00" % payload[20:23].hex(" ").upper(),
        "7A1 03 22 F2 2F 00 00 00 00",
        "7A9 04 62 F2 2F 47 00 00 00",
    ]
    return lines, payload


def test_reassemble_and_summarise():
    lines, payload = sniffed_session()
    messages = analysis.reassemble(frames_from(lines))
    assert [m[2] for m in messages if m[1] == "7A9"][2] == payload
    groups = analysis.summarise(messages)
    by_req = {g["request"].hex().upper(): g for g in groups}
    assert set(by_req) == {"1003", "22F22F", "22F2A0"}
    soc = by_req["22F22F"]
    assert soc["count"] == 2 and soc["changes"]
    assert soc["response_id"] == "7A9"
    cells = by_req["22F2A0"]
    assert cells["last_response"] == payload
    assert cells["cell_run"]["count"] == 10


def test_read_sniff_log(tmp_path):
    lines, _ = sniffed_session()
    log = tmp_path / "sniff.log"
    log.write_text("# header\n" + "".join(
        "%.3f\t%s\n" % (i * 0.01, line) for i, line in enumerate(lines)),
        encoding="utf-8")
    frames = analysis.read_sniff_log(str(log))
    assert len(frames) == len(lines)
    assert frames[1][0] == 0.01
