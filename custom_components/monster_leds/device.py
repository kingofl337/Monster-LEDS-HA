"""Bluetooth connection manager for one set of Monster / Surplife lights."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)

from . import protocol as p
from .const import DEFAULT_LED_COUNT, IDLE_DISCONNECT_SECONDS, PATTERNS, SCENE_BY_ID, SCENES

_LOGGER = logging.getLogger(__name__)

WRITE_GAP = 0.03     # seconds between BLE writes (the app used ~2-5 ms)
COMMAND_GAP = 0.1    # seconds between commands in one request

Color = tuple[float, float, float]


def _dim(color: Color, brightness_pct: float) -> Color:
    h, s, v = color
    return (h, s, v * brightness_pct / 100)


class MonsterLeds:
    """Talks to one controller; keeps an optimistic copy of its state."""

    def __init__(self, ble_device: BLEDevice, name: str) -> None:
        self._ble_device = ble_device
        self.name = name
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._seq = 0
        self._chunk = p.APP_CHUNK
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._disconnect_task: asyncio.Task | None = None
        self._callbacks: list[Callable[[], None]] = []

        self.available = True
        self.is_on: bool | None = None
        self.hs: tuple[float, float] = (0.0, 0.0)
        self.brightness_pct: float = 100.0
        self.effect: str | None = None  # None = solid color
        self.led_count = DEFAULT_LED_COUNT

    # ------------------------------------------------------------ plumbing
    @property
    def address(self) -> str:
        return self._ble_device.address

    def register_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def _remove() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return _remove

    def _fire(self) -> None:
        for callback in list(self._callbacks):
            callback()

    def set_available(self, available: bool) -> None:
        if available != self.available:
            self.available = available
            self._fire()

    def update_from_advertisement(
        self, ble_device: BLEDevice | None, manufacturer_data: dict[int, bytes]
    ) -> None:
        """Called by Home Assistant for every advertisement the lights send."""
        if ble_device is not None:
            self._ble_device = ble_device
        before = self._snapshot()
        self.available = True
        state = p.parse_advertisement(manufacturer_data)
        # while connected, the status replies are authoritative
        if state is not None and self._client is None:
            self._apply_state(state)
        if self._snapshot() != before:
            self._fire()

    def _snapshot(self) -> tuple:
        return (self.available, self.is_on, self.effect, self.led_count)

    def _apply_state(self, state: p.LightState) -> None:
        self.is_on = state.is_on
        if state.led_count:
            self.led_count = state.led_count
        if state.mode == p.MODE_SCENE:
            self.effect = SCENE_BY_ID.get(state.effect_id)
        elif state.mode == p.MODE_PIXELS:
            if self.effect in SCENES:
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

    def _on_disconnected(self, client: Any) -> None:
        _LOGGER.debug("%s: disconnected", self.name)
        if self._client is client:
            self._client = None

    async def _ensure_connected(self) -> None:
        if self._client is not None and self._client.is_connected:
            return
        _LOGGER.debug("%s: connecting to %s", self.name, self.address)
        client = await establish_connection(
            BleakClientWithServiceCache,
            self._ble_device,
            self.name,
            disconnected_callback=self._on_disconnected,
            ble_device_callback=lambda: self._ble_device,
        )
        try:
            self._chunk = p.chunk_for_mtu(await self._read_mtu(client))
            await client.start_notify(p.NOTIFY_UUID, self._on_notify)
            self._client = client
            self._seq = 0
            # Same opening the Surplife app uses: set the clock, then ask for status
            await self._write(p.cmd_set_time())
            await self._write(p.cmd_status())
        except BLEAK_RETRY_EXCEPTIONS:
            self._client = None
            await client.disconnect()
            raise
        _LOGGER.debug("%s: connected, %d-byte chunks", self.name, self._chunk)

    @staticmethod
    async def _read_mtu(client: Any) -> int:
        mtu = getattr(client, "mtu_size", 23) or 23
        if mtu <= 23:
            # Older BlueZ: the MTU is only known after acquiring it
            acquire = getattr(getattr(client, "_backend", None), "_acquire_mtu", None)
            if acquire is not None:
                try:
                    await acquire()
                    mtu = getattr(client, "mtu_size", 23) or 23
                except Exception as err:  # noqa: BLE001 - best effort only
                    _LOGGER.debug("Could not acquire MTU: %s", err)
        return int(mtu)

    async def _write(self, payload: bytes) -> None:
        assert self._client is not None
        self._seq = self._seq % 255 + 1
        for part in p.wrap(payload, self._seq, self._chunk):
            await self._client.write_gatt_char(p.WRITE_UUID, part, response=False)
            await asyncio.sleep(WRITE_GAP)

    async def _send(self, *payloads: bytes) -> None:
        async with self._lock:
            for attempt in (1, 2):
                try:
                    await self._ensure_connected()
                    for payload in payloads:
                        await self._write(payload)
                        await asyncio.sleep(COMMAND_GAP)
                    break
                except BLEAK_RETRY_EXCEPTIONS as err:
                    _LOGGER.debug("%s: send failed (attempt %d): %s", self.name, attempt, err)
                    await self._disconnect_locked()
                    if attempt == 2:
                        raise
        self._reset_disconnect_timer()

    def _reset_disconnect_timer(self) -> None:
        if self._disconnect_timer is not None:
            self._disconnect_timer.cancel()
        self._disconnect_timer = asyncio.get_running_loop().call_later(
            IDLE_DISCONNECT_SECONDS, self._idle_disconnect
        )

    def _idle_disconnect(self) -> None:
        self._disconnect_timer = None
        self._disconnect_task = asyncio.get_running_loop().create_task(self._disconnect())

    async def _disconnect(self) -> None:
        async with self._lock:
            await self._disconnect_locked()

    async def _disconnect_locked(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            _LOGGER.debug("%s: disconnecting", self.name)
            try:
                await client.disconnect()
            except BLEAK_RETRY_EXCEPTIONS as err:
                _LOGGER.debug("%s: disconnect error: %s", self.name, err)

    async def stop(self) -> None:
        if self._disconnect_timer is not None:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None
        await self._disconnect()

    # ------------------------------------------------------------ actions
    async def turn_off(self) -> None:
        await self._send(p.cmd_power(False))
        self.is_on = False
        self._fire()

    async def turn_on(
        self,
        hs: tuple[float, float] | None = None,
        brightness_pct: float | None = None,
        effect: str | None = None,
    ) -> None:
        """Turn on, optionally with a color, brightness and/or effect (None = solid color)."""
        bri = self.brightness_pct if brightness_pct is None else brightness_pct
        new_effect = self.effect
        if effect is not None:
            if effect not in SCENES and effect not in PATTERNS and effect != "off":
                raise ValueError(f"Unknown effect {effect}")
            new_effect = None if effect == "off" else effect
        elif hs is not None:
            new_effect = None
        new_hs = self.hs if hs is None else hs

        commands: list[bytes] = []
        if not self.is_on:
            commands.append(p.cmd_power(True))
        if effect is not None or hs is not None or brightness_pct is not None:
            if new_effect in SCENES:
                effect_id, colors = SCENES[new_effect]
                commands.append(p.cmd_scene(effect_id, [_dim(c, bri) for c in colors]))
            elif new_effect in PATTERNS:
                pattern = PATTERNS[new_effect]
                commands.append(p.cmd_frame(
                    [_dim(pattern[i % len(pattern)], bri) for i in range(self.led_count)]))
            else:
                commands.append(p.cmd_frame([(new_hs[0], new_hs[1], bri)] * self.led_count))
        if not commands:
            commands.append(p.cmd_power(True))

        await self._send(*commands)
        self.is_on = True
        self.brightness_pct = bri
        self.hs = new_hs
        self.effect = new_effect
        self._fire()

    async def set_leds(self, leds: list[int], hs: tuple[float, float], brightness_pct: float) -> None:
        """Color individual LEDs (1-based), leaving the others unchanged."""
        commands = [] if self.is_on else [p.cmd_power(True)]
        commands.append(p.cmd_leds(leds, (hs[0], hs[1], brightness_pct), self.led_count))
        await self._send(*commands)
        self.is_on = True
        self.effect = None
        self._fire()
