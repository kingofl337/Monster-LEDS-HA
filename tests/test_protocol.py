"""Checks protocol.py against packets captured from the Surplife app.

Run with:  python -m pytest tests   (or just: python tests/test_protocol.py)
"""
import datetime
import importlib.util
import pathlib
import sys

# Load protocol.py directly so the tests don't need Home Assistant installed
_PATH = pathlib.Path(__file__).parents[1] / "custom_components" / "surplife" / "protocol.py"
_spec = importlib.util.spec_from_file_location("surplife_protocol", _PATH)
p = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = p
_spec.loader.exec_module(p)

# Writes captured from the app (sequence number is byte 2)
SET_TIME = "00018000000c0d0a10141a0b1b09080405000f8d"
STATUS = "000380000002030aea81"
OFF = "001880000002030a7124"
ON = "001980000002030a7123"
LED1_RED = "000580000012130ae10d00015064000064646480000000000000"
LED2_50_GREEN = "000780000012130ae10d00015064003c64646440000000000040"
LED39_WHITE = "001280000012130ae10d00015064000000646400000000020000"
CHRISTMAS = "00168000001f200ae1010064660001645000a100000004a1006464a1000064a13c6464a1000064"
RAINBOW = ("001c80000027280ae1010064650001645000a100000006a1006464a11e6464a13c6464a15a6464"
           "a1786464a1966464")
FRAME = [
    (
        "401300000103af0ae104010001506400320000646464003c646464007864646400786464640000000064"
        "001e646464000000006400000000640000000064000000006400000000640000000064005a6464640000"
        "000064000000006400000000640000000064000000006400000000640000000064000000006400000000"
        "640000000064000000006400966464640000000064000000006400000000640000000064000000006400"
        "0000006400000000640000000064"
    ),
    (
        "401380015500786464640000000064000000006400000000640000000064000000646400000000640000"
        "000064000000006400000000640000000064000000006400000000640000000064000000006400000000"
        "64003c646464"
    ),
]
STATUS_REPLY = "04068000001b1c15ea810100aa0c23660164f00064640000010032ffffffff04000001"
ADV_MFR = "8024256550f00064640000010032ffffffff040000010000000200"

RED, GREEN, BLUE, YELLOW, CYAN, MAGENTA = (0, 100, 100), (120, 100, 100), (240, 100, 100), \
    (60, 100, 100), (180, 100, 100), (300, 100, 100)
WHITE, BLACK = (0, 0, 100), (0, 0, 0)


def hexes(parts):
    return [x.hex() for x in parts]


def test_set_time():
    when = datetime.datetime(2026, 11, 27, 9, 8, 4)
    assert hexes(p.wrap(p.cmd_set_time(when), 1)) == [SET_TIME]


def test_status_and_power():
    assert hexes(p.wrap(p.cmd_status(), 3)) == [STATUS]
    assert hexes(p.wrap(p.cmd_power(False), 0x18)) == [OFF]
    assert hexes(p.wrap(p.cmd_power(True), 0x19)) == [ON]


def test_single_leds():
    assert hexes(p.wrap(p.cmd_leds([1], RED, 50), 5)) == [LED1_RED]
    assert hexes(p.wrap(p.cmd_leds([2, 50], GREEN, 50), 7)) == [LED2_50_GREEN]
    assert hexes(p.wrap(p.cmd_leds([39], WHITE, 50), 0x12)) == [LED39_WHITE]


def test_full_frame_two_segments():
    frame = [BLACK] * 50
    for led, color in [(1, RED), (2, GREEN), (3, BLUE), (4, BLUE), (6, YELLOW), (13, CYAN),
                       (25, MAGENTA), (34, BLUE), (39, WHITE), (50, GREEN)]:
        frame[led - 1] = color
    assert hexes(p.wrap(p.cmd_frame(frame), 0x13)) == FRAME


def test_scenes():
    assert hexes(p.wrap(p.cmd_scene(0x66, [RED, WHITE, GREEN, WHITE]), 0x16)) == [CHRISTMAS]
    assert hexes(p.wrap(p.cmd_scene(0x65, [RED, YELLOW, GREEN, CYAN, BLUE, MAGENTA]), 0x1C)) == [RAINBOW]


def test_small_mtu_segments_fit_and_reassemble():
    payload = p.cmd_frame([RED] * 50)
    chunk = p.chunk_for_mtu(23)
    parts = p.wrap(payload, 9, chunk)
    assert max(len(x) for x in parts) <= 20
    body = parts[0][8:] + b"".join(x[5:] for x in parts[1:])
    assert body == payload
    assert parts[-1][2] == 0x80 and all(x[2] == 0 for x in parts[:-1])


def test_parse_status_reply():
    body = p.unwrap_notification(bytes.fromhex(STATUS_REPLY))
    st = p.parse_status_reply(body)
    assert st.is_on and st.mode == p.MODE_PIXELS and st.led_count == 50


def test_parse_advertisement():
    st = p.parse_advertisement({p.MANUFACTURER_ID: bytes.fromhex(ADV_MFR)})
    assert not st.is_on and st.mode == p.MODE_SCENE and st.effect_id == 0x65 and st.led_count == 50


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)


def test_all_leds_bitmask_is_one_short_write():
    payload = p.cmd_leds(list(range(1, 51)), (0, 100, 50), 50)
    assert payload.hex() == "e10d000150640000643264ffffffffffffc0"
    assert len(p.wrap(payload, 1, p.APP_CHUNK)) == 1
