# Surplife-LEDS-HA

Control Bluetooth Christmas lights from Home Assistant, with no cloud and no phone:

- Monster / CZJUTAI lights that use the **Surplife** app and advertise as `IOTBTxxxx`
  (tested: controller `JT-EL/FC050V2000-H15`, firmware `BT.80.00AA.55.0D,V10`, 50 RGB LEDs)
- **Lotus Lamp X** strips that advertise as `MELK-OF21…` (28 segments)

## Features

- On / off, color and brightness
- Christmas effects for both kinds of lights:
  - Static patterns: **Candy cane**, **Red & green**, **Red, green & white**, **Classic multicolor**,
    **Gold & white**, **Warm white**, **Winter ice**
  - Animated by Home Assistant: **Candy cane chase**, **Christmas chase**, **Multicolor chase**,
    **Christmas twinkle**, **Warm white twinkle**, **Red & green flip**
  - Surplife lights also get the controller's own scenes: **Christmas**, **Christmas flow**, **Rainbow**
- `monster_leds.set_leds` action to color individual LEDs (Surplife) or segments 1–28 (Lotus)
- Surplife lights report on/off through their Bluetooth advertisements, even when changed from the
  app or remote. Lotus strips don't report anything, so their state is what Home Assistant last sent.
- Works with the Home Assistant host's Bluetooth adapter or an ESPHome Bluetooth proxy

Animated effects keep a Bluetooth connection open and send a new frame every 1–2 seconds
(a Lotus segment update takes about a second). Choosing a color or another effect, or turning the
lights off, stops the animation.

## Install

1. In HACS, open the three-dot menu, choose **Custom repositories**, and add
   `https://github.com/kingofl337/Monster-LEDS-HA` as type **Integration**.
2. Download **Monster LEDs (Surplife & Lotus Bluetooth)** and restart Home Assistant.
3. The lights should be discovered automatically. If not, go to **Settings → Devices & services →
   Add integration → Monster LEDs**.

Close the Surplife / Lotus Lamp X app (or turn off the phone's Bluetooth) while Home Assistant
is talking to the lights: they accept only one connection at a time. Home Assistant disconnects after 60 seconds
of inactivity so the app can be used again.

If you use an ESPHome Bluetooth proxy, it needs `active: true` to make connections.

### Example: color single LEDs

```yaml
action: monster_leds.set_leds
target:
  entity_id: light.monster_lights_iotbtcd8
data:
  leds: [1, 5, 10]
  rgb_color: [255, 0, 0]
  brightness_pct: 80
```

## Protocol

### Surplife (IOTBTxxxx)

Decoded from an Android HCI snoop log of the Surplife app.

- Service `0xFFFF`: write to `0000ff01-0000-1000-8000-00805f9b34fb` (write without response),
  replies arrive as notifications on `0000ff02-0000-1000-8000-00805f9b34fb`.
  No pairing or encryption.
- Every command is wrapped (numbers big-endian):
  - single packet: `00 SEQ 80 00 LEN_HI LEN_LO LEN+1 0A <payload>`
  - split: `40 SEQ 00 00 LEN_HI LEN_LO CHUNK+1 0A <chunk>`, then `40 SEQ 00|80 IDX CHUNK <chunk>`
    (`80` marks the last part)
- Payloads:

| Command | Bytes |
| --- | --- |
| Set clock (app sends on connect) | `10 14 YY MM DD hh mm ss weekday 00 0F checksum` |
| Status request | `EA 81` |
| Power on / off | `71 23` / `71 24` |
| All LEDs | `E1 04 01 00 01 50 64 <count:2>` + count × `00 H S V 64` |
| Some LEDs | `E1 0D 00 01 50 64` + `00 H S V 64` + bitmask (most significant bit = LED 1) |
| Scene | `E1 01 00 64 <effect> 00 01 64 <speed> 00 A1 00 00 00 <n>` + n × `A1 H S V` |

  Colors are HSV: `H` = hue in degrees ÷ 2, `S` and `V` = 0–100.
- Status replies and the advertisement's manufacturer data (company `0x5A02`) share a state block:
  `power(23/24) mode(25 scene, 66 pixels) effect speed F0 H S V 00 00 01 count_hi count_lo`.

### Lotus Lamp X (MELK-OF21)

Decoded from three Android HCI snoop logs of the Lotus Lamp X app. Every command is one 9-byte
write (without response) to `0000fff3-0000-1000-8000-00805f9b34fb`; no notifications, no encryption.

| Command | Bytes |
| --- | --- |
| Set clock (app sends on connect) | `7E 06 83 hh mm ss weekday 00 EF` |
| Query (then read FFF3: `7E 07 37 <segments> …`) | `7E 07 37 FF FF FF 00 FF EF` |
| Power on / off | `7E 07 04 FF 00 01 02 01 EF` / `7E 07 04 00 00 00 02 01 EF` |
| Color | `7E 07 05 03 RR GG BB 10 EF` |
| Brightness 0–100 % | `7E 04 01 PP 01 FF 02 01 EF` |
| Built-in effect | `7E 07 03 <id> 06 FF FF 00 EF` |
| Segment colors | `7E 07 37 <n> 00 01 01 00 EF`, `7E 32 <3n> 00 CK_HI CK_LO FF FF EF`, n/2 × `7E 33 R G B R G B <index>`, `7E 34 00 00 64 FF FF FF EF` |

`CK` is the 16-bit sum of every byte of the `7E 32` packet (without CK), the `7E 33` packets and
the `7E 34` packet.

`tools/surplife_lights.py` is a standalone command-line version (Windows, macOS, Linux) using
[bleak](https://github.com/hbldh/bleak):

```
pip install bleak
python tools/surplife_lights.py scan
python tools/surplife_lights.py pattern red white green white
```

`tests/test_protocol.py` and `tests/test_melk_protocol.py` check the encoders against packets
captured from the apps (`python tests/test_protocol.py`, `python tests/test_melk_protocol.py`).
