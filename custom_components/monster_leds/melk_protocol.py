"""Lotus Lamp X "MELK-OF21" Bluetooth strip protocol.

Pure Python (no Home Assistant imports). Decoded from Android HCI snoop
captures of the Lotus Lamp X app; see README.md.

Every command is a single 9-byte write to characteristic FFF3 (write without
response). There are no notifications and no encryption.
"""
from __future__ import annotations

import datetime

WRITE_UUID = "0000fff3-0000-1000-8000-00805f9b34fb"
NAME_PREFIX = "MELK-OF21"
DEFAULT_CELLS = 28  # the strip reports 0x1C segments

RGB = tuple[int, int, int]


def _clamp(x: float) -> int:
    return max(0, min(255, int(round(x))))


def cmd_set_time(now: datetime.datetime | None = None) -> bytes:
    """7E 06 83 hh mm ss weekday 00 EF (the app sends this right after connecting)."""
    now = now or datetime.datetime.now()
    return bytes([0x7E, 0x06, 0x83, now.hour, now.minute, now.second, now.isoweekday(), 0x00, 0xEF])


def cmd_query() -> bytes:
    """Ask for the strip configuration; read FFF3 afterwards (7E 07 37 <cells> ...)."""
    return bytes.fromhex("7e0737ffffff00ffef")


def parse_query_reply(data: bytes) -> int | None:
    """Number of segments from the FFF3 read after cmd_query(), e.g. 7E 07 37 1C ..."""
    if len(data) >= 4 and data[0] == 0x7E and data[2] == 0x37 and 0 < data[3] < 0xFF:
        return data[3]
    return None


def cmd_power(on: bool) -> bytes:
    return bytes.fromhex("7e0704ff00010201ef" if on else "7e07040000000201ef")


def cmd_color(rgb: RGB) -> bytes:
    """Whole strip one color: 7E 07 05 03 RR GG BB 10 EF."""
    r, g, b = (_clamp(c) for c in rgb)
    return bytes([0x7E, 0x07, 0x05, 0x03, r, g, b, 0x10, 0xEF])


def cmd_brightness(percent: float) -> bytes:
    """Strip brightness 0-100 %: 7E 04 01 PP 01 FF 02 01 EF."""
    pct = max(0, min(100, int(round(percent))))
    return bytes([0x7E, 0x04, 0x01, pct, 0x01, 0xFF, 0x02, 0x01, 0xEF])


def cmd_effect(effect_id: int) -> bytes:
    """Built-in effect: 7E 07 03 <id> 06 FF FF 00 EF."""
    return bytes([0x7E, 0x07, 0x03, effect_id & 0xFF, 0x06, 0xFF, 0xFF, 0x00, 0xEF])


def cmd_segments(cells: list[RGB]) -> list[bytes]:
    """Static per-segment colors (the app's segment painter).

    Sequence: 7E 07 37 <n> 00 01 01 00 EF, then a header 7E 32 <3n> 00 CK_HI CK_LO FF FF EF,
    then n/2 packets 7E 33 R G B R G B <index 1..>, then 7E 34 00 00 64 FF FF FF EF.
    CK is the 16-bit sum of every byte of the header (without CK), the 7E 33 packets
    and the 7E 34 packet.
    """
    cells = list(cells)
    if len(cells) % 2:
        cells.append(cells[-1])
    n = len(cells)
    start = bytes([0x7E, 0x07, 0x37, n, 0x00, 0x01, 0x01, 0x00, 0xEF])
    pairs = []
    for i in range(n // 2):
        a, b = cells[2 * i], cells[2 * i + 1]
        pairs.append(bytes([0x7E, 0x33, *(_clamp(c) for c in a), *(_clamp(c) for c in b), i + 1]))
    tail = bytes.fromhex("7e34000064ffffffef")
    head_wo_ck = bytes([0x7E, 0x32, (3 * n) & 0xFF, 0x00, 0xFF, 0xFF, 0xEF])
    ck = (sum(head_wo_ck) + sum(sum(x) for x in pairs) + sum(tail)) & 0xFFFF
    header = bytes([0x7E, 0x32, (3 * n) & 0xFF, 0x00, ck >> 8, ck & 0xFF, 0xFF, 0xFF, 0xEF])
    return [start, header, *pairs, tail]
