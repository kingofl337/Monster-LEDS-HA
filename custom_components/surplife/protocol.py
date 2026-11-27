"""Surplife IOTBT Bluetooth light protocol.

Pure Python (no Home Assistant imports) so it can be unit tested on its own.
Decoded from an Android HCI snoop capture of the Surplife app; see README.md.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

WRITE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
MANUFACTURER_ID = 0x5A02
NAME_PREFIX = "IOTBT"

APP_CHUNK = 174  # payload bytes per segment the Surplife app used (MTU 186)
MIN_CHUNK = 9    # fits a 20-byte write (MTU 23)

POWER_ON = 0x23
POWER_OFF = 0x24
MODE_SCENE = 0x25
MODE_PIXELS = 0x66

Color = tuple[float, float, float]  # hue degrees 0-360, saturation 0-100, value 0-100


# ----------------------------------------------------------------- framing
def wrap(payload: bytes, seq: int, chunk: int = APP_CHUNK, typ: int = 0x0A) -> list[bytes]:
    """Wrap a command payload into the BLE writes the controller expects.

    single : 00 SEQ 80 00 LEN_HI LEN_LO LEN+1 TYPE <payload>
    split  : 40 SEQ 00 00 LEN_HI LEN_LO CHUNK+1 TYPE <chunk>   (first)
             40 SEQ 00|80 IDX CHUNK <chunk>                    (middle | last)
    """
    n = len(payload)
    seq &= 0xFF
    if n <= chunk and n + 1 <= 0xFF:
        return [bytes([0x00, seq, 0x80, 0x00, n >> 8, n & 0xFF, n + 1, typ]) + payload]
    parts = [payload[i:i + chunk] for i in range(0, n, chunk)]
    out = []
    for idx, part in enumerate(parts):
        if idx == 0:
            out.append(bytes([0x40, seq, 0x00, 0x00, n >> 8, n & 0xFF, len(part) + 1, typ]) + part)
        else:
            last = 0x80 if idx == len(parts) - 1 else 0x00
            out.append(bytes([0x40, seq, last, idx, len(part)]) + part)
    return out


def chunk_for_mtu(mtu: int | None) -> int:
    """Largest segment payload that fits one ATT write for this MTU."""
    if not mtu or mtu <= 23:
        return MIN_CHUNK
    return max(MIN_CHUNK, min(APP_CHUNK, mtu - 3 - 8))


def unwrap_notification(data: bytes) -> bytes | None:
    """Inner payload of a single-packet reply (replies start with 0x04)."""
    if len(data) >= 8 and data[2] == 0x80 and data[3] == 0x00:
        n = (data[4] << 8) | data[5]
        return bytes(data[8:8 + n])
    return None


# ----------------------------------------------------------------- commands
def _hsv(color: Color) -> bytes:
    h, s, v = color
    return bytes([int(round(h / 2)) % 180, _pct(s), _pct(v)])


def _pct(x: float) -> int:
    return max(0, min(100, int(round(x))))


def _pixel(color: Color) -> bytes:
    return b"\x00" + _hsv(color) + b"\x64"


def cmd_set_time(now: datetime.datetime | None = None) -> bytes:
    now = now or datetime.datetime.now()
    body = bytes([0x10, 0x14, now.year - 2000, now.month, now.day, now.hour,
                  now.minute, now.second, now.isoweekday(), 0x00, 0x0F])
    return body + bytes([sum(body) & 0xFF])


def cmd_status() -> bytes:
    return bytes([0xEA, 0x81])


def cmd_power(on: bool) -> bytes:
    return bytes([0x71, POWER_ON if on else POWER_OFF])


def cmd_frame(colors: list[Color]) -> bytes:
    """Set every LED; one color per LED."""
    n = len(colors)
    head = bytes([0xE1, 0x04, 0x01, 0x00, 0x01, 0x50, 0x64, n >> 8, n & 0xFF])
    return head + b"".join(_pixel(c) for c in colors)


def cmd_leds(indices: list[int], color: Color, count: int) -> bytes:
    """Set the given 1-based LEDs to one color, leaving the rest unchanged."""
    nbytes = (count + 7) // 8
    mask = 0
    for i in indices:
        if 1 <= i <= nbytes * 8:
            mask |= 1 << (nbytes * 8 - i)
    return bytes([0xE1, 0x0D, 0x00, 0x01, 0x50, 0x64]) + _pixel(color) + mask.to_bytes(nbytes, "big")


def cmd_scene(effect: int, colors: list[Color], speed: int = 80) -> bytes:
    """Animated scene: effect id plus a color list."""
    head = bytes([0xE1, 0x01, 0x00, 0x64, effect & 0xFF, 0x00, 0x01, 0x64,
                  max(1, min(100, int(speed))), 0x00, 0xA1, 0x00, 0x00, 0x00, len(colors)])
    return head + b"".join(b"\xa1" + _hsv(c) for c in colors)


# ----------------------------------------------------------------- state
@dataclass
class LightState:
    is_on: bool
    mode: int
    effect_id: int
    speed: int
    hue: float
    saturation: int
    value: int
    led_count: int


def parse_state_block(block: bytes) -> LightState | None:
    """Parse the state block shared by status replies and advertisements.

    Layout: power, mode, effect, speed, F0, hue/2, sat, val, 00, 00, 01, count_hi, count_lo
    """
    if len(block) < 13 or block[0] not in (POWER_ON, POWER_OFF):
        return None
    return LightState(
        is_on=block[0] == POWER_ON,
        mode=block[1],
        effect_id=block[2],
        speed=block[3],
        hue=(block[5] % 180) * 2.0,
        saturation=block[6] & 0x7F,
        value=block[7] & 0x7F,
        led_count=(block[11] << 8) | block[12],
    )


def parse_status_reply(body: bytes) -> LightState | None:
    """Status reply payload: EA 81 01 00 AA 0C <state block>."""
    if len(body) < 19 or body[:2] != b"\xea\x81":
        return None
    return parse_state_block(body[6:])


def parse_advertisement(manufacturer_data: dict[int, bytes]) -> LightState | None:
    """Manufacturer data 0x5A02 from the scan response: 80 <state block>."""
    data = manufacturer_data.get(MANUFACTURER_ID)
    if not data or len(data) < 14:
        return None
    return parse_state_block(bytes(data[1:]))
