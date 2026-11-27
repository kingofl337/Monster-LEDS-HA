"""Behaviour shared by every supported light: state, effects and HA-driven animations."""
from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable

from bleak.backends.device import BLEDevice
from bleak_retry_connector import BLEAK_RETRY_EXCEPTIONS

from .ble import BleLink
from .const import ANIMATIONS, PATTERNS, Animation, Color

_LOGGER = logging.getLogger(__name__)

EFFECT_NONE = "off"  # Home Assistant's "no effect"


def dim(color: Color, brightness_pct: float) -> Color:
    h, s, v = color
    return (h, s, v * brightness_pct / 100)


def repeat(pattern: list[Color], n: int, offset: int = 0) -> list[Color]:
    return [pattern[(i + offset) % len(pattern)] for i in range(n)]


def animation_frame(anim: Animation, step: int, n: int, rng: random.Random) -> list[Color]:
    if anim.kind == "chase":
        return repeat(anim.colors, n, offset=-step)
    if anim.kind == "alternate":
        return repeat(anim.frames[step % len(anim.frames)], n)
    if anim.kind == "fade":
        cycle = 2 * anim.steps  # up then down
        h, s, v = anim.colors[(step // cycle) % len(anim.colors)]
        pos = step % cycle
        level = (pos if pos <= anim.steps else cycle - pos) / anim.steps
        return repeat([(h, s, v * level * level)], n)  # squared: looks even to the eye
    if anim.kind == "twinkle":
        frame = repeat(anim.colors, n)
        for i in rng.sample(range(n), max(1, round(n * anim.sparkle_share))):
            frame[i] = anim.sparkle
        return frame
    raise ValueError(anim.kind)


class LightDevice:
    """Base class; subclasses implement the protocol-specific `_cmd_*` coroutines."""

    assumed_state = False
    min_interval = 0.5  # seconds; slower devices raise this
    uniform_min_interval = 0.5  # for frames where every LED has the same color

    def __init__(self, ble_device: BLEDevice, name: str) -> None:
        self.name = name
        self.link = BleLink(ble_device, name, self._on_connect)
        self._callbacks: list[Callable[[], None]] = []
        self._anim_task: asyncio.Task | None = None
        self._rng = random.Random()

        self.available = True
        self.is_on: bool | None = None
        self.hs: tuple[float, float] = (0.0, 0.0)
        self.brightness_pct: float = 100.0
        self.effect: str | None = None  # None = solid color

    # ---------------------------------------------------------- to implement
    @property
    def pixel_count(self) -> int:
        raise NotImplementedError

    @property
    def scenes(self) -> list[str]:
        """Effects built into the controller."""
        return []

    async def _on_connect(self, client) -> None:
        """Run once per connection, before any command."""

    async def _send_power(self, on: bool, client) -> None:
        raise NotImplementedError

    async def _send_solid(self, hs: tuple[float, float], brightness_pct: float, client) -> None:
        raise NotImplementedError

    async def _send_frame(self, colors: list[Color], client) -> None:
        """One color per pixel/segment, already dimmed."""
        raise NotImplementedError

    async def _send_scene(self, name: str, brightness_pct: float, client) -> None:
        raise NotImplementedError

    # ---------------------------------------------------------- plumbing
    @property
    def address(self) -> str:
        return self.link.ble_device.address

    @property
    def effect_list(self) -> list[str]:
        return [*self.scenes, *PATTERNS, *ANIMATIONS]

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
        if ble_device is not None:
            self.link.ble_device = ble_device
        self.set_available(True)

    async def stop(self) -> None:
        await self._stop_animation()
        await self.link.stop()

    # ---------------------------------------------------------- animations
    async def _stop_animation(self) -> None:
        task, self._anim_task = self._anim_task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _start_animation(self, anim: Animation, brightness_pct: float) -> None:
        floor = self.uniform_min_interval if anim.kind == "fade" else self.min_interval
        interval = max(anim.interval, floor)
        self._anim_task = asyncio.get_running_loop().create_task(
            self._animate(anim, brightness_pct, interval), name=f"{self.name} animation"
        )

    async def _animate(self, anim: Animation, brightness_pct: float, interval: float) -> None:
        step = 0
        failures = 0
        while True:
            frame = [dim(c, brightness_pct)
                     for c in animation_frame(anim, step, self.pixel_count, self._rng)]

            async def _send(client, frame=frame) -> None:
                await self._send_frame(frame, client)

            try:
                await self.link.run(_send)
                failures = 0
            except BLEAK_RETRY_EXCEPTIONS as err:
                failures += 1
                _LOGGER.debug("%s: animation frame failed (%d): %s", self.name, failures, err)
                if failures >= 5:
                    _LOGGER.warning("%s: stopping animation, lights unreachable", self.name)
                    return
                await asyncio.sleep(5)
            step += 1
            await asyncio.sleep(interval)

    # ---------------------------------------------------------- actions
    async def turn_off(self) -> None:
        await self._stop_animation()

        async def _act(client) -> None:
            await self._send_power(False, client)

        await self.link.run(_act)
        self.is_on = False
        self._fire()

    async def turn_on(
        self,
        hs: tuple[float, float] | None = None,
        brightness_pct: float | None = None,
        effect: str | None = None,
    ) -> None:
        """Turn on, optionally with color, brightness and/or effect ("off" = solid color)."""
        bri = self.brightness_pct if brightness_pct is None else brightness_pct
        new_effect = self.effect
        if effect is not None:
            if effect != EFFECT_NONE and effect not in self.effect_list:
                raise ValueError(f"Unknown effect {effect}")
            new_effect = None if effect == EFFECT_NONE else effect
        elif hs is not None:
            new_effect = None
        new_hs = self.hs if hs is None else hs
        changed = effect is not None or hs is not None or brightness_pct is not None
        was_on = bool(self.is_on)

        await self._stop_animation()

        async def _act(client) -> None:
            if not was_on:
                await self._send_power(True, client)
            if not changed:
                return
            if new_effect in self.scenes:
                await self._send_scene(new_effect, bri, client)
            elif new_effect in PATTERNS:
                await self._send_frame(
                    [dim(c, bri) for c in repeat(PATTERNS[new_effect], self.pixel_count)],
                    client)
            elif new_effect is None:
                await self._send_solid(new_hs, bri, client)

        if changed or not was_on:
            await self.link.run(_act)
        if new_effect in ANIMATIONS and (changed or not was_on or self._anim_task is None):
            self._start_animation(ANIMATIONS[new_effect], bri)

        self.is_on = True
        self.brightness_pct = bri
        self.hs = new_hs
        self.effect = new_effect
        self._fire()
