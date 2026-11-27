"""String lights that use the Surplife app (IOTBTxxxx)."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from bleak.backends.device import BLEDevice

from . import protocol as p
from .base import LightDevice, dim
from .const import DEFAULT_LED_COUNT, SURPLIFE_SCENE_BY_ID, SURPLIFE_SCENES, Color

_LOGGER = logging.getLogger(__name__)

WRITE_GAP = 0.03     # seconds between BLE writes
COMMAND_GAP = 0.1    # seconds between commands in one request


class SurplifeLights(LightDevice):
    """Talks to one Surplife controller; keeps an optimistic copy of its state."""

    min_interval = 0.3  # one frame is 2 writes, about 0.15 s

    def __init__(self, ble_device: BLEDevice, name: str) -> None:
        super().__init__(ble_device, name)
        self._seq = 0
        self._chunk = p.APP_CHUNK
        self.led_count = DEFAULT_LED_COUNT

    @property
    def pixel_count(self) -> int:
        return self.led_count

    @property
    def scenes(self) -> list[str]:
        return list(SURPLIFE_SCENES)

    # ---------------------------------------------------------- state from the device
    def update_from_advertisement(
        self, ble_device: BLEDevice | None, manufacturer_data: dict[int, bytes]
    ) -> None:
        if ble_device is not None:
            self.link.ble_device = ble_device
        before = self._snapshot()
        self.available = True
        state = p.parse_advertisement(manufacturer_data)
        # while connected, the status replies are authoritative
        if state is not None and not self.link.connected:
            self._apply_state(state)
        if self._snapshot() != before:
            self._fire()

    def _snapshot(self) -> tuple:
        return (self.available, self.is_on, self.effect, self.led_count)

    def _apply_state(self, state: p.LightState) -> None:
        self.is_on = state.is_on
        if state.led_count:
            self.led_count = state.led_count
        if self._anim_task is not None:
            return  # we are animating; the controller just shows our last frame
        if state.mode == p.MODE_SCENE:
            current = SURPLIFE_SCENES.get(self.effect or "")
            if not (current and current[0] == state.effect_id):
                self.effect = SURPLIFE_SCENE_BY_ID.get(state.effect_id)
        elif state.mode == p.MODE_PIXELS:
            if self.effect in SURPLIFE_SCENES:
                self.effect = None
        else:
            self.effect = None

    def _on_notify(self, _char: Any, data: bytearray) -> None:
        body = p.unwrap_notification(bytes(data))
        state = p.parse_status_reply(body) if body else None
        if state is not None:
            _LOGGER.debug("%s: status %s", self.name, state)
            self._apply_state(state)
            self._fire()

    # ---------------------------------------------------------- connection
    async def _on_connect(self, client) -> None:
        self._chunk = p.chunk_for_mtu(await self._read_mtu(client))
        await client.start_notify(p.NOTIFY_UUID, self._on_notify)
        self._seq = 0
        # Same opening the Surplife app uses: set the clock, then ask for status
        await self._write(client, p.cmd_set_time())
        await self._write(client, p.cmd_status())
        _LOGGER.debug("%s: ready, %d-byte chunks", self.name, self._chunk)

    @staticmethod
    async def _read_mtu(client: Any) -> int:
        backend = getattr(client, "_backend", None)
        if getattr(backend, "_mtu_size", 0) is None and hasattr(backend, "_acquire_mtu"):
            try:  # BlueZ only knows the MTU after acquiring it
                await backend._acquire_mtu()
            except Exception as err:  # noqa: BLE001 - best effort only
                _LOGGER.debug("Could not acquire MTU: %s", err)
        try:
            return int(client.mtu_size)
        except Exception:  # noqa: BLE001
            return 23

    async def _write(self, client, payload: bytes) -> None:
        self._seq = self._seq % 255 + 1
        for part in p.wrap(payload, self._seq, self._chunk):
            await client.write_gatt_char(p.WRITE_UUID, part, response=False)
            await asyncio.sleep(WRITE_GAP)

    async def _command(self, client, payload: bytes) -> None:
        await self._write(client, payload)
        await asyncio.sleep(COMMAND_GAP)

    # ---------------------------------------------------------- protocol hooks
    async def _send_power(self, on: bool, client) -> None:
        await self._command(client, p.cmd_power(on))

    async def _send_solid(self, hs, brightness_pct, client) -> None:
        await self._command(client, p.cmd_frame([(hs[0], hs[1], brightness_pct)] * self.led_count))

    async def _send_frame(self, colors: list[Color], client) -> None:
        await self._command(client, p.cmd_frame(colors))

    async def _send_scene(self, name: str, brightness_pct: float, client) -> None:
        effect_id, colors = SURPLIFE_SCENES[name]
        await self._command(client, p.cmd_scene(effect_id, [dim(c, brightness_pct) for c in colors]))

    # ---------------------------------------------------------- extra action
    async def set_leds(self, leds: list[int], hs: tuple[float, float], brightness_pct: float) -> None:
        """Color individual LEDs (1-based), leaving the others unchanged."""
        await self._stop_animation()
        was_on = bool(self.is_on)

        async def _act(client) -> None:
            if not was_on:
                await self._send_power(True, client)
            await self._command(client, p.cmd_leds(leds, (hs[0], hs[1], brightness_pct), self.led_count))

        await self.link.run(_act)
        self.is_on = True
        self.effect = None
        self._fire()


