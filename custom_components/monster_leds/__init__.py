"""Monster / Surplife Bluetooth string lights."""
from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.match import ADDRESS, CONNECTABLE, BluetoothCallbackMatcher
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from .device import MonsterLeds

PLATFORMS = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    address: str = entry.data[CONF_ADDRESS].upper()
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise ConfigEntryNotReady(
            f"Could not find lights with address {address}; are they powered and in range of a "
            "Bluetooth adapter or an ESPHome Bluetooth proxy?"
        )

    device = MonsterLeds(ble_device, entry.title)
    last = bluetooth.async_last_service_info(hass, address, connectable=False)
    if last is not None:
        device.update_from_advertisement(None, last.manufacturer_data)

    @callback
    def _async_advertisement(
        service_info: bluetooth.BluetoothServiceInfoBleak, change: bluetooth.BluetoothChange
    ) -> None:
        device.update_from_advertisement(
            service_info.device if service_info.connectable else None,
            service_info.manufacturer_data,
        )

    @callback
    def _async_unavailable(_service_info: bluetooth.BluetoothServiceInfoBleak) -> None:
        device.set_available(False)

    entry.async_on_unload(
        bluetooth.async_register_callback(
            hass,
            _async_advertisement,
            BluetoothCallbackMatcher({ADDRESS: address, CONNECTABLE: False}),
            bluetooth.BluetoothScanningMode.PASSIVE,
        )
    )
    entry.async_on_unload(
        bluetooth.async_track_unavailable(hass, _async_unavailable, address, connectable=True)
    )

    entry.runtime_data = device
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _async_stop(_event: Event) -> None:
        await device.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
    return unload_ok
