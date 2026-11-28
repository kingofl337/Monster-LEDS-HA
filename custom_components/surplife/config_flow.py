"""Config flow: discovers IOTBT lights over Bluetooth."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import CONF_DISCONNECT_DELAY, DOMAIN, IDLE_DISCONNECT_SECONDS
from .protocol import NAME_PREFIX


def _supported(info: BluetoothServiceInfoBleak) -> bool:
    return (info.name or "").upper().startswith(NAME_PREFIX)


def _title(info: BluetoothServiceInfoBleak) -> str:
    return f"Surplife lights {info.name or info.address}"


def _data(info: BluetoothServiceInfoBleak) -> dict[str, str]:
    return {CONF_ADDRESS: info.address}


class SurplifeConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup of Surplife lights."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "SurplifeOptionsFlow":
        return SurplifeOptionsFlow()

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
            return self.async_create_entry(title=_title(self._discovery), data=_data(self._discovery))
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
            info = self._found[address]
            return self.async_create_entry(title=_title(info), data=_data(info))

        configured = self._async_current_ids()
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in configured:
                continue
            if _supported(info):
                self._found[info.address] = info
        if not self._found:
            return self.async_abort(reason="no_devices_found")

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDRESS): vol.In(
                    {addr: f"{_title(info)} ({addr})" for addr, info in self._found.items()}
                )
            }),
        )


class SurplifeOptionsFlow(OptionsFlow):
    """Disconnect delay: how long an idle connection stays open."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(CONF_DISCONNECT_DELAY, IDLE_DISCONNECT_SECONDS)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DISCONNECT_DELAY, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=1, max=3600)
                    )
                }
            ),
        )
