#!/usr/bin/env python3
"""
Control Monster / CZJUTAI "Surplife" Bluetooth string lights (advertise as IOTBTxxxx)
without the phone app.

Protocol decoded from an Android HCI snoop capture of the Surplife app.

Connection
  Service 0xFFFF
    write  : 0000ff01-0000-1000-8000-00805f9b34fb  (write without response)
    notify : 0000ff02-0000-1000-8000-00805f9b34fb  (status replies)
  No pairing or encryption. Only one phone/PC can be connected at a time.

Wrapper around every command (all multi-byte numbers big-endian)
  single  : 00 SEQ 80 00 LEN_HI LEN_LO LEN+1 0A <payload>
  split   : first  40 SEQ 00 00 LEN_HI LEN_LO CHUNK+1 0A <chunk>
            middle 40 SEQ 00 IDX CHUNK <chunk>
            last   40 SEQ 80 IDX CHUNK <chunk>
  SEQ counts up by 1 per command. Replies come back with first byte 04.

Payloads (inside the wrapper)
  set clock   10 14 YY MM DD hh mm ss weekday 00 0F checksum   (app sends on connect)
  status      EA 81
  power       71 23 = on, 71 24 = off
  all pixels  E1 04 01 00 01 50 64 <count:2> then <count> x [00 H S V 64]
  some pixels E1 0D 00 01 50 64 [00 H S V 64] <bitmask, MSB = LED 1>
  scene       E1 01 00 64 <effect> 00 01 64 <speed> 00 A1 00 00 00 <n> then n x [A1 H S V]
  Colors are HSV: H = hue degrees / 2 (0-179), S and V = 0-100. White = S 0.

Status reply (EA 81 ...): byte 6 power (23 on / 24 off), byte 7 mode
  (25 scene, 66 custom pixels), bytes 17-18 LED count (0x0032 = 50).

Usage (Windows, macOS or Linux; needs Python 3.9+ and `pip install bleak`):
  python surplife_lights.py scan
  python surplife_lights.py on
  python surplife_lights.py off
  python surplife_lights.py color 0 100 100          # hue saturation brightness
  python surplife_lights.py rgb 255 120 0
  python surplife_lights.py white 60                 # brightness %
  python surplife_lights.py pattern red white green white
  python surplife_lights.py led 1,5,10 blue
  python surplife_lights.py scene christmas --speed 80
  python surplife_lights.py status
Add --address XX:XX:XX:XX:XX:XX to skip scanning, and --count N if you add
or remove LEDs (default 50).
"""
import argparse
import asyncio
import colorsys
import datetime
import sys

WRITE_UUID = "0000ff01-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000ff02-0000-1000-8000-00805f9b34fb"
NAME_PREFIX = "IOTB"
DEFAULT_COUNT = 50
APP_CHUNK = 174  # payload bytes per segment the app used (MTU 186)

NAMED = {  # hue degrees, saturation, value
    "red": (0, 100, 100), "orange": (30, 100, 100), "yellow": (60, 100, 100),
    "green": (120, 100, 100), "cyan": (180, 100, 100), "blue": (240, 100, 100),
    "purple": (270, 100, 100), "magenta": (300, 100, 100), "pink": (330, 60, 100),
    "white": (0, 0, 100), "off": (0, 0, 0), "black": (0, 0, 0),
}

# Scenes exactly as the Surplife app sent them in the capture: effect id + colors
SCENES = {
    "christmas": (0x66, ["red", "white", "green", "white"]),
    "rainbow": (0x65, [(0, 100, 100), (60, 100, 100), (120, 100, 100),
                       (180, 100, 100), (240, 100, 100), (300, 100, 100)]),
}


# ---------------------------------------------------------------- encoding
def hsv_bytes(h, s, v):
    return bytes([int(round(h / 2)) % 180, max(0, min(100, int(s))), max(0, min(100, int(v)))])


def pixel(h, s, v):
    return b"\x00" + hsv_bytes(h, s, v) + b"\x64"


def wrap(payload: bytes, seq: int, chunk: int = APP_CHUNK, typ: int = 0x0A):
    """Split a payload into the BLE writes the lights expect."""
    n = len(payload)
    seq &= 0xFF
    if n <= chunk and n + 1 <= 0xFF:
        return [bytes([0x00, seq, 0x80, 0x00, n >> 8, n & 0xFF, n + 1, typ]) + payload]
    parts = [payload[i:i + chunk] for i in range(0, n, chunk)]
    out = []
    for idx, part in enumerate(parts):
        last = 0x80 if idx == len(parts) - 1 else 0x00
        if idx == 0:
            out.append(bytes([0x40, seq, 0x00, 0x00, n >> 8, n & 0xFF, len(part) + 1, typ]) + part)
        else:
            out.append(bytes([0x40, seq, last, idx, len(part)]) + part)
    return out


def cmd_set_time(now=None):
    now = now or datetime.datetime.now()
    body = bytes([0x10, 0x14, now.year - 2000, now.month, now.day, now.hour,
                  now.minute, now.second, now.isoweekday(), 0x00, 0x0F])
    return body + bytes([sum(body) & 0xFF])


def cmd_power(on: bool):
    return bytes([0x71, 0x23 if on else 0x24])


def cmd_status():
    return bytes([0xEA, 0x81])


def cmd_frame(colors):
    """colors: list of (h, s, v), one per LED."""
    n = len(colors)
    return bytes([0xE1, 0x04, 0x01, 0x00, 0x01, 0x50, 0x64, n >> 8, n & 0xFF]) + b"".join(
        pixel(*c) for c in colors)


def cmd_leds(indices, hsv, count=DEFAULT_COUNT):
    """indices are 1-based LED numbers."""
    nbytes = (count + 7) // 8
    mask = 0
    for i in indices:
        mask |= 1 << (nbytes * 8 - i)
    return bytes([0xE1, 0x0D, 0x00, 0x01, 0x50, 0x64]) + pixel(*hsv) + mask.to_bytes(nbytes, "big")


def cmd_scene(effect, colors, speed=80):
    body = bytes([0xE1, 0x01, 0x00, 0x64, effect & 0xFF, 0x00, 0x01, 0x64,
                  max(1, min(100, speed)), 0x00, 0xA1, 0x00, 0x00, 0x00, len(colors)])
    return body + b"".join(b"\xa1" + hsv_bytes(*c) for c in colors)


def parse_color(text):
    t = text.lower()
    if t in NAMED:
        return NAMED[t]
    if t.startswith("#") and len(t) == 7:
        r, g, b = (int(t[i:i + 2], 16) for i in (1, 3, 5))
        return rgb_to_hsv(r, g, b)
    raise SystemExit(f"Unknown color '{text}'. Use a name ({', '.join(NAMED)}) or #RRGGBB.")


def rgb_to_hsv(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    return (h * 360, s * 100, v * 100)


def parse_status(body: bytes):
    if len(body) < 19 or body[:2] != b"\xea\x81":
        return None
    return {
        "power": {0x23: "on", 0x24: "off"}.get(body[6], hex(body[6])),
        "mode": {0x25: "scene", 0x66: "custom pixels"}.get(body[7], hex(body[7])),
        "effect": body[8],
        "led_count": (body[17] << 8) | body[18],
        "raw": body.hex(" "),
    }


def unwrap_notification(data: bytes):
    """Return the inner payload of a single-packet reply."""
    if len(data) >= 8 and data[2] & 0x80 and (data[3] == 0):
        n = (data[4] << 8) | data[5]
        return data[8:8 + n]
    return None


# ---------------------------------------------------------------- BLE
class Lights:
    def __init__(self, client):
        self.client = client
        self.seq = 0
        self.replies = asyncio.Queue()
        self.chunk = APP_CHUNK

    async def start(self):
        backend = getattr(self.client, "_backend", None)
        if hasattr(backend, "_acquire_mtu"):  # BlueZ (Linux / Raspberry Pi)
            try:
                await backend._acquire_mtu()
            except Exception:
                pass
        mtu = getattr(self.client, "mtu_size", 0) or 23
        # each write = 8 header bytes + chunk, and must fit in MTU - 3
        self.chunk = APP_CHUNK if mtu >= 186 else max(9, mtu - 11)
        await self.client.start_notify(NOTIFY_UUID, self._on_notify)
        await self.send(cmd_set_time())
        return await self.status()

    def _on_notify(self, _sender, data: bytearray):
        body = unwrap_notification(bytes(data))
        if body:
            self.replies.put_nowait(body)

    async def send(self, payload: bytes):
        self.seq = (self.seq % 255) + 1
        for part in wrap(payload, self.seq, self.chunk):
            await self.client.write_gatt_char(WRITE_UUID, part, response=False)
            await asyncio.sleep(0.03)

    async def status(self, timeout=2.0):
        while not self.replies.empty():
            self.replies.get_nowait()
        await self.send(cmd_status())
        try:
            while True:
                body = await asyncio.wait_for(self.replies.get(), timeout)
                st = parse_status(body)
                if st:
                    return st
        except asyncio.TimeoutError:
            return None


async def find_address(timeout=8.0):
    from bleak import BleakScanner
    devices = await BleakScanner.discover(timeout=timeout)
    hits = [d for d in devices if (d.name or "").upper().startswith(NAME_PREFIX)]
    return hits


async def run(args):
    try:
        from bleak import BleakClient
    except ImportError:
        raise SystemExit("Install bleak first:  pip install bleak")

    if args.command == "scan":
        hits = await find_address()
        if not hits:
            print("No IOTB... lights found. Close the Surplife app (or turn off the phone's Bluetooth) and retry.")
        for d in hits:
            print(f"{d.address}  {d.name}")
        return

    address = args.address
    if not address:
        hits = await find_address()
        if not hits:
            raise SystemExit("No lights found. Close the Surplife app and try again, or pass --address.")
        address = hits[0].address
        print(f"Using {hits[0].name} at {address}")

    async with BleakClient(address, timeout=20.0) as client:
        lights = Lights(client)
        st = await lights.start()
        count = args.count or (st["led_count"] if st and st["led_count"] else DEFAULT_COUNT)
        c = args.command
        if c == "status":
            print(st or "No status reply")
        elif c in ("on", "off"):
            await lights.send(cmd_power(c == "on"))
        elif c == "color":
            await lights.send(cmd_frame([(args.hue, args.sat, args.bright)] * count))
        elif c == "rgb":
            await lights.send(cmd_frame([rgb_to_hsv(args.r, args.g, args.b)] * count))
        elif c == "white":
            await lights.send(cmd_frame([(0, 0, args.bright)] * count))
        elif c == "pattern":
            cols = [parse_color(x) for x in args.colors]
            await lights.send(cmd_frame([cols[i % len(cols)] for i in range(count)]))
        elif c == "led":
            idx = [int(x) for x in args.leds.split(",") if x.strip()]
            await lights.send(cmd_leds(idx, parse_color(args.color), count))
        elif c == "scene":
            effect, cols = SCENES[args.name]
            cols = [parse_color(x) if isinstance(x, str) else x for x in cols]
            await lights.send(cmd_scene(effect, cols, args.speed))
        await asyncio.sleep(0.3)
        if c not in ("status",):
            st = await lights.status()
            if st:
                print(f"power={st['power']} mode={st['mode']} leds={st['led_count']}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Control Surplife (IOTBT) Bluetooth string lights")
    p.add_argument("--address", help="Bluetooth address (skip scanning)")
    p.add_argument("--count", type=int, help="number of LEDs (default: read from lights, else 50)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("scan")
    sub.add_parser("status")
    sub.add_parser("on")
    sub.add_parser("off")
    s = sub.add_parser("color"); s.add_argument("hue", type=float); s.add_argument("sat", type=float); s.add_argument("bright", type=float)
    s = sub.add_parser("rgb"); s.add_argument("r", type=int); s.add_argument("g", type=int); s.add_argument("b", type=int)
    s = sub.add_parser("white"); s.add_argument("bright", type=float, nargs="?", default=100)
    s = sub.add_parser("pattern"); s.add_argument("colors", nargs="+")
    s = sub.add_parser("led"); s.add_argument("leds", help="e.g. 1,2,10"); s.add_argument("color")
    s = sub.add_parser("scene"); s.add_argument("name", choices=sorted(SCENES)); s.add_argument("--speed", type=int, default=80)
    args = p.parse_args(argv)
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
