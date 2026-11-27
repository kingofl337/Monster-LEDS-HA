# Monster-LEDS-HA

Control Monster / CZJUTAI Bluetooth Christmas lights (the ones that use the **Surplife** app
and advertise as `IOTBTxxxx`) from Home Assistant, with no cloud and no phone.

Tested hardware: CZJUTAI LED controller `JT-EL/FC050V2000-H15`, firmware `BT.80.00AA.55.0D,V10`,
50 RGB LEDs.

## Features

- On / off
- Color and brightness for the whole string
- Effects:
  - Animated scenes from the app: **Christmas** (red, white, green, white) and **Rainbow**
  - Static patterns: **Candy cane**, **Red & green**, **Red, green & white**
- `monster_leds.set_leds` action to color individual LEDs
- On/off state updates from the lights' Bluetooth advertisements, even when changed from the app
  or remote
- Works with the Home Assistant host's Bluetooth adapter or an ESPHome Bluetooth proxy

## Install

1. In HACS, open the three-dot menu, choose **Custom repositories**, and add
   `https://github.com/kingofl337/Monster-LEDS-HA` as type **Integration**.
2. Download **Monster LEDs (Surplife Bluetooth)** and restart Home Assistant.
3. The lights should be discovered automatically. If not, go to **Settings → Devices & services →
   Add integration → Monster LEDs**.

Close the Surplife app (or turn off the phone's Bluetooth) while Home Assistant is talking to
the lights: they accept only one connection at a time. Home Assistant disconnects after 60 seconds
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

`tools/surplife_lights.py` is a standalone command-line version (Windows, macOS, Linux) using
[bleak](https://github.com/hbldh/bleak):

```
pip install bleak
python tools/surplife_lights.py scan
python tools/surplife_lights.py pattern red white green white
```

`tests/test_protocol.py` checks the encoder against packets captured from the app
(`python tests/test_protocol.py`).
