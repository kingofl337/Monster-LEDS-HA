"""Checks melk_protocol.py against packets captured from the Lotus Lamp X app.

Run with:  python -m pytest tests   (or just: python tests/test_melk_protocol.py)
"""
import datetime
import importlib.util
import pathlib
import sys

_PATH = pathlib.Path(__file__).parents[1] / "custom_components" / "monster_leds" / "melk_protocol.py"
_spec = importlib.util.spec_from_file_location("monster_leds_melk_protocol", _PATH)
m = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = m
_spec.loader.exec_module(m)

# Two complete segment tables as the app sent them (start, header with checksum, 14 pairs, tail)
BURST_FIRST = [
    "7e07371c00010100ef", "7e3254003ae8ffffef", "7e330000ff0000ff01", "7e330000ffff000002",
    "7e3300ffff80ff0003", "7e33ff008000ff0004", "7e33ff000000ffff05", "7e338000ffff00ff06",
    "7e33ff00000080ff07", "7e3300ffffff000008", "7e3300ff000080ff09", "7e330080ff0080ff0a",
    "7e3300ff8000ff000b", "7e33ff00ff80ff000c", "7e3300ffffff00800d", "7e330080ff00ff800e",
    "7e34000064ffffffef",
]
BURST_LAST = [
    "7e07371c00010100ef", "7e32540034ebffffef", "7e330000ff0000ff01", "7e330000ff0000ff02",
    "7e330000ff0000ff03", "7e330000ff0000ff04", "7e330000ff0000ff05", "7e330000ff0000ff06",
    "7e33ff00000080ff07", "7e3300ffffff000008", "7e3300ff000080ff09", "7e330080ff0080ff0a",
    "7e330000ff00ff000b", "7e33ff00ff80ff000c", "7e3300ffff0000ff0d", "7e330080ff0000ff0e",
    "7e34000064ffffffef",
]


def cells_of(burst):
    out = []
    for packet in burst[2:-1]:
        b = bytes.fromhex(packet)
        out += [tuple(b[2:5]), tuple(b[5:8])]
    return out


def test_segment_tables_and_checksum():
    for burst in (BURST_FIRST, BURST_LAST):
        assert [x.hex() for x in m.cmd_segments(cells_of(burst))] == burst


def test_power_color_brightness():
    assert m.cmd_power(True).hex() == "7e0704ff00010201ef"
    assert m.cmd_power(False).hex() == "7e07040000000201ef"
    assert m.cmd_color((255, 0, 0)).hex() == "7e070503ff000010ef"
    assert m.cmd_color((0, 255, 0)).hex() == "7e07050300ff0010ef"
    assert m.cmd_color((0, 0, 255)).hex() == "7e0705030000ff10ef"
    assert m.cmd_color((255, 255, 255)).hex() == "7e070503ffffff10ef"
    assert m.cmd_brightness(3).hex() == "7e04010301ff0201ef"
    assert m.cmd_brightness(50).hex() == "7e04013201ff0201ef"
    assert m.cmd_brightness(100).hex() == "7e04016401ff0201ef"


def test_connect_sequence():
    when = datetime.datetime(2026, 11, 27, 11, 21, 58)
    assert m.cmd_set_time(when).hex() == "7e06830b153a0500ef"
    assert m.cmd_query().hex() == "7e0737ffffff00ffef"
    assert m.parse_query_reply(bytes.fromhex("7e07371c00ff0088ef")) == 28


def test_effect_command():
    assert m.cmd_effect(0xD4).hex() == "7e0703d406ffff00ef"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)
