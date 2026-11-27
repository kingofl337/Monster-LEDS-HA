"""Config flow: discovers IOTBT lights over Bluetooth."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_ADDRESS

from .const import DOMAIN
from .protocol import NAME_PREFIX


def _title(info: BluetoothServiceInfoBleak) -> str:
    return f"Monster lights {info.name or info.address}"


class MonsterLedsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup of Monster / Surplife lights."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovery: BluetoothServiceInfoBleak | None = None
        self._found: dict[str, BluetoothServiceInfoBleak] = {}

    async def async_step_bluetooth(self, discovery_info: BluetoothServiceInfoBleak):
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        self._discovery = discovery_info
        self.context["title_placeholders"] = {"name": discovery_info.name or discovery_info.address}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(self, user_input: dict[str, Any] | None = None):
        assert self._discovery is not None
        if user_input is not None:
            return self.async_create_entry(
                title=_title(self._discovery), data={CONF_ADDRESS: self._discovery.address}
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm",
            description_placeholders={"name": self._discovery.name or self._discovery.address},
        )

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_title(self._found[address]), data={CONF_ADDRESS: address}
            )

        configured = self._async_current_ids()
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in configured:
                continue
            if (info.name or "").upper().startswith(NAME_PREFIX):
                self._found[info.address] = info
        if not self._found:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): vol.In(
                    {addr: f"{info.name} ({addr})" for addr, info in self._found.items()}
                )
            }),
        )
