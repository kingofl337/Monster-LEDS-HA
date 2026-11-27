"""Shared Bluetooth connection handling: connect on demand, retry once, idle disconnect."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from bleak.backends.device import BLEDevice
from bleak_retry_connector import (
    BLEAK_RETRY_EXCEPTIONS,
    BleakClientWithServiceCache,
    establish_connection,
)

from .const import IDLE_DISCONNECT_SECONDS

_LOGGER = logging.getLogger(__name__)

Client = Any
Action = Callable[[Client], Awaitable[None]]


class BleLink:
    """One connection to one device, shared by all commands for it."""

    def __init__(self, ble_device: BLEDevice, name: str, on_connect: Action | None = None) -> None:
        self.ble_device = ble_device
        self.name = name
        self._on_connect = on_connect
        self._client: BleakClientWithServiceCache | None = None
        self._lock = asyncio.Lock()
        self._timer: asyncio.TimerHandle | None = None
        self._disconnect_task: asyncio.Task | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected

    def _on_disconnected(self, client: Any) -> None:
        _LOGGER.debug("%s: disconnected", self.name)
        if self._client is client:
            self._client = None

    async def _ensure_connected(self) -> Client:
        if self._client is not None and self._client.is_connected:
            return self._client
        _LOGGER.debug("%s: connecting to %s", self.name, self.ble_device.address)
        client = await establish_connection(
            BleakClientWithServiceCache,
            self.ble_device,
            self.name,
            disconnected_callback=self._on_disconnected,
            ble_device_callback=lambda: self.ble_device,
        )
        try:
            if self._on_connect is not None:
                await self._on_connect(client)
        except BLEAK_RETRY_EXCEPTIONS:
            await client.disconnect()
            raise
        self._client = client
        _LOGGER.debug("%s: connected", self.name)
        return client

    async def run(self, action: Action) -> None:
        """Run action(client) with a live connection; reconnect and retry once on failure."""
        async with self._lock:
            for attempt in (1, 2):
                try:
                    client = await self._ensure_connected()
                    await action(client)
                    break
                except BLEAK_RETRY_EXCEPTIONS as err:
                    _LOGGER.debug("%s: attempt %d failed: %s", self.name, attempt, err)
                    await self._disconnect_locked()
                    if attempt == 2:
                        raise
        self._reset_timer()

    def _reset_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = asyncio.get_running_loop().call_later(IDLE_DISCONNECT_SECONDS, self._idle)

    def _idle(self) -> None:
        self._timer = None
        self._disconnect_task = asyncio.get_running_loop().create_task(self.disconnect())

    async def disconnect(self) -> None:
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
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        await self.disconnect()
