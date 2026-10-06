import pytest

from deepal_s05.elm327 import (
    ElmError, assemble_isotp, parse_can_frames, response_header)


def test_single_frame_with_spaces():
    frames = parse_can_frames("7A9 04 62 F2 2F 48\r\r")
    assert frames == [("7A9", bytes.fromhex("0462F22F48"))]
    assert assemble_isotp([f for _, f in frames]) == [
        bytes.fromhex("62F22F48")]


def test_single_frame_without_spaces_and_padding():
    frames = parse_can_frames("7A90462F22F48AAAA")
    assert assemble_isotp([f for _, f in frames]) == [
        bytes.fromhex("62F22F48")]


def test_multi_frame():
    text = ("7A9 10 0B 62 F2 80 01 02 03\n"
            "7A9 21 04 05 06 07 08 AA AA\n")
    frames = [f for _, f in parse_can_frames(text)]
    assert assemble_isotp(frames) == [
        bytes.fromhex("62F2800102030405060708")]


def test_incomplete_multi_frame():
    frames = [f for _, f in parse_can_frames("7A9 10 0B 62 F2 80 01 02 03")]
    with pytest.raises(ElmError):
        assemble_isotp(frames)


def test_non_frame_lines_ignored():
    assert parse_can_frames("SEARCHING...\nNO DATA\nOK") == []


def test_response_header():
    assert response_header("7A1") == "7A9"
    assert response_header("7E0") == "7E8"
