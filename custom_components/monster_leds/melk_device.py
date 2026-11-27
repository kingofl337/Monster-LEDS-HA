"""Lotus Lamp X strips that advertise as MELK-OF21."""
from __future__ import annotations

import asyncio
import colorsys
import logging

from bleak.backends.device import BLEDevice

from . import melk_protocol as m
from .base import LightDevice
from .const import Color

_LOGGER = logging.getLogger(__name__)

WRITE_GAP = 0.05  # the app spaces its segment packets 60-90 ms apart


def to_rgb(color: Color) -> m.RGB:
    h, s, v = color
    r, g, b = colorsys.hsv_to_rgb((h % 360) / 360, s / 100, v / 100)
    return (round(r * 255), round(g * 255), round(b * 255))


class MelkLights(LightDevice):
    """No status from this strip, so its state is tracked from our own commands."""

    assumed_state = True
    min_interval = 1.2  # a segment table takes 16 writes
    hardware_brightness = True

    def __init__(self, ble_device: BLEDevice, name: str) -> None:
        super().__init__(ble_device, name)
        self.cells = m.DEFAULT_CELLS
        self._frame: list[m.RGB] = [(0, 0, 0)] * self.cells

    @property
    def pixel_count(self) -> int:
        return self.cells

    async def _on_connect(self, client) -> None:
        # Same opening the Lotus Lamp X app uses: set the clock, then query and read back
        await self._write(client, m.cmd_set_time())
        await self._write(client, m.cmd_query())
        try:
            cells = m.parse_query_reply(bytes(await client.read_gatt_char(m.WRITE_UUID)))
        except Exception as err:  # noqa: BLE001 - optional
            _LOGGER.debug("%s: could not read segment count: %s", self.name, err)
            cells = None
        if cells and cells != self.cells:
            _LOGGER.debug("%s: strip reports %d segments", self.name, cells)
            self.cells = cells
            self._frame = (self._frame + [(0, 0, 0)] * cells)[:cells]

    async def _write(self, client, *packets: bytes) -> None:
        for packet in packets:
            await client.write_gatt_char(m.WRITE_UUID, packet, response=False)
            await asyncio.sleep(WRITE_GAP)

    async def _send_power(self, on: bool, client) -> None:
        await self._write(client, m.cmd_power(on))

    async def _send_solid(self, hs, brightness_pct, client) -> None:
        rgb = to_rgb((hs[0], hs[1], 100))
        await self._write(client, m.cmd_color(rgb))
        self._frame = [rgb] * self.cells

    async def _send_brightness(self, brightness_pct: float, client) -> None:
        await self._write(client, m.cmd_brightness(max(1, brightness_pct)))

    async def _send_frame(self, colors: list[Color], client) -> None:
        frame = [to_rgb(c) for c in colors]
        await self._write(client, *m.cmd_segments(frame))
        self._frame = frame

    async def set_leds(self, leds: list[int], hs: tuple[float, float], brightness_pct: float) -> None:
        """Color individual segments (1-based), leaving the others unchanged."""
        await self._stop_animation()
        was_on = bool(self.is_on)
        rgb = to_rgb((hs[0], hs[1], brightness_pct))  # dims just these segments
        frame = list(self._frame)
        for i in leds:
            if 1 <= i <= self.cells:
                frame[i - 1] = rgb

        async def _act(client) -> None:
            if not was_on:
                await self._send_power(True, client)
            await self._write(client, *m.cmd_segments(frame))

        await self.link.run(_act)
        self._frame = frame
        self.is_on = True
        self.effect = None
        self._fire()
